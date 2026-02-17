"""
FastAPI dependencies for authentication and authorization.
Provides JWT validation, user retrieval, and role-based access control.
"""
# pylint: disable=too-many-lines

from typing import Optional, List, Callable
from functools import wraps
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import Session, select
from sqlalchemy import func as sql_func
from app.database import get_session
from app.models import User, UserRole, Client, Dropper
from app.services.cognito import cognito_service, CognitoJWTError
from app.services.user_service import get_user_service, UserService, UserServiceError
from app.config import settings
from app.security import (
    DEFAULT_SUPERADMIN_EMAILS,
    get_primary_superadmin_email,
    is_superadmin_email,
)


# OAuth2 scheme for token extraction
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="token",  # This is just for OpenAPI docs, we don't use this endpoint
    scheme_name="JWT",
    auto_error=False  # Don't auto-raise error, we'll handle it manually
)

# HTTP Bearer scheme for optional token extraction
http_bearer = HTTPBearer(auto_error=False)


class AuthenticationError(HTTPException):
    """Custom authentication error with consistent formatting."""

    def __init__(self, detail: str = "Could not validate credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthorizationError(HTTPException):
    """Custom authorization error for insufficient permissions."""

    def __init__(self, detail: str | dict = "Insufficient permissions"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


async def _get_or_create_dev_user(db: Session) -> User:
    """
    Create or get a default development user when in development environment.
    This bypasses Cognito authentication for local development.
    In debug mode, uses the primary superadmin email to get admin access.

    WARNING: This function should ONLY be called in development environment.
    
    SECURITY: Triple-check validation ensures this NEVER runs in production:
    1. ENVIRONMENT must be 'development', 'dev', or 'local'
    2. DATABASE_URL must contain 'localhost' or '127.0.0.1'
    3. DEBUG must be True
    All three conditions must be met, or authentication fails.
    """
    import logging  # pylint: disable=import-outside-toplevel
    logger = logging.getLogger(__name__)
    
    # CRITICAL FIX #1: TRIPLE-CHECK validation - ALL conditions must be true
    is_dev_environment = settings.environment.lower() in ["development", "dev", "local"]
    is_localhost_db = "localhost" in settings.database_url or "127.0.0.1" in settings.database_url
    is_debug_enabled = settings.debug
    
    # All three conditions must be true - fail hard if any is false
    if not (is_dev_environment and is_localhost_db and is_debug_enabled):
        logger.error(
            "❌ CRITICAL: _get_or_create_dev_user() BLOCKED by triple-check validation!\n"
            "   Environment: %s (must be development/dev/local) ✓=%s\n"
            "   Database: %s (must contain localhost/127.0.0.1) ✓=%s\n"
            "   Debug: %s (must be True) ✓=%s\n"
            "   RESULT: Authentication DENIED - production safety enforced",
            settings.environment, is_dev_environment,
            "localhost" if is_localhost_db else "remote", is_localhost_db,
            settings.debug, is_debug_enabled
        )
        error_msg = (
            "Development user fallback is DISABLED in production. "
            "All three conditions required: ENVIRONMENT=development, DEBUG=true, localhost database. "
            f"Current state: ENV={settings.environment} ({'✓' if is_dev_environment else '✗'}), "
            f"DEBUG={settings.debug} ({'✓' if is_debug_enabled else '✗'}), "
            f"DB={'localhost' if is_localhost_db else 'remote'} ({'✓' if is_localhost_db else '✗'})"
        )
        raise AuthenticationError(error_msg)
    
    logger.warning("⚠️ Using development user fallback (dev environment only)")

    # Use admin email in debug mode to get admin access through existing logic
    admin_email = get_primary_superadmin_email() or DEFAULT_SUPERADMIN_EMAILS[0]
    dev_email = admin_email if settings.debug else "dev@local.test"
    
    # Generate a deterministic cognito_sub based on email for consistency
    import hashlib  # pylint: disable=import-outside-toplevel
    deterministic_sub = f"dev-{hashlib.sha256(dev_email.encode()).hexdigest()[:36]}"
    
    # Try to find existing user by cognito_sub first (most reliable)
    statement = select(User).where(User.cognito_sub == deterministic_sub)
    existing_user = db.exec(statement).first()
    
    # If not found by cognito_sub, try by email
    # (for backwards compatibility with existing dev users)
    if not existing_user:
        statement = select(User).where(User.email == dev_email)
        existing_user = db.exec(statement).first()
        
        # If found by email, update its cognito_sub to be deterministic
        if existing_user and not existing_user.cognito_sub.startswith("dev-"):
            pass  # Keep existing cognito_sub if it's a real Cognito user
        elif existing_user:
            # Update to deterministic sub for consistency
            existing_user.cognito_sub = deterministic_sub
            db.add(existing_user)
            db.commit()
            db.refresh(existing_user)
            logger.info("✅ Updated dev user cognito_sub to deterministic value")
    
    if existing_user:
        # Update existing user to CLIENT role in debug mode (but keep admin email for access)
        if settings.debug and existing_user.role != UserRole.CLIENT:
            existing_user.role = UserRole.CLIENT
            db.add(existing_user)
            db.commit()
            db.refresh(existing_user)
            logger.info(
                "✅ Updated dev user to CLIENT role for debug mode (using admin email for access)"
            )
        
        # Ensure Client profile exists with admin role if using admin email
        if settings.debug and dev_email == admin_email:
            existing_client = db.exec(
                select(Client).where(Client.user_id == existing_user.id)
            ).first()
            
            if existing_client:
                # Update Client.role to 'ADMIN' for admin access
                if existing_client.role != 'ADMIN':
                    existing_client.role = 'ADMIN'
                    db.add(existing_client)
                    db.commit()
            else:
                # Create Client profile with admin role
                from datetime import datetime  # pylint: disable=import-outside-toplevel  # pylint: disable=import-outside-toplevel
                client = Client(
                    user_id=existing_user.id,
                    business_name="Development Business",
                    business_type="general",
                    role='ADMIN',  # Admin role for admin email
                    created_at=datetime.utcnow()
                )
                db.add(client)
                db.commit()
        
        return existing_user
    
    # Create new dev user with CLIENT role in debug mode (but use admin email for access)
    # Use a deterministic cognito_sub based on email to ensure consistency across logins
    # pylint: disable=import-outside-toplevel
    import hashlib  # pylint: disable=reimported
    deterministic_sub = f"dev-{hashlib.sha256(dev_email.encode()).hexdigest()[:36]}"
    
    dev_user = User(
        cognito_sub=deterministic_sub,
        email=dev_email,
        name="Development User",
        role=UserRole.CLIENT,  # CLIENT role, but admin email grants admin access
        is_active=True
    )
    db.add(dev_user)
    db.commit()
    db.refresh(dev_user)
    
    # Ensure Client profile exists
    existing_client = db.exec(
        select(Client).where(Client.user_id == dev_user.id)
    ).first()
    
    if not existing_client:
        from datetime import datetime  # pylint: disable=import-outside-toplevel
        # If using admin email, set role to 'admin', otherwise 'client'
        client_role = 'admin' if (settings.debug and dev_email == admin_email) else 'client'
        client = Client(
            user_id=dev_user.id,
            business_name="Development Business",
            business_type="general",
            role=client_role.upper(),  # Admin role if using admin email
            created_at=datetime.utcnow()
        )
        db.add(client)
        db.commit()
    
    if settings.debug:
        # pylint: disable=import-outside-toplevel
        import logging  # pylint: disable=reimported
        logger = logging.getLogger(__name__)
        logger.info(
            "✅ Created dev user with email %s for debug mode (admin access enabled)",
            dev_email
        )
    
    return dev_user


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    db: Session = Depends(get_session)
) -> User:
    """
    Dependency to get the current authenticated user from JWT token.
    Auto-creates user if not exists in database (via user_service).
    
    CLEAN ARCHITECTURE:
    1. Validate JWT
    2. Extract Claims (including custom:role)
    3. Delegate to UserService to Get or Create User
    4. Return User
    """
    import logging
    logger = logging.getLogger(__name__)
    
    token = credentials.credentials if credentials else None
    
    # Check if we're in development mode
    is_dev_environment = settings.environment.lower() in ["development", "dev", "local"]
    
    # Development mode: allow requests without token ONLY in dev environment
    if is_dev_environment and not token:
        logger.warning("⚠️ No JWT token provided in development mode. Falling back to dev user.")
        return await _get_or_create_dev_user(db)
    
    # Production mode or token provided: validate token
    if not token:
        raise AuthenticationError(
            "Authentication required. Please provide a valid JWT token."
        )
    
    try:
        # 1. Validate token and extract user claims
        user_claims = await cognito_service.validate_and_extract_user(token)
        
        if settings.debug:
            logger.debug("✅ JWT validated. Claims: %s", user_claims.keys())
        
        # 2. Get User Service
        user_service = get_user_service(db)
        
        # 3. Get or Create User
        # This handles:
        # - Checking if user exists (by cognito_sub)
        # - Creating if new (using custom:role from JWT)
        # - Ensuring profile exists
        # - Updating basic info (email/name) if changed
        # - IGNORING role changes (DB is authority)
        user = user_service.get_or_create_user_from_claims(user_claims)
        
        if not user:
            raise AuthenticationError("User creation failed internally.")
            
        # 4. Check status
        if not user.is_active:
             raise AuthenticationError("User account is inactive/blocked.")
             
        # 5. DIAGNOSTIC: Check for Role Mismatch (No logic change, just logging)
        # We want to know if the JWT says one thing but the DB says another.
        # This is common during development or if a user re-signs up.
        jwt_role = user_claims.get("custom:role")
        if jwt_role and jwt_role.upper() != user.role.value.upper():
            logger.warning(
                "⚠️ ROLE MISMATCH DETECTED:\n"
                "   User Email: %s\n"
                "   JWT Role:   %s\n"
                "   DB Role:    %s\n"
                "   Action:     Using DB role (Source of Truth)",
                user.email, jwt_role, user.role.value
            )
            # Attach diagnostic info to user object for potential use in endpoints (optional)
            # We use a dynamic attribute so it doesn't affect Pydantic validation if not in schema
            setattr(user, "_jwt_role_diagnostic", jwt_role)

        return user
        
    except CognitoJWTError as e:
        # [FIX] Fail loudly if token is invalid, even in dev mode.
        # If the user provides a token, it MUST be valid.
        # To use the dev user, the client should send NO token (handled above).
        logger.error("❌ JWT token validation failed: %s", str(e))
        raise AuthenticationError(f"Token validation failed: {str(e)}") from e
        
    except Exception as e:
        # Log full error details for debugging
        logger.error("❌ Unexpected authentication error: %s", str(e), exc_info=True)

        # In development mode ONLY, fallback
        if is_dev_environment:
            logger.warning("⚠️ Unexpected error in dev mode. Fallback to dev user.")
            return await _get_or_create_dev_user(db)

        raise AuthenticationError(f"Authentication error: {str(e)}") from e


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to get current active user.
    
    Args:
        current_user: Current user from get_current_user dependency
        
    Returns:
        User: Current active user
        
    Raises:
        AuthenticationError: If user is inactive
    """
    if not current_user.is_active:
        raise AuthenticationError("User account is inactive")
    return current_user


def require_role(allowed_roles: List[UserRole]) -> Callable:
    """
    Dependency factory for role-based access control.
    
    Args:
        allowed_roles: List of roles that are allowed to access the endpoint
        
    Returns:
        Callable: Dependency function that validates user role
        
    Example:
        @app.get("/admin/users")
        async def get_users(user: User = Depends(require_role([UserRole.ADMIN]))):
            return users
    """
    def role_checker(current_user: User = Depends(get_current_active_user)) -> User:
        # Debug logging
        import logging  # pylint: disable=import-outside-toplevel  # pylint: disable=import-outside-toplevel
        role_logger = logging.getLogger(__name__)
        
        # Get role value for comparison (handle both enum and string)
        if isinstance(current_user.role, UserRole):
            user_role_value = current_user.role.value
        else:
            user_role_value = str(current_user.role)
        allowed_role_values = [
            r.value if isinstance(r, UserRole) else str(r)
            for r in allowed_roles
        ]
        
        # Log role check details with more context
        role_type = type(current_user.role).__name__
        cognito_sub = getattr(current_user, 'cognito_sub', 'N/A')
        role_logger.info(
            "🔐 Role check: user_id=%s, email=%s, "
            "user_role=%s (type: %s), "
            "allowed_roles=%s, "
            "is_active=%s, "
            "cognito_sub=%s",
            current_user.id, current_user.email, user_role_value, role_type,
            allowed_role_values, current_user.is_active, cognito_sub
        )
        
        # Robust role comparison - handle both enum and string comparisons
        role_match = False
        if isinstance(current_user.role, UserRole):
            # Direct enum comparison
            role_match = current_user.role in allowed_roles
        else:
            # String comparison fallback
            role_match = user_role_value in allowed_role_values
        
        # Additional check: if role doesn't match, log detailed info for debugging
        if not role_match:
            # SECURITY: Role source is always database (source of truth)
            role_source = "Database (source of truth)"

            cognito_sub = getattr(current_user, 'cognito_sub', 'N/A')
            role_type = type(current_user.role).__name__
            role_logger.warning(
                "⚠️  Role mismatch detected:\n"
                "   User ID: %s\n"
                "   Email: %s\n"
                "   Current Role: %s (type: %s)\n"
                "   Required Roles: %s\n"
                "   Role Source: %s\n"
                "   Is Active: %s\n"
                "   Cognito Sub: %s",
                current_user.id, current_user.email, user_role_value, role_type,
                allowed_role_values, role_source, current_user.is_active, cognito_sub
            )
        
        if not role_match:
            allowed_role_names = allowed_role_values
            
            # Provide helpful error message based on user's role
            if isinstance(current_user.role, UserRole):
                user_role_enum = current_user.role
            else:
                user_role_enum = UserRole(user_role_value)
            
            if user_role_enum == UserRole.CLIENT and UserRole.DROPPER in allowed_roles:
                suggestion = (
                    "If you have a dropper account, please sign out and "
                    "sign in with your dropper credentials."
                )
            elif user_role_enum == UserRole.DROPPER and UserRole.CLIENT in allowed_roles:
                suggestion = (
                    "If you have a client account, please sign out and "
                    "sign in with your client credentials."
                )
            else:
                suggestion = "Please contact support if you believe this is an error."
            
            # Provide more helpful error message
            error_message = (
                f"Access denied. This endpoint requires one of these "
                f"roles: {', '.join(allowed_role_names)}. "
                f"Your current role is '{user_role_value}'. "
            )
            
            # Add specific suggestions based on the role mismatch
            if UserRole.DROPPER in allowed_roles and user_role_value != "DROPPER":
                error_message += (
                    "To access dropper features, your account role in the database "
                    "must be set to 'DROPPER'. "
                    "Please contact support to update your account role."
                )
            elif UserRole.CLIENT in allowed_roles and user_role_value != "CLIENT":
                error_message += (
                    "To access client features, your Cognito user must have "
                    "'custom:user_role' set to 'CLIENT'. "
                    "Please contact support to update your account role."
                )
            else:
                error_message += suggestion
            
            error_detail = {
                "error": {
                    "type": "role_access_denied",
                    "code": 403,
                    "message": error_message,
                    "current_role": user_role_value,
                    "required_roles": allowed_role_names,
                    "suggestion": suggestion,
                    "user_email": current_user.email,
                    "user_id": str(current_user.id),
                    "role_type": type(current_user.role).__name__,
                    "troubleshooting": {
                        "check_database": (
                            "Your role is stored in the database. "
                            "Contact support to update your account role if needed."
                        ),
                        "contact_support": (
                            "If the issue persists, contact support with "
                            "your email and user ID"
                        )
                    }
                }
            }
            
            role_logger.warning(
                "❌ Role access DENIED: user_id=%s, email=%s, "
                "user_role=%s, required_roles=%s",
                current_user.id, current_user.email, user_role_value, allowed_role_names
            )
            
            # Pass dict directly - FastAPI will serialize it to JSON
            raise AuthorizationError(error_detail)
        return current_user
    
    return role_checker


def require_client_role() -> Callable:
    """
    Convenience dependency for client-only endpoints.
    Also allows configured superadmin emails (vraj.suthar+admin@thelinetech.uk, info@thelinetech.uk)
    to access client features.
    
    Returns:
        Callable: Dependency function that requires client role or admin email
    """
    def client_checker(current_user: User = Depends(get_current_active_user)) -> User:
        import logging  # pylint: disable=import-outside-toplevel
        logger = logging.getLogger(__name__)
        
        # CRITICAL: Allow configured superadmin emails regardless of stored role
        user_email_raw = current_user.email if current_user.email else None
        user_email_normalized = user_email_raw.strip().lower() if user_email_raw else None
        if is_superadmin_email(user_email_normalized):
            if settings.debug:
                logger.debug(
                    "✅ Superadmin email accessing client endpoint - ALLOWED (role: %s)",
                    current_user.role.value,
                )
            else:
                logger.info("✅ Superadmin email accessing client endpoint - ALLOWED")
            return current_user
        
        # Allow regular clients - robust comparison
        user_role_str = str(current_user.role.value if hasattr(current_user.role, 'value') else current_user.role).upper()
        if user_email_normalized and is_superadmin_email(user_email_normalized):
            return current_user
        
        if user_role_str == UserRole.CLIENT:
            return current_user
        
        # Deny access for other users
        if settings.debug:
            logger.debug(
                "❌ Client access DENIED:\n"
                "   Role: %s",
                current_user.role.value
            )
        else:
            logger.warning("❌ Client access DENIED")
        
        # Provide helpful error message based on user's role
        if current_user.role == UserRole.DROPPER:
            error_detail = {
                "error": {
                    "type": "role_access_denied",
                    "code": 403,
                    "message": (
                        "This endpoint is for client accounts only. "
                        "You are logged in as a dropper."
                    ),
                    "current_role": current_user.role.value,
                    "required_roles": ["client"],
                    "suggestion": (
                        "If you have a client account, please sign out and sign in "
                        "with your client credentials."
                    ),
                    "user_email": current_user.email
                }
            }
        else:
            error_detail = {
                "error": {
                    "type": "role_access_denied", 
                    "code": 403,
                    "message": (
                        f"Access denied. Required roles: ['client']. "
                        f"Current role: {current_user.role.value}"
                    ),
                    "current_role": current_user.role.value,
                    "required_roles": ["client"],
                    "user_email": current_user.email
                }
            }
        
        raise AuthorizationError(str(error_detail))
    
    return client_checker


def require_dropper_role() -> Callable:
    """
    Convenience dependency for dropper-only endpoints.
    Also checks JWT token role as fallback if database role doesn't match.
    This ensures users registered in Cognito with dropper role can access
    endpoints even if role sync hasn't completed yet.
    
    Returns:
        Callable: Dependency function that requires dropper role
    """
    async def dropper_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        # pylint: disable=import-outside-toplevel
        import logging
        logger = logging.getLogger(__name__)
 
        # Check if user has dropper role in database
        if current_user.role == UserRole.DROPPER:
            return current_user
 
        # If not, provide detailed error
        role_value = current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role)

        # Build detailed error message
        error_message = (
            f"Access denied. This endpoint requires 'dropper' role. "
            f"Your current role is '{role_value}'. "
            "Please ensure you are signed in with a dropper account."
        )

        error_detail = {
            "error": {
                "type": "role_access_denied",
                "code": 403,
                "message": error_message,
                "current_role": role_value,
                "required_roles": ["dropper"],
                "user_email": current_user.email,
                "user_id": str(current_user.id),
                "cognito_sub": current_user.cognito_sub,
                "suggestion": (
                    "To access dropper features:\n"
                    "1. Ensure your Cognito user has 'custom:user_role' set to 'dropper'\n"
                    "2. Verify App Client attribute mapping includes custom:user_role in ID token\n"
                    "3. Sign out and sign in again to get a fresh token with updated role\n"
                    "4. If the issue persists, contact support with your email and user ID"
                ),
                "troubleshooting": {
                    "step_1": "Check Cognito User Pool → Users → Your User → Attributes → custom:user_role should be 'dropper'",
                    "step_2": "Check App Client → Attribute read permissions → Ensure custom:user_role is included",
                    "step_3": "Sign out completely and sign in again to refresh your JWT token",
                    "step_4": "Check backend logs for detailed role check information",
                    "debug_endpoint": (
                        "Use /api/v1/dropper/debug/role to check your current role and profile status"
                    )
                }
            }
        }

        logger.error(
            "❌ Dropper access DENIED: user_id=%s, email=%s, role=%s, cognito_sub=%s",
            current_user.id, current_user.email, current_user.role.value,
            current_user.cognito_sub
        )

        raise AuthorizationError(error_detail)

    return dropper_checker


def require_admin_role() -> Callable:
    """
    Convenience dependency for admin-only endpoints.
    Allows users with ADMIN role OR clients with role='admin' in the clients table.
    
    Security Implementation:
    - JWT token validation: Validates signature, expiration, issuer, and audience
    - Role extraction: Extracts role from JWT token claims (custom:user_role)
    - Role mapping: Maps 'superadmin' from Cognito to 'admin' in backend
    - Email-based security: Only configured superadmin emails can have admin access
    - Database verification: Checks Client.role='admin' for CLIENT users
    - Audit logging: All admin access attempts are logged
    
    JWT Validation Process:
    1. Extract JWT token from Authorization header (Bearer token)
    2. Validate JWT signature using Cognito JWKS public keys
    3. Verify token expiration (exp claim)
    4. Verify token issuer (iss claim) matches Cognito User Pool
    5. Verify token audience (aud claim) matches App Client ID
    6. Extract user claims including custom:user_role
    7. Map 'superadmin' role to 'admin' for backend processing
    
    Role Extraction:
    - Role is ONLY extracted from JWT token claims (custom:user_role)
    - Role is NEVER accepted from request body or query parameters
    - Role mapping: 'superadmin' -> 'admin', 'client' -> 'client', 'dropper' -> 'dropper'
    
    Returns:
        Callable: Dependency function that requires admin role or client
        with admin role in clients table
        
    Raises:
        401 Unauthorized: If JWT token is invalid, expired, or missing
        403 Forbidden: If user does not have admin role or admin email
    """
    def admin_checker(
        current_user: User = Depends(get_current_active_user),
        db: Session = Depends(get_session)
    ) -> User:
        import logging  # pylint: disable=import-outside-toplevel
        logger = logging.getLogger(__name__)
        
        # Allow users with ADMIN role in users table
        if current_user.role == UserRole.ADMIN:
            return current_user
        
        # Check if user is a CLIENT with role='admin' in the clients table
        if current_user.role == UserRole.CLIENT:
            client_profile = db.exec(
                select(Client).where(Client.user_id == current_user.id)
            ).first()
            
            if current_user.email:
                user_email_normalized = current_user.email.strip().lower()
            else:
                user_email_normalized = None
            is_superadmin = is_superadmin_email(user_email_normalized)
            
            # If Client profile doesn't exist, check if this is the superadmin email
            if not client_profile:
                if is_superadmin:
                    # Create Client profile with role='admin'
                    if settings.debug:
                        logger.debug(
                            "Creating missing Client profile for "
                            "superadmin user with role='admin'"
                        )
                    else:
                        logger.info("Creating missing Client profile for superadmin user")
                    from datetime import datetime  # pylint: disable=import-outside-toplevel  # pylint: disable=import-outside-toplevel
                    client_profile = Client(
                        user_id=current_user.id,
                        business_name=f"{current_user.name}'s Business",
                        business_type="general",
                        role='ADMIN',
                        created_at=datetime.utcnow()
                    )
                    db.add(client_profile)
                    db.commit()
                    db.refresh(client_profile)
                else:
                    # Not superadmin, deny access
                    logger.info(
                        "❌ Admin access DENIED: No Client profile and email is not superadmin:\n"
                        "   Email: %s\n"
                        "   User ID: %s",
                        current_user.email, current_user.id
                    )
                    raise AuthorizationError(
                        "Access denied. Required roles: ['admin']. "
                        f"Current user role: {current_user.role.value}. "
                        f"Email: {current_user.email}"
                    )
            
            # Check if Client profile has role='ADMIN' (only for configured superadmin emails)
            if client_profile and client_profile.role == 'ADMIN':
                # Security check: Verify this is actually the superadmin email
                if is_superadmin:
                    if settings.debug:
                        logger.debug(
                            "✅ Admin access GRANTED to client user:\n"
                            "   User Role: %s\n"
                            "   Client Role: %s",
                            current_user.role.value, client_profile.role
                        )
                    else:
                        logger.info("✅ Admin access GRANTED to client user")
                    return current_user
                else:
                    # Security: If Client.role is 'admin' but email doesn't match, fix it
                    logger.warning(
                        "⚠️ Security issue: Client.role='ADMIN' but email "
                        "doesn't match superadmin. "
                        "Fixing Client.role to 'CLIENT'"
                    )
                    client_profile.role = 'CLIENT'
                    db.add(client_profile)
                    db.commit()
                    raise AuthorizationError(
                        "Access denied. Only configured superadmin emails have admin access. "
                        f"Email: {current_user.email}"
                    )
        
        # Deny access for other users
        client_role = None
        if current_user.role == UserRole.CLIENT:
            client_profile = db.exec(
                select(Client).where(Client.user_id == current_user.id)
            ).first()
            if client_profile:
                client_role = client_profile.role
        
        if settings.debug:
            logger.debug(
                "❌ Admin access DENIED:\n"
                "   User Role: %s\n"
                "   Client Role: %s",
                current_user.role.value, client_role
            )
        else:
            logger.info("❌ Admin access DENIED")
        raise AuthorizationError(
            f"Access denied. Required roles: ['admin']. "
            f"Current user role: {current_user.role.value}. "
            f"Client role: {client_role if client_role else 'N/A'}. "
            f"Email: {current_user.email}"
        )
    
    return admin_checker


def require_client_or_admin() -> Callable:
    """
    Convenience dependency for endpoints accessible by clients or admins.
    Allows regular clients, admins, or the configured superadmin emails
    (vraj.suthar+admin@thelinetech.uk, info@thelinetech.uk)
    
    Returns:
        Callable: Dependency function that requires client or admin role,
        or superadmin client access
    """
    def client_or_admin_checker(current_user: User = Depends(get_current_active_user)) -> User:
        # Only this specific email gets admin access. All other clients remain regular clients.
        # Normalize email for comparison
        user_email_normalized = current_user.email.strip().lower() if current_user.email else None
        
        # Allow client, admin, or superadmin client users
        if current_user.role in [UserRole.CLIENT, UserRole.ADMIN]:
            # If it's a client with superadmin email, log it
            if (current_user.role == UserRole.CLIENT and 
                user_email_normalized and 
                is_superadmin_email(user_email_normalized)):
                import logging  # pylint: disable=import-outside-toplevel
                logger = logging.getLogger(__name__)
                logger.debug(
                    "Superadmin client user accessing client_or_admin endpoint: %s",
                    current_user.email,
                )
            return current_user
        
        # Deny access for other users
        raise AuthorizationError(
            f"Access denied. Required roles: ['client', 'admin']. "
            f"Current role: {current_user.role.value}"
        )
    
    return client_or_admin_checker


def require_dropper_or_admin() -> Callable:
    """
    Convenience dependency for endpoints accessible by droppers or admins.
    
    Returns:
        Callable: Dependency function that requires dropper or admin role
    """
    return require_role([UserRole.DROPPER, UserRole.ADMIN])


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    db: Session = Depends(get_session)
) -> Optional[User]:
    """
    Dependency to get current user if token is provided, otherwise None.
    Useful for endpoints that work with or without authentication.
    
    Args:
        credentials: Optional HTTP Bearer credentials
        db: Database session
        
    Returns:
        Optional[User]: Current user if authenticated, None otherwise
    """
    import logging  # pylint: disable=import-outside-toplevel
    logger = logging.getLogger(__name__)
    
    # Check if we're in development mode (explicit check)
    is_dev_environment = settings.environment.lower() in ["development", "dev", "local"]
    
    if not credentials or not credentials.credentials:
        # In development mode ONLY, return dev user if no token
        if is_dev_environment:
            logger.debug("No token provided in development mode. Returning dev user.")
            return await _get_or_create_dev_user(db)
        return None
    
    try:
        # Create a mock credentials object to pass to get_current_user
        # get_current_user will extract the token from credentials
        return await get_current_user(credentials, db)
    except AuthenticationError:
        # In development mode ONLY, fall back to dev user on auth error
        if is_dev_environment:
            logger.debug("Authentication error in development mode. Returning dev user.")
            return await _get_or_create_dev_user(db)
        return None


# Decorator for role-based access control on route functions
def requires_role(allowed_roles: List[UserRole]):
    """
    Decorator for role-based access control on route functions.
    Alternative to using the dependency directly.
    
    Args:
        allowed_roles: List of roles that are allowed to access the function
        
    Example:
        @requires_role([UserRole.ADMIN])
        async def admin_function(user: User):
            return {"message": "Admin access granted"}
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user from kwargs (assumes user is passed as parameter)
            user = kwargs.get('user') or kwargs.get('current_user')
            if not user or not isinstance(user, User):
                raise AuthenticationError("User not found in function parameters")
            
            if user.role not in allowed_roles:
                allowed_role_names = [role.value for role in allowed_roles]
                raise AuthorizationError(
                    f"Access denied. Required roles: {allowed_role_names}. "
                    f"Current role: {user.role.value}"
                )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator


# Helper function to check if user has specific role
def user_has_role(user: User, role: UserRole) -> bool:
    """
    Check if user has a specific role.
    
    Args:
        user: User to check
        role: Role to check for
        
    Returns:
        bool: True if user has the role, False otherwise
    """
    return user.role == role


def user_has_any_role(user: User, roles: List[UserRole]) -> bool:
    """
    Check if user has any of the specified roles.
    
    Args:
        user: User to check
        roles: List of roles to check for
        
    Returns:
        bool: True if user has any of the roles, False otherwise
    """
    return user.role in roles


# Export commonly used dependencies
__all__ = [
    "oauth2_scheme",
    "get_current_user",
    "get_current_active_user",
    "get_optional_user",
    "require_role",
    "require_client_role",
    "require_dropper_role", 
    "require_admin_role",
    "require_client_or_admin",
    "require_dropper_or_admin",
    "requires_role",
    "user_has_role",
    "user_has_any_role",
    "AuthenticationError",
    "AuthorizationError",
]
