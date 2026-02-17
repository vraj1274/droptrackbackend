"""
Client profile management API endpoints.

Handles CRUD operations for client profiles, including business information
and profile updates.
"""
# pylint: disable=no-member,not-callable,too-many-lines
# SQLModel/SQLAlchemy dynamic attributes (isoformat, in_, asc, func.count) are valid at runtime

import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, func, select
from fastapi import Query  # HIGH-RISK FIX 3: Import Query for pagination limits

from app.database import get_session
from app.api.deps import require_client_role
from app.models import (
    User, Client, DropJob, JobStatus, JobArea, Transaction, PaymentStatus,
    DropPoint, Invoice, JobAssignment
)
from app.schemas.user_schemas import (
    ClientProfileCreate,
    ClientProfileResponse,
    ClientProfileUpdate,
    ClientStatsResponse,
)
from app.schemas.job_schemas import JobListResponse
from app.config import settings
from app.services.job_service import get_job_service
from app.services.stripe_service import stripe_service
from app.services.user_service import UserService, UserServiceError
from app.security import is_superadmin_email
from slowapi import Limiter
from slowapi.util import get_remote_address

# Configure Stripe
stripe.api_key = settings.stripe_secret_key

# Set up logger
logger = logging.getLogger(__name__)

router = APIRouter()

# SECURITY FIX 4: Initialize rate limiter for payment endpoints
limiter = Limiter(key_func=get_remote_address)


class PayMultipleJobsRequest(BaseModel):
    """Request body for paying multiple jobs."""
    job_ids: List[UUID]


class ConfirmPaymentRequest(BaseModel):
    """Request body for confirming payment."""
    payment_intent_id: str


@router.get(
    "/profile",
    response_model=ClientProfileResponse,
    summary="Get client profile",
    description="Get the current user's client profile information"
)
async def get_client_profile(
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Get the client profile for the current authenticated user.
    Creates a default profile if one doesn't exist.
    """
    try:
        # Check if client profile exists
        result = db.exec(
            select(Client).where(Client.user_id == current_user.id)
        ).first()

        if result:
            # Profile exists, return it
            return ClientProfileResponse(
                id=str(result.id),
                user_id=str(result.user_id),
                business_name=result.business_name,
                business_type=result.business_type,
                business_address=result.business_address,
                phone_number=result.phone_number,
                created_at=result.created_at,
            )
        else:
            # No profile exists, create default one
            user_service = UserService(db)
            # pylint: disable=protected-access
            client = user_service._create_client_profile(current_user)
            db.commit()
            db.refresh(client)

            return ClientProfileResponse(
                id=str(client.id),
                user_id=str(client.user_id),
                business_name=client.business_name,
                business_type=client.business_type,
                business_address=client.business_address,
                phone_number=client.phone_number,
                created_at=client.created_at,
            )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve client profile: {str(e)}"
        ) from e


@router.put(
    "/profile",
    response_model=ClientProfileResponse,
    summary="Update client profile",
    description="Update the current user's client profile information"
)
async def update_client_profile(
    profile_data: ClientProfileUpdate,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Update the client profile for the current authenticated user.
    Creates a profile if one doesn't exist.
    """
    try:
        # Check if client profile exists
        client = db.exec(
            select(Client).where(Client.user_id == current_user.id)
        ).first()

        if not client:
            # Create new profile if it doesn't exist
            user_service = UserService(db)
            # pylint: disable=protected-access
            client = user_service._create_client_profile(current_user)
            db.flush()

        # Update fields
        if profile_data.business_name is not None:
            client.business_name = profile_data.business_name
        if profile_data.business_type is not None:
            client.business_type = profile_data.business_type
        if profile_data.business_address is not None:
            client.business_address = profile_data.business_address
        if profile_data.phone_number is not None:
            client.phone_number = profile_data.phone_number

        # Enforce role: ensure it matches the user's email
        # (admin only for superadmin emails)
        correct_role = 'admin' if is_superadmin_email(current_user.email) else 'client'
        client.role = correct_role  # Always enforce correct role based on email

        db.add(client)
        db.commit()
        db.refresh(client)

        return ClientProfileResponse(
            id=str(client.id),
            user_id=str(client.user_id),
            business_name=client.business_name,
            business_type=client.business_type,
            business_address=client.business_address,
            phone_number=client.phone_number,
            created_at=client.created_at.isoformat() if client.created_at else None,
        )
    except UserServiceError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update client profile: {str(e)}"
        ) from e


@router.post(
    "/profile",
    response_model=ClientProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create client profile",
    description="Create a new client profile for the current user"
)
async def create_client_profile(
    profile_data: ClientProfileCreate,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Create a new client profile for the current authenticated user.
    Fails if a profile already exists.
    """
    try:
        # Check if profile already exists
        existing = db.exec(
            select(Client).where(Client.user_id == current_user.id)
        ).first()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Client profile already exists. Use PUT to update."
            )

        # Create new profile
        # Determine role: 'admin' only for superadmin emails,
        # 'client' for all others
        client_role = 'admin' if is_superadmin_email(current_user.email) else 'client'

        client = Client(
            user_id=current_user.id,
            business_name=profile_data.business_name,
            business_type=profile_data.business_type,
            business_address=profile_data.business_address,
            phone_number=profile_data.phone_number,
            role=client_role,  # Set role based on email
        )

        db.add(client)
        db.commit()
        db.refresh(client)

        return ClientProfileResponse(
            id=str(client.id),
            user_id=str(client.user_id),
            business_name=client.business_name,
            business_type=client.business_type,
            business_address=client.business_address,
            phone_number=client.phone_number,
            created_at=client.created_at.isoformat() if client.created_at else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create client profile: {str(e)}"
        ) from e


@router.get(
    "/stats",
    response_model=ClientStatsResponse,
    summary="Get client statistics",
    description="Get aggregated statistics for the authenticated client"
)
async def get_client_stats(
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Get statistics for the authenticated client including:
    - Total amount spent (from completed payment transactions)
    - Number of active campaigns (jobs with status paid, assigned,
      or completed)
    - Total jobs created (all jobs regardless of status)
    - Last payment date (most recent completed payment)

    Returns default values (zeros/null) for clients with no data.
    """
    try:
        # Calculate total spent from completed payment transactions
        total_spent_result = db.exec(
            select(func.sum(Transaction.amount_pence))
            .where(Transaction.user_id == current_user.id)
            .where(Transaction.transaction_type == 'payment')
            .where(Transaction.status == PaymentStatus.COMPLETED)
        ).first()
        total_spent_pence = total_spent_result if total_spent_result is not None else 0

        # Count active campaigns (paid, assigned, or completed jobs)
        active_statuses = [
            JobStatus.PENDING_APPROVAL,
            JobStatus.PAID,
            JobStatus.ASSIGNED,
            JobStatus.COMPLETED
        ]
        active_campaigns_result = db.exec(
            select(func.count(DropJob.id))
            .where(DropJob.client_id == current_user.id)
            .where(DropJob.status.in_(active_statuses))
        ).first()
        active_campaigns_count = (
            active_campaigns_result if active_campaigns_result is not None else 0
        )

        # Count total jobs created
        total_jobs_result = db.exec(
            select(func.count(DropJob.id))
            .where(DropJob.client_id == current_user.id)
        ).first()
        total_jobs_count = total_jobs_result if total_jobs_result is not None else 0

        # Get last payment date
        last_payment_result = db.exec(
            select(Transaction.processed_at)
            .where(Transaction.user_id == current_user.id)
            .where(Transaction.transaction_type == 'payment')
            .where(Transaction.status == PaymentStatus.COMPLETED)
            .order_by(Transaction.processed_at.desc())
            .limit(1)
        ).first()

        last_payment_date = None
        if last_payment_result:
            last_payment_date = last_payment_result.strftime('%Y-%m-%d')

        return ClientStatsResponse(
            total_spent_pence=total_spent_pence,
            active_campaigns_count=active_campaigns_count,
            total_jobs_count=total_jobs_count,
            last_payment_date=last_payment_date
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve client statistics: {str(e)}"
        ) from e



# Cart API Endpoints

@router.get(
    "/cart",
    response_model=List[JobListResponse],
    summary="Get cart items",
    description="Get all draft jobs in the client's cart"
)
async def get_cart(
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Get all draft jobs for the authenticated client (cart items).
    Only returns jobs with status='draft'.
    """
    try:
        job_service = get_job_service(db)
        cart_jobs = job_service.get_client_jobs(
            current_user,
            status=JobStatus.DRAFT,
            limit=100,  # Higher limit for cart
            offset=0
        )

        # Convert to JobListResponse format
        response_jobs = []
        for job in cart_jobs:
            response_jobs.append(JobListResponse(
                id=job.id,
                status=job.status,
                title=job.title,
                household_count=job.household_count,
                cost_total_pence=job.cost_total_pence,
                scheduled_date=job.scheduled_date,
                paid_at=job.paid_at,
                created_at=job.created_at
            ))

        return response_jobs

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve cart: {str(e)}"
        ) from e


@router.delete(
    "/cart/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove job from cart",
    description="Delete a draft job from the client's cart"
)
async def remove_from_cart(
    job_id: UUID,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Remove a draft job from the cart by deleting it.
    Validates that:
    - Job exists
    - Job belongs to the authenticated client
    - Job is in draft status
    """
    try:
        # Get the job
        job_service = get_job_service(db)
        job = job_service.get_job_by_id(job_id, current_user)

        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )

        # Verify job belongs to client (already checked by get_job_by_id)
        if job.client_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to delete this job"
            )

        # Verify job is in draft status
        if job.status != JobStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot remove job with status '{job.status.value}'. "
                    "Only draft jobs can be removed from cart."
                )
            )

        # Delete the job (cascade will delete related JobArea)
        db.delete(job)
        db.commit()

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            detail=f"Failed to remove job from cart: {str(e)}"
        ) from e


# End of Client Profile and Stats API
# Cart and Payment logic should use the dedicated /api/v1/payments endpoints
# or the job_service for job state management.

@router.get(
    "/jobs/unpaid",
    response_model=JobListResponse,
    summary="Get unpaid jobs (cart)",
    description="Retrieve all unpaid jobs for the current client (cart items)"
)
async def get_unpaid_jobs(
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """Get all unpaid jobs for the current client (cart items)."""
    try:
        # Query unpaid jobs (DRAFT status)
        statement = select(DropJob).where(
            DropJob.client_id == current_user.id,
            DropJob.status == JobStatus.DRAFT
        ).order_by(DropJob.created_at.desc())

        jobs = db.exec(statement).all()

        # Calculate total cart value
        total_cost = sum(job.cost_total_pence for job in jobs)

        return JobListResponse(
            jobs=jobs,
            total_count=len(jobs),
            total_cost=total_cost / 100  # Convert pence to dollars
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch unpaid jobs: {str(e)}"
        ) from e


# Job Tracking Models
class JobTrackingLocation(BaseModel):
    """Location coordinates for job tracking."""

    lat: float
    lng: float


class JobTrackingDropper(BaseModel):
    """Dropper information for job tracking."""

    id: str
    name: str
    email: str


class DropPointTracking(BaseModel):
    """Drop point information for job tracking."""

    id: str
    lat: float
    lng: float
    name: Optional[str] = None
    status: Optional[str] = None
    order: Optional[int] = None


class JobAssignmentTracking(BaseModel):
    """Job assignment timing information."""

    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class JobAreaInfo(BaseModel):
    """Job area information for tracking."""

    center_lat: float
    center_lng: float
    radius_km: Optional[float] = None
    area_type: Optional[str] = None


class ClientJobTrackingResponse(BaseModel):
    """Complete job tracking information for clients."""
    job_id: str
    title: str
    description: Optional[str] = None
    status: str
    household_count: int
    cost_total_pence: int
    scheduled_date: Optional[str] = None
    location: Optional[JobTrackingLocation] = None
    job_area: Optional[JobAreaInfo] = None
    dropper: Optional[JobTrackingDropper] = None
    created_at: str
    drop_points: Optional[List[DropPointTracking]] = None
    assignment: Optional[JobAssignmentTracking] = None


@router.get(
    "/jobs/{job_id}/tracking",
    response_model=ClientJobTrackingResponse,
    summary="Get job tracking data",
    description=(
        "Get tracking information for a specific job owned by the client, "
        "including dropper assignment and drop points"
    )
)
async def get_client_job_tracking(
    job_id: UUID,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Get tracking information for a specific job.
    Clients can only track their own jobs.
    """
    try:
        # Get the job and verify ownership
        job = db.exec(select(DropJob).where(DropJob.id == job_id)).first()

        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )

        # Verify ownership - handle UUID comparison properly
        if job.client_id is None:
            logger.error("Job %s has no client_id set", job_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Job has no client assigned"
            )

        # Check if user is superadmin (allow superadmins to track any job)
        user_email_normalized = (
            current_user.email.strip().lower()
            if current_user.email
            else None
        )
        is_superadmin = is_superadmin_email(user_email_normalized)

        # If superadmin, allow access to any job
        if not is_superadmin:
            # Convert both to UUID for proper comparison
            # (handle both UUID and string types)
            job_client_id = (
                UUID(str(job.client_id)) if job.client_id else None
            )
            user_id = (
                UUID(str(current_user.id)) if current_user.id else None
            )

            if job_client_id is None or user_id is None:
                logger.error(
                    "Invalid UUID: job_client_id=%s, user_id=%s",
                    job.client_id,
                    current_user.id
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid user or job ID"
                )

            if job_client_id != user_id:
                logger.warning(
                    "Access denied: Job %s client_id=%s != current_user.id=%s "
                    "(user_email=%s, user_role=%s)",
                    job_id,
                    job_client_id,
                    user_id,
                    current_user.email,
                    current_user.role
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only track your own jobs"
                )
        else:
            logger.info(
                "Superadmin %s accessing job %s tracking",
                current_user.email,
                job_id
            )

        # Get job area for location
        job_area = db.exec(select(JobArea).where(JobArea.job_id == job.id)).first()

        location = None
        job_area_info = None
        if job_area and job_area.center_lat is not None and job_area.center_lng is not None:
            location = JobTrackingLocation(
                lat=job_area.center_lat,
                lng=job_area.center_lng
            )
            job_area_info = JobAreaInfo(
                center_lat=job_area.center_lat,
                center_lng=job_area.center_lng,
                radius_km=job_area.radius_km,
                area_type=job_area.area_type
            )

        # Get dropper assignment if exists
        assignment_query = (
            select(JobAssignment, User)
            .join(User, JobAssignment.dropper_id == User.id)
            .where(JobAssignment.job_id == job.id)
        )
        assignment_result = db.exec(assignment_query).first()

        dropper_info = None
        assignment_info = None
        if assignment_result:
            assignment, dropper_user = assignment_result
            dropper_info = JobTrackingDropper(
                id=str(dropper_user.id),
                name=dropper_user.name,
                email=dropper_user.email
            )
            assignment_info = JobAssignmentTracking(
                started_at=(
                    assignment.started_at.isoformat()
                    if assignment.started_at
                    else None
                ),
                completed_at=(
                    assignment.completed_at.isoformat()
                    if assignment.completed_at
                    else None
                )
            )

        # Get drop points for this job
        drop_points = None
        drop_points_statement = select(DropPoint).where(
            DropPoint.job_id == job.id
        ).order_by(DropPoint.order.asc().nulls_last(), DropPoint.created_at.asc())
        drop_points_data = db.exec(drop_points_statement).all()
        if drop_points_data:
            drop_points = [
                DropPointTracking(
                    id=str(dp.id),
                    lat=dp.lat,
                    lng=dp.lng,
                    name=dp.name,
                    status=dp.status,
                    order=dp.order
                )
                for dp in drop_points_data
            ]

        return ClientJobTrackingResponse(
            job_id=str(job.id),
            title=job.title,
            description=job.description,
            status=job.status.value,
            household_count=job.household_count,
            cost_total_pence=job.cost_total_pence or 0,
            scheduled_date=str(job.scheduled_date) if job.scheduled_date else None,
            location=location,
            job_area=job_area_info,
            dropper=dropper_info,
            created_at=job.created_at.isoformat(),
            drop_points=drop_points,
            assignment=assignment_info
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error getting job tracking data: %s",
            e,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve job tracking data: {str(e)}"
        ) from e
