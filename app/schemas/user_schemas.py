"""
Pydantic schemas for user and authentication API requests and responses.
Defines validation and serialization for user management and auth endpoints.
"""

from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.models import UserRole


class UserCreate(BaseModel):
    """Schema for creating a new user (from Cognito JWT claims)."""
    
    cognito_sub: str = Field(
        ...,
        max_length=255,
        description="Cognito user identifier from JWT sub claim"
    )
    email: str = Field(
        ...,
        max_length=255,
        description="User email address from Cognito"
    )
    name: str = Field(
        ...,
        max_length=255,
        description="User full name from Cognito"
    )
    role: UserRole = Field(
        ...,
        description="User role from custom:user_role claim"
    )
    
    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        """Basic email validation."""
        if '@' not in v or '.' not in v.split('@')[1]:
            raise ValueError("Invalid email format")
        return v.lower()


class UserResponse(BaseModel):
    """Schema for user responses."""
    
    id: UUID
    cognito_sub: str
    email: str
    name: str
    role: UserRole
    stripe_customer_id: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """Schema for updating user information."""
    
    name: Optional[str] = Field(
        None,
        max_length=255,
        description="User full name"
    )
    is_active: Optional[bool] = Field(
        None,
        description="Whether the user account is active"
    )


class ClientProfileCreate(BaseModel):
    """Schema for creating a client profile."""
    
    business_name: str = Field(
        ...,
        max_length=255,
        description="Name of the client's business"
    )
    business_type: str = Field(
        ...,
        max_length=100,
        description="Type of business (e.g., restaurant, retail, service)"
    )
    business_address: Optional[str] = Field(
        None,
        max_length=500,
        description="Business address for billing purposes"
    )
    phone_number: Optional[str] = Field(
        None,
        max_length=20,
        description="Contact phone number"
    )
    
    @field_validator('phone_number')
    @classmethod
    def validate_phone_number(cls, v):
        """Basic phone number validation."""
        if v and not v.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '').isdigit():
            raise ValueError("Phone number must contain only digits, spaces, hyphens, parentheses, and plus sign")
        return v


class ClientProfileResponse(BaseModel):
    """Schema for client profile responses."""
    
    id: UUID
    user_id: UUID
    business_name: str
    business_type: str
    business_address: Optional[str]
    phone_number: Optional[str]
    website: Optional[str]
    description: Optional[str]
    street: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip_code: Optional[str]
    email_notifications: bool
    sms_notifications: bool
    timezone: str = Field(default='Europe/London', description="User's timezone")
    language: str = Field(default='en', description="User's preferred language")
    created_at: datetime
    
    class Config:
        from_attributes = True


class ClientProfileUpdate(BaseModel):
    """Schema for updating client profile."""
    
    business_name: Optional[str] = Field(
        None,
        max_length=255,
        description="Name of the client's business"
    )
    business_type: Optional[str] = Field(
        None,
        max_length=100,
        description="Type of business"
    )
    business_address: Optional[str] = Field(
        None,
        max_length=500,
        description="Business address for billing purposes"
    )
    phone_number: Optional[str] = Field(
        None,
        max_length=20,
        description="Contact phone number"
    )
    
    @field_validator('phone_number')
    @classmethod
    def validate_phone_number(cls, v):
        """Basic phone number validation."""
        if v and not v.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '').isdigit():
            raise ValueError("Phone number must contain only digits, spaces, hyphens, parentheses, and plus sign")
        return v


class DropperProfileCreate(BaseModel):
    """Schema for creating a dropper profile."""
    
    service_radius_km: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Service radius in kilometers for job matching"
    )
    base_location_lat: Optional[float] = Field(
        None,
        ge=-90,
        le=90,
        description="Base latitude for service area center"
    )
    base_location_lng: Optional[float] = Field(
        None,
        ge=-180,
        le=180,
        description="Base longitude for service area center"
    )
    phone_number: Optional[str] = Field(
        None,
        max_length=20,
        description="Contact phone number"
    )
    emergency_contact_name: Optional[str] = Field(
        None,
        max_length=255,
        description="Emergency contact name"
    )
    emergency_contact_phone: Optional[str] = Field(
        None,
        max_length=20,
        description="Emergency contact phone number"
    )
    
    @field_validator('phone_number', 'emergency_contact_phone')
    @classmethod
    def validate_phone_number(cls, v):
        """Basic phone number validation."""
        if v and not v.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '').isdigit():
            raise ValueError("Phone number must contain only digits, spaces, hyphens, parentheses, and plus sign")
        return v


class DropperProfileResponse(BaseModel):
    """Schema for dropper profile responses."""
    
    id: UUID
    user_id: UUID
    id_verified: bool
    service_radius_km: int
    base_location_lat: Optional[float]
    base_location_lng: Optional[float]
    rating: float
    total_jobs_completed: int
    stripe_connect_account_id: Optional[str]
    phone_number: Optional[str]
    emergency_contact_name: Optional[str]
    emergency_contact_phone: Optional[str]
    is_available: bool
    email_notifications: bool
    sms_notifications: bool
    timezone: str = Field(default='Europe/London', description="User's timezone")
    language: str = Field(default='en', description="User's preferred language")
    created_at: datetime
    
    class Config:
        from_attributes = True


class DropperProfileUpdate(BaseModel):
    """Schema for updating dropper profile."""
    
    service_radius_km: Optional[int] = Field(
        None,
        ge=1,
        le=50,
        description="Service radius in kilometers for job matching"
    )
    base_location_lat: Optional[float] = Field(
        None,
        ge=-90,
        le=90,
        description="Base latitude for service area center"
    )
    base_location_lng: Optional[float] = Field(
        None,
        ge=-180,
        le=180,
        description="Base longitude for service area center"
    )
    phone_number: Optional[str] = Field(
        None,
        max_length=20,
        description="Contact phone number"
    )
    emergency_contact_name: Optional[str] = Field(
        None,
        max_length=255,
        description="Emergency contact name"
    )
    emergency_contact_phone: Optional[str] = Field(
        None,
        max_length=20,
        description="Emergency contact phone number"
    )
    is_available: Optional[bool] = Field(
        None,
        description="Whether the dropper is available for new jobs"
    )
    email_notifications: Optional[bool] = Field(
        None,
        description="Whether to receive email notifications"
    )
    sms_notifications: Optional[bool] = Field(
        None,
        description="Whether to receive SMS notifications"
    )
    timezone: Optional[str] = Field(
        None,
        max_length=50,
        description="User's timezone"
    )
    language: Optional[str] = Field(
        None,
        max_length=10,
        description="User's preferred language"
    )
    
    @field_validator('phone_number', 'emergency_contact_phone')
    @classmethod
    def validate_phone_number(cls, v):
        """Basic phone number validation."""
        if v and not v.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '').isdigit():
            raise ValueError("Phone number must contain only digits, spaces, hyphens, parentheses, and plus sign")
        return v


class AuthTokenResponse(BaseModel):
    """Schema for authentication token responses."""
    
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class UserProfileResponse(BaseModel):
    """Schema for complete user profile responses."""
    
    user: UserResponse
    client_profile: Optional[ClientProfileResponse] = None
    dropper_profile: Optional[DropperProfileResponse] = None
    
    class Config:
        from_attributes = True


class PaymentSetupRequest(BaseModel):
    """Schema for payment setup requests."""
    
    return_url: Optional[str] = Field(
        None,
        description="URL to redirect to after payment setup completion"
    )


class PaymentSetupResponse(BaseModel):
    """Schema for payment setup responses."""
    
    stripe_customer_id: str
    setup_intent_client_secret: Optional[str] = Field(
        None,
        description="Client secret for SetupIntent if additional setup is required"
    )
    requires_action: bool = False
    message: str
    
    class Config:
        from_attributes = True


class UserProfileUpdateRequest(BaseModel):
    """Schema for updating user profile - supports both client and dropper fields."""
    
    # Shared fields
    name: Optional[str] = Field(
        None,
        max_length=255,
        description="User full name"
    )
    phone: Optional[str] = Field(
        None,
        max_length=20,
        description="Contact phone number"
    )
    
    # Client-specific fields
    business_name: Optional[str] = Field(
        None,
        max_length=255,
        description="Name of the client's business"
    )
    business_type: Optional[str] = Field(
        None,
        max_length=100,
        description="Type of business (e.g., restaurant, retail, service)"
    )
    website: Optional[str] = Field(
        None,
        max_length=500,
        description="Client's website URL"
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Business description"
    )
    
    # Address fields (client)
    street: Optional[str] = Field(
        None,
        max_length=255,
        description="Street address"
    )
    city: Optional[str] = Field(
        None,
        max_length=100,
        description="City"
    )
    state: Optional[str] = Field(
        None,
        max_length=100,
        description="State or county"
    )
    zip_code: Optional[str] = Field(
        None,
        max_length=20,
        description="Postal/ZIP code"
    )
    
    # Dropper-specific fields
    service_radius_km: Optional[int] = Field(
        None,
        ge=1,
        le=50,
        description="Service radius in kilometers for job matching"
    )
    base_location_lat: Optional[float] = Field(
        None,
        ge=-90,
        le=90,
        description="Base latitude for service area center"
    )
    base_location_lng: Optional[float] = Field(
        None,
        ge=-180,
        le=180,
        description="Base longitude for service area center"
    )
    emergency_contact_name: Optional[str] = Field(
        None,
        max_length=255,
        description="Emergency contact name"
    )
    emergency_contact_phone: Optional[str] = Field(
        None,
        max_length=20,
        description="Emergency contact phone number"
    )
    is_available: Optional[bool] = Field(
        None,
        description="Whether the dropper is available for new jobs"
    )
    
    # Preferences (both roles)
    email_notifications: Optional[bool] = Field(
        None,
        description="Whether to receive email notifications"
    )
    sms_notifications: Optional[bool] = Field(
        None,
        description="Whether to receive SMS notifications"
    )
    timezone: Optional[str] = Field(
        None,
        max_length=50,
        description="User's timezone (e.g., 'Europe/London')"
    )
    language: Optional[str] = Field(
        None,
        max_length=10,
        description="User's preferred language code (e.g., 'en')"
    )
    
    @field_validator('phone', 'emergency_contact_phone')
    @classmethod
    def validate_phone_number(cls, v):
        """Validate phone number format."""
        if v and not v.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '').isdigit():
            raise ValueError("Phone number must contain only digits and formatting characters (+, -, space, parentheses)")
        return v
    
    @field_validator('website')
    @classmethod
    def validate_website(cls, v):
        """Validate and normalize website URL."""
        if v:
            v = v.strip()
            if v and not (v.startswith('http://') or v.startswith('https://')):
                return f'https://{v}'
        return v
    
    @field_validator('base_location_lat', 'base_location_lng')
    @classmethod
    def validate_coordinates(cls, v, info):
        """Validate coordinate values."""
        if v is not None:
            field_name = info.field_name
            if field_name == 'base_location_lat' and not (-90 <= v <= 90):
                raise ValueError("Latitude must be between -90 and 90")
            elif field_name == 'base_location_lng' and not (-180 <= v <= 180):
                raise ValueError("Longitude must be between -180 and 180")
        return v


class ClientStatsResponse(BaseModel):
    """Schema for client statistics response."""
    
    total_spent_pence: int = Field(
        description="Total amount spent by client in pence"
    )
    active_campaigns_count: int = Field(
        description="Number of active campaigns (paid, assigned, or completed jobs)"
    )
    total_jobs_count: int = Field(
        description="Total number of jobs created by client"
    )
    last_payment_date: Optional[str] = Field(
        None,
        description="Date of last completed payment in YYYY-MM-DD format"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "total_spent_pence": 125000,
                "active_campaigns_count": 3,
                "total_jobs_count": 5,
                "last_payment_date": "2024-01-15"
            }
        }


# Export all schemas
__all__ = [
    "UserCreate",
    "UserResponse",
    "UserUpdate",
    "ClientProfileCreate",
    "ClientProfileResponse",
    "ClientProfileUpdate",
    "DropperProfileCreate",
    "DropperProfileResponse",
    "DropperProfileUpdate",
    "AuthTokenResponse",
    "UserProfileResponse",
    "PaymentSetupRequest",
    "PaymentSetupResponse",
    "UserProfileUpdateRequest",
    "ClientStatsResponse",
]