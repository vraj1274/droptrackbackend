"""
FastAPI exception handlers for consistent error responses.

This module provides centralized exception handling for all application errors,
ensuring consistent error response formats and proper logging.
"""

import logging
from typing import Union
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import stripe

from app.exceptions import (
    DropTrackException,
    DropTrackHTTPException,
    DatabaseError,
    ExternalServiceError,
    StripeError,
)
from app.utils import sanitize_error_message, log_audit_event

logger = logging.getLogger(__name__)


def create_error_response(
    status_code: int,
    error_type: str,
    message: str,
    error_code: str = None,
    metadata: dict = None,
    path: str = None
) -> JSONResponse:
    """
    Create a standardized error response.
    
    Args:
        status_code: HTTP status code
        error_type: Type of error (e.g., 'validation_error', 'business_error')
        message: Error message
        error_code: Application-specific error code
        metadata: Additional error metadata
        path: Request path where error occurred
        
    Returns:
        JSONResponse with standardized error format
    """
    content = {
        "error": {
            "type": error_type,
            "code": status_code,
            "message": message
        }
    }
    
    if error_code:
        content["error"]["error_code"] = error_code
    
    if metadata:
        content["error"]["metadata"] = metadata
    
    if path:
        content["error"]["path"] = path
    
    return JSONResponse(
        status_code=status_code,
        content=content
    )


async def droptrack_exception_handler(
    request: Request,
    exc: DropTrackException
) -> JSONResponse:
    """
    Handle custom DropTrack exceptions.
    
    Args:
        request: FastAPI request object
        exc: DropTrack exception instance
        
    Returns:
        JSONResponse with error details
    """
    logger.error(
        f"DropTrack exception: {exc.error_code} - {exc.message}",
        extra={
            "error_code": exc.error_code,
            "metadata": exc.metadata,
            "path": str(request.url.path)
        }
    )
    
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_type="application_error",
        message=exc.message,
        error_code=exc.error_code,
        metadata=exc.metadata,
        path=str(request.url.path)
    )


async def droptrack_http_exception_handler(
    request: Request,
    exc: DropTrackHTTPException
) -> JSONResponse:
    """
    Handle custom DropTrack HTTP exceptions.
    
    Args:
        request: FastAPI request object
        exc: DropTrack HTTP exception instance
        
    Returns:
        JSONResponse with error details
    """
    # Suppress warnings for expected authentication errors (missing Cognito config)
    error_message = exc.detail.get('error', {}).get('message', '') if isinstance(exc.detail, dict) else str(exc.detail)
    is_auth_error = (exc.status_code in [401, 403] and 
                    ("Token validation" in error_message or "JWKS" in error_message or "authentication" in error_message.lower()))
    
    if not is_auth_error:
        logger.warning(
            f"HTTP exception: {exc.error_code} - {error_message}",
            extra={
                "status_code": exc.status_code,
                "error_code": exc.error_code,
                "metadata": exc.metadata,
                "path": str(request.url.path)
            }
        )
    
    # Add path to the error response
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        exc.detail["error"]["path"] = str(request.url.path)
    
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail,
        headers=exc.headers
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException
) -> JSONResponse:
    """
    Handle standard HTTP exceptions.
    
    Args:
        request: FastAPI request object
        exc: Starlette HTTP exception instance
        
    Returns:
        JSONResponse with standardized error format
    """
    path = str(request.url.path)
    
    # Suppress warnings for expected authentication errors (missing Cognito config)
    detail_str = str(exc.detail)
    is_auth_error = (exc.status_code == 401 and 
                    ("Token validation" in detail_str or "JWKS" in detail_str or "authentication" in detail_str.lower()))
    
    # Suppress 404 warnings for common files that browsers request automatically
    # Check if path contains or ends with common browser file names (works for any path)
    common_404_files = [
        "favicon.ico",
        "robots.txt",
        "apple-touch-icon.png",
        "favicon-16x16.png",
        "favicon-32x32.png",
        "site.webmanifest",
        "browserconfig.xml",
        "sw.js",  # Service worker
        "manifest.json",
    ]
    is_common_404 = exc.status_code == 404 and any(
        file_name in path for file_name in common_404_files
    )
    
    # Suppress 404 warnings for API endpoints that might not exist yet or are optional
    is_optional_api_endpoint = exc.status_code == 404 and (
        "/map-data" in path or
        "/socket.io" in path or
        "/admin/" in path or  # Admin endpoints may not exist in all configurations
        path.startswith("/api/v1/admin/")  # Admin API endpoints
    )
    
    if not is_auth_error and not is_common_404 and not is_optional_api_endpoint:
        logger.warning(
            f"HTTP {exc.status_code}: {exc.detail}",
            extra={
                "status_code": exc.status_code,
                "path": path
            }
        )
    
    return create_error_response(
        status_code=exc.status_code,
        error_type="http_error",
        message=exc.detail,
        path=str(request.url.path)
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
) -> JSONResponse:
    """
    Handle request validation errors with detailed information.
    
    Args:
        request: FastAPI request object
        exc: Pydantic validation error
        
    Returns:
        JSONResponse with validation error details
    """
    # Convert errors to JSON-serializable format
    serializable_errors = []
    for error in exc.errors():
        serializable_error = {}
        for key, value in error.items():
            if key == 'ctx' and isinstance(value, dict):
                # Convert ValueError objects in context to strings
                serializable_ctx = {}
                for ctx_key, ctx_value in value.items():
                    if isinstance(ctx_value, Exception):
                        serializable_ctx[ctx_key] = str(ctx_value)
                    else:
                        serializable_ctx[ctx_key] = ctx_value
                serializable_error[key] = serializable_ctx
            else:
                serializable_error[key] = value
        serializable_errors.append(serializable_error)
    
    logger.warning(
        f"Validation error: {len(serializable_errors)} errors",
        extra={
            "errors": serializable_errors,
            "path": str(request.url.path)
        }
    )
    
    return create_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_type="validation_error",
        message="Request validation failed",
        error_code="VALIDATION_ERROR",
        metadata={"details": serializable_errors},
        path=str(request.url.path)
    )


async def database_exception_handler(
    request: Request,
    exc: Union[SQLAlchemyError, IntegrityError]
) -> JSONResponse:
    """
    Handle database-related exceptions.
    
    Args:
        request: FastAPI request object
        exc: SQLAlchemy exception instance
        
    Returns:
        JSONResponse with database error details
    """
    logger.error(
        f"Database error: {type(exc).__name__} - {str(exc)}",
        exc_info=True,
        extra={"path": str(request.url.path)}
    )
    
    # Determine specific error type and message
    if isinstance(exc, IntegrityError):
        error_code = "DATABASE_INTEGRITY_ERROR"
        message = "Data integrity constraint violation"
        
        # Check for specific constraint violations
        error_str = str(exc).lower()
        if "unique" in error_str:
            message = "Resource already exists"
            error_code = "DUPLICATE_RESOURCE"
        elif "foreign key" in error_str:
            message = "Referenced resource not found"
            error_code = "INVALID_REFERENCE"
        elif "not null" in error_str:
            message = "Required field is missing"
            error_code = "MISSING_REQUIRED_FIELD"
    else:
        error_code = "DATABASE_ERROR"
        message = "Database operation failed"
    
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_type="database_error",
        message=message,
        error_code=error_code,
        path=str(request.url.path)
    )


async def stripe_exception_handler(
    request: Request,
    exc: stripe.StripeError
) -> JSONResponse:
    """
    Handle Stripe API exceptions.
    
    Args:
        request: FastAPI request object
        exc: Stripe exception instance
        
    Returns:
        JSONResponse with Stripe error details
    """
    logger.error(
        f"Stripe error: {type(exc).__name__} - {str(exc)}",
        extra={
            "stripe_error_code": getattr(exc, 'code', None),
            "stripe_error_type": getattr(exc, 'type', None),
            "path": str(request.url.path)
        }
    )
    
    # Map Stripe error types to appropriate HTTP status codes
    status_code_map = {
        stripe.CardError: status.HTTP_402_PAYMENT_REQUIRED,
        stripe.RateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
        stripe.InvalidRequestError: status.HTTP_400_BAD_REQUEST,
        stripe.AuthenticationError: status.HTTP_401_UNAUTHORIZED,
        stripe.PermissionError: status.HTTP_403_FORBIDDEN,
    }
    
    status_code = status_code_map.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # Sanitize error message for client
    if hasattr(exc, 'user_message') and exc.user_message:
        message = exc.user_message
    else:
        message = sanitize_error_message(exc, include_details=False)
    
    return create_error_response(
        status_code=status_code,
        error_type="payment_error",
        message=message,
        error_code="STRIPE_ERROR",
        metadata={
            "stripe_error_code": getattr(exc, 'code', None),
            "stripe_error_type": getattr(exc, 'type', None)
        },
        path=str(request.url.path)
    )


async def general_exception_handler(
    request: Request,
    exc: Exception
) -> JSONResponse:
    """
    Handle unexpected exceptions with proper logging and sanitized responses.
    
    Args:
        request: FastAPI request object
        exc: Exception instance
        
    Returns:
        JSONResponse with generic error message
    """
    logger.error(
        f"Unhandled exception: {type(exc).__name__} - {str(exc)}",
        exc_info=True,
        extra={
            "exception_type": type(exc).__name__,
            "path": str(request.url.path)
        }
    )
    
    # Log audit event for security-related errors
    if any(keyword in str(exc).lower() for keyword in ['auth', 'token', 'permission']):
        try:
            # Try to extract user info from request state if available
            user_id = getattr(request.state, 'user_id', 'anonymous')
            log_audit_event(
                user_id=user_id,
                action="security_error",
                resource_type="system",
                metadata={
                    "exception_type": type(exc).__name__,
                    "path": str(request.url.path),
                    "error_message": str(exc)
                }
            )
        except Exception:
            # Don't let audit logging failure break error handling
            pass
    
    # Use sanitized error message
    message = sanitize_error_message(exc, include_details=False)
    
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_type="internal_error",
        message=message,
        error_code="INTERNAL_ERROR",
        path=str(request.url.path)
    )


# Exception handler mapping for FastAPI
EXCEPTION_HANDLERS = {
    DropTrackHTTPException: droptrack_http_exception_handler,
    DropTrackException: droptrack_exception_handler,
    StarletteHTTPException: http_exception_handler,
    RequestValidationError: validation_exception_handler,
    SQLAlchemyError: database_exception_handler,
    IntegrityError: database_exception_handler,
    stripe.StripeError: stripe_exception_handler,
    Exception: general_exception_handler,
}


def register_exception_handlers(app):
    """Register exception handlers with minimal logging."""
    """
    Register all exception handlers with the FastAPI application.
    
    Args:
        app: FastAPI application instance
    """
    for exception_class, handler in EXCEPTION_HANDLERS.items():
        app.add_exception_handler(exception_class, handler)
    
    # Suppressed: logger.info(f"Registered {len(EXCEPTION_HANDLERS)} exception handlers")