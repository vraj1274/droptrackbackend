"""
Lazy Sync Utility Functions
Provides standalone functions for user synchronization and Cognito role recovery
"""

import logging
from typing import Dict, Any, Optional
from sqlmodel import Session
from app.services.user_service import get_user_service
from app.services.cognito import cognito_service
from app.services.cognito_admin import get_cognito_admin_service

logger = logging.getLogger(__name__)


async def get_or_create_user_from_jwt(
    token: str,
    db: Session,
    patch_cognito: bool = True
) -> Dict[str, Any]:
    """
    Standalone function to get or create user from JWT token.
    Implements the Lazy Sync pattern with role recovery.
    
    This function:
    1. Validates the JWT token
    2. Extracts user claims
    3. Recovers missing custom:role attribute (defaults to 'dropper')
    4. Patches Cognito if role is missing
    5. Creates or updates user in PostgreSQL (UPSERT)
    6. Ensures atomic database transaction
    7. Provides detailed logging
    
    Args:
        token: JWT token string
        db: Database session
        patch_cognito: If True, attempt to patch missing Cognito attributes
        
    Returns:
        Dict containing:
            - user: User object
            - is_new: Boolean indicating if user was newly created
            - role_patched: Boolean indicating if Cognito role was patched
            - error: Optional error message
            
    Raises:
        Exception: If token validation or user creation fails
    """
    try:
        # Step 1: Validate token and extract claims
        logger.info("🔐 [LAZY_SYNC] Validating JWT token...")
        user_claims = await cognito_service.validate_and_extract_user(token)
        
        cognito_sub = user_claims.get("cognito_sub")
        email = user_claims.get("email")
        role_from_jwt = user_claims.get("custom:role")
        
        logger.info(
            "✅ [LAZY_SYNC] Token validated:\n"
            "   Email: %s\n"
            "   Cognito Sub: %s\n"
            "   Role from JWT: %s",
            email, cognito_sub[:20] + "..." if cognito_sub else "N/A",
            role_from_jwt or "MISSING"
        )
        
        # Step 2: Check if role is missing
        role_patched = False
        if not role_from_jwt:
            logger.warning(
                "⚠️  [LAZY_SYNC] Missing custom:role in JWT. "
                "Will default to 'dropper' and patch Cognito."
            )
            role_patched = True
        
        # Step 3: Get or create user (with role recovery)
        user_service = get_user_service(db)
        user = user_service.get_or_create_user_from_jwt(
            user_claims,
            patch_cognito=patch_cognito
        )
        
        # Step 4: Determine if user was newly created
        is_new = user.created_at and (
            (user.updated_at is None) or 
            (user.created_at == user.updated_at)
        )
        
        logger.info(
            "✅ [LAZY_SYNC] User sync complete:\n"
            "   User ID: %s\n"
            "   Email: %s\n"
            "   Role: %s\n"
            "   Is New: %s\n"
            "   Role Patched: %s",
            user.id, user.email, user.role.value, is_new, role_patched
        )
        
        return {
            "user": user,
            "is_new": is_new,
            "role_patched": role_patched,
            "error": None
        }
        
    except Exception as e:
        logger.error(
            "❌ [LAZY_SYNC] Failed to sync user: %s",
            str(e),
            exc_info=True
        )
        return {
            "user": None,
            "is_new": False,
            "role_patched": False,
            "error": str(e)
        }


def check_and_patch_cognito_role(
    cognito_sub: str,
    expected_role: str = "dropper"
) -> Dict[str, Any]:
    """
    Check if user has custom:role in Cognito and patch if missing.
    
    Args:
        cognito_sub: User's Cognito sub identifier
        expected_role: Role to set if missing (default: 'dropper')
        
    Returns:
        Dict containing:
            - has_role: Boolean indicating if role exists
            - patched: Boolean indicating if role was patched
            - current_role: Current role value (or None)
            - error: Optional error message
    """
    try:
        cognito_admin = get_cognito_admin_service()
        
        # Get current attributes
        logger.info(
            "🔍 [COGNITO_CHECK] Checking custom:role for user %s...",
            cognito_sub[:20] + "..."
        )
        
        attributes = cognito_admin.get_user_attributes(cognito_sub)
        
        if not attributes:
            return {
                "has_role": False,
                "patched": False,
                "current_role": None,
                "error": "Failed to fetch user attributes"
            }
        
        current_role = attributes.get("custom:role")
        
        if current_role:
            logger.info(
                "✅ [COGNITO_CHECK] User has custom:role = '%s'",
                current_role
            )
            return {
                "has_role": True,
                "patched": False,
                "current_role": current_role,
                "error": None
            }
        
        # Role is missing - patch it
        logger.warning(
            "⚠️  [COGNITO_CHECK] Missing custom:role. Patching to '%s'...",
            expected_role
        )
        
        success = cognito_admin.update_user_role(cognito_sub, expected_role)
        
        if success:
            logger.info(
                "✅ [COGNITO_CHECK] Successfully patched custom:role to '%s'",
                expected_role
            )
            return {
                "has_role": False,
                "patched": True,
                "current_role": expected_role,
                "error": None
            }
        else:
            logger.error(
                "❌ [COGNITO_CHECK] Failed to patch custom:role"
            )
            return {
                "has_role": False,
                "patched": False,
                "current_role": None,
                "error": "Failed to update Cognito attribute"
            }
            
    except Exception as e:
        logger.error(
            "❌ [COGNITO_CHECK] Error checking/patching role: %s",
            str(e),
            exc_info=True
        )
        return {
            "has_role": False,
            "patched": False,
            "current_role": None,
            "error": str(e)
        }
