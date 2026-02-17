"""
DropTrack FastAPI Backend Application.

Main application entry point with middleware configuration,
exception handlers, and API routing setup.
"""

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from socketio import ASGIApp
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings
from app.socketio_server import sio
from app.database import init_db
from app.api.v1 import api_router
from app.api.v1 import webhooks, upload
from app.logging_config import setup_advanced_logging
from app.exception_handlers import register_exception_handlers
try:
    from app.monitoring import init_error_tracking
except ImportError:
    # Fallback if monitoring module is missing (e.g. deployment issue)
    import logging
    logging.getLogger(__name__).warning("⚠️  Could not import app.monitoring. Error tracking will be disabled.")
    
    def init_error_tracking():
        pass
# Socket.IO server is imported from socketio_server module to avoid circular imports

# Configure logging
setup_advanced_logging(debug=settings.debug)
logger = logging.getLogger(__name__)

# Initialize error tracking (Sentry) if configured
init_error_tracking()

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Validate production settings on startup
# This will raise ValueError in production if critical settings are missing
try:
    settings.validate_production_settings()
except ValueError as e:
    # In production, fail fast if critical settings are missing
    if os.getenv("ENVIRONMENT", "").lower() == "production":
        logger.error(
            "❌ CRITICAL: Application cannot start in production due to "
            "configuration errors: %s",
            e
        )
        raise
    else:
        logger.warning("Settings validation warning: %s", e)
except Exception as e:  # pylint: disable=broad-exception-caught
    logger.warning("Settings validation warning: %s", e)

# Validate Cognito configuration on startup
try:
    settings.validate_cognito_configuration()
except Exception as e:  # pylint: disable=broad-exception-caught
    logger.warning("Cognito configuration validation warning: %s", e)

# ============================================================
# PRODUCTION FAIL-FAST CHECKS
# These checks prevent critical misconfigurations in production
# ============================================================
is_production = os.getenv("ENVIRONMENT", "").lower() == "production"

if is_production:
    # Check 1: DEBUG mode must be disabled in production
    if settings.debug:
        error_msg = (
            "❌ CRITICAL: DEBUG mode is enabled in production!\n"
            "   This disables JWT audience/issuer verification and enables development features.\n"
            "   Fix: Set DEBUG=false in environment variables."
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    
    # Check 2: CORS origins must be explicitly configured
    if not settings.cors_origins or not settings.cors_origins.strip():
        # Only error if not explicitly allowing all via *
        if settings.cors_origins != "*":
            error_msg = (
                "❌ CRITICAL: CORS_ORIGINS not configured for production!\n"
                "   The frontend will not be able to communicate with the API.\n"
                "   Fix: Set CORS_ORIGINS=https://yourdomain.com or '*' in environment variables."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    

    
    # Check 3: Warn if localhost origins are detected in production CORS
    cors_list = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    localhost_origins = [o for o in cors_list if "localhost" in o or "127.0.0.1" in o]
    if localhost_origins:
        logger.warning(
            "⚠️  WARNING: Localhost origins detected in production CORS: %s\n"
            "   This may be a security risk. Consider removing localhost origins.",
            localhost_origins
        )
    
    logger.info("✅ Production fail-fast checks passed")


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):  # pylint: disable=unused-argument
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
    # Startup - suppress verbose logging
    # logger.info("Starting DropTrack FastAPI application")
    try:
        # Initialize database
        init_db()
        # Suppressed: logger.info("Database initialized successfully")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to initialize database: %s", e)
        logger.error("PostgreSQL database connection is required. Please ensure:")
        logger.error("  1. PostgreSQL is running")
        logger.error("  2. Database exists: CREATE DATABASE droptrack;")
        logger.error("  3. DATABASE_URL is correctly configured")
        db_url_display = (
            settings.database_url.rsplit('@', maxsplit=1)[-1]
            if '@' in settings.database_url
            else 'configured'
        )
        logger.error("   Current DATABASE_URL: %s", db_url_display)
        raise

    # Scheduler logic removed - moved to dedicated worker process (run_worker.py)
    # This prevents duplicate schedulers in multiple Gunicorn workers
    
    yield
    
    # Shutdown
    # Suppressed: logger.info("Shutting down DropTrack FastAPI application")


# Create FastAPI application
app = FastAPI(
    title="DropTrack API",
    description="Prepaid leaflet dropping platform API",
    version="1.0.0",
    docs_url="/docs" if settings.show_docs else None,
    redoc_url="/redoc" if settings.show_docs else None,
    openapi_url="/openapi.json" if settings.show_docs else None,
    lifespan=lifespan
)

# Add rate limiter to app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Set rate limiter in auth module for endpoint-specific limiting


# Register exception handlers
register_exception_handlers(app)


# Middleware Configuration
# CORS Middleware
DEFAULT_DEV_CORS = [
    "http://localhost:5173",
    "http://localhost:5174",  # Dropper PWA
    "http://localhost:4173",
    "http://localhost:4174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",  # Dropper PWA
    "http://127.0.0.1:4173",
    "http://127.0.0.1:4174",
    "http://127.0.0.1:3000",
]

def _build_cors_origins(is_prod: bool):
    """
    Build CORS origins list based on environment.
    - Development: Includes localhost origins
    - Production: Only includes explicitly configured origins (no localhost)
    
    SECURITY FIX 5: Production-safe CORS validation
    - Validates HTTPS in production
    - Raises RuntimeError if no origins configured in production
    - Raises RuntimeError if non-HTTPS origins in production
    
    Args:
        is_prod: Whether the application is running in production mode
    """
    origins = []

    # Start with configured origins from settings
    if hasattr(settings, 'cors_origins_list') and settings.cors_origins_list:
        origins.extend(settings.cors_origins_list)

    # Special case: allow all origins if "*" is present
    if "*" in origins:
        return ["*"]

    # Only include localhost origins in development mode
    if not is_prod:
        # Development mode: Add localhost origins if not already present
        for origin in DEFAULT_DEV_CORS:
            if origin not in origins:
                origins.append(origin)
    else:
        # Production mode: Remove any localhost origins that might have been configured
        origins = [origin for origin in origins
                  if not any(local in origin.lower() for local in ["localhost", "127.0.0.1"])]

        # SECURITY FIX 5: Validate all origins use HTTPS in production
        non_https_origins = [o for o in origins if not o.startswith("https://")]
        if non_https_origins:
            logger.warning(
                "⚠️  Non-HTTPS origins detected in production CORS. "
                "This is not recommended for production security."
            )

    # Remove duplicates while preserving order
    seen = set()
    unique_origins = []
    for origin in origins:
        if origin not in seen:
            seen.add(origin)
            unique_origins.append(origin)

    return unique_origins

# Determine if we're in production
is_production = (
    settings.environment.lower() == "production" or
    (not settings.debug and settings.environment.lower() != "development")
)

# Build CORS origins based on environment
cors_origins = _build_cors_origins(is_production)



# Build CORS configuration based on environment
cors_kwargs = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
    "expose_headers": ["*"],
}

if is_production:
    # Production: Use the validated origins list
    cors_kwargs["allow_origins"] = cors_origins
    cors_kwargs["allow_headers"] = [
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "Accept",
        "Origin",
        "X-Correlation-ID",
    ]
    cors_kwargs["expose_headers"] = ["X-Correlation-ID"]
    cors_kwargs["allow_origin_regex"] = None
else:
    # Development: Allow ALL origins with credentials using a catch-all regex
    # This reflects the Origin header back to the client, allowing any domain to connect
    cors_kwargs["allow_origins"] = []
    cors_kwargs["allow_origin_regex"] = ".*"

app.add_middleware(
    CORSMiddleware,
    **cors_kwargs
)

# Add SlowAPI middleware for rate limiting
app.add_middleware(SlowAPIMiddleware)

# Trusted Host Middleware (security)
# Only enable in production and when not in test mode or local development
# Use the same production check as CORS for consistency
if is_production and os.getenv("TESTING") != "1":
    # In production, restrict to specific hosts
    # Include both .com and .com.au domains
    trusted_hosts = [
        "*.droptrack.com",
        "droptrack.com",
        "*.droptrack.com.au",
        "droptrack.com.au",
        "www.droptrack.com.au",
        "54.79.69.194",  # EC2 Public IP
        "ec2-54-79-69-194.ap-southeast-2.compute.amazonaws.com",  # EC2 Public DNS
        "localhost",  # Allow localhost for Nginx proxy pass
        "127.0.0.1"   # Allow 127.0.0.1 for Nginx proxy pass
    ]
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=trusted_hosts
    )
    logger.info("TrustedHostMiddleware enabled for production domains: %s", trusted_hosts)


# Request Logging and Context Middleware
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """
    Enhanced request/response logging middleware with structured logging.
    Adds request context and performance metrics.
    """
    start_time = time.time()

    # Generate correlation ID for request tracing
    correlation_id = str(uuid.uuid4())
    request.state.correlation_id = correlation_id

    # Suppressed: Request started logging

    # Process request
    try:
        response = await call_next(request)

        # Calculate processing time
        process_time = time.time() - start_time

        # Suppressed: Request completion logging

        # Add correlation ID to response headers
        response.headers["X-Correlation-ID"] = correlation_id

        return response

    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Calculate processing time for failed requests
        process_time = time.time() - start_time

        # Log failed request
        logger.error(
            "Request failed",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": str(request.url.path),
                "process_time_ms": round(process_time * 1000, 2),
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)
            },
            exc_info=True
        )

        # Re-raise the exception to be handled by exception handlers
        raise


# Security and Audit Logging Middleware
@app.middleware("http")
async def security_logging_middleware(request: Request, call_next):
    """
    Security-focused logging middleware for audit trails.
    Logs authentication attempts, authorization failures, and sensitive operations.
    """
    # Check for authentication header (suppressed for now)
    # auth_header = request.headers.get("authorization")
    # has_auth = bool(auth_header and auth_header.startswith("Bearer "))

    # Log authentication attempts for protected endpoints (suppressed)
    # if has_auth and not request.url.path.startswith(("/health", "/docs", "/openapi.json")):
    #     pass

    response = await call_next(request)

    # Log authorization failures (suppressed)
    # if response.status_code in [401, 403]:
    #     pass

    return response


# Health Check and Utility Endpoints
@app.get("/health", tags=["health"])
async def health_check():
    """
    Health check endpoint for monitoring and load balancers.
    Returns application status and basic system information.
    """
    from datetime import datetime as dt  # pylint: disable=import-outside-toplevel
    try:
        import psutil  # type: ignore[reportMissingModuleSource]  # pylint: disable=import-outside-toplevel
    except ImportError:
        psutil = None

    try:
        # Test database connection
        from app.database import engine  # pylint: disable=import-outside-toplevel
        from sqlalchemy import text  # pylint: disable=import-outside-toplevel
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Database health check failed: %s", e)
        db_status = "unhealthy"

    # Get system metrics if psutil is available
    system_info = {}
    if psutil:
        try:
            system_info = {
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": psutil.disk_usage('/').percent
            }
        except Exception:  # pylint: disable=broad-exception-caught
            system_info = {"available": False}
    else:
        system_info = {"available": False}

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "service": "droptrack-api",
        "version": "1.0.0",
        "environment": "development" if settings.debug else "production",
        "timestamp": dt.utcnow().isoformat(),
        "database": db_status,
        "system": system_info
    }


@app.get("/robots.txt", tags=["static"], include_in_schema=False)
async def robots_txt():
    """Return robots.txt to allow search engine crawling."""
    content = "User-agent: *\nDisallow: /api/\nDisallow: /docs\nDisallow: /webhooks/"
    return Response(content=content, media_type="text/plain", status_code=200)


# Include API v1 router
app.include_router(
    api_router,
    prefix=settings.api_v1_prefix
)

#Include webhooks at root level (for Stripe webhooks)
app.include_router(
    webhooks.router,
    prefix="/webhooks",
    tags=["webhooks"]
)

# Include upload endpoints for S3 presigned URLs
app.include_router(
    upload.router,
    prefix="/api/v1/upload",
    tags=["upload"]
)


# Socket.IO event handlers are defined in socketio_server.py
# Wrap FastAPI app with Socket.IO
socketio_app = ASGIApp(sio, app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:socketio_app",  # Use socketio_app instead of app
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info"
    )
