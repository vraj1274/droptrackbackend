"""
Authentication endpoints for DropTrack users.
Handles login initialization, verification, and role-based redirects.
"""

from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select
from app.database import get_session
from app.models import User, UserRole, Client, Dropper
from app.services.user_service import get_user_service, UserService
from app.api.deps import get_current_user, get_current_active_user, get_optional_user, AuthenticationError
from app.services.cognito import cognito_service
from app.config import settings
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.utils.log_redaction import redact_email  # HIGH-RISK FIX 4: Import log redaction


# Initialize logger
logger = logging.getLogger(__name__)

router = APIRouter()

# SECURITY FIX 4: Initialize rate limiter for critical endpoints
limiter = Limiter(key_func=get_remote_address)


class AuthInitRequest(BaseModel):
    """Request model for auth initialization."""
    role: Optional[str] = None
    email: Optional[EmailStr] = None  # Optional, extracted from token if not provided
    name: Optional[str] = None


class AuthInitResponse(BaseModel):
    """Response model for auth initialization."""
    user_id: str
    email: str
    role: str
    name: str = ""
    status: str
    redirect_to: str
    profile_complete: bool = False
    message: Optional[str] = None





@router.get("/me", response_model=AuthInitResponse)
async def get_me(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
) -> AuthInitResponse:
    """
    Get current authenticated user info.
    """
    return _build_auth_response(current_user, db)


@router.post("/initialize", response_model=AuthInitResponse)
@limiter.limit("10/minute")  # SECURITY FIX 4: Rate limit to 10 requests per minute
async def initialize_auth(
    request: Request,  # Required for rate limiting
    auth_request: AuthInitRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
) -> AuthInitResponse:
    """
    Initialize user session after login.
    
    SECURITY FIX 4: Rate limited to 10 requests per minute per IP to prevent abuse.
    
    This endpoint is called by the frontend after successful Cognito login.
    It ensures the user exists in our DB, has the correct profile created,
    and returns the correct dashboard redirect path.
    """
    # HIGH-RISK FIX 4: Redact email in logs
    logger.info(f"Initializing auth for user {redact_email(current_user.email)} with role {current_user.role}")
    


    # Ensure profile exists for the CURRENT database role
    user_service = get_user_service(db)
    
    # We don't override the DB role here. If a sync was needed, 
    # it should have happened in get_current_user via UserService.update_user_from_claims
    
    profile_complete = False
    
    if current_user.role == UserRole.DROPPER:
        profile = user_service.get_dropper_profile(current_user.id)
        if not profile:
             # Auto-create if missing (failsafe)
             # HIGH-RISK FIX 4: Redact email in logs
             logger.info(f"Creating missing Dropper profile for {redact_email(current_user.email)}")
             user_service._create_dropper_profile(current_user)
             db.commit()
             profile_complete = False # Newly created profile is likely incomplete
        else:
             # Simple completeness check (can be expanded)
             profile_complete = bool(profile.phone_number and profile.service_radius_km)
             
    elif current_user.role == UserRole.CLIENT:
        profile = user_service.get_client_profile(current_user.id)
        if not profile:
             # HIGH-RISK FIX 4: Redact email in logs
             logger.info(f"Creating missing Client profile for {redact_email(current_user.email)}")
             user_service._create_client_profile(current_user)
             db.commit()
             profile_complete = False
        else:
             profile_complete = bool(profile.business_name)

    return _build_auth_response(current_user, db, profile_complete)





def _build_auth_response(user: User, db: Session, profile_complete: bool = None) -> AuthInitResponse:
    """Helper to build consistent auth response."""
    
    redirect_map = {
        UserRole.CLIENT: "/client/dashboard",
        UserRole.DROPPER: "/jobs",
        UserRole.ADMIN: "/admin/dashboard"
    }
    
    redirect_to = redirect_map.get(user.role, "/dashboard")
    
    # If profile_complete wasn't passed, calculate it basically
    if profile_complete is None:
        # Check if profile exists
        if user.role == UserRole.DROPPER:
             profile = db.exec(select(Dropper).where(Dropper.user_id == user.id)).first()
             profile_complete = bool(profile)
        elif user.role == UserRole.CLIENT:
             profile = db.exec(select(Client).where(Client.user_id == user.id)).first()
             profile_complete = bool(profile)
        else:
             profile_complete = True
             
    message = None

    return AuthInitResponse(
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
        name=user.name or "",
        status="active" if user.is_active else "inactive",
        redirect_to=redirect_to,
        profile_complete=profile_complete,
        message=message
    )
