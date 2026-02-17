"""
Dropper API endpoints.
Handles job discovery, acceptance, and completion for droppers.
Also manages dropper profile data.
"""
# pylint: disable=too-many-lines

from typing import List, Optional
from uuid import UUID
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func as sql_func
from sqlmodel import Session, select, and_

from app.api.deps import require_dropper_role, require_client_or_admin, get_current_user
from app.database import get_session
from app.models import (
    User, JobStatus, Dropper, DroperLocation, DropPoint, DropJob, JobArea,
    JobAssignment
)
from app.services.cognito import cognito_service  # noqa: F401
from app.schemas.user_schemas import (
    DropperProfileCreate,
    DropperProfileResponse,
    DropperProfileUpdate,
)
from app.schemas.dropper_schemas import (
    AvailableJobResponse, JobAcceptanceRequest, JobAcceptanceResponse,
    JobStartRequest, JobStartResponse,
    JobCompletionRequest, JobCompletionResponse, DropperJobResponse,
    JobRejectionRequest, JobRejectionResponse,
    JobPauseResponse, JobResumeResponse,
    JobPauseRequest, JobResumeRequest
)
from app.services.user_service import UserService, UserServiceError
from app.services.job_service import get_job_service, JobServiceError
from app.config import settings

# Initialize logger
logger = logging.getLogger(__name__)


router = APIRouter(tags=["dropper"])


@router.get(
    "/debug/role",
    summary="Debug user role and profile",
    description=(
        "Debug endpoint to check current user role and dropper profile. "
        "Only available in development mode."
    )
)
async def debug_user_role(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
):
    """
    Debug endpoint to check user role and profile.
    Only available in development/debug mode for security.
    """
    # Only allow in development/debug mode
    if not settings.debug and settings.environment.lower() not in ["development", "dev", "local"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Debug endpoint is only available in development mode"
        )

    try:
        # Get dropper profile
        dropper_statement = select(Dropper).where(Dropper.user_id == current_user.id)
        dropper_profile = db.exec(dropper_statement).first()

        return {
            "user_id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role.value,
            "has_dropper_profile": dropper_profile is not None,
            "dropper_profile": {
                "service_radius_km": dropper_profile.service_radius_km if dropper_profile else None,
                "is_available": dropper_profile.is_available if dropper_profile else None,
                "rating": dropper_profile.rating if dropper_profile else None,
            } if dropper_profile else None,
            "debug_info": {
                "role_type": type(current_user.role).__name__,
                "is_active": current_user.is_active,
                "created_at": (
                    current_user.created_at.isoformat()
                    if current_user.created_at else None
                ),
                "environment": settings.environment
            }
        }
    except Exception as e:
        logger.error("Debug role endpoint error: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Debug error: {str(e)}"
        ) from e


@router.get(
    "/debug/token-claims",
    summary="Debug token claims (development only)",
    description="Inspect JWT token claims for troubleshooting role issues"
)
async def debug_token_claims(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    current_user: User = Depends(get_current_user)
):
    """
    Debug endpoint to inspect JWT token claims.
    Only available in debug mode.
    """
    if not settings.debug:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found"
        )

    token = credentials.credentials if credentials else None
    if not token:
        return {
            "error": "No token provided",
            "user_info": {
                "user_id": str(current_user.id),
                "email": current_user.email,
                "role": current_user.role.value,
                "cognito_sub": current_user.cognito_sub
            }
        }

    try:
        # Get verified claims
        verified_claims = await cognito_service.validate_token(token)
        user_claims = cognito_service.extract_user_claims(verified_claims)

        return {
            "token_info": {
                "token_type": verified_claims.get("token_use"),
                "has_custom_user_role": "custom:user_role" in verified_claims,
                "custom_user_role_value": verified_claims.get("custom:user_role"),
                "cognito_groups": verified_claims.get("cognito:groups", []),
                "available_claims": list(verified_claims.keys()),
                "custom_claims": {
                    k: v for k, v in verified_claims.items()
                    if k.startswith("custom:")
                },
            },
            "extracted_role": user_claims.get("role"),
            "user_info": {
                "user_id": str(current_user.id),
                "email": current_user.email,
                "role": current_user.role.value,
                "cognito_sub": current_user.cognito_sub
            },
            "cognito_config": {
                "user_pool_id": (
                    settings.cognito_user_pool_id[:10] + "..."
                    if settings.cognito_user_pool_id
                    else "NOT SET"
                ),
                "app_client_id": (
                    settings.cognito_app_client_id[:10] + "..."
                    if settings.cognito_app_client_id
                    else "NOT SET"
                ),
                "region": settings.cognito_region,
            },
            "diagnosis": {
                "role_in_token": verified_claims.get("custom:user_role"),
                "role_in_database": current_user.role.value,
                "role_match": (
                    verified_claims.get("custom:user_role", "").lower() ==
                    current_user.role.value.lower()
                ),
                "recommendation": (
                    "Configure App Client to map custom:user_role to ID token"
                    if not verified_claims.get("custom:user_role")
                    else "Role found in token - check database sync"
                )
            }
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Debug token claims error: %s", str(e), exc_info=True)
        return {
            "error": str(e),
            "user_info": {
                "user_id": str(current_user.id),
                "email": current_user.email,
                "role": current_user.role.value
            }
        }


@router.get(
    "/jobs/available",
    response_model=List[AvailableJobResponse],
    summary="Get available jobs",
    description="Get available jobs for dropper including assigned and broadcasted jobs"
)
async def get_available_jobs(
    limit: int = Query(50, ge=1, le=100, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Get available jobs for the current dropper.
    Includes:
    - Jobs assigned to this dropper
    - Jobs broadcasted to all droppers (not assigned to anyone)

    Excludes jobs assigned to other droppers.
    Results are ordered by: assigned jobs first, then by distance.
    """
    try:
        if settings.debug:
            logger.debug("🔍 Getting available jobs for dropper")

        job_service = get_job_service(db)
        available_jobs = job_service.get_available_jobs_for_dropper(
            current_user,
            limit=limit,
            offset=offset
        )

        if settings.debug:
            logger.debug("🔍 Found %d available jobs for dropper", len(available_jobs))

        # Convert to response format
        response_jobs = []
        for job_data in available_jobs:
            job = job_data["job"]
            job_area = job_data["job_area"]
            distance_km = job_data["distance_km"]
            is_assigned_to_me = job_data.get("is_assigned_to_me", False)
            is_broadcasted = job_data.get("is_broadcasted", False)

            # Determine assignment type
            if is_assigned_to_me:
                assignment_type = "assigned"
            elif is_broadcasted:
                assignment_type = "broadcasted"
            else:
                assignment_type = "available"

            # Prepare job area data for response
            job_area_data = None
            if job_area:
                job_area_data = {
                    "id": str(job_area.id),
                    "area_type": job_area.area_type,
                    "geojson": job_area.geojson,
                    "postcodes": job_area.postcodes,
                    "center_lat": job_area.center_lat,
                    "center_lng": job_area.center_lng,
                    "radius_km": job_area.radius_km
                }

            # Fetch drop points for this job
            # Order by the order field, then by created_at as fallback for points without order
            # NULL values in order field will naturally come last in ASC order
            drop_points_statement = select(DropPoint).where(DropPoint.job_id == job.id).order_by(
                DropPoint.order,  # ASC by default
                DropPoint.created_at
            )
            drop_points = db.exec(drop_points_statement).all()
            drop_points_data = None
            if drop_points:
                drop_points_data = [
                    {
                        "id": str(dp.id),
                        "lat": dp.lat,
                        "lng": dp.lng,
                        "name": dp.name,
                        "status": dp.status,
                        # Use stored order or fallback to index
                        "order": dp.order if dp.order is not None else (idx + 1)
                    }
                    for idx, dp in enumerate(drop_points)
                ]

            response_job = AvailableJobResponse(
                id=job.id,
                title=job.title,
                description=job.description,
                household_count=job.household_count,
                cost_total_pence=job.cost_total_pence,
                dropper_payout_pence=job.dropper_payout_pence,
                scheduled_date=job.scheduled_date,
                min_time_per_segment_sec=job.min_time_per_segment_sec,
                special_instructions=job.special_instructions,
                distance_km=distance_km,
                job_area=job_area_data,
                drop_points=drop_points_data,
                created_at=job.created_at,
                is_assigned_to_me=is_assigned_to_me,
                is_broadcasted=is_broadcasted,
                assignment_type=assignment_type
            )
            response_jobs.append(response_job)

        return response_jobs

    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get available jobs: {str(e)}"
        ) from e


@router.post(
    "/jobs/{job_id}/accept",
    response_model=JobAcceptanceResponse,
    summary="Accept a job",
    description="Accept an available job for completion"
)
async def accept_job(
    job_id: UUID,
    acceptance_data: JobAcceptanceRequest,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Accept an available job for completion.
    Creates a job assignment and updates the job status to 'assigned'.

    Requires:
    - User must have 'dropper' role in Cognito (custom:user_role attribute)
    - User must have an active dropper profile in the database
    """
    try:
        # Log for debugging with request body validation
        location_provided = (
            acceptance_data.dropper_location_lat is not None
            and acceptance_data.dropper_location_lng is not None
        )

        # Verify user has dropper profile
        dropper_profile = db.exec(
            select(Dropper).where(Dropper.user_id == current_user.id)
        ).first()

        if not dropper_profile:
            logger.error(
                "❌ Dropper profile not found for user %s (email: %s) "
                "when trying to accept job %s",
                current_user.id, current_user.email, job_id
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "type": "dropper_profile_missing",
                        "message": (
                            "Dropper profile not found. "
                            "Please complete your dropper profile setup."
                        ),
                        "user_id": str(current_user.id),
                        "user_email": current_user.email
                    }
                }
            )

        logger.info(
            "accept_job called: job_id=%s, user_id=%s, user_email=%s, "
            "user_role=%s, dropper_profile_id=%s, location_provided=%s",
            job_id, current_user.id, current_user.email, current_user.role.value,
            dropper_profile.id, location_provided
        )

        job_service = get_job_service(db)
        assignment = job_service.accept_job(job_id, current_user, acceptance_data)

        return JobAcceptanceResponse(
            job_id=job_id,
            assignment_id=assignment.id,
            accepted_at=assignment.accepted_at,
            message="Job accepted successfully"
        )

    except JobServiceError as e:
        logger.error("JobServiceError in accept_job: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        logger.error(
            "Unexpected error in accept_job for job %s: %s",
            job_id, str(e), exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to accept job: {str(e)}"
        ) from e


@router.post(
    "/jobs/{job_id}/start",
    response_model=JobStartResponse,
    summary="Start a job",
    description="Mark an assigned job as started and begin tracking"
)
async def start_job(
    job_id: UUID,
    start_data: JobStartRequest,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Start an assigned job.
    Sets the started_at timestamp on the job assignment.
    """
    try:
        job_service = get_job_service(db)
        assignment = job_service.start_job(
            job_id,
            current_user,
            start_data.start_location
        )

        return JobStartResponse(
            job_id=job_id,
            assignment_id=assignment.id,
            started_at=assignment.started_at,
            message="Job started successfully"
        )

    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start job: {str(e)}"
        ) from e


@router.post(
    "/jobs/{job_id}/pause",
    response_model=JobPauseResponse,
    summary="Pause a job",
    description="Pause an in-progress job"
)
async def pause_job(
    job_id: UUID,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Pause an in-progress job.
    Sets the assignment status to 'paused'.
    """
    try:
        job_service = get_job_service(db)
        assignment = job_service.pause_job(
            job_id,
            current_user
        )

        return JobPauseResponse(
            job_id=job_id,
            assignment_id=assignment.id,
            paused_at=datetime.utcnow(),
            message="Job paused successfully"
        )

    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to pause job: {str(e)}"
        ) from e


@router.post(
    "/jobs/{job_id}/resume",
    response_model=JobResumeResponse,
    summary="Resume a job",
    description="Resume a paused job"
)
async def resume_job(
    job_id: UUID,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Resume a paused job.
    Sets the assignment status to 'in_progress'.
    """
    try:
        job_service = get_job_service(db)
        assignment = job_service.resume_job(
            job_id,
            current_user
        )

        return JobResumeResponse(
            job_id=job_id,
            assignment_id=assignment.id,
            resumed_at=datetime.utcnow(),
            message="Job resumed successfully"
        )

    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resume job: {str(e)}"
        ) from e


@router.post(
    "/jobs/{job_id}/complete",
    response_model=JobCompletionResponse,
    summary="Complete a job",
    description="Submit completion proof for an assigned job"
)
async def complete_job(
    job_id: UUID,
    completion_data: JobCompletionRequest,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Submit completion proof for an assigned job.
    Requires proof photos, GPS log, and time spent information.
    Sets the job status to 'completed' and verification status to 'pending'.
    """
    try:
        job_service = get_job_service(db)
        assignment = job_service.complete_job(job_id, current_user, completion_data)

        return JobCompletionResponse(
            job_id=job_id,
            assignment_id=assignment.id,
            completed_at=assignment.completed_at,
            verification_status=assignment.verification_status,
            message="Job completed successfully. Awaiting admin verification."
        )

    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to complete job: {str(e)}"
        ) from e


@router.post(
    "/jobs/{job_id}/reject",
    response_model=JobRejectionResponse,
    summary="Reject a job",
    description="Reject an assigned job with optional reason"
)
async def reject_job(
    job_id: UUID,
    rejection_data: JobRejectionRequest,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Reject an assigned job.
    Updates the JobAssignment verification_status to 'rejected' and stores the optional reason.
    Updates the DropJob status to 'rejected' atomically.
    Only the assigned dropper can reject their assigned job.
    """
    try:
        job_service = get_job_service(db)
        assignment = job_service.reject_job(
            job_id,
            current_user,
            rejection_data.reason
        )

        return JobRejectionResponse(
            job_id=job_id,
            assignment_id=assignment.id,
            rejected_at=assignment.verified_at,
            rejection_reason=assignment.rejection_reason,
            message="Job rejected successfully"
        )

    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject job: {str(e)}"
        ) from e


@router.get(
    "/jobs/assigned",
    response_model=List[DropperJobResponse],
    summary="Get assigned jobs",
    description=(
        "Get jobs assigned to the current dropper with full details "
        "including map data and leaflet URL"
    )
)
async def get_assigned_jobs(
    limit: int = Query(50, ge=1, le=100, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Get jobs assigned to the current dropper.
    Returns jobs filtered by JobAssignment.dropper_id matching authenticated user.
    Includes job details, map data (JobArea), leaflet URL, and assignment details
    including rejection_reason if present.
    Supports pagination.
    """
    try:
        if settings.debug:
            logger.debug("Fetching assigned jobs for dropper")
        job_service = get_job_service(db)
        # Get all assigned jobs (no status filter to include all assigned jobs)
        dropper_jobs = job_service.get_dropper_jobs(
            current_user,
            status_filter=None,
            limit=limit,
            offset=offset
        )

        logger.info(
            "Found %d assigned jobs for dropper %s",
            len(dropper_jobs), current_user.id
        )

        # Convert to response format
        response_jobs = []
        for job_data in dropper_jobs:
            try:
                job = job_data.get("job")
                assignment = job_data.get("assignment")
                job_area = job_data.get("job_area")

                if not job:
                    logger.warning("Skipping job data entry with no job: %s", job_data)
                    continue

                # Prepare job area data for response (map data)
                job_area_data = None
                if job_area:
                    try:
                        job_area_data = {
                            "id": str(job_area.id),
                            "area_type": job_area.area_type,
                            "geojson": job_area.geojson,
                            "postcodes": job_area.postcodes,
                            "center_lat": job_area.center_lat,
                            "center_lng": job_area.center_lng,
                            "radius_km": job_area.radius_km
                        }
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        logger.warning(
                            "Error processing job area for job %s: %s",
                            job.id, str(e)
                        )
                        job_area_data = None

                # Prepare assignment data for response (includes rejection_reason)
                assignment_data = None
                if assignment:
                    try:
                        verification_status = None
                        if (
                            hasattr(assignment, 'verification_status')
                            and assignment.verification_status
                        ):
                            if hasattr(assignment.verification_status, 'value'):
                                verification_status = assignment.verification_status.value
                            else:
                                verification_status = str(assignment.verification_status)

                        assignment_data = {
                            "id": str(assignment.id),
                            "status": assignment.status,
                            "accepted_at": (
                                assignment.accepted_at.isoformat()
                                if assignment.accepted_at else None
                            ),
                            "started_at": (
                                assignment.started_at.isoformat()
                                if assignment.started_at else None
                            ),
                            "completed_at": (
                                assignment.completed_at.isoformat()
                                if assignment.completed_at else None
                            ),
                            "time_spent_sec": assignment.time_spent_sec,
                            "verification_status": verification_status,
                            "verification_notes": assignment.verification_notes,
                            "verified_at": (
                                assignment.verified_at.isoformat()
                                if assignment.verified_at else None
                            ),
                            "rejection_reason": assignment.rejection_reason
                        }
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        logger.warning(
                            "Error processing assignment for job %s: %s",
                            job.id, str(e)
                        )
                        assignment_data = None

                # Fetch drop points for this job
                drop_points_data = None
                try:
                    # Order by the order field, then by created_at as fallback
                    # pylint: disable=import-outside-toplevel
                    from sqlalchemy import nullslast
                    drop_points_statement = (
                        select(DropPoint)
                        .where(DropPoint.job_id == job.id)
                        .order_by(nullslast(DropPoint.order), DropPoint.created_at)
                    )
                    drop_points = db.exec(drop_points_statement).all()
                    if drop_points:
                        drop_points_data = [
                            {
                                "id": str(dp.id),
                                "lat": dp.lat,
                                "lng": dp.lng,
                                "name": dp.name,
                                "status": dp.status,
                                "order": dp.order if dp.order is not None else (idx + 1)
                            }
                            for idx, dp in enumerate(drop_points)
                        ]
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "Error fetching drop points for job %s: %s",
                        job.id, str(e)
                    )
                    drop_points_data = None

                # Determine assignment type - for assigned jobs, always "assigned"
                is_assigned_to_me = True  # This endpoint only returns jobs assigned to current user
                is_broadcasted = getattr(job, 'is_broadcasted', False)
                assignment_type = "assigned"

                response_job = DropperJobResponse(
                    id=job.id,
                    title=job.title,
                    description=job.description,
                    leaflet_file_url=job.leaflet_file_url,
                    household_count=job.household_count,
                    dropper_payout_pence=job.dropper_payout_pence,
                    scheduled_date=job.scheduled_date,
                    min_time_per_segment_sec=job.min_time_per_segment_sec,
                    special_instructions=job.special_instructions,
                    status=job.status,
                    job_area=job_area_data,
                    drop_points=drop_points_data,
                    assignment=assignment_data,
                    is_assigned_to_me=is_assigned_to_me,
                    is_broadcasted=is_broadcasted,
                    assignment_type=assignment_type,
                    created_at=job.created_at
                )
                response_jobs.append(response_job)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error processing job data: %s", str(e), exc_info=True)
                # Continue processing other jobs even if one fails
                continue

        logger.info(
            "Returning %d jobs for dropper %s",
            len(response_jobs), current_user.id
        )
        return response_jobs

    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get assigned jobs: {str(e)}"
        ) from e


@router.get(
    "/jobs",
    response_model=List[DropperJobResponse],
    summary="Get dropper jobs",
    description="Get jobs assigned to the current dropper"
)
async def get_dropper_jobs(
    status_filter: Optional[JobStatus] = Query(None, description="Filter jobs by status"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Get jobs assigned to the current dropper.
    Supports filtering by status and pagination.
    """
    try:
        job_service = get_job_service(db)
        dropper_jobs = job_service.get_dropper_jobs(
            current_user,
            status_filter=status_filter,
            limit=limit,
            offset=offset
        )

        # Convert to response format
        response_jobs = []
        for job_data in dropper_jobs:
            job = job_data["job"]
            assignment = job_data["assignment"]
            job_area = job_data["job_area"]

            # Prepare job area data for response
            job_area_data = None
            if job_area:
                job_area_data = {
                    "id": str(job_area.id),
                    "area_type": job_area.area_type,
                    "geojson": job_area.geojson,
                    "postcodes": job_area.postcodes,
                    "center_lat": job_area.center_lat,
                    "center_lng": job_area.center_lng,
                    "radius_km": job_area.radius_km
                }

            # Prepare assignment data for response
            assignment_data = None
            if assignment:
                assignment_data = {
                    "id": str(assignment.id),
                    "status": assignment.status,
                    "accepted_at": (
                        assignment.accepted_at.isoformat()
                        if assignment.accepted_at else None
                    ),
                    "started_at": (
                        assignment.started_at.isoformat()
                        if assignment.started_at else None
                    ),
                    "completed_at": (
                        assignment.completed_at.isoformat()
                        if assignment.completed_at else None
                    ),
                    "time_spent_sec": assignment.time_spent_sec,
                    "verification_status": assignment.verification_status.value,
                    "verification_notes": assignment.verification_notes,
                    "verified_at": (
                        assignment.verified_at.isoformat()
                        if assignment.verified_at else None
                    ),
                    "rejection_reason": assignment.rejection_reason
                }

            # Determine assignment type - for assigned jobs, always "assigned"
            is_assigned_to_me = True  # This endpoint only returns jobs assigned to current user
            is_broadcasted = getattr(job, 'is_broadcasted', False)
            assignment_type = "assigned"

            response_job = DropperJobResponse(
                id=job.id,
                title=job.title,
                description=job.description,
                leaflet_file_url=job.leaflet_file_url,
                household_count=job.household_count,
                dropper_payout_pence=job.dropper_payout_pence,
                scheduled_date=job.scheduled_date,
                min_time_per_segment_sec=job.min_time_per_segment_sec,
                special_instructions=job.special_instructions,
                status=job.status,
                job_area=job_area_data,
                assignment=assignment_data,
                is_assigned_to_me=is_assigned_to_me,
                is_broadcasted=is_broadcasted,
                assignment_type=assignment_type,
                created_at=job.created_at
            )
            response_jobs.append(response_job)

        return response_jobs

    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get dropper jobs: {str(e)}"
        ) from e


@router.get(
    "/jobs/{job_id}",
    response_model=DropperJobResponse,
    summary="Get job details",
    description="Get detailed information about a specific job (assigned or available)"
)
async def get_dropper_job_details(
    job_id: UUID,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Get detailed information about a specific job.
    Allows viewing both assigned jobs and available jobs (for accepting).
    """
    try:
        # Log for debugging
        logger.info(
            "get_dropper_job_details called: job_id=%s, user_id=%s, user_role=%s",
            job_id, current_user.id, current_user.role.value
        )

        job_service = get_job_service(db)

        # First, try to get the job directly
        job_statement = select(DropJob).where(DropJob.id == job_id)
        job = db.exec(job_statement).first()

        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )

        # Get dropper profile
        dropper_statement = select(Dropper).where(Dropper.user_id == current_user.id)
        dropper = db.exec(dropper_statement).first()

        if not dropper:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dropper profile not found"
            )

        # Check if job is assigned to this dropper
        assignment_statement = select(JobAssignment).where(
            and_(
                JobAssignment.job_id == job_id,
                JobAssignment.dropper_id == current_user.id
            )
        )
        assignment = db.exec(assignment_statement).first()
        is_assigned_to_me = assignment is not None

        # Check if job is broadcasted and available
        is_broadcasted = (
            job.is_broadcasted is True
            if hasattr(job, 'is_broadcasted') else False
        )

        # Check if job is available to this dropper
        # Job is available if:
        # 1. It's assigned to this dropper, OR
        # 2. It's broadcasted and status is PAID/ASSIGNED/PENDING_APPROVAL, OR
        # 3. It's in available jobs list (most permissive check)
        job_available = False

        # First, check if job is in available jobs list (most reliable check)
        try:
            available_jobs = job_service.get_available_jobs_for_dropper(current_user, limit=1000)
            for job_item in available_jobs:
                if job_item["job"].id == job_id:
                    job_available = True
                    # Use the data from available jobs if it has more info
                    if "assignment" in job_item:
                        assignment = job_item.get("assignment")
                    is_assigned_to_me = job_item.get("is_assigned_to_me", False)
                    is_broadcasted = job_item.get("is_broadcasted", False)
                    logger.debug("Job %s found in available jobs list", job_id)
                    break
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error checking available jobs list: %s", str(e))
            # Fall back to direct checks

        # If not in available jobs, check direct conditions
        if not job_available:
            if is_assigned_to_me:
                job_available = True
                logger.debug("Job %s is assigned to dropper", job_id)
            elif is_broadcasted and job.status in [
                JobStatus.PENDING_APPROVAL, JobStatus.PAID,
                JobStatus.APPROVED, JobStatus.ASSIGNED
            ]:
                # Check if job is not assigned to someone else
                other_assignment = db.exec(
                    select(JobAssignment).where(JobAssignment.job_id == job_id)
                ).first()
                if not other_assignment:
                    job_available = True
                    logger.debug("Job %s is broadcasted and available", job_id)

        if not job_available:
            logger.warning(
                "Job %s not available to dropper %s. "
                "Job status: %s, is_broadcasted: %s, is_assigned_to_me: %s",
                job_id, current_user.id, job.status.value,
                is_broadcasted, is_assigned_to_me
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found or not available to you"
            )

        # Get job area
        job_area_statement = select(JobArea).where(JobArea.job_id == job_id)
        job_area = db.exec(job_area_statement).first()

        # Prepare job area data for response
        job_area_data = None
        if job_area:
            job_area_data = {
                "id": str(job_area.id),
                "area_type": job_area.area_type,
                "geojson": job_area.geojson,
                "postcodes": job_area.postcodes,
                "center_lat": job_area.center_lat,
                "center_lng": job_area.center_lng,
                "radius_km": job_area.radius_km
            }

        # Prepare assignment data for response
        assignment_data = None
        if assignment:
            assignment_data = {
                "id": str(assignment.id),
                "accepted_at": (
                    assignment.accepted_at.isoformat()
                    if assignment.accepted_at else None
                ),
                "started_at": (
                    assignment.started_at.isoformat()
                    if assignment.started_at else None
                ),
                "completed_at": (
                    assignment.completed_at.isoformat()
                    if assignment.completed_at else None
                ),
                "time_spent_sec": assignment.time_spent_sec,
                "verification_status": assignment.verification_status.value,
                "verification_notes": assignment.verification_notes,
                "verified_at": (
                    assignment.verified_at.isoformat()
                    if assignment.verified_at else None
                ),
                "rejection_reason": assignment.rejection_reason
            }

        # Determine assignment type based on job data
        # is_assigned_to_me and is_broadcasted are already set above

        if is_assigned_to_me:
            assignment_type = "assigned"
        elif is_broadcasted:
            assignment_type = "broadcasted"
        else:
            assignment_type = "available"

        return DropperJobResponse(
            id=job.id,
            title=job.title,
            description=job.description,
            leaflet_file_url=job.leaflet_file_url,
            household_count=job.household_count,
            dropper_payout_pence=job.dropper_payout_pence,
            scheduled_date=job.scheduled_date,
            min_time_per_segment_sec=job.min_time_per_segment_sec,
            special_instructions=job.special_instructions,
            status=job.status,
            job_area=job_area_data,
            assignment=assignment_data,
            is_assigned_to_me=is_assigned_to_me,
            is_broadcasted=is_broadcasted,
            assignment_type=assignment_type,
            created_at=job.created_at
        )

    except HTTPException:
        raise
    except JobServiceError as e:
        logger.error(
            "JobServiceError in get_dropper_job_details: %s",
            str(e), exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        logger.error(
            "Unexpected error in get_dropper_job_details for job %s: %s",
            job_id, str(e), exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job details: {str(e)}"
        ) from e


# ====================================================================
# Location Tracking Endpoints
# ====================================================================

@router.post(
    "/location/update",
    summary="Update dropper location",
    description="Update current location for real-time tracking (for droppers)"
)
async def update_dropper_location(
    location_data: dict,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Update dropper's current location for real-time tracking.
    This endpoint can be used as an alternative to SocketIO location_update event.

    Expected payload:
    {
        "location": {
            "lat": float,
            "lng": float
        },
        "timestamp": "ISO8601 string (optional)"
    }
    """
    try:
        location = location_data.get("location")
        if not location or "lat" not in location or "lng" not in location:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid location data. Expected: {'location': {'lat': float, 'lng': float}}"
            )

        # Update dropper's base location in database
        dropper = db.exec(select(Dropper).where(Dropper.user_id == current_user.id)).first()
        if dropper:
            dropper.base_location_lat = float(location["lat"])
            dropper.base_location_lng = float(location["lng"])
            db.add(dropper)

            # Log location to DroperLocation table for history
            from datetime import datetime as dt  # pylint: disable=import-outside-toplevel
            new_location = DroperLocation(
                dropper_id=dropper.user_id,
                lat=float(location["lat"]),
                lng=float(location["lng"]),
                timestamp=dt.utcnow()
            )
            db.add(new_location)

            db.commit()
            db.refresh(dropper)

        # Broadcast location update via WebSocket for real-time tracking
        try:
            from app.socketio_server import sio  # pylint: disable=import-outside-toplevel
            from datetime import datetime as dt  # pylint: disable=import-outside-toplevel
            await sio.emit('location_broadcast', {
                'dropper_id': str(current_user.id),
                'location': {
                    'lat': float(location["lat"]),
                    'lng': float(location["lng"]),
                },
                'timestamp': location_data.get("timestamp", dt.utcnow().isoformat())
            })
            if settings.debug:
                logger.debug("Location broadcasted via WebSocket for dropper")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to broadcast location via WebSocket: %s", e)

        return {
            "success": True,
            "message": "Location updated",
            "location": {
                "lat": float(location["lat"]),
                "lng": float(location["lng"]),
            },
            "timestamp": location_data.get("timestamp", dt.utcnow().isoformat())
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating dropper location: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update location: {str(e)}"
        ) from e


@router.get(
    "/location/current",
    summary="Get current dropper location",
    description="Get the current/base location of the dropper"
)
async def get_current_dropper_location(
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Get the current location of the dropper.
    Returns base location from database.
    """
    try:
        dropper = db.exec(select(Dropper).where(Dropper.user_id == current_user.id)).first()

        if not dropper:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dropper profile not found"
            )

        if not dropper.base_location_lat or not dropper.base_location_lng:
            return {
                "location": None,
                "message": "No location set"
            }

        return {
            "location": {
                "lat": dropper.base_location_lat,
                "lng": dropper.base_location_lng,
            },
            "service_radius_km": dropper.service_radius_km,
            "is_available": dropper.is_available,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting dropper location: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get location: {str(e)}"
        ) from e


@router.get(
    "/location/all",
    summary="Get all active dropper locations",
    description="Get current locations of all active droppers (admin/client only)"
)
async def get_all_dropper_locations(
    _current_user: User = Depends(require_client_or_admin()),
    db: Session = Depends(get_session)
):
    """
    Get current locations of all active droppers.
    Available to admins and clients for map display.
    """
    try:

        # Get all active droppers with their user info
        # pylint: disable=no-member
        statement = (
            select(Dropper)
            .join(User, Dropper.user_id == User.id)
            .where(User.is_active.is_(True))  # noqa: E1101,no-member
        )
        droppers = db.exec(statement).all()

        result = []
        for dropper in droppers:
            user = db.exec(select(User).where(User.id == dropper.user_id)).first()

            if dropper.base_location_lat and dropper.base_location_lng:
                result.append({
                    "dropper_id": str(dropper.user_id),
                    "name": user.name if user else "Unknown",
                    "email": user.email if user else None,
                    "location": {
                        "lat": dropper.base_location_lat,
                        "lng": dropper.base_location_lng,
                    },
                    "service_radius_km": dropper.service_radius_km,
                    "is_available": dropper.is_available,
                    "rating": dropper.rating,
                    "total_jobs_completed": dropper.total_jobs_completed,
                })

        return {
            "droppers": result,
            "count": len(result)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error getting all dropper locations: %s", e, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get dropper locations: {str(e)}"
        ) from e


# Dropper Stats Endpoint

@router.get(
    "/stats",
    summary="Get dropper statistics",
    description="Get aggregated statistics for the authenticated dropper"
)
async def get_dropper_stats(
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Get statistics for the authenticated dropper including:
    - Total earnings (from completed payout transactions)
    - Number of active jobs (jobs with status assigned or in_progress)
    - Number of completed jobs
    - Number of pending jobs (awaiting verification)
    - Average rating
    - Total jobs count

    Returns default values (zeros/null) for droppers with no data.
    """
    try:
        # Get dropper profile
        dropper = db.exec(
            select(Dropper).where(Dropper.user_id == current_user.id)
        ).first()

        if not dropper:
            # Return default stats if no profile exists
            return {
                "total_earnings_pence": 0,
                "active_jobs_count": 0,
                "completed_jobs_count": 0,
                "pending_jobs_count": 0,
                "average_rating": None,
                "total_jobs_count": 0
            }

        # Calculate total earnings from completed payout transactions
        # pylint: disable=import-outside-toplevel
        from app.models import Transaction, PaymentStatus, VerificationStatus
        from sqlalchemy import or_ as sql_or, and_ as sql_and
        # pylint: disable=not-callable
        total_earnings_result = db.exec(
            select(sql_func.sum(Transaction.amount_pence))
            .where(Transaction.user_id == current_user.id)
            .where(Transaction.transaction_type == 'payout')
            .where(Transaction.status == PaymentStatus.COMPLETED)
        ).first()
        # sql_func.sum() returns None when no rows match, convert to 0
        total_earnings_pence = (
            int(total_earnings_result)
            if total_earnings_result is not None else 0
        )

        # Debug logging (only in development)
        logger.debug(
            "Dropper %s earnings query result: %s, converted: %s",
            current_user.id, total_earnings_result, total_earnings_pence
        )

        # Count active jobs (started but not completed, or completed but pending)
        # Active = jobs that are in progress (started_at exists but completed_at is null)
        # OR jobs that are completed but pending verification
        # NOTE: JobAssignment.dropper_id stores user.id (not dropper.id)
        # pylint: disable=not-callable,no-member
        active_jobs_result = db.exec(
            select(sql_func.count(JobAssignment.id))  # noqa: E1102
            .where(JobAssignment.dropper_id == current_user.id)
            .where(
                sql_or(
                    sql_and(
                        JobAssignment.started_at.isnot(None),  # noqa: E1101
                        JobAssignment.completed_at.is_(None)  # noqa: E1101
                    ),
                    sql_and(
                        JobAssignment.completed_at.isnot(None),  # noqa: E1101
                        JobAssignment.verification_status == VerificationStatus.PENDING
                    )
                )
            )
        ).first()
        active_jobs_count = (
            int(active_jobs_result)
            if active_jobs_result is not None else 0
        )
        logger.debug(
            "Dropper %s active jobs count: %s",
            current_user.id, active_jobs_count
        )

        # Count completed jobs (approved verification)
        # pylint: disable=not-callable
        completed_jobs_result = db.exec(
            select(sql_func.count(JobAssignment.id))  # noqa: E1102,not-callable
            .where(JobAssignment.dropper_id == current_user.id)
            .where(JobAssignment.verification_status == VerificationStatus.APPROVED)
        ).first()
        completed_jobs_count = (
            int(completed_jobs_result)
            if completed_jobs_result is not None else 0
        )

        # Count pending jobs (awaiting verification)
        # pylint: disable=not-callable
        pending_jobs_result = db.exec(
            select(sql_func.count(JobAssignment.id))  # noqa: E1102,not-callable
            .where(JobAssignment.dropper_id == current_user.id)
            .where(JobAssignment.verification_status == VerificationStatus.PENDING)
        ).first()
        pending_jobs_count = (
            int(pending_jobs_result)
            if pending_jobs_result is not None else 0
        )

        # Count total jobs
        # pylint: disable=not-callable
        total_jobs_result = db.exec(
            select(sql_func.count(JobAssignment.id))  # noqa: E1102,not-callable
            .where(JobAssignment.dropper_id == current_user.id)
        ).first()
        total_jobs_count = (
            int(total_jobs_result)
            if total_jobs_result is not None else 0
        )

        logger.debug(
            "Dropper %s stats: completed=%s, pending=%s, total=%s",
            current_user.id, completed_jobs_count,
            pending_jobs_count, total_jobs_count
        )

        result = {
            "total_earnings_pence": total_earnings_pence,
            "active_jobs_count": active_jobs_count,
            "completed_jobs_count": completed_jobs_count,
            "pending_jobs_count": pending_jobs_count,
            "average_rating": dropper.rating,
            "total_jobs_count": total_jobs_count
        }

        logger.info("Returning stats for dropper %s: %s", current_user.id, result)
        return result

    except Exception as e:
        logger.error("Error getting dropper stats: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve dropper statistics: {str(e)}"
        ) from e


# Dropper Profile Management Endpoints

@router.get(
    "/profile",
    response_model=DropperProfileResponse,
    summary="Get dropper profile",
    description="Get the current user's dropper profile information"
)
async def get_dropper_profile(
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Get the dropper profile for the current authenticated user.
    Creates a default profile if one doesn't exist.
    """
    try:
        # Log the user_id being queried for debugging
        logger.info(
            "🔍 Fetching dropper profile for user_id=%s, email=%s, cognito_sub=%s",
            current_user.id, current_user.email, current_user.cognito_sub
        )

        # Check if dropper profile exists - use explicit query with refresh
        result = db.exec(
            select(Dropper).where(Dropper.user_id == current_user.id)
        ).first()

        if result:
            # CRITICAL FIX: Refresh from database to ensure we get fresh data
            # This prevents returning stale/cached data
            db.refresh(result)

            # Log what we found for debugging
            logger.debug(
                "✅ Found dropper profile: profile_id=%s, user_id=%s, "
                "service_radius_km=%s, is_available=%s",
                result.id, result.user_id, result.service_radius_km,
                result.is_available
            )

            # Verify the profile belongs to the current user (security check)
            if result.user_id != current_user.id:
                logger.error(
                    "❌ SECURITY ISSUE: Dropper profile user_id (%s) doesn't match "
                    "current_user.id (%s)",
                    result.user_id, current_user.id
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Profile data mismatch detected"
                )

            # Profile exists, return it using model_validate to handle all fields
            # including defaults
            return DropperProfileResponse.model_validate(result)
        else:
            # No profile exists, create default one
            logger.info(
                "📝 No dropper profile found for user_id=%s, creating default profile",
                current_user.id
            )
            user_service = UserService(db)
            dropper = user_service._create_dropper_profile(current_user)  # pylint: disable=protected-access  # noqa: SLF001,W0212
            db.commit()
            db.refresh(dropper)

            logger.info(
                "✅ Created default dropper profile: profile_id=%s, user_id=%s",
                dropper.id, dropper.user_id
            )

            return DropperProfileResponse.model_validate(dropper)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            "❌ Error getting dropper profile for user_id=%s, email=%s: %s",
            current_user.id, current_user.email, str(e),
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve dropper profile: {str(e)}"
        ) from e


@router.put(
    "/profile",
    response_model=DropperProfileResponse,
    summary="Update dropper profile",
    description="Update the current user's dropper profile information"
)
async def update_dropper_profile(
    profile_data: DropperProfileUpdate,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Update the dropper profile for the current authenticated user.
    Creates a profile if one doesn't exist.
    """
    try:
        # Check if dropper profile exists
        dropper = db.exec(
            select(Dropper).where(Dropper.user_id == current_user.id)
        ).first()

        if not dropper:
            # Create new profile if it doesn't exist
            user_service = UserService(db)
            dropper = user_service._create_dropper_profile(current_user)  # pylint: disable=protected-access  # noqa: SLF001,W0212
            db.flush()

        # Update fields
        if profile_data.service_radius_km is not None:
            dropper.service_radius_km = profile_data.service_radius_km
        if profile_data.base_location_lat is not None:
            dropper.base_location_lat = profile_data.base_location_lat
        if profile_data.base_location_lng is not None:
            dropper.base_location_lng = profile_data.base_location_lng
        if profile_data.phone_number is not None:
            dropper.phone_number = profile_data.phone_number
        if profile_data.emergency_contact_name is not None:
            dropper.emergency_contact_name = profile_data.emergency_contact_name
        if profile_data.emergency_contact_phone is not None:
            dropper.emergency_contact_phone = profile_data.emergency_contact_phone
        if profile_data.is_available is not None:
            dropper.is_available = profile_data.is_available
        if profile_data.email_notifications is not None:
            dropper.email_notifications = profile_data.email_notifications
        if profile_data.sms_notifications is not None:
            dropper.sms_notifications = profile_data.sms_notifications
        if profile_data.timezone is not None:
            dropper.timezone = profile_data.timezone
        if profile_data.language is not None:
            dropper.language = profile_data.language

        db.add(dropper)
        db.commit()
        db.refresh(dropper)

        return DropperProfileResponse.model_validate(dropper)
    except UserServiceError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        db.rollback()
        logger.error("Error updating dropper profile: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update dropper profile: {str(e)}"
        ) from e


@router.post(
    "/profile",
    response_model=DropperProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create dropper profile",
    description="Create a new dropper profile for the current user"
)
async def create_dropper_profile(
    profile_data: DropperProfileCreate,
    current_user: User = Depends(require_dropper_role()),
    db: Session = Depends(get_session)
):
    """
    Create a new dropper profile for the current authenticated user.
    Fails if a profile already exists.
    """
    try:
        # Check if profile already exists
        existing = db.exec(
            select(Dropper).where(Dropper.user_id == current_user.id)
        ).first()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dropper profile already exists. Use PUT to update."
            )

        # Create new profile
        dropper = Dropper(
            user_id=current_user.id,
            service_radius_km=profile_data.service_radius_km,
            base_location_lat=profile_data.base_location_lat,
            base_location_lng=profile_data.base_location_lng,
            phone_number=profile_data.phone_number,
            emergency_contact_name=profile_data.emergency_contact_name,
            emergency_contact_phone=profile_data.emergency_contact_phone,
        )

        db.add(dropper)
        db.commit()
        db.refresh(dropper)

        return DropperProfileResponse.model_validate(dropper)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Error creating dropper profile: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create dropper profile: {str(e)}"
        ) from e
