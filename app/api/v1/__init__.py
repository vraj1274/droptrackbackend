"""
API v1 endpoints module.
Organizes all v1 API routers for the DropTrack platform.
"""

from fastapi import APIRouter
from . import jobs, dropper, admin, payments, webhooks, client, map, auth, pricing, disputes, user, saved_jobs

# Create main v1 router
api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["auth"]
)

api_router.include_router(
    user.router,
    prefix="/users",
    tags=["users"]
)

api_router.include_router(
    jobs.router,
    prefix="/jobs",
    tags=["jobs"]
)

api_router.include_router(
    client.router,
    prefix="/client",
    tags=["client"]
)

api_router.include_router(
    dropper.router,
    prefix="/dropper", 
    tags=["dropper"]
)

api_router.include_router(
    admin.router,
    prefix="/admin",
    tags=["admin"]
)

api_router.include_router(
    payments.router,
    prefix="/payments",
    tags=["payments"]
)

# Map endpoints (no prefix, direct paths)
api_router.include_router(
    map.router,
    tags=["map"]
)

# Pricing endpoints
api_router.include_router(
    pricing.router,
    prefix="/pricing",
    tags=["pricing"]
)

# Dispute endpoints
api_router.include_router(
    disputes.router,
    prefix="/disputes",
    tags=["disputes"]
)

# Saved jobs endpoints
api_router.include_router(
    saved_jobs.router,
    prefix="/saved-jobs",
    tags=["saved-jobs"]
)

# Note: webhooks router is included at root level in main.py

__all__ = ["api_router", "auth", "jobs", "client", "dropper", "admin", "payments", "webhooks", "map", "pricing", "disputes", "user", "saved_jobs"]