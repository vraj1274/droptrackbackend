"""
Pydantic schemas for job-related API requests and responses.
Defines validation and serialization for job management endpoints.
"""

from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from app.models import JobStatus, VerificationStatus


class DropPointCreate(BaseModel):
    """Schema for creating a drop point."""
    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lng: float = Field(..., ge=-180, le=180, description="Longitude")
    name: Optional[str] = Field(None, max_length=255, description="Name or description of the drop point")
    order: Optional[int] = Field(None, ge=1, description="Order in the route (for route optimization)")


class JobAreaCreate(BaseModel):
    """Schema for creating job area (distribution area)."""
    
    area_type: str = Field(
        ...,
        description="Type of area definition: 'polygon' or 'postcodes'",
        pattern="^(polygon|postcodes)$"
    )
    geojson: Optional[Dict[str, Any]] = Field(
        None,
        description="GeoJSON polygon defining the distribution area"
    )
    postcodes: Optional[List[str]] = Field(
        None,
        description="List of postcodes for distribution area"
    )
    center_lat: Optional[float] = Field(
        None,
        ge=-90,
        le=90,
        description="Center latitude of the area"
    )
    center_lng: Optional[float] = Field(
        None,
        ge=-180,
        le=180,
        description="Center longitude of the area"
    )
    radius_km: Optional[float] = Field(
        None,
        gt=0,
        description="Approximate radius of the area in kilometers"
    )
    
    @model_validator(mode='after')
    def validate_area_definition(self):
        """Validate that area is properly defined based on type."""
        if self.area_type == 'polygon':
            if not self.geojson:
                raise ValueError("geojson is required when area_type is 'polygon'")
            if not isinstance(self.geojson, dict) or self.geojson.get('type') != 'Polygon':
                raise ValueError("geojson must be a valid Polygon GeoJSON object")
        
        elif self.area_type == 'postcodes':
            if not self.postcodes or len(self.postcodes) == 0:
                raise ValueError("postcodes list is required when area_type is 'postcodes'")
        
        return self


class JobCreate(BaseModel):
    """Schema for creating a new job."""
    
    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Job title/description"
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Detailed job description"
    )
    leaflet_file_url: Optional[str] = Field(
        None,
        max_length=200000,  # Increased to support base64 data URLs (can be 50KB-100KB)
        description="URL to the leaflet file to be distributed (HTTP/HTTPS URL or base64 data URL)"
    )
    household_count: int = Field(
        ...,
        gt=0,
        le=10000,
        description="Number of households to receive leaflets"
    )
    scheduled_date: date = Field(
        ...,
        description="Date when the job should be completed"
    )
    special_instructions: Optional[str] = Field(
        None,
        max_length=2000,
        description="Special instructions for the dropper"
    )
    job_area: JobAreaCreate = Field(
        ...,
        description="Distribution area definition"
    )
    drop_points: Optional[List[DropPointCreate]] = Field(
        None,
        description="Optional list of specific drop points for the job"
    )
    
    @field_validator('scheduled_date')
    @classmethod
    def validate_scheduled_date(cls, v):
        """Ensure scheduled date is not in the past."""
        if v < date.today():
            raise ValueError("Scheduled date cannot be in the past")
        return v
    
    @field_validator('leaflet_file_url')
    @classmethod
    def validate_leaflet_url(cls, v):
        """Basic URL validation for leaflet file - accepts HTTP/HTTPS URLs and data URLs."""
        if v is not None:
            if not v.strip():
                raise ValueError("Leaflet file URL cannot be empty if provided")
            # Accept HTTP/HTTPS URLs or data URLs (base64 images)
            if not v.startswith(('http://', 'https://', 'data:')):
                raise ValueError("Leaflet file URL must be a valid HTTP/HTTPS URL or data URL")
        return v


class JobUpdate(BaseModel):
    """Schema for updating a draft job."""
    
    title: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="Job title/description"
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Detailed job description"
    )
    leaflet_file_url: Optional[str] = Field(
        None,
        max_length=200000,  # Increased to support base64 data URLs
        description="URL to the leaflet file to be distributed (HTTP/HTTPS URL or base64 data URL)"
    )
    household_count: Optional[int] = Field(
        None,
        gt=0,
        le=10000,
        description="Number of households to receive leaflets"
    )
    scheduled_date: Optional[date] = Field(
        None,
        description="Date when the job should be completed"
    )
    special_instructions: Optional[str] = Field(
        None,
        max_length=2000,
        description="Special instructions for the dropper"
    )
    
    @field_validator('scheduled_date')
    @classmethod
    def validate_scheduled_date(cls, v):
        """Ensure scheduled date is not in the past."""
        if v and v < date.today():
            raise ValueError("Scheduled date cannot be in the past")
        return v
    
    @field_validator('leaflet_file_url')
    @classmethod
    def validate_leaflet_url(cls, v):
        """Basic URL validation for leaflet file - accepts HTTP/HTTPS URLs and data URLs."""
        if v and not v.startswith(('http://', 'https://', 'data:')):
            raise ValueError("Leaflet file URL must be a valid HTTP/HTTPS URL or data URL")
        return v


class JobAreaResponse(BaseModel):
    """Schema for job area in responses."""
    
    id: UUID
    area_type: str
    geojson: Optional[Dict[str, Any]]
    postcodes: Optional[List[str]]
    center_lat: Optional[float]
    center_lng: Optional[float]
    radius_km: Optional[float]
    created_at: datetime
    
    class Config:
        from_attributes = True


class JobResponse(BaseModel):
    """Schema for job responses."""
    
    id: UUID
    client_id: UUID
    status: JobStatus
    title: str
    description: Optional[str]
    leaflet_file_url: Optional[str]
    household_count: int
    cost_per_household_pence: int
    cost_total_pence: int
    platform_fee_pence: int
    dropper_payout_pence: int
    payment_intent_id: Optional[str]
    scheduled_date: date
    min_time_per_segment_sec: int
    special_instructions: Optional[str]
    paid_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]
    job_area: Optional[JobAreaResponse]
    
    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    """Schema for job list responses."""
    
    id: UUID
    status: JobStatus
    title: str
    description: Optional[str] = None
    household_count: int
    cost_total_pence: int
    scheduled_date: date
    paid_at: Optional[datetime] = None
    created_at: datetime
    job_area: Optional[JobAreaResponse] = None
    
    class Config:
        from_attributes = True


class PublicJobListResponse(BaseModel):
    """Schema for public job list responses (shows jobs from all users)."""
    
    id: UUID
    status: JobStatus
    title: str
    description: Optional[str] = None
    household_count: int
    cost_total_pence: int
    dropper_payout_pence: int
    scheduled_date: date
    paid_at: Optional[datetime] = None
    created_at: datetime
    client_business_name: Optional[str] = Field(
        None,
        description="Business name of the job creator (public information only)"
    )
    job_area: Optional[JobAreaResponse] = None
    
    class Config:
        from_attributes = True


class JobPaymentRequest(BaseModel):
    """Schema for job payment requests."""
    
    payment_method_id: Optional[str] = Field(
        None,
        description="Stripe payment method ID for automatic confirmation"
    )
    return_url: Optional[str] = Field(
        None,
        description="URL to redirect to after payment completion"
    )


class JobPaymentResponse(BaseModel):
    """Schema for job payment responses."""
    
    payment_intent_id: str
    client_secret: str
    status: str
    amount: int
    currency: str
    requires_action: bool = False
    
    class Config:
        from_attributes = True


class JobCostCalculation(BaseModel):
    """Schema for job cost calculation responses."""
    
    household_count: int
    cost_per_household_pence: int
    subtotal_pence: int
    platform_fee_pence: int
    total_cost_pence: int
    dropper_payout_pence: int
    
    class Config:
        from_attributes = True


class JobAssignmentResponse(BaseModel):
    """Schema for job assignment responses."""
    
    id: UUID
    job_id: UUID
    dropper_id: UUID
    dropper_name: str
    dropper_email: str
    accepted_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    time_spent_sec: Optional[int]
    proof_photos: Optional[List[str]]
    gps_log: Optional[Dict[str, Any]]
    verification_status: VerificationStatus
    verification_notes: Optional[str]
    verified_at: Optional[datetime]
    verified_by: Optional[UUID]
    rejection_reason: Optional[str]
    
    class Config:
        from_attributes = True


class JobAssignmentSummary(BaseModel):
    """Schema for job assignment summary in job responses."""
    
    id: UUID
    dropper_name: str
    dropper_email: str
    accepted_at: datetime
    completed_at: Optional[datetime]
    verification_status: VerificationStatus
    
    class Config:
        from_attributes = True


class MultiJobCheckoutRequest(BaseModel):
    """Schema for multi-job checkout request."""
    
    job_ids: List[UUID] = Field(
        ...,
        min_length=1,
        description="List of job IDs to checkout"
    )
    payment_method_id: Optional[str] = Field(
        None,
        description="Stripe payment method ID for automatic confirmation"
    )
    return_url: Optional[str] = Field(
        None,
        description="URL to redirect to after payment completion"
    )


class MultiJobCheckoutResponse(BaseModel):
    """Schema for multi-job checkout response."""
    
    payment_intent_id: str
    client_secret: str
    status: str
    total_amount_pence: int
    currency: str
    job_count: int
    job_ids: List[UUID]
    
    class Config:
        from_attributes = True


# Export all schemas
__all__ = [
    "JobAreaCreate",
    "JobCreate",
    "JobUpdate",
    "JobAreaResponse",
    "JobResponse",
    "JobListResponse",
    "PublicJobListResponse",
    "JobPaymentRequest",
    "JobPaymentResponse",
    "JobCostCalculation",
    "JobAssignmentResponse",
    "JobAssignmentSummary",
    "MultiJobCheckoutRequest",
    "MultiJobCheckoutResponse",
]