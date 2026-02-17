"""
Socket.IO server for real-time location tracking.
Separated from main.py to avoid circular imports.
"""

import logging
from datetime import datetime, timezone

import socketio
from sqlmodel import select

from app.config import settings
from app.database import get_session
from app.models import User, UserRole
from app.services.cognito import cognito_service

logger = logging.getLogger(__name__)

# Default development CORS origins
DEFAULT_DEV_CORS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

def _build_socketio_cors_origins():
    """Build list of allowed CORS origins for Socket.IO"""
    origins = []

    # Always include production origin
    origins.append("https://droptrack.com.au")

    # Add configured origins from settings
    if hasattr(settings, 'cors_origins_list') and settings.cors_origins_list:
        origins.extend(settings.cors_origins_list)

    # Add development origins
    origins.extend(DEFAULT_DEV_CORS)

    # Remove duplicates while preserving order
    seen = set()
    unique_origins = []
    for origin in origins:
        if origin not in seen:
            seen.add(origin)
            unique_origins.append(origin)

    return unique_origins

def _socketio_cors_validator(origin: str, environ: dict) -> bool:
    """
    Validate CORS origin for Socket.IO dynamically.
    This prevents multiple CORS header values by checking origin per-request.
    Returns True if origin is allowed, False otherwise.
    The environ parameter is required by Socket.IO but not used here.
    """
    _ = environ  # Explicitly mark as unused to satisfy linter
    # Always allow production origin
    if origin == "https://droptrack.com.au":
        return True

    # Always allow localhost origins for development
    if origin.startswith('http://localhost') or origin.startswith('http://127.0.0.1'):
        return True

    # Check if origin is in configured list
    allowed_origins = _build_socketio_cors_origins()
    if origin in allowed_origins:
        return True

    return False

# Create Socket.IO server with CORS validator
sio = socketio.AsyncServer(
    cors_allowed_origins=_socketio_cors_validator,  # Use validator instead of "*"
    async_mode='asgi',
    logger=False,
    engineio_logger=False,
    cors_credentials=True,
    allow_upgrades=True,
    transports=['polling', 'websocket']
)


@sio.event
async def connect(sid, environ, auth):  # pylint: disable=unused-argument
    """Handle client connection with token verification.
    
    Args:
        sid: Session ID
        environ: Environment dict (required by Socket.IO but not used)
        auth: Authentication data containing token
    """
    _ = environ  # Required by Socket.IO but not used
    try:
        token = auth.get('token') if auth else None
        if not token:
            logger.warning("WebSocket connection rejected: No token provided for %s", sid)
            return False

        # Verify token and get user information
        try:
            user_claims = await cognito_service.validate_and_extract_user(token)
            cognito_sub = user_claims.get('cognito_sub')
            email = user_claims.get('email')

            if not cognito_sub:
                logger.warning(
                    "WebSocket connection rejected: No cognito_sub in token for %s", sid
                )
                return False

            # Get user from database
            db = next(get_session())
            try:
                user = db.exec(
                    select(User).where(User.cognito_sub == cognito_sub)
                ).first()
                if not user:
                    logger.warning(
                        "WebSocket connection rejected: User not found for cognito_sub %s",
                        cognito_sub
                    )
                    return False

                # Store session with user_id and role
                await sio.save_session(sid, {
                    'user_id': str(user.id),
                    'user_role': user.role.value if user.role else None,
                    'cognito_sub': cognito_sub,
                    'email': email,
                    'token': token
                })
                if settings.debug:
                    role_value = user.role.value if user.role else 'unknown'
                    logger.debug(
                        "WebSocket client connected: %s (role: %s)", sid, role_value
                    )
                else:
                    logger.info("WebSocket client connected: %s", sid)
                return True
            finally:
                db.close()
        except Exception as token_error:  # pylint: disable=broad-except
            logger.error(
                "WebSocket token validation error for %s: %s", sid, token_error
            )
            return False
    except Exception as e:  # pylint: disable=broad-except
        logger.error("WebSocket connection error: %s", e)
        return False


@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    logger.info("WebSocket client disconnected: %s", sid)


@sio.event
async def location_update(sid, data):
    """Handle dropper location updates - only droppers can send location updates"""
    try:
        session = await sio.get_session(sid)
        user_id = session.get('user_id')
        user_role = session.get('user_role')

        if not user_id or user_id == 'unknown':
            logger.warning(
                "Location update rejected: Invalid user_id for session %s", sid
            )
            return

        # Validate that only droppers can send location updates
        if user_role != UserRole.DROPPER.value:
            logger.warning(
                "Location update rejected: User with role %s is not a dropper", user_role
            )
            return

        if not data.get('location'):
            logger.warning(
                "Location update rejected: No location data for session %s", sid
            )
            return

        location = data['location']
        timestamp = data.get('timestamp', datetime.now(timezone.utc).isoformat())

        # Validate location coordinates
        lat = float(location.get('lat', 0))
        lng = float(location.get('lng', 0))

        # Validate coordinate ranges
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            logger.warning(
                "Location update rejected: Invalid coordinate ranges for dropper"
            )
            return

        if lat == 0 and lng == 0:
            logger.warning(
                "Location update rejected: Invalid coordinates (0,0) for dropper"
            )
            return

        # Broadcast location to all clients (admin/client) except sender
        await sio.emit('location_broadcast', {
            'dropper_id': user_id,
            'location': {
                'lat': lat,
                'lng': lng,
            },
            'timestamp': timestamp
        }, skip_sid=sid)  # Don't send back to sender

        # Security: Don't log location coordinates
        if settings.debug:
            logger.debug("Location broadcasted for dropper")
        else:
            logger.debug("Location broadcasted")
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Error handling location_update: %s", e, exc_info=True)
