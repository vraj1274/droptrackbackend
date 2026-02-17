"""
User service for managing user creation and profile management.
Handles automatic user creation from Cognito JWT claims.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from sqlmodel import Session, select
from app.models import User, Client, Dropper, UserRole
from app.security import is_superadmin_email

logger = logging.getLogger(__name__)


class UserServiceError(Exception):
    """Custom exception for user service errors."""


class UserService:
    """Service for user management operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_user_by_cognito_sub(self, cognito_sub: str) -> Optional[User]:
        """
        Get user by Cognito sub identifier.

        Args:
            cognito_sub: Cognito user identifier

        Returns:
            Optional[User]: User if found, None otherwise
        """
        statement = select(User).where(User.cognito_sub == cognito_sub)
        return self.db.exec(statement).first()

    def get_user_by_email(self, email: str) -> Optional[User]:
        """
        Get user by email address.

        Args:
            email: User email address

        Returns:
            Optional[User]: User if found, None otherwise
        """
        statement = select(User).where(User.email == email)
        return self.db.exec(statement).first()

    def create_user_from_claims(self, user_claims: Dict[str, Any]) -> User:
        """
        Create a new user from Cognito JWT claims.
        
        STRICT FLOW:
        1. [AUTH_CREATE] Validate custom:role from JWT (Required).
        2. [AUTH_DB] Create user in DB with that role.
        3. [AUTH_PROFILE] Create ONLY the matching profile row.
        """
        try:
            # ---------------------------------------------------------
            # 1. VALIDATION (Fail Fast)
            # ---------------------------------------------------------
            email_raw = user_claims.get("email")
            email_normalized = email_raw.strip().lower() if email_raw else None
            
            if not email_normalized:
                raise UserServiceError("Email is required for signup")

            role_from_claims = user_claims.get("custom:role")
            
            # LOG: [AUTH_CREATE]
            logger.info(
                "[AUTH_CREATE] Processing signup for email=%s. "
                "Source: JWT custom:role=%s", 
                email_normalized, role_from_claims
            )

            # Strict Role Validation
            if not role_from_claims:
                logger.error("[AUTH_CREATE] FAILED: Missing custom:role in JWT")
                raise UserServiceError("Signup requires 'custom:role' in JWT claims")
                
            if role_from_claims not in ["client", "dropper", "admin"]:
                 logger.error("[AUTH_CREATE] FAILED: Invalid role '%s'", role_from_claims)
                 raise UserServiceError(f"Invalid role '{role_from_claims}'. Must be 'client', 'dropper' or 'admin'.")

            # ---------------------------------------------------------
            # 2. DUPLICATE CHECK
            # ---------------------------------------------------------
            # Check cognito_sub
            if self.get_user_by_cognito_sub(user_claims["cognito_sub"]):
                raise UserServiceError(f"User with sub {user_claims['cognito_sub']} already exists")

            # Check email collision
            existing_user = self.get_user_by_email(email_normalized)
            if existing_user:
                 logger.warning(
                     "[AUTH_DB] COLLISION: User %s already exists with role=%s. "
                     "IGNORING new role=%s. Returning existing user.", 
                     email_normalized, existing_user.role.value, role_from_claims
                 )
                 # Safety: Return existing user. Do NOT overwrite.
                 return existing_user

            # ---------------------------------------------------------
            # 3. DB CREATION
            # ---------------------------------------------------------
            try:
                # Normalize role to uppercase to match UserRole enum (e.g., "client" -> "CLIENT")
                user_role = UserRole(role_from_claims.upper())
            except ValueError:
                 raise UserServiceError(f"Role map error for {role_from_claims}. Must match UserRole enum values.")

            logger.info("[AUTH_DB] Creating user row. Role: %s", user_role.value)

            user = User(
                cognito_sub=user_claims["cognito_sub"],
                email=email_normalized,
                name=user_claims.get("name", email_normalized.split("@")[0]),
                role=user_role,
                is_active=True,
                created_at=datetime.utcnow()
            )

            self.db.add(user)
            self.db.flush() # Generate ID

            logger.info(
                "[AUTH_DB] User inserted. ID: %s. Role stored: %s", 
                user.id, user.role.value
            )

            # ---------------------------------------------------------
            # 4. PROFILE CREATION
            # ---------------------------------------------------------
            logger.info("[AUTH_PROFILE] Creating profile table for role: %s", user_role.value)

            if user_role == UserRole.CLIENT:
                self._create_client_profile(user, cognito_role="CLIENT") # Use explicit values
            elif user_role == UserRole.DROPPER:
                 self._create_dropper_profile(user)
            elif user_role == UserRole.ADMIN:
                 # Admin users don't have a separate profile table yet
                 pass
            
            # Final Commit
            self.db.commit()
            self.db.refresh(user)
            return user

        except Exception as e:
            self.db.rollback()
            logger.error("[AUTH_CREATE] Critical Error: %s", e, exc_info=True)
            if isinstance(e, UserServiceError):
                raise
            raise UserServiceError(f"Signup failed: {str(e)}") from e

    def update_user_from_claims(self, user: User, user_claims: Dict[str, Any]) -> User:
        """
        Update existing user.
        
        STRICT FLOW:
        1. [AUTH_DB] Trusted Source. Ignore JWT role.
        2. Verify Profile Integrity.
        """
        try:
            updated = False
            jwt_role = user_claims.get("custom:role")
            
            # 1. Role Migration (Cognito -> DB Sync)
            # If the user's role in Cognito is different from the DB, trust Cognito (ID Token)
            # This allows users to correctly switch roles or fix setup issues.
            new_role_value = jwt_role
            if new_role_value and new_role_value.upper() != user.role.value.upper():
                logger.warning(
                    "[AUTH_SYNC] Role mismatch detected for user %s. "
                    "DB Role: '%s' vs JWT Role: '%s'. "
                    "ACTION: Updating DB to match JWT.",
                    user.email, user.role.value, new_role_value
                )
                try:
                    # Normalize role to uppercase to match UserRole enum
                    user.role = UserRole(new_role_value.upper())
                    updated = True
                except ValueError:
                    logger.error("Invalid role in JWT: %s. Keeping existing role.", new_role_value)

            # 2. Update allowed fields (Email/Name)
            email_raw = user_claims.get("email")
            new_email = email_raw.strip().lower() if email_raw else None
            
            if new_email and new_email != user.email:
                logger.info("Updating email %s -> %s", user.email, new_email)
                user.email = new_email
                updated = True

            new_name = user_claims.get("name")
            if new_name and new_name != user.name:
                user.name = new_name
                updated = True

            # 3. Profile Integrity Check & Auto-Creation
            # Ensure the correct profile exists for the CURRENT role
            if user.role == UserRole.DROPPER:
                dropper_profile = self.db.exec(
                    select(Dropper).where(Dropper.user_id == user.id)
                ).first()
                if not dropper_profile:
                    logger.info("[AUTH_PROFILE] Creating missing DROPPER profile for migrated user.")
                    self._create_dropper_profile(user)
                    updated = True # Ensure we commit
            
            elif user.role == UserRole.CLIENT:
                client_profile = self.db.exec(
                    select(Client).where(Client.user_id == user.id)
                ).first()
                if not client_profile:
                    logger.info("[AUTH_PROFILE] Creating missing CLIENT profile for migrated user.")
                    self._create_client_profile(user, cognito_role="CLIENT")
                    updated = True # Ensure we commit

            if updated:
                self.db.add(user)
                self.db.commit()
                self.db.refresh(user)

            return user

        except Exception as e:
            self.db.rollback()
            if isinstance(e, UserServiceError):
                raise
            raise UserServiceError(f"Login update failed: {str(e)}") from e

    def get_or_create_user_from_jwt(
        self, 
        token_claims: Dict[str, Any],
        patch_cognito: bool = True
    ) -> User:
        """
        LAZY SYNC PATTERN: Get or create user from JWT with role recovery.
        
        This implements the "Lazy Sync" pattern for user synchronization:
        1. Cognito Role Recovery: If custom:role is missing, default to 'dropper' and patch Cognito
        2. UPSERT Logic: Search by cognito_sub, create if not found
        3. Atomic Transaction: Ensure database record is created before login succeeds
        4. Detailed Logging: Log first-time login events
        
        Args:
            token_claims: JWT token claims (must include cognito_sub, email)
            patch_cognito: If True, attempt to patch missing Cognito attributes
            
        Returns:
            User: Existing or newly created user (stored in PostgreSQL)
            
        Raises:
            UserServiceError: If user creation fails or transaction cannot complete
        """
        cognito_sub = token_claims.get("cognito_sub")
        email = token_claims.get("email", "").strip().lower()
        
        if not cognito_sub:
            raise UserServiceError("Missing cognito_sub in JWT token")
        if not email:
            raise UserServiceError("Missing email in JWT token")
        
        # ============================================================
        # STEP 1: COGNITO ROLE RECOVERY
        # ============================================================
        role_from_jwt = token_claims.get("custom:role")
        role_patched = False
        
        if not role_from_jwt:
            logger.warning(
                "🔧 [LAZY_SYNC] Missing custom:role in JWT for user %s. "
                "Defaulting to 'dropper' and will patch Cognito.",
                email
            )
            role_from_jwt = "dropper"  # Safe default
            role_patched = True
            
            # Attempt to patch Cognito (non-blocking)
            if patch_cognito:
                try:
                    from app.services.cognito_admin import get_cognito_admin_service
                    cognito_admin = get_cognito_admin_service()
                    success = cognito_admin.update_user_role(cognito_sub, role_from_jwt)
                    if success:
                        logger.info(
                            "✅ [LAZY_SYNC] Successfully patched Cognito custom:role to '%s' for %s",
                            role_from_jwt, email
                        )
                    else:
                        logger.warning(
                            "⚠️  [LAZY_SYNC] Failed to patch Cognito custom:role for %s. "
                            "User will continue with default role.",
                            email
                        )
                except Exception as e:
                    logger.error(
                        "❌ [LAZY_SYNC] Error patching Cognito custom:role for %s: %s",
                        email, str(e)
                    )
        
        # Normalize role
        try:
            user_role = UserRole(role_from_jwt.upper())
        except ValueError:
            logger.error(
                "❌ [LAZY_SYNC] Invalid role '%s' for %s. Defaulting to DROPPER.",
                role_from_jwt, email
            )
            user_role = UserRole.DROPPER
            role_patched = True
        
        # ============================================================
        # STEP 2: UPSERT LOGIC - Search by cognito_sub
        # ============================================================
        user = self.get_user_by_cognito_sub(cognito_sub)
        
        if user:
            # Existing user - update and return
            logger.info(
                "🔄 [LAZY_SYNC] Existing user login: %s (role: %s)",
                email, user.role.value
            )
            return self.update_user_from_claims(user, token_claims)
        
        # ============================================================
        # STEP 3: FIRST-TIME LOGIN - Create user atomically
        # ============================================================
        logger.info(
            "🆕 [LAZY_SYNC] FIRST-TIME LOGIN detected for %s. Creating user record...",
            email
        )
        
        try:
            # Begin atomic transaction
            name = token_claims.get("name", email.split("@")[0])
            
            logger.info(
                "📝 [LAZY_SYNC] Creating user:\n"
                "   Email: %s\n"
                "   Cognito Sub: %s\n"
                "   Role: %s\n"
                "   Role Source: %s",
                email, cognito_sub[:20] + "...", user_role.value,
                "Patched (missing in JWT)" if role_patched else "JWT custom:role"
            )
            
            # Create user record
            user = User(
                cognito_sub=cognito_sub,
                email=email,
                name=name,
                role=user_role,
                is_active=True,
                created_at=datetime.utcnow()
            )
            
            self.db.add(user)
            self.db.flush()  # Generate ID
            
            logger.info(
                "✅ [LAZY_SYNC] User record created with ID: %s",
                user.id
            )
            
            # Create profile based on role
            if user_role == UserRole.CLIENT:
                logger.info("📋 [LAZY_SYNC] Creating CLIENT profile...")
                self._create_client_profile(user, cognito_role="CLIENT")
            elif user_role == UserRole.DROPPER:
                logger.info("📋 [LAZY_SYNC] Creating DROPPER profile...")
                self._create_dropper_profile(user)
            
            # Commit transaction
            self.db.commit()
            self.db.refresh(user)
            
            logger.info(
                "✅ [LAZY_SYNC] FIRST-TIME LOGIN COMPLETE:\n"
                "   User ID: %s\n"
                "   Email: %s\n"
                "   Role: %s\n"
                "   Profile Created: Yes\n"
                "   Database: Synced",
                user.id, email, user_role.value
            )
            
            return user
            
        except Exception as e:
            # Rollback on any error
            self.db.rollback()
            logger.error(
                "❌ [LAZY_SYNC] CRITICAL: Failed to create user for first-time login:\n"
                "   Email: %s\n"
                "   Cognito Sub: %s\n"
                "   Error: %s",
                email, cognito_sub, str(e),
                exc_info=True
            )
            raise UserServiceError(
                f"Failed to create user record for first-time login: {str(e)}"
            ) from e
    
    def get_or_create_user_from_claims(self, user_claims: Dict[str, Any]) -> User:
        """
        Get existing user or create new user from JWT claims.
        This is the main method used by authentication dependencies.

        This method ensures that:
        - New users (registration) are immediately stored in PostgreSQL
        - Existing users (login) are updated with fresh claims
        - Client/Dropper profiles are created automatically for new users
        - All data is persisted in PostgreSQL database

        Args:
            user_claims: User claims extracted from JWT token
                (must include cognito_sub, email, name, role)

        Returns:
            User: Existing or newly created user (stored in PostgreSQL)

        Raises:
            UserServiceError: If user retrieval or creation fails
        """
        # Use the new lazy sync method
        return self.get_or_create_user_from_jwt(user_claims, patch_cognito=True)

    def deactivate_user(self, user: User) -> User:
        """
        Deactivate a user account.

        Args:
            user: User to deactivate

        Returns:
            User: Deactivated user
        """
        user.is_active = False
        user.updated_at = datetime.utcnow()

        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    def activate_user(self, user: User) -> User:
        """
        Activate a user account.

        Args:
            user: User to activate

        Returns:
            User: Activated user
        """
        user.is_active = True
        user.updated_at = datetime.utcnow()

        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    def get_client_profile(self, user_id) -> Optional[Client]:
        """
        Get client profile by user ID.

        Args:
            user_id: User ID

        Returns:
            Optional[Client]: Client profile if found, None otherwise
        """
        statement = select(Client).where(Client.user_id == user_id)
        return self.db.exec(statement).first()

    def get_dropper_profile(self, user_id) -> Optional[Dropper]:
        """
        Get dropper profile by user ID.

        Args:
            user_id: User ID

        Returns:
            Optional[Dropper]: Dropper profile if found, None otherwise
        """
        statement = select(Dropper).where(Dropper.user_id == user_id)
        return self.db.exec(statement).first()

    def get_client_profile_data(self, user_id) -> Dict[str, Any]:
        """
        Get formatted client profile data with all fields including new profile fields.

        Args:
            user_id: User ID

        Returns:
            Dict[str, Any]: Formatted client profile data
        """
        client = self.get_client_profile(user_id)
        if not client:
            return {}

        # Format address as nested object
        address = None
        if client.street or client.city or client.state or client.zip_code:
            address = {
                "street": client.street,
                "city": client.city,
                "state": client.state,
                "zip_code": client.zip_code
            }

        return {
            "business_name": client.business_name,
            "business_type": client.business_type,
            "phone": client.phone_number,
            "address": address,
            "website": client.website,
            "description": client.description,
            "email_notifications": client.email_notifications,
            "sms_notifications": client.sms_notifications,
            "timezone": client.timezone,
            "language": client.language
        }

    def get_dropper_profile_data(self, user_id) -> Dict[str, Any]:
        """
        Get formatted dropper profile data with all fields including new profile fields.

        Args:
            user_id: User ID

        Returns:
            Dict[str, Any]: Formatted dropper profile data
        """
        dropper = self.get_dropper_profile(user_id)
        if not dropper:
            return {}

        return {
            "service_radius_km": dropper.service_radius_km,
            "base_location_lat": dropper.base_location_lat,
            "base_location_lng": dropper.base_location_lng,
            "id_verified": dropper.id_verified,
            "phone": dropper.phone_number,
            "emergency_contact_name": dropper.emergency_contact_name,
            "emergency_contact_phone": dropper.emergency_contact_phone,
            "is_available": dropper.is_available,
            "rating": dropper.rating,
            "total_jobs_completed": dropper.total_jobs_completed,
            "email_notifications": dropper.email_notifications,
            "sms_notifications": dropper.sms_notifications,
            "timezone": dropper.timezone,
            "language": dropper.language
        }

    def _validate_phone_number(self, phone: str) -> None:
        """
        Validate phone number format.

        Args:
            phone: Phone number to validate

        Raises:
            UserServiceError: If phone number is invalid
        """
        if not phone:
            return

        # Remove common formatting characters
        cleaned = phone.replace('+', '').replace('-', '').replace(' ', '')
        cleaned = cleaned.replace('(', '').replace(')', '')

        if not cleaned.isdigit():
            raise UserServiceError(
                "Phone number must contain only digits and formatting "
                "characters (+, -, space, parentheses)"
            )

        if len(cleaned) < 10 or len(cleaned) > 15:
            raise UserServiceError("Phone number must be between 10 and 15 digits")

    def _validate_coordinates(self, lat: Optional[float], lng: Optional[float]) -> None:
        """
        Validate geographic coordinates.

        Args:
            lat: Latitude
            lng: Longitude

        Raises:
            UserServiceError: If coordinates are invalid
        """
        if lat is not None and (lat < -90 or lat > 90):
            raise UserServiceError("Latitude must be between -90 and 90")

        if lng is not None and (lng < -180 or lng > 180):
            raise UserServiceError("Longitude must be between -180 and 180")

        # Both must be provided together or both None
        if (lat is None) != (lng is None):
            raise UserServiceError("Both latitude and longitude must be provided together")

    def _validate_service_radius(self, radius: Optional[int]) -> None:
        """
        Validate service radius.

        Args:
            radius: Service radius in kilometers

        Raises:
            UserServiceError: If radius is invalid
        """
        if radius is not None and (radius < 1 or radius > 50):
            raise UserServiceError("Service radius must be between 1 and 50 kilometers")

    def _validate_website(self, website: Optional[str]) -> Optional[str]:
        """
        Validate and normalize website URL.

        Args:
            website: Website URL

        Returns:
            Optional[str]: Normalized website URL

        Raises:
            UserServiceError: If website URL is invalid
        """
        if not website:
            return None

        # Add https:// if no protocol specified
        if not website.startswith('http://') and not website.startswith('https://'):
            website = f'https://{website}'

        # Basic validation - check for valid URL structure
        if not ('.' in website and len(website) > 10):
            raise UserServiceError("Invalid website URL format")

        return website

    def update_user_profile(self, user_id, profile_data: Dict[str, Any]) -> User:
        """
        Update user profile with partial update support and role-based field filtering.

        Args:
            user_id: User ID
            profile_data: Dictionary of fields to update (only provided fields will be updated)

        Returns:
            User: Updated user

        Raises:
            UserServiceError: If update fails or validation errors occur
        """
        try:
            # Get user
            user = self.db.get(User, user_id)
            if not user:
                raise UserServiceError(f"User with ID {user_id} not found")

            # Email is immutable - reject any attempt to change it
            if 'email' in profile_data:
                raise UserServiceError("Email address cannot be changed")

            # Update user-level fields if provided
            if 'name' in profile_data and profile_data['name']:
                user.name = profile_data['name']
                user.updated_at = datetime.utcnow()
                self.db.add(user)

            # Update role-specific profile
            if user.role == UserRole.CLIENT:
                self._update_client_profile(user_id, profile_data)
            elif user.role == UserRole.DROPPER:
                self._update_dropper_profile(user_id, profile_data)
            elif user.role == UserRole.ADMIN:
                # For admin users, we can update phone if provided
                # Note: Notification preferences would need to be stored in
                # User model or separate table
                # For now, we'll just handle name and phone which are already handled above
                pass

            # Commit all changes
            self.db.commit()
            self.db.refresh(user)

            logger.info("Profile updated successfully for user %s", user_id)
            return user

        except UserServiceError:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(
                "Profile update failed for user %s: %s",
                user_id, e, exc_info=True
            )
            raise UserServiceError(f"Failed to update profile: {str(e)}") from e

    def _update_client_profile(self, user_id, profile_data: Dict[str, Any]) -> None:
        """
        Update client-specific profile fields.

        Args:
            user_id: User ID
            profile_data: Dictionary of fields to update

        Raises:
            UserServiceError: If update fails
        """
        # Get or create client profile
        client = self.get_client_profile(user_id)
        if not client:
            raise UserServiceError("Client profile not found")

        # Validate phone if provided
        if 'phone' in profile_data and profile_data['phone']:
            self._validate_phone_number(profile_data['phone'])
            client.phone_number = profile_data['phone']

        # Validate and update website if provided
        if 'website' in profile_data:
            client.website = self._validate_website(profile_data['website'])

        # Update business information
        if 'business_name' in profile_data and profile_data['business_name']:
            client.business_name = profile_data['business_name']

        if 'business_type' in profile_data and profile_data['business_type']:
            client.business_type = profile_data['business_type']

        if 'description' in profile_data:
            client.description = profile_data['description']

        # Update address fields
        if 'street' in profile_data:
            client.street = profile_data['street']

        if 'city' in profile_data:
            client.city = profile_data['city']

        if 'state' in profile_data:
            client.state = profile_data['state']

        if 'zip_code' in profile_data:
            client.zip_code = profile_data['zip_code']

        # Update notification preferences
        if 'email_notifications' in profile_data:
            client.email_notifications = profile_data['email_notifications']

        if 'sms_notifications' in profile_data:
            client.sms_notifications = profile_data['sms_notifications']

        # Update display settings
        if 'timezone' in profile_data and profile_data['timezone']:
            client.timezone = profile_data['timezone']

        if 'language' in profile_data and profile_data['language']:
            client.language = profile_data['language']

        self.db.add(client)

    def _update_dropper_profile(self, user_id, profile_data: Dict[str, Any]) -> None:
        """
        Update dropper-specific profile fields.

        Args:
            user_id: User ID
            profile_data: Dictionary of fields to update

        Raises:
            UserServiceError: If update fails
        """
        # Get or create dropper profile
        dropper = self.get_dropper_profile(user_id)
        if not dropper:
            raise UserServiceError("Dropper profile not found")

        # Validate phone if provided
        if 'phone' in profile_data and profile_data['phone']:
            self._validate_phone_number(profile_data['phone'])
            dropper.phone_number = profile_data['phone']

        # Validate emergency contact phone if provided
        if 'emergency_contact_phone' in profile_data and profile_data['emergency_contact_phone']:
            self._validate_phone_number(profile_data['emergency_contact_phone'])
            dropper.emergency_contact_phone = profile_data['emergency_contact_phone']

        # Update emergency contact name
        if 'emergency_contact_name' in profile_data:
            dropper.emergency_contact_name = profile_data['emergency_contact_name']

        # Validate and update service radius
        if 'service_radius_km' in profile_data:
            self._validate_service_radius(profile_data['service_radius_km'])
            dropper.service_radius_km = profile_data['service_radius_km']

        # Validate and update base location
        if 'base_location_lat' in profile_data or 'base_location_lng' in profile_data:
            lat = profile_data.get('base_location_lat', dropper.base_location_lat)
            lng = profile_data.get('base_location_lng', dropper.base_location_lng)
            self._validate_coordinates(lat, lng)

            if 'base_location_lat' in profile_data:
                dropper.base_location_lat = profile_data['base_location_lat']
            if 'base_location_lng' in profile_data:
                dropper.base_location_lng = profile_data['base_location_lng']

        # Update availability
        if 'is_available' in profile_data:
            dropper.is_available = profile_data['is_available']

        # Update notification preferences
        if 'email_notifications' in profile_data:
            dropper.email_notifications = profile_data['email_notifications']

        if 'sms_notifications' in profile_data:
            dropper.sms_notifications = profile_data['sms_notifications']

        # Update display settings
        if 'timezone' in profile_data and profile_data['timezone']:
            dropper.timezone = profile_data['timezone']

        if 'language' in profile_data and profile_data['language']:
            dropper.language = profile_data['language']

        self.db.add(dropper)

    def _create_client_profile(self, user: User, cognito_role: str = "CLIENT") -> Client:
        """Create new client profile."""
        client = Client(
            user_id=user.id,
            business_name="New Business", # Default placeholder
            business_type="Other",
            role=cognito_role.upper(),
            created_at=datetime.utcnow()
        )
        self.db.add(client)
        return client

    def _create_dropper_profile(self, user: User) -> Dropper:
        """Create new dropper profile."""
        dropper = Dropper(
            user_id=user.id,
            id_verified=False,
            service_radius_km=5,
            created_at=datetime.utcnow()
        )
        self.db.add(dropper)
        return dropper


def get_user_service(db: Session) -> UserService:
    """
    Factory function to create UserService instance.

    Args:
        db: Database session

    Returns:
        UserService: Service instance
    """
    return UserService(db)

