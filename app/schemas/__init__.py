# Pydantic request/response schemas

from .job_schemas import *
from .dropper_schemas import *
from .user_schemas import *
from .admin_schemas import *

__all__ = [
    # Job schemas
    "JobAreaCreate",
    "JobCreate", 
    "JobUpdate",
    "DropPointCreate",
    "JobAreaResponse",
    "JobResponse",
    "JobListResponse",
    "JobPaymentRequest",
    "JobPaymentResponse",
    "JobCostCalculation",
    "JobAssignmentResponse",
    "JobAssignmentSummary",
    # Dropper schemas
    "AvailableJobResponse",
    "JobAcceptanceRequest",
    "JobAcceptanceResponse",
    "JobCompletionRequest",
    "JobCompletionResponse",
    "DropperJobResponse",
    # User and authentication schemas
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
    # Admin and verification schemas
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
]