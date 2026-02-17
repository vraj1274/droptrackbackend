"""
Custom exception classes for the DropTrack application.

This module defines application-specific exceptions with consistent error handling
and standardized error response formats.
"""

from typing import Optional, Dict, Any
from fastapi import HTTPException, status


class DropTrackException(Exception):
    """
    Base exception class for all DropTrack application errors.
    
    Provides consistent error handling with error codes, messages,
    and optional metadata for debugging.
    """
    
    def __init__(
        self,
        message: str,
        error_code: str = "DROPTRACK_ERROR",
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.error_code = error_code
        self.metadata = metadata or {}
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for API responses."""
        return {
            "error_code": self.error_code,
            "message": self.message,
            "metadata": self.metadata
        }


class DropTrackHTTPException(HTTPException):
    """
    Base HTTP exception class for API errors.
    
    Extends FastAPI's HTTPException with consistent error formatting
    and additional metadata support.
    """
    
    def __init__(
        self,
        status_code: int,
        message: str,
        error_code: str = "HTTP_ERROR",
        metadata: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ):
        self.error_code = error_code
        self.metadata = metadata or {}
        
        detail = {
            "error": {
                "type": "application_error",
                "code": status_code,
                "error_code": error_code,
                "message": message,
                "metadata": self.metadata
            }
        }
        
        super().__init__(status_code=status_code, detail=detail, headers=headers)


# Authentication and Authorization Exceptions
class AuthenticationError(DropTrackHTTPException):
    """Authentication failed - invalid or missing credentials."""
    
    def __init__(
        self,
        message: str = "Authentication failed",
        metadata: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            message=message,
            error_code="AUTHENTICATION_ERROR",
            metadata=metadata,
            headers={"WWW-Authenticate": "Bearer"}
        )


class AuthorizationError(DropTrackHTTPException):
    """Authorization failed - insufficient permissions."""
    
    def __init__(
        self,
        message: str = "Insufficient permissions",
        metadata: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            message=message,
            error_code="AUTHORIZATION_ERROR",
            metadata=metadata
        )


class TokenExpiredError(AuthenticationError):
    """JWT token has expired."""
    
    def __init__(self, message: str = "Token has expired"):
        super().__init__(
            message=message,
            metadata={"error_code": "TOKEN_EXPIRED"}
        )


class InvalidTokenError(AuthenticationError):
    """JWT token is invalid or malformed."""
    
    def __init__(self, message: str = "Invalid token"):
        super().__init__(
            message=message,
            metadata={"error_code": "INVALID_TOKEN"}
        )


# Business Logic Exceptions
class BusinessLogicError(DropTrackHTTPException):
    """Base class for business logic errors."""
    
    def __init__(
        self,
        message: str,
        error_code: str = "BUSINESS_LOGIC_ERROR",
        metadata: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=message,
            error_code=error_code,
            metadata=metadata
        )


class ResourceNotFoundError(DropTrackHTTPException):
    """Requested resource was not found."""
    
    def __init__(
        self,
        resource_type: str,
        resource_id: str = None,
        message: str = None
    ):
        if not message:
            if resource_id:
                message = f"{resource_type} with ID '{resource_id}' not found"
            else:
                message = f"{resource_type} not found"
        
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            message=message,
            error_code="RESOURCE_NOT_FOUND",
            metadata={
                "resource_type": resource_type,
                "resource_id": resource_id
            }
        )


class ResourceConflictError(DropTrackHTTPException):
    """Resource conflict - operation cannot be completed due to current state."""
    
    def __init__(
        self,
        message: str,
        resource_type: str = None,
        resource_id: str = None
    ):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            message=message,
            error_code="RESOURCE_CONFLICT",
            metadata={
                "resource_type": resource_type,
                "resource_id": resource_id
            }
        )


class ValidationError(BusinessLogicError):
    """Input validation failed."""
    
    def __init__(
        self,
        message: str,
        field: str = None,
        value: Any = None
    ):
        super().__init__(
            message=message,
            error_code="VALIDATION_ERROR",
            metadata={
                "field": field,
                "value": str(value) if value is not None else None
            }
        )


# Job-related Exceptions
class JobError(BusinessLogicError):
    """Base class for job-related errors."""
    
    def __init__(
        self,
        message: str,
        job_id: str = None,
        error_code: str = "JOB_ERROR"
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            metadata={"job_id": job_id}
        )


class JobNotFoundError(ResourceNotFoundError):
    """Job not found."""
    
    def __init__(self, job_id: str):
        super().__init__(
            resource_type="Job",
            resource_id=job_id
        )


class JobStatusError(JobError):
    """Job is in wrong status for requested operation."""
    
    def __init__(
        self,
        message: str,
        job_id: str = None,
        current_status: str = None,
        required_status: str = None
    ):
        super().__init__(
            message=message,
            job_id=job_id,
            error_code="JOB_STATUS_ERROR"
        )
        self.metadata.update({
            "current_status": current_status,
            "required_status": required_status
        })


class JobAlreadyAssignedError(ResourceConflictError):
    """Job is already assigned to another dropper."""
    
    def __init__(self, job_id: str):
        super().__init__(
            message="Job is already assigned to another dropper",
            resource_type="Job",
            resource_id=job_id
        )


class JobOutsideServiceRadiusError(BusinessLogicError):
    """Job is outside dropper's service radius."""
    
    def __init__(self, job_id: str, distance_km: float, max_radius_km: int):
        super().__init__(
            message=f"Job is {distance_km:.1f}km away, outside service radius of {max_radius_km}km",
            error_code="JOB_OUTSIDE_SERVICE_RADIUS",
            metadata={
                "job_id": job_id,
                "distance_km": distance_km,
                "max_radius_km": max_radius_km
            }
        )


# Payment-related Exceptions
class PaymentError(DropTrackHTTPException):
    """Base class for payment-related errors."""
    
    def __init__(
        self,
        message: str,
        error_code: str = "PAYMENT_ERROR",
        metadata: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            message=message,
            error_code=error_code,
            metadata=metadata
        )


class StripeError(PaymentError):
    """Stripe API error."""
    
    def __init__(
        self,
        message: str,
        stripe_error_code: str = None,
        stripe_error_type: str = None
    ):
        super().__init__(
            message=message,
            error_code="STRIPE_ERROR",
            metadata={
                "stripe_error_code": stripe_error_code,
                "stripe_error_type": stripe_error_type
            }
        )


class PaymentIntentError(PaymentError):
    """Payment intent creation or processing failed."""
    
    def __init__(
        self,
        message: str,
        payment_intent_id: str = None
    ):
        super().__init__(
            message=message,
            error_code="PAYMENT_INTENT_ERROR",
            metadata={"payment_intent_id": payment_intent_id}
        )


class PayoutError(PaymentError):
    """Payout processing failed."""
    
    def __init__(
        self,
        message: str,
        payout_id: str = None,
        dropper_id: str = None
    ):
        super().__init__(
            message=message,
            error_code="PAYOUT_ERROR",
            metadata={
                "payout_id": payout_id,
                "dropper_id": dropper_id
            }
        )


# User-related Exceptions
class UserError(BusinessLogicError):
    """Base class for user-related errors."""
    
    def __init__(
        self,
        message: str,
        user_id: str = None,
        error_code: str = "USER_ERROR"
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            metadata={"user_id": user_id}
        )


class UserNotFoundError(ResourceNotFoundError):
    """User not found."""
    
    def __init__(self, user_id: str = None, cognito_sub: str = None):
        identifier = user_id or cognito_sub
        super().__init__(
            resource_type="User",
            resource_id=identifier
        )


class UserInactiveError(AuthorizationError):
    """User account is inactive."""
    
    def __init__(self, user_id: str = None):
        super().__init__(
            message="User account is inactive",
            metadata={"user_id": user_id}
        )


class UserAlreadyExistsError(ResourceConflictError):
    """User already exists."""
    
    def __init__(self, identifier: str, identifier_type: str = "email"):
        super().__init__(
            message=f"User with {identifier_type} '{identifier}' already exists",
            resource_type="User",
            resource_id=identifier
        )


# Database and External Service Exceptions
class DatabaseError(DropTrackException):
    """Database operation failed."""
    
    def __init__(
        self,
        message: str,
        operation: str = None,
        table: str = None
    ):
        super().__init__(
            message=message,
            error_code="DATABASE_ERROR",
            metadata={
                "operation": operation,
                "table": table
            }
        )


class ExternalServiceError(DropTrackException):
    """External service integration failed."""
    
    def __init__(
        self,
        message: str,
        service_name: str,
        error_code: str = "EXTERNAL_SERVICE_ERROR"
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            metadata={"service_name": service_name}
        )


class CognitoError(ExternalServiceError):
    """Cognito service error."""
    
    def __init__(self, message: str):
        super().__init__(
            message=message,
            service_name="cognito",
            error_code="COGNITO_ERROR"
        )


# Configuration and System Exceptions
class ConfigurationError(DropTrackException):
    """Application configuration error."""
    
    def __init__(
        self,
        message: str,
        config_key: str = None
    ):
        super().__init__(
            message=message,
            error_code="CONFIGURATION_ERROR",
            metadata={"config_key": config_key}
        )


class SystemError(DropTrackException):
    """System-level error."""
    
    def __init__(
        self,
        message: str,
        component: str = None
    ):
        super().__init__(
            message=message,
            error_code="SYSTEM_ERROR",
            metadata={"component": component}
        )


# Export all exception classes
__all__ = [
    # Base exceptions
    "DropTrackException",
    "DropTrackHTTPException",
    
    # Authentication/Authorization
    "AuthenticationError",
    "AuthorizationError", 
    "TokenExpiredError",
    "InvalidTokenError",
    
    # Business Logic
    "BusinessLogicError",
    "ResourceNotFoundError",
    "ResourceConflictError",
    "ValidationError",
    
    # Job-related
    "JobError",
    "JobNotFoundError",
    "JobStatusError",
    "JobAlreadyAssignedError",
    "JobOutsideServiceRadiusError",
    
    # Payment-related
    "PaymentError",
    "StripeError",
    "PaymentIntentError",
    "PayoutError",
    
    # User-related
    "UserError",
    "UserNotFoundError",
    "UserInactiveError",
    "UserAlreadyExistsError",
    
    # Database/External Services
    "DatabaseError",
    "ExternalServiceError",
    "CognitoError",
    
    # System
    "ConfigurationError",
    "SystemError",
]