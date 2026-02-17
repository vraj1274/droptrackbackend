"""
User profile API endpoints.
Provides endpoints for user profile management and current user information.
"""

import logging
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session
from pydantic import ValidationError as PydanticValidationError
from app.database import get_session
from app.api.deps import get_current_active_user
from app.models import User, UserRole
from app.schemas.user import UserProfileResponse, UserProfileUpdateRequest
from app.services.user_service import get_user_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/profile", response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
) -> UserProfileResponse:
    """
    Get profile data for the currently authenticated user.
    
    This endpoint returns comprehensive profile information based on the user's role:
    - Client: includes business info, address, website, description, notification preferences
    - Dropper: includes service settings, location, emergency contact, notification preferences
    - Admin: basic profile information
    
    The user identity is determined from the JWT token's cognito_sub claim,
    ensuring users can only access their own profile data.
    
    Returns:
        UserProfileResponse: User profile data with all role-specific fields,
                           address formatted as nested object, and notification preferences
        
    Raises:
        HTTPException 401: If user is not authenticated
        HTTPException 500: If profile cannot be retrieved
    """
    start_time = time.time()
    try:
        # Suppress cognito_sub from logs
        logger.debug(f"Profile retrieval request for user (role: {current_user.role.value})")
        
        user_service = get_user_service(db)
        
        # Build base profile response
        profile_data = {
            "cognito_sub": current_user.cognito_sub,
            "email": current_user.email,
            "name": current_user.name,
            "role": current_user.role.value,
            "is_active": current_user.is_active,
            "created_at": current_user.created_at,
            "updated_at": current_user.updated_at
        }
        
        # DIAGNOSTIC: Include role mismatch info in development/debug mode
        # This helps frontend developers see why they might be getting the wrong dashboard
        from app.config import settings
        if settings.debug and hasattr(current_user, "_jwt_role_diagnostic"):
            jwt_role = getattr(current_user, "_jwt_role_diagnostic")
            if jwt_role != current_user.role.value:
                # Add validation warning to response logic (not model yet, but useful for inspection)
                logger.warning(
                    "⚠️ [API] Returning profile with known role mismatch: JWT=%s, DB=%s",
                    jwt_role, current_user.role.value
                )
                # We could add this to the response model if we wanted frontend to show a banner
                # For now, it's just in the logs, but we could add it to a 'meta' field if valid
        
        # Add role-specific data with all new fields
        if current_user.role == UserRole.CLIENT:
            # Get client profile data (create if doesn't exist) - optimized query
            client_profile = user_service.get_client_profile(current_user.id)
            if not client_profile:
                # Auto-create client profile if it doesn't exist (non-blocking)
                logger.info(
                    "📝 Auto-creating client profile - user_id=%s, email=%s",
                    current_user.id, current_user.email
                )
                try:
                    client_profile = user_service._create_client_profile(current_user)
                    db.add(client_profile)
                    db.commit()
                    db.refresh(client_profile)
                    logger.info(
                        "✅ Client profile created - user_id=%s, email=%s",
                        current_user.id, current_user.email
                    )
                except Exception as create_error:
                    logger.error(
                        "❌ Failed to create client profile - user_id=%s, email=%s, error=%s",
                        current_user.id, current_user.email, str(create_error)
                    )
                    # Continue without profile - return basic profile
                    client_profile = None
            
            if client_profile:
                # Format address as nested object
                address = None
                if any([client_profile.street, client_profile.city, client_profile.state, client_profile.zip_code]):
                    address = {
                        "street": client_profile.street,
                        "city": client_profile.city,
                        "state": client_profile.state,
                        "zip_code": client_profile.zip_code
                    }
                
                profile_data.update({
                    "business_name": client_profile.business_name,
                    "business_type": client_profile.business_type,
                    "phone": client_profile.phone_number,
                    "address": address,
                    "website": getattr(client_profile, 'website', None),
                    "description": getattr(client_profile, 'description', None),
                    # Notification preferences
                    "email_notifications": getattr(client_profile, 'email_notifications', True),
                    "sms_notifications": getattr(client_profile, 'sms_notifications', False),
                    "timezone": getattr(client_profile, 'timezone', 'Europe/London'),
                    "language": getattr(client_profile, 'language', 'en'),
                    "verification_status": "pending",  # Not in current model
                    "stripe_customer_id": current_user.stripe_customer_id
                })
        
        elif current_user.role == UserRole.DROPPER:
            # Get dropper profile data (create if doesn't exist) - optimized query
            dropper_profile = user_service.get_dropper_profile(current_user.id)
            if not dropper_profile:
                # Auto-create dropper profile if it doesn't exist (non-blocking)
                logger.info(
                    "📝 Auto-creating dropper profile - user_id=%s, email=%s",
                    current_user.id, current_user.email
                )
                try:
                    dropper_profile = user_service._create_dropper_profile(current_user)
                    db.add(dropper_profile)
                    db.commit()
                    db.refresh(dropper_profile)
                    logger.info(
                        "✅ Dropper profile created - user_id=%s, email=%s",
                        current_user.id, current_user.email
                    )
                except Exception as create_error:
                    logger.error(
                        "❌ Failed to create dropper profile - user_id=%s, email=%s, error=%s",
                        current_user.id, current_user.email, str(create_error)
                    )
                    # Continue without profile - return basic profile
                    dropper_profile = None
            
            if dropper_profile:
                profile_data.update({
                    "service_radius_km": dropper_profile.service_radius_km,
                    "id_verified": dropper_profile.id_verified,
                    "phone": dropper_profile.phone_number,
                    "base_location_lat": dropper_profile.base_location_lat,
                    "base_location_lng": dropper_profile.base_location_lng,
                    "emergency_contact_name": dropper_profile.emergency_contact_name,
                    "emergency_contact_phone": dropper_profile.emergency_contact_phone,
                    "is_available": dropper_profile.is_available,
                    # Notification preferences
                    "email_notifications": getattr(dropper_profile, 'email_notifications', True),
                    "sms_notifications": getattr(dropper_profile, 'sms_notifications', False),
                    "timezone": getattr(dropper_profile, 'timezone', 'Europe/London'),
                    "language": getattr(dropper_profile, 'language', 'en'),
                    "stripe_connect_account_id": dropper_profile.stripe_connect_account_id,
                    # Note: earnings would be calculated from completed jobs
                    "total_earnings": 0.0  # Placeholder - implement earnings calculation
                })
        
        # Handle ADMIN/SUPERADMIN roles - add default profile fields
        elif current_user.role == UserRole.ADMIN:
            # Admin users get basic profile with notification preferences
            profile_data.update({
                "phone": None,  # Can be updated via profile update
                "email_notifications": True,
                "sms_notifications": False,
                "timezone": "Europe/London",  # Default timezone
                "language": "en",  # Default language
            })
        else:
            # For any other role, add default fields
            profile_data.update({
                "phone": None,
                "email_notifications": True,
                "sms_notifications": False,
                "timezone": "Europe/London",
                "language": "en",
            })
        
        # Log successful response with timing
        elapsed_time = time.time() - start_time
        logger.info(
            "✅ Profile retrieved successfully - user_id=%s, email=%s, role=%s, elapsed=%.3fs",
            current_user.id, current_user.email, current_user.role.value, elapsed_time
        )
        
        if elapsed_time > 0.3:
            logger.warning(
                "⚠️ Profile retrieval took %.3fs (target: <0.3s) - user_id=%s",
                elapsed_time, current_user.id
            )
        
        return UserProfileResponse(**profile_data)
        
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(
            "❌ Error retrieving profile - user_id=%s, email=%s, elapsed=%.3fs, error=%s",
            current_user.id if 'current_user' in locals() else 'unknown',
            current_user.email if 'current_user' in locals() else 'unknown',
            elapsed_time,
            str(e),
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "server_error",
                "message": "Failed to retrieve user profile. Please try again later."
            }
        )


@router.put("/profile", response_model=UserProfileResponse)
async def update_current_user_profile(
    profile_update: UserProfileUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
) -> UserProfileResponse:
    """
    Update profile data for the currently authenticated user.
    
    This endpoint allows users to update their profile information based on their role.
    Email addresses cannot be updated through this endpoint for security reasons.
    
    Args:
        profile_update: Profile fields to update (only provided fields are updated)
        current_user: Current authenticated user from JWT token
        db: Database session
        
    Returns:
        UserProfileResponse: Updated user profile data with all new fields
        
    Raises:
        HTTPException 422: If validation fails on any field
        HTTPException 400: If profile update fails due to business logic
        HTTPException 401: If user is not authenticated
        HTTPException 500: If an unexpected server error occurs
    """
    try:
        logger.debug(f"Profile update request (role: {current_user.role.value})")
        
        user_service = get_user_service(db)
        
        # Convert Pydantic model to dict, excluding unset fields for partial updates
        profile_dict = profile_update.model_dump(exclude_unset=True)
        
        # Convert address_* fields to the format expected by the service (street, city, state, zip_code)
        if 'address_street' in profile_dict:
            profile_dict['street'] = profile_dict.pop('address_street')
        if 'address_city' in profile_dict:
            profile_dict['city'] = profile_dict.pop('address_city')
        if 'address_state' in profile_dict:
            profile_dict['state'] = profile_dict.pop('address_state')
        if 'address_zip_code' in profile_dict:
            profile_dict['zip_code'] = profile_dict.pop('address_zip_code')
        
        # Security: Ensure email field is never updated
        if 'email' in profile_dict:
            logger.warning(f"Attempted email update blocked")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "validation_error",
                    "message": "Email address cannot be updated. Email is managed by the authentication system.",
                    "field": "email"
                }
            )
        
        # Log the fields being updated (excluding sensitive data)
        update_fields = list(profile_dict.keys())
        logger.debug(f"Updating profile fields: {update_fields}")
        
        # Update user profile based on role and provided data
        updated_user = user_service.update_user_profile(
            current_user.id, 
            profile_dict
        )
        
        # Refresh user from database to get latest data
        db.refresh(updated_user)
        
        logger.debug(f"Profile successfully updated")
        
        # Rebuild profile response with updated data
        # Get fresh user data
        current_user = updated_user
        user_service = get_user_service(db)
        
        # Build base profile response
        profile_data = {
            "cognito_sub": current_user.cognito_sub,
            "email": current_user.email,
            "name": current_user.name,
            "role": current_user.role.value,
            "is_active": current_user.is_active,
            "created_at": current_user.created_at,
            "updated_at": current_user.updated_at
        }
        
        # Add role-specific data
        if current_user.role == UserRole.CLIENT:
            client_profile = user_service.get_client_profile(current_user.id)
            if client_profile:
                address = None
                if any([client_profile.street, client_profile.city, client_profile.state, client_profile.zip_code]):
                    address = {
                        "street": client_profile.street,
                        "city": client_profile.city,
                        "state": client_profile.state,
                        "zip_code": client_profile.zip_code
                    }
                profile_data.update({
                    "business_name": client_profile.business_name,
                    "business_type": client_profile.business_type,
                    "phone": client_profile.phone_number,
                    "address": address,
                    "website": getattr(client_profile, 'website', None),
                    "description": getattr(client_profile, 'description', None),
                    "email_notifications": getattr(client_profile, 'email_notifications', True),
                    "sms_notifications": getattr(client_profile, 'sms_notifications', False),
                    "timezone": getattr(client_profile, 'timezone', 'Europe/London'),
                    "language": getattr(client_profile, 'language', 'en'),
                })
        elif current_user.role == UserRole.DROPPER:
            dropper_profile = user_service.get_dropper_profile(current_user.id)
            if dropper_profile:
                profile_data.update({
                    "service_radius_km": dropper_profile.service_radius_km,
                    "id_verified": dropper_profile.id_verified,
                    "phone": dropper_profile.phone_number,
                    "base_location_lat": dropper_profile.base_location_lat,
                    "base_location_lng": dropper_profile.base_location_lng,
                    "emergency_contact_name": dropper_profile.emergency_contact_name,
                    "emergency_contact_phone": dropper_profile.emergency_contact_phone,
                    "is_available": dropper_profile.is_available,
                    "email_notifications": getattr(dropper_profile, 'email_notifications', True),
                    "sms_notifications": getattr(dropper_profile, 'sms_notifications', False),
                    "timezone": getattr(dropper_profile, 'timezone', 'Europe/London'),
                    "language": getattr(dropper_profile, 'language', 'en'),
                    "total_earnings": 0.0
                })
        elif current_user.role == UserRole.ADMIN:
            # For admin, use the updated values from profile_dict or defaults
            profile_data.update({
                "phone": profile_dict.get('phone'),
                "email_notifications": profile_dict.get('email_notifications', True),
                "sms_notifications": profile_dict.get('sms_notifications', False),
                "timezone": profile_dict.get('timezone', 'Europe/London'),
                "language": profile_dict.get('language', 'en'),
            })
        
        return UserProfileResponse(**profile_data)
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
        
    except PydanticValidationError as e:
        logger.error(f"Validation error: {str(e)}")
        # Format validation errors for client
        errors = []
        for error in e.errors():
            field = ".".join(str(loc) for loc in error["loc"])
            errors.append({
                "field": field,
                "message": error["msg"]
            })
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "type": "validation_error",
                "message": "Validation failed for one or more fields",
                "errors": errors
            }
        )
        
    except ValueError as e:
        logger.error(f"Value error during profile update: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "type": "validation_error",
                "message": str(e)
            }
        )
        
    except Exception as e:
        logger.error(f"Unexpected error updating profile: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "server_error",
                "message": "An unexpected error occurred while updating your profile. Please try again later."
            }
        )
