"""
Pydantic schemas for dropper-related API requests and responses.
Defines validation and serialization for dropper functionality.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.models import JobStatus, VerificationStatus


class AvailableJobResponse(BaseModel):
    """Schema for available job responses for droppers."""
    
    id: UUID
    title: str
    description: Optional[str]
    household_count: int
    cost_total_pence: int
    dropper_payout_pence: int
    scheduled_date: date
    min_time_per_segment_sec: int
    special_instructions: Optional[str]
    distance_km: Optional[float] = Field(
        None,
        description="Distance from dropper's location in kilometers"
    )
    job_area: Optional[Dict[str, Any]] = Field(
        None,
        description="Job area information for mapping"
    )
    drop_points: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of drop points for this job"
    )
    is_assigned_to_me: bool = Field(
        default=False,
        description="Whether this job is assigned to the current dropper"
    )
    is_broadcasted: bool = Field(
        default=False,
        description="Whether this job is broadcasted to all droppers"
    )
    assignment_type: str = Field(
        default="available",
        description="Type of assignment: 'assigned', 'broadcasted', or 'available'"
    )
    created_at: datetime
    
    class Config:
        from_attributes = True


class JobAcceptanceRequest(BaseModel):
    """Schema for job acceptance requests."""
    
    dropper_location_lat: Optional[float] = Field(
        None,
        ge=-90,
        le=90,
        description="Current latitude of dropper for validation"
    )
    dropper_location_lng: Optional[float] = Field(
        None,
        ge=-180,
        le=180,
        description="Current longitude of dropper for validation"
    )


class JobAcceptanceResponse(BaseModel):
    """Schema for job acceptance responses."""
    
    job_id: UUID
    assignment_id: UUID
    accepted_at: datetime
    message: str
    
    class Config:
        from_attributes = True


class JobCompletionRequest(BaseModel):
    """Schema for job completion proof submission."""
    
    proof_photos: List[str] = Field(
        ...,
        min_items=1,
        max_items=10,
        description="URLs of proof photos (1-10 photos required)"
    )
    gps_log: Dict[str, Any] = Field(
        ...,
        description="GPS tracking data during job completion"
    )
    time_spent_sec: int = Field(
        ...,
        gt=0,
        description="Time spent on the job in seconds"
    )
    completion_notes: Optional[str] = Field(
        None,
        max_length=1000,
        description="Optional notes about job completion"
    )
    
    @field_validator('proof_photos')
    @classmethod
    def validate_photo_urls(cls, v):
        """Validate that all photo URLs are valid HTTP/HTTPS URLs."""
        for url in v:
            if not url.startswith(('http://', 'https://')):
                raise ValueError("All photo URLs must be valid HTTP/HTTPS URLs")
        return v
    
    @field_validator('gps_log')
    @classmethod
    def validate_gps_log(cls, v):
        """Basic validation of GPS log structure."""
        required_fields = ['start_location', 'end_location']
        for field in required_fields:
            if field not in v:
                raise ValueError(f"GPS log must contain '{field}' field")
            
            location = v[field]
            if not isinstance(location, dict) or 'lat' not in location or 'lng' not in location:
                raise ValueError(f"GPS log '{field}' must contain 'lat' and 'lng' coordinates")
        
        return v


class JobCompletionResponse(BaseModel):
    """Schema for job completion responses."""
    
    job_id: UUID
    assignment_id: UUID
    completed_at: datetime
    verification_status: VerificationStatus
    message: str
    
    class Config:
        from_attributes = True


class DropperJobResponse(BaseModel):
    """Schema for dropper's assigned job details."""
    
    id: UUID
    title: str
    description: Optional[str]
    leaflet_file_url: str = Field(
        description="URL to the leaflet file to be distributed"
    )
    household_count: int
    dropper_payout_pence: int
    scheduled_date: date
    min_time_per_segment_sec: int
    special_instructions: Optional[str]
    status: JobStatus
    job_area: Optional[Dict[str, Any]] = Field(
        None,
        description="Job area information for mapping (map data)"
    )
    drop_points: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of drop points for this job"
    )
    assignment: Optional[Dict[str, Any]] = Field(
        None,
        description="Assignment details if job is assigned to this dropper"
    )
    is_assigned_to_me: bool = Field(
        default=True,
        description="Whether this job is assigned to the current dropper"
    )
    is_broadcasted: bool = Field(
        default=False,
        description="Whether this job is broadcasted to all droppers"
    )
    assignment_type: str = Field(
        default="assigned",
        description="Type of assignment: 'assigned', 'broadcasted', or 'available'"
    )
    created_at: datetime
    
    class Config:
        from_attributes = True


class JobStartRequest(BaseModel):
    """Schema for starting a job."""
    
    start_location: Optional[Dict[str, Any]] = Field(
        None,
        description="GPS location where the job was started (optional)"
    )


class JobStartResponse(BaseModel):
    """Schema for job start response."""
    
    job_id: UUID
    assignment_id: UUID
    started_at: datetime
    message: str = "Job started successfully"


class JobRejectionRequest(BaseModel):
    """Schema for job rejection requests."""
    
    reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional reason for rejecting the job"
    )


class JobRejectionResponse(BaseModel):
    """Schema for job rejection responses."""
    
    job_id: UUID
    assignment_id: UUID
    rejected_at: datetime
    rejection_reason: Optional[str]
    message: str
    
    class Config:
        from_attributes = True


class JobPauseResponse(BaseModel):
    """Schema for job pause response."""
    
    job_id: UUID
    assignment_id: UUID
    paused_at: datetime
    message: str = "Job paused successfully"


class JobResumeResponse(BaseModel):
    """Schema for job resume response."""
    
    job_id: UUID
    assignment_id: UUID
    resumed_at: datetime
    message: str = "Job resumed successfully"


# Export all schemas
__all__ = [
    "AvailableJobResponse",
    "JobAcceptanceRequest",
    "JobAcceptanceResponse", 
    "JobStartRequest",
    "JobStartResponse",
    "JobPauseResponse",
    "JobResumeResponse",
    "JobCompletionRequest",
    "JobCompletionResponse",
    "DropperJobResponse",
    "JobRejectionRequest",
    "JobRejectionResponse",
]