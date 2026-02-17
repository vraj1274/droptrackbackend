"""
Pydantic schemas for admin API requests and responses.
Defines validation and serialization for admin verification endpoints.
"""

from typing import Optional, List, Any
from datetime import datetime, date
from uuid import UUID
from pydantic import BaseModel, Field
from app.models import VerificationStatus


class JobVerificationRequest(BaseModel):
    """Schema for job verification requests."""
    
    verification_status: VerificationStatus = Field(
        ...,
        description="Verification decision: approved or rejected"
    )
    reason: Optional[str] = Field(
        None,
        max_length=2000,
        description="Reason for verification decision (required for rejection)"
    )
    
    def model_validate(self, values):
        """Validate that reason is provided for rejections."""
        if isinstance(values, dict):
            verification_status = values.get('verification_status')
            reason = values.get('reason')
            
            if verification_status == VerificationStatus.REJECTED and not reason:
                raise ValueError("Reason is required when rejecting a job")
        
        return values


class JobVerificationResponse(BaseModel):
    """Schema for job verification responses."""
    
    job_id: UUID
    verification_status: VerificationStatus
    verification_notes: Optional[str]
    verified_at: datetime
    verified_by: UUID
    payout_amount_pence: Optional[int] = Field(
        None,
        description="Amount paid to dropper (only for approved jobs)"
    )
    platform_fee_pence: Optional[int] = Field(
        None,
        description="Platform fee amount (only for approved jobs)"
    )
    
    class Config:
        from_attributes = True


class AdminJobListResponse(BaseModel):
    """Schema for admin job list responses."""
    
    job_id: UUID
    title: str
    household_count: int
    cost_total_pence: int
    scheduled_date: date
    dropper_name: str
    dropper_email: str
    completed_at: Optional[datetime]
    time_spent_sec: Optional[int]
    proof_photos_count: int
    has_gps_log: bool
    
    class Config:
        from_attributes = True


class PayoutCalculationResponse(BaseModel):
    """Schema for payout calculation responses."""
    
    total_amount_pence: int
    platform_fee_pence: int
    dropper_payout_pence: int
    fee_percentage: float = 0.15
    
    class Config:
        from_attributes = True


class TransactionSummaryResponse(BaseModel):
    """Schema for transaction summary responses."""
    
    transaction_id: UUID
    transaction_type: str
    amount_pence: int
    status: str
    description: str
    created_at: datetime
    processed_at: Optional[datetime]
    stripe_transfer_id: Optional[str]
    failure_reason: Optional[str]
    
    class Config:
        from_attributes = True


class TransactionDetailResponse(BaseModel):
    """Schema for detailed transaction responses."""
    
    id: UUID
    user_id: UUID
    job_id: Optional[UUID]
    transaction_type: str
    amount_pence: int
    currency: str
    status: str
    stripe_payment_intent_id: Optional[str]
    stripe_transfer_id: Optional[str]
    stripe_charge_id: Optional[str]
    stripe_refund_id: Optional[str]
    description: str
    transaction_metadata: Optional[dict]
    failure_reason: Optional[str]
    processed_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class AdminDashboardStats(BaseModel):
    """Schema for admin dashboard statistics."""
    
    total_jobs: int
    pending_verifications: int
    completed_jobs: int
    rejected_jobs: int
    total_revenue_pence: int
    total_payouts_pence: int
    active_droppers: int
    active_clients: int
    
    class Config:
        from_attributes = True


class ErrorResponse(BaseModel):
    """Schema for standardized error responses."""
    
    error: str
    message: str
    details: Optional[dict] = None
    timestamp: datetime
    
    class Config:
        from_attributes = True


class ValidationErrorResponse(BaseModel):
    """Schema for validation error responses."""
    
    error: str = "validation_error"
    message: str
    field_errors: List[dict]
    timestamp: datetime
    
    class Config:
        from_attributes = True


class PayoutRequest(BaseModel):
    """Schema for manual payout requests."""
    
    amount_pence: int = Field(
        ...,
        gt=0,
        description="Payout amount in pence"
    )
    description: str = Field(
        ...,
        max_length=500,
        description="Description for the payout"
    )


class PayoutResponse(BaseModel):
    """Schema for payout responses."""
    
    transaction_id: UUID
    stripe_transfer_id: str
    amount_pence: int
    status: str
    description: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class AssignJobRequest(BaseModel):
    """Schema for job assignment requests."""
    
    dropper_id: UUID = Field(
        ...,
        description="ID of the dropper to assign the job to"
    )


class AssignJobResponse(BaseModel):
    """Schema for job assignment responses."""
    
    success: bool
    job_id: UUID
    dropper_id: UUID
    message: str
    
    class Config:
        from_attributes = True


class UnassignedJobResponse(BaseModel):
    """Schema for unassigned job list responses."""
    
    job_id: UUID
    title: str
    description: Optional[str]
    household_count: int
    cost_total_pence: int
    scheduled_date: Optional[str]
    leaflet_file_url: str
    paid_at: Optional[str]
    client: dict
    area_coverage: Optional[dict]
    is_broadcasted: bool = False
    broadcasted_at: Optional[str] = None
    
    class Config:
        from_attributes = True


class DropperSearchResponse(BaseModel):
    """Schema for dropper search responses."""
    
    id: UUID
    name: str
    email: str
    rating: float
    service_radius_km: int
    total_jobs_completed: int
    is_available: bool
    
    class Config:
        from_attributes = True


class BroadcastJobResponse(BaseModel):
    """Schema for job broadcast responses."""
    
    success: bool
    job_id: UUID
    message: str
    broadcasted_at: datetime
    
    class Config:
        from_attributes = True


class PendingJobResponse(BaseModel):
    """Schema for pending approval job responses."""
    
    job_id: UUID
    title: str
    description: Optional[str]
    household_count: int
    cost_total_pence: int
    scheduled_date: Optional[date]
    created_at: Optional[datetime]
    client: dict
    
    class Config:
        from_attributes = True


class ApprovedJobResponse(BaseModel):
    """Schema for approved job responses."""
    
    job_id: UUID
    title: str
    household_count: int
    cost_total_pence: int
    scheduled_date: Optional[date]
    is_assigned: bool
    client: dict
    
    class Config:
        from_attributes = True


class DropperListResponse(BaseModel):
    """Schema for dropper list responses."""
    
    id: UUID
    name: str
    email: str
    is_active: bool
    rating: float
    total_jobs_completed: int
    active_assignments: int
    created_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class ClientListResponse(BaseModel):
    """Schema for client list responses."""
    
    id: UUID
    name: str
    email: str
    is_active: bool
    business_name: Optional[str]
    total_jobs: int
    total_spent_pence: int
    created_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    
    items: List[Any]
    total: int
    limit: int
    offset: int
    
    class Config:
        from_attributes = True


# Export all schemas
__all__ = [
    "JobVerificationRequest",
    "JobVerificationResponse", 
    "AdminJobListResponse",
    "PayoutCalculationResponse",
    "TransactionSummaryResponse",
    "TransactionDetailResponse",
    "AdminDashboardStats",
    "ErrorResponse",
    "ValidationErrorResponse",
    "PayoutRequest",
    "PayoutResponse",
    "AssignJobRequest",
    "AssignJobResponse",
    "UnassignedJobResponse",
    "DropperSearchResponse",
    "BroadcastJobResponse",
    "PendingJobResponse",
    "ApprovedJobResponse",
    "DropperListResponse",
    "ClientListResponse",
    "PaginatedResponse",
]