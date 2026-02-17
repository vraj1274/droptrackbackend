"""
User-related Pydantic schemas for request/response validation.
"""

from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr


class AddressSchema(BaseModel):
    """Address information schema."""
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None


class UserProfileResponse(BaseModel):
    """
    User profile response schema with role-specific fields.
    
    This schema adapts based on the user's role:
    - All users: cognito_sub, email, name, role, is_active, timestamps
    - Clients: business_name, business_type, phone, address, website, description, verification_status
    - Droppers: service_radius_km, id_verified, phone, address, total_earnings
    """
    
    # Base fields for all users
    cognito_sub: str = Field(..., description="Cognito user identifier")
    email: EmailStr = Field(..., description="User email address")
    name: Optional[str] = Field(None, description="User full name")
    role: str = Field(..., description="User role (client, dropper, superadmin)")
    is_active: bool = Field(True, description="Whether user account is active")
    created_at: datetime = Field(..., description="Account creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    
    # Client-specific fields
    business_name: Optional[str] = Field(None, description="Business name (clients only)")
    business_type: Optional[str] = Field(None, description="Type of business (clients only)")
    website: Optional[str] = Field(None, description="Business website (clients only)")
    description: Optional[str] = Field(None, description="Business description (clients only)")
    verification_status: Optional[str] = Field(None, description="Verification status (clients only)")
    stripe_customer_id: Optional[str] = Field(None, description="Stripe customer ID (clients only)")
    
    # Dropper-specific fields
    service_radius_km: Optional[int] = Field(None, description="Service radius in kilometers (droppers only)")
    id_verified: Optional[bool] = Field(None, description="ID verification status (droppers only)")
    total_earnings: Optional[float] = Field(None, description="Total earnings (droppers only)")
    rating: Optional[float] = Field(None, description="Average rating (droppers only)")
    total_jobs_completed: Optional[int] = Field(None, description="Total jobs completed (droppers only)")
    stripe_connect_account_id: Optional[str] = Field(None, description="Stripe Connect account ID (droppers only)")
    emergency_contact_name: Optional[str] = Field(None, description="Emergency contact name (droppers only)")
    emergency_contact_phone: Optional[str] = Field(None, description="Emergency contact phone (droppers only)")
    emergency_contact_relationship: Optional[str] = Field(None, description="Emergency contact relationship (droppers only)")
    date_of_birth: Optional[str] = Field(None, description="Date of birth ISO format (droppers only)")
    experience: Optional[str] = Field(None, description="Experience level (droppers only)")
    transportation: Optional[str] = Field(None, description="Transportation method (droppers only)")
    skills: Optional[list] = Field(None, description="List of skills (droppers only)")
    availability_days: Optional[list] = Field(None, description="Available days (droppers only)")
    availability_time_slots: Optional[list] = Field(None, description="Available time slots (droppers only)")
    base_location_lat: Optional[float] = Field(None, description="Base location latitude (droppers only)")
    base_location_lng: Optional[float] = Field(None, description="Base location longitude (droppers only)")
    is_available: Optional[bool] = Field(None, description="Whether dropper is available for jobs (droppers only)")
    
    # Shared fields for clients and droppers
    phone: Optional[str] = Field(None, description="Phone number")
    address: Optional[AddressSchema] = Field(None, description="Address information")
    
    # Notification and preference fields (all users)
    email_notifications: Optional[bool] = Field(None, description="Email notification preference")
    sms_notifications: Optional[bool] = Field(None, description="SMS notification preference")
    timezone: Optional[str] = Field(None, description="User timezone preference")
    language: Optional[str] = Field(None, description="User language preference")
    
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class UserProfileUpdateRequest(BaseModel):
    """
    User profile update request schema.
    Only includes fields that users are allowed to update.
    """
    
    name: Optional[str] = Field(None, description="User full name")
    phone: Optional[str] = Field(None, description="Phone number")
    
    # Notification and preference fields (all users)
    email_notifications: Optional[bool] = Field(None, description="Email notification preference")
    sms_notifications: Optional[bool] = Field(None, description="SMS notification preference")
    timezone: Optional[str] = Field(None, description="User timezone preference")
    language: Optional[str] = Field(None, description="User language preference")
    
    # Address fields
    address_street: Optional[str] = Field(None, description="Street address")
    address_city: Optional[str] = Field(None, description="City")
    address_state: Optional[str] = Field(None, description="State")
    address_zip_code: Optional[str] = Field(None, description="ZIP code")
    
    # Client-specific updatable fields
    business_name: Optional[str] = Field(None, description="Business name (clients only)")
    business_type: Optional[str] = Field(None, description="Type of business (clients only)")
    website: Optional[str] = Field(None, description="Business website (clients only)")
    description: Optional[str] = Field(None, description="Business description (clients only)")
    
    # Dropper-specific updatable fields
    service_radius_km: Optional[int] = Field(None, ge=1, le=100, description="Service radius in kilometers (droppers only)")
    emergency_contact_name: Optional[str] = Field(None, description="Emergency contact name (droppers only)")
    emergency_contact_phone: Optional[str] = Field(None, description="Emergency contact phone (droppers only)")
    emergency_contact_relationship: Optional[str] = Field(None, description="Emergency contact relationship (droppers only)")
    date_of_birth: Optional[str] = Field(None, description="Date of birth in ISO format YYYY-MM-DD (droppers only)")
    experience: Optional[str] = Field(None, description="Experience level: beginner, intermediate, experienced (droppers only)")
    transportation: Optional[str] = Field(None, description="Transportation method: walking, bicycle, car, public_transport (droppers only)")
    skills: Optional[list] = Field(None, description="List of skills as array of strings (droppers only)")
    availability_days: Optional[list] = Field(None, description="Available days as array of strings (droppers only)")
    availability_time_slots: Optional[list] = Field(None, description="Available time slots as array of strings (droppers only)")
    base_location_lat: Optional[float] = Field(None, ge=-90, le=90, description="Base location latitude (droppers only)")
    base_location_lng: Optional[float] = Field(None, ge=-180, le=180, description="Base location longitude (droppers only)")
    is_available: Optional[bool] = Field(None, description="Whether dropper is available for jobs (droppers only)")
    
    class Config:
        extra = "forbid"  # Prevent additional fields


class ClientRegistrationRequest(BaseModel):
    """
    Client registration request schema.
    Used for registering a new client account with business information.
    """
    
    cognito_sub: str = Field(..., description="Cognito user identifier (sub from JWT)")
    email: EmailStr = Field(..., description="User email address")
    name: str = Field(..., min_length=1, max_length=255, description="User full name")
    business_name: str = Field(..., min_length=1, max_length=255, description="Business name")
    business_type: str = Field(..., min_length=1, max_length=100, description="Type of business (e.g., restaurant, retail, service)")
    phone_number: Optional[str] = Field(None, max_length=20, description="Contact phone number")
    business_address: Optional[str] = Field(None, max_length=500, description="Business address")
    
    class Config:
        extra = "forbid"  # Prevent additional fields


class UserBasicInfo(BaseModel):
    """Basic user information for listings and references."""
    
    id: int = Field(..., description="User ID")
    cognito_sub: str = Field(..., description="Cognito user identifier")
    email: EmailStr = Field(..., description="User email address")
    name: Optional[str] = Field(None, description="User full name")
    role: str = Field(..., description="User role")
    is_active: bool = Field(True, description="Whether user account is active")
    
    class Config:
        from_attributes = True