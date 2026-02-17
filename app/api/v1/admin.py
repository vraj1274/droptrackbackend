"""
Admin API endpoints for job verification and platform management.
Provides admin-only functionality for verifying completed jobs and processing payouts.

SECURITY IMPLEMENTATION:
=======================

All endpoints in this module require admin or superadmin role validation via the
require_admin_role() dependency. This ensures comprehensive security through:

1. JWT Token Validation:
   - Validates JWT signature using Cognito JWKS public keys
   - Verifies token expiration (exp claim)
   - Verifies token issuer (iss claim) matches Cognito User Pool
   - Verifies token audience (aud claim) matches App Client ID
   - Implemented in: app.services.cognito.CognitoService.validate_token()

2. Role Extraction from JWT:
   - Role is ONLY extracted from JWT token claims (custom:user_role)
   - Role is NEVER accepted from request body or query parameters
   - Maps 'superadmin' from Cognito to 'admin' in backend
   - Implemented in: app.services.cognito.CognitoService.extract_user_claims()

3. Role-Based Authorization:
   - Checks if user has UserRole.ADMIN in users table
   - OR checks if CLIENT user has role='admin' in clients table
   - Only configured superadmin emails (vraj.suthar+admin@thelinetech.uk, info@thelinetech.uk) can have admin access
   - Implemented in: app.api.deps.require_admin_role()

4. Audit Logging:
   - All admin actions are logged via log_admin_action() function
   - Logs include: action type, user email/ID/role, timestamp, and action details
   - Audit logs are written to stdout and optionally to audit.log file
   - Implemented in: log_admin_action() function in this module

USAGE:
======

All endpoints use the require_admin_role() dependency:

    @router.post("/jobs/{job_id}/approve")
    async def approve_job(
        job_id: UUID,
        current_user: User = Depends(require_admin_role()),  # <-- Security enforcement
        db: Session = Depends(get_session)
    ):
        # Endpoint implementation
        log_admin_action("Job Approved", current_user, {...})  # <-- Audit logging

REQUIREMENTS SATISFIED:
=======================
- Requirement 7.1: JWT token validation with role='superadmin' check
- Requirement 7.2: HTTP 403 Forbidden for unauthorized access
- Requirement 7.3: Role extracted from JWT token only (not request body)
- Requirement 7.4: JWT signature and expiration validation
- Requirement 7.5: Audit logging for all superadmin actions
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlmodel import Session, select
from sqlalchemy import func, and_
from datetime import datetime
import logging

from app.database import get_session
from app.models import User, DropJob, JobAssignment, Transaction, VerificationStatus, JobStatus, PaymentStatus, UserRole, JobArea, Client, Dropper, Invoice, DropPoint
from app.api.deps import require_admin_role
from app.schemas.admin_schemas import (
    JobVerificationRequest, 
    JobVerificationResponse, 
    AdminJobListResponse,
    AssignJobRequest,
    AssignJobResponse,
    UnassignedJobResponse,
    DropperSearchResponse,
    BroadcastJobResponse,
    PendingJobResponse,
    ApprovedJobResponse,
    DropperListResponse,
    ClientListResponse,
    PaginatedResponse
)
from pydantic import BaseModel, Field
from app.services.stripe_service import stripe_service
from app.services.job_service import get_job_service
from app.services.transaction_service import get_transaction_service

router = APIRouter(tags=["admin"])
logger = logging.getLogger(__name__)


def log_admin_action(action: str, user: User, details: Dict[str, Any] = None):
    """
    Log admin actions for audit trail.
    
    Args:
        action: Description of the action performed
        user: Admin user who performed the action
        details: Additional details about the action
    """
    log_message = (
        f"🔒 AUDIT: Admin action performed:\n"
        f"   Action: {action}\n"
        f"   User: {user.email} (ID: {user.id}, Role: {user.role.value})\n"
        f"   Timestamp: {datetime.utcnow().isoformat()}"
    )
    
    if details:
        log_message += "\n   Details:"
        for key, value in details.items():
            log_message += f"\n      {key}: {value}"
    
    logger.info(log_message)


# Job Approval Endpoints (approve jobs before they're published to droppers)
@router.get("/jobs/review-queue", response_model=List[PendingJobResponse])
async def get_review_queue_jobs(
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 jobs per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get list of jobs in the review queue.
    
    Returns all jobs that are awaiting approval or in draft status.
    """
    try:
        query = (
            select(DropJob, User)
            .join(User, DropJob.client_id == User.id)
            .where(DropJob.status.in_([JobStatus.PENDING_APPROVAL, JobStatus.DRAFT]))
            .order_by(DropJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        
        results = db.exec(query).all()
        
        jobs = []
        for job, client in results:
            jobs.append(PendingJobResponse(
                job_id=job.id,
                title=job.title,
                description=job.description or "",
                household_count=job.household_count,
                cost_total_pence=job.cost_total_pence,
                scheduled_date=job.scheduled_date,
                created_at=job.created_at,
                client={
                    "id": str(client.id),
                    "name": client.name,
                    "email": client.email
                }
            ))
        
        return jobs
    except Exception as e:
        logger.error(f"Error getting pending approval jobs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve pending approval jobs: {str(e)}"
        )


@router.post("/jobs/{job_id}/approve")
async def approve_job(
    job_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Approve a paid job, making it available for droppers.
    
    Changes job status from PENDING_APPROVAL to PAID (available for droppers).
    Requires admin or superadmin role. All approval actions are logged for audit purposes.
    """
    job_query = select(DropJob).where(DropJob.id == job_id)
    job = db.exec(job_query).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.status not in [JobStatus.PENDING_APPROVAL, JobStatus.DRAFT]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only draft or pending approval jobs can be approved. Current status: {job.status.value}"
        )
    
    # Change status to PAID (available for droppers to accept)
    job.status = JobStatus.PAID
    job.updated_at = datetime.utcnow()
    
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Log admin action for audit trail
    log_admin_action(
        "Job Approved",
        current_user,
        {
            "job_id": str(job.id),
            "job_title": job.title,
            "client_id": str(job.client_id),
            "household_count": job.household_count,
            "cost_pence": job.cost_total_pence
        }
    )
    
    return {
        "success": True,
        "job_id": job.id,
        "status": job.status.value,
        "message": "Job approved and made available for droppers"
    }


class RejectJobRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=2000, description="Optional reason for rejection")


@router.post("/jobs/{job_id}/reject")
async def reject_job(
    job_id: UUID,
    request: Optional[RejectJobRequest] = None,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Reject a draft job with optional reason.
    
    Changes job status to REJECTED.
    Requires admin or superadmin role. All rejection actions are logged for audit purposes.
    Request body: { "reason": "optional rejection reason" } (optional)
    """
    reason = request.reason if request else None
    
    job_query = select(DropJob).where(DropJob.id == job_id)
    job = db.exec(job_query).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.status != JobStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only draft jobs can be rejected. Current status: {job.status.value}"
        )
    
    job.status = JobStatus.REJECTED
    job.updated_at = datetime.utcnow()
    # Store rejection reason in special_instructions or create a notes field
    if reason:
        job.special_instructions = f"[REJECTED] {reason}"
    
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Log admin action for audit trail
    log_admin_action(
        "Job Rejected",
        current_user,
        {
            "job_id": str(job.id),
            "job_title": job.title,
            "client_id": str(job.client_id),
            "reason": reason or "No reason provided"
        }
    )
    
    return {
        "success": True,
        "job_id": job.id,
        "status": job.status.value,
        "message": "Job rejected"
    }


@router.get("/jobs/pending-approval", response_model=List[ApprovedJobResponse])
async def get_pending_approval_jobs(
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 jobs per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get list of paid jobs pending superadmin approval.
    """
    try:
        query = (
            select(DropJob, User)
            .join(User, DropJob.client_id == User.id)
            .where(DropJob.status == JobStatus.PENDING_APPROVAL)
            .order_by(DropJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        
        results = db.exec(query).all()
        
        jobs = []
        for job, client in results:
            jobs.append(ApprovedJobResponse(
                job_id=job.id,
                title=job.title,
                household_count=job.household_count,
                cost_total_pence=job.cost_total_pence,
                scheduled_date=job.scheduled_date,
                is_assigned=False,
                client={
                    "name": client.name,
                    "email": client.email
                }
            ))
        
        return jobs
    except Exception as e:
        logger.error(f"Error getting pending approval jobs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve pending approval jobs: {str(e)}"
        )


@router.get("/jobs/approved", response_model=List[ApprovedJobResponse])
async def get_approved_jobs(
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 jobs per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get list of approved jobs available for allocation to droppers.
    """
    try:
        query = (
            select(DropJob, User)
            .join(User, DropJob.client_id == User.id)
            .where(DropJob.status == JobStatus.PAID)
            .order_by(DropJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        
        results = db.exec(query).all()
        
        jobs = []
        for job, client in results:
            # Check if job is already assigned
            assignment_query = select(JobAssignment).where(JobAssignment.job_id == job.id)
            assignment = db.exec(assignment_query).first()
            
            jobs.append(ApprovedJobResponse(
                job_id=job.id,
                title=job.title,
                household_count=job.household_count,
                cost_total_pence=job.cost_total_pence,
                scheduled_date=job.scheduled_date,
                is_assigned=assignment is not None,
                client={
                    "name": client.name,
                    "email": client.email
                }
            ))
        
        return jobs
    except Exception as e:
        logger.error(f"Error getting approved jobs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve approved jobs: {str(e)}"
        )


# Old assign endpoint removed - replaced by assign_job_to_dropper_v2 below with proper request/response models


class JobTrackingLocation(BaseModel):
    lat: float
    lng: float


class JobTrackingDropper(BaseModel):
    id: str
    name: str
    email: str


class DropPointTracking(BaseModel):
    id: str
    lat: float
    lng: float
    name: Optional[str] = None
    status: Optional[str] = None
    order: Optional[int] = None

class JobAssignmentTracking(BaseModel):
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

class JobTrackingResponse(BaseModel):
    job_id: str
    title: str
    status: str
    household_count: int
    cost_total_pence: int
    scheduled_date: Optional[str] = None
    location: Optional[JobTrackingLocation] = None
    dropper: Optional[JobTrackingDropper] = None
    client_name: Optional[str] = None
    created_at: str
    drop_points: Optional[List[DropPointTracking]] = None
    assignment: Optional[JobAssignmentTracking] = None


@router.get("/jobs/tracking", response_model=List[JobTrackingResponse])
async def get_jobs_for_tracking(
    status_filter: Optional[str] = None,
    limit: int = Query(100, ge=1, le=200, description="Maximum 200 jobs per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get jobs with location data for the tracking map.
    Returns jobs with their center coordinates and assigned dropper info.
    """
    try:
        # Build query for jobs with job areas
        query = (
            select(DropJob, JobArea, User)
            .outerjoin(JobArea, JobArea.job_id == DropJob.id)
            .join(User, DropJob.client_id == User.id)
        )
        
        # Filter by status if provided
        if status_filter:
            status_list = [s.strip().lower() for s in status_filter.split(',')]
            status_enums = []
            for s in status_list:
                try:
                    status_enums.append(JobStatus(s))
                except ValueError:
                    pass
            if status_enums:
                query = query.where(DropJob.status.in_(status_enums))
        else:
            # Default: show active jobs (paid, assigned, broadcasted)
            query = query.where(DropJob.status.in_([
                JobStatus.PAID, 
                JobStatus.ASSIGNED, 
                JobStatus.BROADCASTED
            ]))
        
        query = query.order_by(DropJob.created_at.desc()).offset(offset).limit(limit)
        results = db.exec(query).all()
        
        jobs = []
        for job, job_area, client in results:
            # Get dropper assignment if exists
            assignment_query = (
                select(JobAssignment, User)
                .join(User, JobAssignment.dropper_id == User.id)
                .where(JobAssignment.job_id == job.id)
            )
            assignment_result = db.exec(assignment_query).first()
            
            dropper_info = None
            if assignment_result:
                assignment, dropper_user = assignment_result
                dropper_info = JobTrackingDropper(
                    id=str(dropper_user.id),
                    name=dropper_user.name,
                    email=dropper_user.email
                )
            
            location = None
            if job_area and job_area.center_lat is not None and job_area.center_lng is not None:
                location = JobTrackingLocation(
                    lat=job_area.center_lat,
                    lng=job_area.center_lng
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
            
            # Get assignment details
            assignment_info = None
            if assignment_result:
                assignment, _ = assignment_result
                assignment_info = JobAssignmentTracking(
                    started_at=assignment.started_at.isoformat() if assignment.started_at else None,
                    completed_at=assignment.completed_at.isoformat() if assignment.completed_at else None
                )
            
            jobs.append(JobTrackingResponse(
                job_id=str(job.id),
                title=job.title,
                status=job.status.value,
                household_count=job.household_count,
                cost_total_pence=job.cost_total_pence,
                scheduled_date=str(job.scheduled_date) if job.scheduled_date else None,
                location=location,
                dropper=dropper_info,
                client_name=client.name,
                created_at=job.created_at.isoformat(),
                drop_points=drop_points,
                assignment=assignment_info
            ))
        
        return jobs
    except Exception as e:
        logger.error(f"Error getting jobs for tracking: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve jobs for tracking: {str(e)}"
        )


class JobAssignmentResponse(BaseModel):
    dropper_id: str
    dropper_name: str
    dropper_email: str
    assigned_at: Optional[str] = None
    status: str


@router.get("/jobs/{job_id}/assignment", response_model=JobAssignmentResponse)
async def get_job_assignment(
    job_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get assignment details for a specific job including dropper info.
    """
    try:
        # Get job assignment with dropper info
        query = (
            select(JobAssignment, User)
            .join(User, JobAssignment.dropper_id == User.id)
            .where(JobAssignment.job_id == job_id)
        )
        result = db.exec(query).first()
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No assignment found for this job"
            )
        
        assignment, dropper = result
        
        return JobAssignmentResponse(
            dropper_id=str(dropper.id),
            dropper_name=dropper.name,
            dropper_email=dropper.email,
            assigned_at=assignment.assigned_at.isoformat() if assignment.assigned_at else None,
            status=assignment.verification_status.value if assignment.verification_status else "pending"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job assignment: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve job assignment: {str(e)}"
        )


@router.get("/droppers", response_model=List[DropperListResponse])
async def list_droppers(
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 droppers per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of droppers to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get list of all droppers with their stats.
    """
    try:
        query = (
            select(User, Dropper)
            .join(Dropper, User.id == Dropper.user_id, isouter=True)
            .where(User.role == UserRole.DROPPER)
            .offset(offset)
            .limit(limit)
        )
        
        results = db.exec(query).all()
        
        droppers = []
        for user, dropper_profile in results:
            try:
                # Get job stats using database aggregation for better performance
                # Count completed jobs using database query
                completed_jobs_query = (
                    select(func.count(JobAssignment.id))
                    .join(DropJob, JobAssignment.job_id == DropJob.id)
                    .where(JobAssignment.dropper_id == user.id)
                    .where(DropJob.status == JobStatus.COMPLETED)
                )
                completed_jobs = db.exec(completed_jobs_query).first() or 0
                
                # Count active assignments (ASSIGNED status) using database query
                active_assignments_query = (
                    select(func.count(JobAssignment.id))
                    .join(DropJob, JobAssignment.job_id == DropJob.id)
                    .where(JobAssignment.dropper_id == user.id)
                    .where(DropJob.status == JobStatus.ASSIGNED)
                )
                active_assignments = db.exec(active_assignments_query).first() or 0
                
                # Use dropper profile total_jobs_completed if available, otherwise use calculated value
                total_completed = (
                    int(dropper_profile.total_jobs_completed) 
                    if dropper_profile and dropper_profile.total_jobs_completed is not None 
                    else completed_jobs
                )
                
                droppers.append(DropperListResponse(
                    id=user.id,
                    name=user.name,
                    email=user.email,
                    is_active=user.is_active,
                    rating=float(dropper_profile.rating) if dropper_profile and dropper_profile.rating else 0.0,
                    total_jobs_completed=total_completed,
                    active_assignments=active_assignments,
                    created_at=user.created_at
                ))
            except Exception as e:
                logger.error(f"Error processing dropper {user.id}: {e}", exc_info=True)
                # Continue with next dropper even if one fails
                continue
        
        return droppers
    except Exception as e:
        logger.error(f"Error listing droppers: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve droppers: {str(e)}"
        )


@router.get("/clients", response_model=List[ClientListResponse])
async def list_clients(
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 clients per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of clients to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get list of all clients with their stats.
    """
    try:
        query = (
            select(User, Client)
            .join(Client, User.id == Client.user_id, isouter=True)
            .where(User.role == UserRole.CLIENT)
            .offset(offset)
            .limit(limit)
        )
        
        results = db.exec(query).all()
        
        clients = []
        for user, client_profile in results:
            try:
                # Get job stats using database aggregation for better performance
                # Count all jobs
                total_jobs_query = select(func.count(DropJob.id)).where(DropJob.client_id == user.id)
                total_jobs = db.exec(total_jobs_query).first() or 0
                
                # Calculate total spent from jobs that are PAID or have been completed using database sum
                total_spent_query = (
                    select(func.coalesce(func.sum(DropJob.cost_total_pence), 0))
                    .where(DropJob.client_id == user.id)
                    .where(DropJob.status.in_([JobStatus.PENDING_APPROVAL, JobStatus.PAID, JobStatus.ASSIGNED, JobStatus.COMPLETED]))
                )
                total_spent = db.exec(total_spent_query).first() or 0
                
                clients.append(ClientListResponse(
                    id=user.id,
                    name=user.name,
                    email=user.email,
                    is_active=user.is_active,
                    business_name=client_profile.business_name if client_profile else None,
                    total_jobs=total_jobs,
                    total_spent_pence=int(total_spent),
                    created_at=user.created_at
                ))
            except Exception as e:
                logger.error(f"Error processing client {user.id}: {e}", exc_info=True)
                # Continue with next client even if one fails
                continue
        
        return clients
    except Exception as e:
        logger.error(f"Error listing clients: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve clients: {str(e)}"
        )


@router.patch("/droppers/{dropper_id}/suspend")
async def suspend_dropper(
    dropper_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Suspend a dropper account (set is_active to False).
    Requires admin or superadmin role. All suspension actions are logged for audit purposes.
    """
    from app.models import Dropper
    
    user = db.exec(select(User).where(User.id == dropper_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dropper not found"
        )
    
    if user.role != UserRole.DROPPER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a dropper"
        )
    
    user.is_active = False
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Log admin action for audit trail
    log_admin_action(
        "Dropper Suspended",
        current_user,
        {
            "dropper_id": str(dropper_id),
            "dropper_email": user.email,
            "dropper_name": user.name
        }
    )
    
    return {
        "success": True,
        "message": f"Dropper {user.email} has been suspended",
        "dropper_id": str(dropper_id),
        "is_active": user.is_active
    }


@router.patch("/droppers/{dropper_id}/activate")
async def activate_dropper(
    dropper_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Activate a dropper account (set is_active to True).
    Requires admin or superadmin role. All activation actions are logged for audit purposes.
    """
    user = db.exec(select(User).where(User.id == dropper_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dropper not found"
        )
    
    if user.role != UserRole.DROPPER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a dropper"
        )
    
    user.is_active = True
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Log admin action for audit trail
    log_admin_action(
        "Dropper Activated",
        current_user,
        {
            "dropper_id": str(dropper_id),
            "dropper_email": user.email,
            "dropper_name": user.name
        }
    )
    
    return {
        "success": True,
        "message": f"Dropper {user.email} has been activated",
        "dropper_id": str(dropper_id),
        "is_active": user.is_active
    }


@router.patch("/droppers/{dropper_id}/verify")
async def verify_dropper(
    dropper_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Verify a dropper's identity (set id_verified to True).
    Requires admin or superadmin role. All verification actions are logged for audit purposes.
    """
    from app.models import Dropper
    
    dropper = db.exec(select(Dropper).where(Dropper.user_id == dropper_id)).first()
    if not dropper:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dropper profile not found"
        )
    
    # Get user info for logging
    user = db.exec(select(User).where(User.id == dropper_id)).first()
    
    dropper.id_verified = True
    db.add(dropper)
    db.commit()
    db.refresh(dropper)
    
    # Log admin action for audit trail
    log_admin_action(
        "Dropper Identity Verified",
        current_user,
        {
            "dropper_id": str(dropper_id),
            "dropper_email": user.email if user else "Unknown",
            "dropper_name": user.name if user else "Unknown"
        }
    )
    
    return {
        "success": True,
        "message": f"Dropper identity has been verified",
        "dropper_id": str(dropper_id),
        "id_verified": dropper.id_verified
    }


@router.patch("/droppers/{dropper_id}/reject")
async def reject_dropper(
    dropper_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Reject a dropper's verification (set id_verified to False and optionally suspend).
    Requires admin or superadmin role. All rejection actions are logged for audit purposes.
    """
    from app.models import Dropper
    
    dropper = db.exec(select(Dropper).where(Dropper.user_id == dropper_id)).first()
    if not dropper:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dropper profile not found"
        )
    
    # Get user info for logging
    user = db.exec(select(User).where(User.id == dropper_id)).first()
    
    dropper.id_verified = False
    db.add(dropper)
    db.commit()
    db.refresh(dropper)
    
    # Log admin action for audit trail
    log_admin_action(
        "Dropper Verification Rejected",
        current_user,
        {
            "dropper_id": str(dropper_id),
            "dropper_email": user.email if user else "Unknown",
            "dropper_name": user.name if user else "Unknown"
        }
    )
    
    return {
        "success": True,
        "message": f"Dropper verification has been rejected",
        "dropper_id": str(dropper_id),
        "id_verified": dropper.id_verified
    }


@router.patch("/clients/{client_id}/suspend")
async def suspend_client(
    client_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Suspend a client account (set is_active to False).
    Requires admin or superadmin role. All suspension actions are logged for audit purposes.
    """
    user = db.exec(select(User).where(User.id == client_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found"
        )
    
    if user.role != UserRole.CLIENT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a client"
        )
    
    user.is_active = False
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Log admin action for audit trail
    log_admin_action(
        "Client Suspended",
        current_user,
        {
            "client_id": str(client_id),
            "client_email": user.email,
            "client_name": user.name
        }
    )
    
    return {
        "success": True,
        "message": f"Client {user.email} has been suspended",
        "client_id": str(client_id),
        "is_active": user.is_active
    }


@router.patch("/clients/{client_id}/activate")
async def activate_client(
    client_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Activate a client account (set is_active to True).
    Requires admin or superadmin role. All activation actions are logged for audit purposes.
    """
    user = db.exec(select(User).where(User.id == client_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found"
        )
    
    if user.role != UserRole.CLIENT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a client"
        )
    
    user.is_active = True
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Log admin action for audit trail
    log_admin_action(
        "Client Activated",
        current_user,
        {
            "client_id": str(client_id),
            "client_email": user.email,
            "client_name": user.name
        }
    )
    
    return {
        "success": True,
        "message": f"Client {user.email} has been activated",
        "client_id": str(client_id),
        "is_active": user.is_active
    }


@router.patch("/jobs/{job_id}/verify", response_model=JobVerificationResponse)
async def verify_job(
    job_id: UUID,
    verification_request: JobVerificationRequest,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Verify a completed job (approve or reject).
    
    Admin endpoint to review completed jobs and approve/reject them.
    On approval, initiates payout to the dropper.
    Requires admin or superadmin role. All verification actions are logged for audit purposes.
    
    Args:
        job_id: UUID of the job to verify
        verification_request: Verification decision and optional reason
        current_user: Current admin user
        db: Database session
        
    Returns:
        JobVerificationResponse with updated verification status
        
    Raises:
        404: Job or assignment not found
        400: Job not in completed status or already verified
        500: Payout processing error
    """
    # Get job with assignment
    job_query = select(DropJob).where(DropJob.id == job_id)
    job = db.exec(job_query).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # Get job assignment
    assignment_query = select(JobAssignment).where(JobAssignment.job_id == job_id)
    assignment = db.exec(assignment_query).first()
    
    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job assignment not found"
        )
    
    # Validate job is in completed status
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job must be in 'completed' status for verification. Current status: {job.status}"
        )
    
    # Check if already verified
    if assignment.verification_status != VerificationStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job already verified with status: {assignment.verification_status}"
        )
    
    # Update verification status
    assignment.verification_status = verification_request.verification_status
    assignment.verification_notes = verification_request.reason
    assignment.verified_at = datetime.utcnow()
    assignment.verified_by = current_user.id
    
    if verification_request.verification_status == VerificationStatus.REJECTED:
        assignment.rejection_reason = verification_request.reason
        job.status = JobStatus.REJECTED
    
    # If approved, process payout
    payout_transaction = None
    if verification_request.verification_status == VerificationStatus.APPROVED:
        try:
            # Get dropper user
            dropper_query = select(User).where(User.id == assignment.dropper_id)
            dropper = db.exec(dropper_query).first()
            
            if not dropper or not dropper.dropper_profile:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Dropper profile not found"
                )
            
            # Check if dropper has Connect account
            if not dropper.dropper_profile.stripe_connect_account_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Dropper does not have a Stripe Connect account set up"
                )
            
            # Calculate payout amount
            platform_fee = await stripe_service.calculate_platform_fee(job.cost_total_pence)
            dropper_payout = job.cost_total_pence - platform_fee
            
            # Update job with fee calculations
            job.platform_fee_pence = platform_fee
            job.dropper_payout_pence = dropper_payout
            
            # Use transaction service to create and process payout
            transaction_service = get_transaction_service(db)
            
            # Create payout transaction record
            payout_transaction = transaction_service.create_payout_transaction(
                dropper=dropper,
                job=job,
                amount_pence=dropper_payout,
                platform_fee_pence=platform_fee,
                verified_by=current_user.id
            )
            
            # Process Stripe payout
            payout_success = await transaction_service.process_payout(
                transaction=payout_transaction,
                connect_account_id=dropper.dropper_profile.stripe_connect_account_id,
                job=job
            )
            
            if not payout_success:
                # Log error but continue with verification - payout can be retried later
                logger.error(f"Payout failed for job {job_id}, transaction {payout_transaction.id}")
                
                # The transaction service already marked it as failed
                # Admin can retry later through retry endpoint
        
        except HTTPException:
            # Re-raise HTTP exceptions as-is
            db.rollback()
            raise
        except Exception as e:
            logger.error(f"Error processing payout for job {job_id}: {e}", exc_info=True)
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error processing payout: {str(e)}"
            )
    
    # Save all changes
    db.add(assignment)
    db.add(job)
    if payout_transaction:
        db.add(payout_transaction)
    
    db.commit()
    db.refresh(assignment)
    
    # Log admin action for audit trail
    log_admin_action(
        f"Job Verification - {verification_request.verification_status.value}",
        current_user,
        {
            "job_id": str(job.id),
            "job_title": job.title,
            "dropper_id": str(assignment.dropper_id),
            "verification_status": verification_request.verification_status.value,
            "payout_amount_pence": job.dropper_payout_pence if verification_request.verification_status == VerificationStatus.APPROVED else None,
            "reason": verification_request.reason or "No reason provided"
        }
    )
    
    return JobVerificationResponse(
        job_id=job.id,
        verification_status=assignment.verification_status,
        verification_notes=assignment.verification_notes,
        verified_at=assignment.verified_at,
        verified_by=current_user.id,
        payout_amount_pence=job.dropper_payout_pence if verification_request.verification_status == VerificationStatus.APPROVED else None,
        platform_fee_pence=job.platform_fee_pence if verification_request.verification_status == VerificationStatus.APPROVED else None
    )


@router.get("/jobs/pending-verification", response_model=List[AdminJobListResponse])
async def get_pending_verification_jobs(
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get list of jobs pending admin verification.
    
    Returns all completed jobs that are waiting for admin verification.
    
    Args:
        current_user: Current admin user
        db: Database session
        
    Returns:
        List of jobs pending verification
    """
    # Query for completed jobs with pending verification
    query = (
        select(DropJob, JobAssignment, User)
        .join(JobAssignment, DropJob.id == JobAssignment.job_id)
        .join(User, JobAssignment.dropper_id == User.id)
        .where(DropJob.status == JobStatus.COMPLETED)
        .where(JobAssignment.verification_status == VerificationStatus.PENDING)
        .order_by(JobAssignment.completed_at.desc())
    )
    
    results = db.exec(query).all()
    
    jobs = []
    for job, assignment, dropper in results:
        jobs.append(AdminJobListResponse(
            job_id=job.id,
            title=job.title,
            household_count=job.household_count,
            cost_total_pence=job.cost_total_pence,
            scheduled_date=job.scheduled_date,
            dropper_name=dropper.name,
            dropper_email=dropper.email,
            completed_at=assignment.completed_at,
            time_spent_sec=assignment.time_spent_sec,
            proof_photos_count=len(assignment.proof_photos) if assignment.proof_photos else 0,
            has_gps_log=assignment.gps_log is not None
        ))
    
    return jobs


@router.get("/jobs/{job_id}/verification-details")
async def get_job_verification_details(
    job_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get detailed information for job verification.
    
    Returns comprehensive job details including completion proof for admin review.
    
    Args:
        job_id: UUID of the job
        current_user: Current admin user
        db: Database session
        
    Returns:
        Detailed job verification information
        
    Raises:
        404: Job or assignment not found
    """
    # Get job with assignment and related data
    job_query = (
        select(DropJob, JobAssignment, User)
        .join(JobAssignment, DropJob.id == JobAssignment.job_id)
        .join(User, JobAssignment.dropper_id == User.id)
        .where(DropJob.id == job_id)
    )
    
    result = db.exec(job_query).first()
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job or assignment not found"
        )
    
    job, assignment, dropper = result
    
    # Get client information
    client_query = select(User).where(User.id == job.client_id)
    client = db.exec(client_query).first()
    
    # Query dropper profile directly from database to avoid DetachedInstanceError
    from app.models import Dropper
    dropper_profile_query = select(Dropper).where(Dropper.user_id == dropper.id)
    dropper_profile = db.exec(dropper_profile_query).first()
    
    return {
        "job": {
            "id": str(job.id),
            "title": job.title,
            "description": job.description,
            "leaflet_file_url": job.leaflet_file_url,
            "household_count": job.household_count,
            "cost_total_pence": job.cost_total_pence,
            "scheduled_date": job.scheduled_date.isoformat() if job.scheduled_date else None,
            "special_instructions": job.special_instructions,
            "created_at": job.created_at.isoformat() if job.created_at else None
        },
        "client": {
            "name": client.name if client else "Unknown",
            "email": client.email if client else "Unknown"
        },
        "dropper": {
            "name": dropper.name,
            "email": dropper.email,
            "rating": float(dropper_profile.rating) if dropper_profile and dropper_profile.rating else 0.0,
            "total_jobs_completed": int(dropper_profile.total_jobs_completed) if dropper_profile and dropper_profile.total_jobs_completed else 0
        },
        "assignment": {
            "accepted_at": assignment.accepted_at.isoformat() if assignment.accepted_at else None,
            "started_at": assignment.started_at.isoformat() if assignment.started_at else None,
            "completed_at": assignment.completed_at.isoformat() if assignment.completed_at else None,
            "time_spent_sec": assignment.time_spent_sec,
            "proof_photos": assignment.proof_photos,
            "gps_log": assignment.gps_log,
            "verification_status": assignment.verification_status.value if assignment.verification_status else None,
            "verification_notes": assignment.verification_notes
        },
        "payout_calculation": {
            "total_amount_pence": job.cost_total_pence,
            "platform_fee_pence": await stripe_service.calculate_platform_fee(job.cost_total_pence),
            "dropper_payout_pence": await stripe_service.calculate_dropper_payout(job.cost_total_pence)
        }
    }


@router.get("/transactions/failed-payouts")
async def get_failed_payouts(
    hours_ago: int = 24,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get failed payout transactions for admin review.
    
    Args:
        hours_ago: Look for failures within this many hours (default 24)
        current_user: Current admin user
        db: Database session
        
    Returns:
        List of failed payout transactions
    """
    try:
        transaction_service = get_transaction_service(db)
        failed_payouts = transaction_service.get_failed_payouts(hours_ago=hours_ago)
        
        return [
            {
                "transaction_id": str(t.id),
                "user_id": str(t.user_id) if t.user_id else None,
                "job_id": str(t.job_id) if t.job_id else None,
                "amount_pence": t.amount_pence,
                "failure_reason": t.failure_reason,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "metadata": t.transaction_metadata
            }
            for t in failed_payouts
        ]
    except Exception as e:
        logger.error(f"Error getting failed payouts: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve failed payouts: {str(e)}"
        )


@router.post("/transactions/{transaction_id}/retry-payout")
async def retry_failed_payout(
    transaction_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Retry a failed payout transaction.
    
    Args:
        transaction_id: ID of the failed transaction to retry
        current_user: Current admin user
        db: Database session
        
    Returns:
        Success status and updated transaction info
        
    Raises:
        404: Transaction not found
        400: Transaction not eligible for retry
    """
    transaction_service = get_transaction_service(db)
    
    success = await transaction_service.retry_failed_payout(transaction_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to retry payout transaction"
        )
    
    # Get updated transaction
    updated_transaction = db.get(Transaction, transaction_id)
    
    return {
        "success": True,
        "transaction_id": str(transaction_id),
        "new_status": updated_transaction.status.value if updated_transaction and updated_transaction.status else "unknown",
        "processed_at": updated_transaction.processed_at.isoformat() if updated_transaction and updated_transaction.processed_at else None
    }


@router.get("/jobs/all")
async def get_all_jobs(
    status: Optional[JobStatus] = None,
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 jobs per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get all jobs with optional status filtering.
    Admin-only endpoint for comprehensive job management.
    """
    query = (
        select(DropJob, User)
        .join(User, DropJob.client_id == User.id)
    )
    
    if status:
        query = query.where(DropJob.status == status)
    
    query = query.order_by(DropJob.created_at.desc()).offset(offset).limit(limit)
    
    results = db.exec(query).all()
    
    jobs = []
    for job, client in results:
        # Check if job is assigned
        assignment_query = select(JobAssignment).where(JobAssignment.job_id == job.id)
        assignment = db.exec(assignment_query).first()
        
        jobs.append({
            "job_id": str(job.id),
            "title": job.title,
            "description": job.description or "",
            "status": job.status.value,
            "household_count": job.household_count,
            "cost_total_pence": job.cost_total_pence,
            "scheduled_date": job.scheduled_date.isoformat() if job.scheduled_date else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "is_assigned": assignment is not None,
            "client": {
                "id": str(client.id),
                "name": client.name,
                "email": client.email
            }
        })
    
    # Get total count
    count_query = select(func.count(DropJob.id))
    if status:
        count_query = count_query.where(DropJob.status == status)
    total_count = db.exec(count_query).first()
    
    return {
        "jobs": jobs,
        "total": total_count or 0,
        "limit": limit,
        "offset": offset
    }


@router.get("/jobs/assigned")
async def get_assigned_jobs(
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 jobs per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get all jobs that have been accepted/assigned to droppers.
    Admin-only endpoint to view jobs accepted by droppers.
    """
    try:
        # Query jobs with ASSIGNED status that have an assignment
        query = (
            select(DropJob, User, JobAssignment)
            .join(User, DropJob.client_id == User.id)
            .join(JobAssignment, JobAssignment.job_id == DropJob.id)
            .where(DropJob.status == JobStatus.ASSIGNED)
            .order_by(JobAssignment.accepted_at.desc())
            .offset(offset)
            .limit(limit)
        )
        
        results = db.exec(query).all()
        
        jobs = []
        for job, client, assignment in results:
            # Get dropper info
            dropper_query = select(User).where(User.id == assignment.dropper_id)
            dropper = db.exec(dropper_query).first()
            
            jobs.append({
                "job_id": str(job.id),
                "title": job.title,
                "description": job.description or "",
                "status": job.status.value,
                "household_count": job.household_count,
                "cost_total_pence": job.cost_total_pence,
                "scheduled_date": job.scheduled_date.isoformat() if job.scheduled_date else None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "accepted_at": assignment.accepted_at.isoformat() if assignment.accepted_at else None,
                "client": {
                    "id": str(client.id),
                    "name": client.name,
                    "email": client.email
                },
                "dropper": {
                    "id": str(dropper.id) if dropper else None,
                    "name": dropper.name if dropper else "Unknown",
                    "email": dropper.email if dropper else None
                } if dropper else None,
                "assignment_status": assignment.status
            })
        
        # Get total count
        count_query = (
            select(func.count(DropJob.id))
            .join(JobAssignment, JobAssignment.job_id == DropJob.id)
            .where(DropJob.status == JobStatus.ASSIGNED)
        )
        total_count = db.exec(count_query).first()
        
        log_admin_action("Viewed Assigned Jobs", current_user, {"count": len(jobs)})
        
        return {
            "jobs": jobs,
            "total": total_count or 0,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Error getting assigned jobs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve assigned jobs: {str(e)}"
        )


@router.get("/jobs/completed")
async def get_completed_jobs(
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 jobs per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get all completed jobs.
    Admin-only endpoint to view jobs that have been completed by droppers.
    """
    try:
        # Query jobs with COMPLETED status that have an assignment
        query = (
            select(DropJob, User, JobAssignment)
            .join(User, DropJob.client_id == User.id)
            .join(JobAssignment, JobAssignment.job_id == DropJob.id)
            .where(DropJob.status == JobStatus.COMPLETED)
            .order_by(DropJob.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        
        results = db.exec(query).all()
        
        jobs = []
        for job, client, assignment in results:
            # Get dropper info
            dropper_query = select(User).where(User.id == assignment.dropper_id)
            dropper = db.exec(dropper_query).first()
            
            jobs.append({
                "job_id": str(job.id),
                "title": job.title,
                "description": job.description or "",
                "status": job.status.value,
                "household_count": job.household_count,
                "cost_total_pence": job.cost_total_pence,
                "scheduled_date": job.scheduled_date.isoformat() if job.scheduled_date else None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "completed_at": assignment.completed_at.isoformat() if assignment.completed_at else None,
                "client": {
                    "id": str(client.id),
                    "name": client.name,
                    "email": client.email
                },
                "dropper": {
                    "id": str(dropper.id) if dropper else None,
                    "name": dropper.name if dropper else "Unknown",
                    "email": dropper.email if dropper else None
                } if dropper else None,
                "assignment_id": str(assignment.id),
                "verification_status": assignment.verification_status.value if assignment.verification_status else "pending"
            })
        
        # Get total count
        count_query = (
            select(func.count(DropJob.id))
            .join(JobAssignment, JobAssignment.job_id == DropJob.id)
            .where(DropJob.status == JobStatus.COMPLETED)
        )
        total_count = db.exec(count_query).first()
        
        log_admin_action("Viewed Completed Jobs", current_user, {"count": len(jobs)})
        
        return {
            "data": jobs,
            "total": total_count or 0,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Error getting completed jobs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve completed jobs: {str(e)}"
        )


@router.get("/locations/zones")
async def get_all_drop_zones(
    limit: int = Query(100, ge=1, le=200, description="Maximum 200 zones per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of zones to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get all drop zones for admin management.
    Admin-only endpoint for location management.
    """
    from app.models import DropZone
    
    query = (
        select(DropZone, User)
        .join(User, DropZone.client_id == User.id)
        .order_by(DropZone.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    
    results = db.exec(query).all()
    
    zones = []
    for zone, client in results:
        zones.append({
            "id": str(zone.id),
            "name": zone.name or "Unnamed Zone",
            "polygon_json": zone.polygon_json,
            "client_id": str(zone.client_id),
            "client": {
                "id": str(client.id),
                "name": client.name,
                "email": client.email
            },
            "created_at": zone.created_at.isoformat() if zone.created_at else None,
            "updated_at": zone.updated_at.isoformat() if zone.updated_at else None,
        })
    
    # Get total count
    count_query = select(func.count(DropZone.id))
    total_count = db.exec(count_query).first()
    
    return {
        "zones": zones,
        "total": total_count or 0,
        "limit": limit,
        "offset": offset
    }


@router.get("/platform/metrics")
async def get_platform_metrics(
    days: int = 30,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get platform financial metrics for admin dashboard.
    
    Args:
        days: Number of days to calculate metrics for (default 30)
        current_user: Current admin user
        db: Database session
        
    Returns:
        Platform financial metrics
    """
    try:
        transaction_service = get_transaction_service(db)
        metrics = transaction_service.calculate_platform_metrics(days=days)
        
        return metrics
    except Exception as e:
        logger.error(f"Error getting platform metrics: {e}", exc_info=True)
        # Return default metrics structure on error
        return {
            "total_revenue_pence": 0,
            "total_payouts_pence": 0,
            "platform_fee_pence": 0,
            "net_profit_pence": 0,
            "transaction_count": 0,
            "failed_transactions": 0,
            "period_days": days
        }



@router.get("/jobs/unassigned", response_model=List[UnassignedJobResponse])
async def get_unassigned_jobs(
    limit: int = Query(50, ge=1, le=200, description="Maximum 200 jobs per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get list of paid jobs without assignments.
    
    Returns all jobs with status 'paid' that don't have a JobAssignment record.
    Includes client info, area coverage, cost, and leaflet preview.
    
    Args:
        limit: Maximum number of jobs to return (default 50)
        offset: Number of jobs to skip for pagination (default 0)
        current_user: Current admin user
        db: Database session
        
    Returns:
        List of unassigned jobs with client and area information
    """
    try:
        # Efficient query: Use LEFT JOIN with IS NULL to filter unassigned jobs in SQL
        # This avoids loading all assigned job IDs into memory
        query = (
            select(DropJob, User, JobArea, JobAssignment)
            .join(User, DropJob.client_id == User.id)
            .outerjoin(JobArea, DropJob.id == JobArea.job_id)
            .outerjoin(JobAssignment, DropJob.id == JobAssignment.job_id)
            .where(DropJob.status == JobStatus.PAID)
            .where(JobAssignment.id.is_(None))  # Only jobs without assignments
            .order_by(DropJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        
        all_results = db.exec(query).all()
        
        # Extract job, client, and job_area from results (JobAssignment will be None)
        results = [
            (job, client, job_area) 
            for job, client, job_area, _ in all_results
        ]
        
        jobs = []
        for job, client, job_area in results:
            try:
                # Get client profile for additional info
                client_profile_query = select(Client).where(Client.user_id == client.id)
                client_profile = db.exec(client_profile_query).first()
                
                # Build area coverage info
                area_coverage = None
                if job_area:
                    area_coverage = {
                        "area_type": job_area.area_type,
                        "center_lat": job_area.center_lat,
                        "center_lng": job_area.center_lng,
                        "radius_km": job_area.radius_km,
                        "postcodes": job_area.postcodes if job_area.area_type == "postcodes" else None
                    }
                
                jobs.append(UnassignedJobResponse(
                    job_id=job.id,
                    title=job.title,
                    description=job.description,
                    household_count=job.household_count,
                    cost_total_pence=job.cost_total_pence,
                    scheduled_date=job.scheduled_date.isoformat() if job.scheduled_date else None,
                    leaflet_file_url=job.leaflet_file_url,
                    paid_at=job.paid_at.isoformat() if job.paid_at else None,
                    client={
                        "id": str(client.id),
                        "name": client.name,
                        "email": client.email,
                        "business_name": client_profile.business_name if client_profile else None
                    },
                    area_coverage=area_coverage,
                    is_broadcasted=job.is_broadcasted if hasattr(job, 'is_broadcasted') else False,
                    broadcasted_at=job.broadcasted_at.isoformat() if hasattr(job, 'broadcasted_at') and job.broadcasted_at else None
                ))
            except Exception as e:
                logger.error(f"Error processing job {job.id}: {e}", exc_info=True)
                # Continue with next job even if one fails
                continue
        
        return jobs
    except Exception as e:
        logger.error(f"Error getting unassigned jobs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve unassigned jobs: {str(e)}"
        )


@router.post("/jobs/{job_id}/assign", response_model=AssignJobResponse)
async def assign_job_to_dropper(
    job_id: UUID,
    request: AssignJobRequest,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Assign a paid job to a specific dropper.
    
    Validates that:
    - Job exists and is in 'paid' status
    - Job is not already assigned
    - Dropper exists and has 'dropper' role
    
    Creates JobAssignment record and updates job status to 'assigned' atomically.
    Requires admin or superadmin role. All assignment actions are logged for audit purposes.
    
    Args:
        job_id: UUID of the job to assign
        request: Assignment request with dropper_id
        current_user: Current admin user
        db: Database session
        
    Returns:
        Assignment confirmation with job and dropper IDs
        
    Raises:
        404: Job or dropper not found
        400: Job not in paid status or already assigned
    """
    # Get job
    job_query = select(DropJob).where(DropJob.id == job_id)
    job = db.exec(job_query).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # Validate job is in paid status
    if job.status != JobStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only paid jobs can be assigned. Current status: {job.status.value}"
        )
    
    # Check if already assigned
    assignment_query = select(JobAssignment).where(JobAssignment.job_id == job_id)
    existing_assignment = db.exec(assignment_query).first()
    
    if existing_assignment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is already assigned to a dropper"
        )
    
    # Get dropper and validate role
    dropper_query = select(User).where(User.id == request.dropper_id)
    dropper = db.exec(dropper_query).first()
    
    if not dropper:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dropper not found"
        )
    
    if dropper.role != UserRole.DROPPER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User is not a dropper. Current role: {dropper.role.value}"
        )
    
    # Create assignment with status 'assigned'
    assignment = JobAssignment(
        job_id=job_id,
        dropper_id=request.dropper_id,
        accepted_at=datetime.utcnow(),
        verification_status=VerificationStatus.PENDING
    )
    
    # Update job status to assigned atomically
    job.status = JobStatus.ASSIGNED
    job.updated_at = datetime.utcnow()
    
    db.add(assignment)
    db.add(job)
    db.commit()
    db.refresh(assignment)
    db.refresh(job)
    
    # Log admin action for audit trail
    log_admin_action(
        "Job Assigned to Dropper",
        current_user,
        {
            "job_id": str(job.id),
            "job_title": job.title,
            "dropper_id": str(request.dropper_id),
            "dropper_name": dropper.name,
            "dropper_email": dropper.email,
            "household_count": job.household_count,
            "cost_pence": job.cost_total_pence
        }
    )
    
    return AssignJobResponse(
        success=True,
        job_id=job.id,
        dropper_id=request.dropper_id,
        message=f"Job assigned to {dropper.name}"
    )


@router.post("/jobs/{job_id}/broadcast", response_model=BroadcastJobResponse)
async def broadcast_job_to_all_droppers(
    job_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Broadcast a paid job to all droppers.
    
    Makes a job visible to all droppers in the system by setting is_broadcasted flag.
    Requires admin or superadmin role. All broadcast actions are logged for audit purposes.
    
    Validates that:
    - User has admin or superadmin role (via JWT token validation)
    - Job exists and is in 'paid' status
    - Job is not already assigned to a dropper
    - Job is not already broadcasted
    
    Args:
        job_id: UUID of the job to broadcast
        current_user: Current admin user (validated via require_admin_role dependency)
        db: Database session
        
    Returns:
        Broadcast confirmation with job ID and timestamp
        
    Raises:
        401: Invalid or expired JWT token
        403: User does not have admin/superadmin role
        404: Job not found
        400: Job not in paid status, already assigned, or already broadcasted
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Log broadcast attempt for audit trail
    logger.info(
        f"🔒 AUDIT: Job broadcast attempt:\n"
        f"   Job ID: {job_id}\n"
        f"   Requested By: {current_user.email} (ID: {current_user.id}, Role: {current_user.role.value})\n"
        f"   Timestamp: {datetime.utcnow().isoformat()}"
    )
    
    # Get job
    job_query = select(DropJob).where(DropJob.id == job_id)
    job = db.exec(job_query).first()
    
    if not job:
        logger.warning(
            f"❌ AUDIT: Job broadcast failed - Job not found:\n"
            f"   Job ID: {job_id}\n"
            f"   Requested By: {current_user.email} (ID: {current_user.id})"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # Validate job is in paid status
    if job.status != JobStatus.PAID:
        logger.warning(
            f"❌ AUDIT: Job broadcast failed - Invalid job status:\n"
            f"   Job ID: {job.id}\n"
            f"   Job Title: {job.title}\n"
            f"   Current Status: {job.status.value}\n"
            f"   Required Status: PAID\n"
            f"   Requested By: {current_user.email} (ID: {current_user.id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only paid jobs can be broadcasted. Current status: {job.status.value}"
        )
    
    # Check if already assigned
    assignment_query = select(JobAssignment).where(JobAssignment.job_id == job_id)
    existing_assignment = db.exec(assignment_query).first()
    
    if existing_assignment:
        logger.warning(
            f"❌ AUDIT: Job broadcast failed - Job already assigned:\n"
            f"   Job ID: {job.id}\n"
            f"   Job Title: {job.title}\n"
            f"   Assigned To: Dropper ID {existing_assignment.dropper_id}\n"
            f"   Requested By: {current_user.email} (ID: {current_user.id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is already assigned to a dropper and cannot be broadcasted"
        )
    
    # Check if already broadcasted
    if job.is_broadcasted:
        logger.warning(
            f"❌ AUDIT: Job broadcast failed - Job already broadcasted:\n"
            f"   Job ID: {job.id}\n"
            f"   Job Title: {job.title}\n"
            f"   Previously Broadcasted At: {job.broadcasted_at}\n"
            f"   Requested By: {current_user.email} (ID: {current_user.id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is already broadcasted"
        )
    
    # Set broadcast flags
    job.is_broadcasted = True
    job.broadcasted_at = datetime.utcnow()
    job.updated_at = datetime.utcnow()
    
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Log successful broadcast action for audit
    logger.info(
        f"✅ AUDIT: Job broadcast successful:\n"
        f"   Job ID: {job.id}\n"
        f"   Job Title: {job.title}\n"
        f"   Household Count: {job.household_count}\n"
        f"   Cost: £{job.cost_total_pence / 100:.2f}\n"
        f"   Broadcasted By: {current_user.email} (ID: {current_user.id}, Role: {current_user.role.value})\n"
        f"   Broadcasted At: {job.broadcasted_at.isoformat()}\n"
        f"   Client ID: {job.client_id}"
    )
    
    return BroadcastJobResponse(
        success=True,
        job_id=job.id,
        message=f"Job '{job.title}' has been broadcasted to all droppers",
        broadcasted_at=job.broadcasted_at
    )


@router.post("/jobs/{job_id}/unbroadcast")
async def unbroadcast_job(
    job_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Remove a job from broadcast (Leave Broadcast).
    
    Makes a job no longer visible to droppers by clearing the is_broadcasted flag.
    Only works if the job hasn't been assigned to a dropper yet.
    """
    # Log unbroadcast attempt
    logger.info(
        f"🔒 AUDIT: Job unbroadcast attempt:\n"
        f"   Job ID: {job_id}\n"
        f"   Requested By: {current_user.email} (ID: {current_user.id})"
    )
    
    # Get the job
    job = db.exec(select(DropJob).where(DropJob.id == job_id)).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # Check if job is broadcasted
    if not job.is_broadcasted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is not currently broadcasted"
        )
    
    # Check if job is already assigned
    assignment = db.exec(
        select(JobAssignment).where(JobAssignment.job_id == job_id)
    ).first()
    
    if assignment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unbroadcast - job is already assigned to a dropper"
        )
    
    # Clear broadcast flags
    job.is_broadcasted = False
    job.broadcasted_at = None
    db.add(job)
    db.commit()
    
    logger.info(
        f"✅ AUDIT: Job unbroadcast successful:\n"
        f"   Job ID: {job_id}\n"
        f"   Job Title: {job.title}\n"
        f"   Unbroadcasted By: {current_user.email}"
    )
    
    return {
        "success": True,
        "job_id": str(job.id),
        "message": f"Job '{job.title}' has been removed from broadcast"
    }


@router.get("/droppers/search", response_model=List[DropperSearchResponse])
async def search_droppers(
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Search for droppers by name or email.
    
    Returns droppers with their profile information including rating,
    service radius, and total jobs completed.
    
    Args:
        q: Search query to match against name or email (optional)
        limit: Maximum number of droppers to return (default 50)
        offset: Number of droppers to skip for pagination (default 0)
        current_user: Current admin user
        db: Database session
        
    Returns:
        List of droppers matching the search criteria
    """
    # Build query for droppers
    query = (
        select(User, Dropper)
        .join(Dropper, User.id == Dropper.user_id)
        .where(User.role == UserRole.DROPPER)
    )
    
    # Add search filter if query provided
    if q:
        search_term = f"%{q}%"
        query = query.where(
            (User.name.ilike(search_term)) | (User.email.ilike(search_term))
        )
    
    # Order by rating and total jobs completed
    query = query.order_by(Dropper.rating.desc(), Dropper.total_jobs_completed.desc())
    query = query.offset(offset).limit(limit)
    
    results = db.exec(query).all()
    
    droppers = []
    for user, dropper_profile in results:
        droppers.append(DropperSearchResponse(
            id=user.id,
            name=user.name,
            email=user.email,
            rating=float(dropper_profile.rating) if dropper_profile.rating else 0.0,
            service_radius_km=dropper_profile.service_radius_km,
            total_jobs_completed=dropper_profile.total_jobs_completed,
            is_available=dropper_profile.is_available
        ))
    
    return droppers


@router.get("/transactions", response_model=List[Dict[str, Any]])
async def get_all_transactions(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get all transactions (payments and payouts) with user and job details.
    """
    try:
        query = (
            select(Transaction, User, DropJob)
            .join(User, Transaction.user_id == User.id)
            .outerjoin(DropJob, Transaction.job_id == DropJob.id)
            .order_by(Transaction.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        results = db.exec(query).all()
        
        # Convert to list of dicts with all fields
        result = []
        for t, user, job in results:
            t_dict = {
                "id": str(t.id),
                "user_id": str(t.user_id),
                "user_name": user.name,
                "user_email": user.email,
                "job_id": str(t.job_id) if t.job_id else None,
                "job_title": job.title if job else None,
                "transaction_type": t.transaction_type,
                "amount_pence": t.amount_pence,
                "currency": t.currency,
                "status": t.status.value if hasattr(t.status, 'value') else t.status,
                "stripe_payment_intent_id": t.stripe_payment_intent_id,
                "stripe_transfer_id": t.stripe_transfer_id if hasattr(t, 'stripe_transfer_id') else None,
                "stripe_charge_id": t.stripe_charge_id if hasattr(t, 'stripe_charge_id') else None,
                "stripe_refund_id": t.stripe_refund_id if hasattr(t, 'stripe_refund_id') else None,
                "description": t.description,
                "failure_reason": t.failure_reason if hasattr(t, 'failure_reason') else None,
                "transaction_metadata": t.transaction_metadata if hasattr(t, 'transaction_metadata') else None,
                "processed_at": t.processed_at.isoformat() if t.processed_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if hasattr(t, 'updated_at') and t.updated_at else None
            }
            result.append(t_dict)
            
        return result
    except Exception as e:
        logger.error(f"Error listing transactions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve transactions: {str(e)}"
        )


@router.get("/invoices", response_model=List[Dict[str, Any]])
async def get_all_invoices(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """
    Get all invoices with client details.
    """
    try:
        query = (
            select(Invoice, User)
            .join(User, Invoice.user_id == User.id)
            .order_by(Invoice.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        results = db.exec(query).all()
        
        result = []
        for i, user in results:
            i_dict = {
                "id": str(i.id),
                "user_id": str(i.user_id),
                "client_name": user.name,
                "client_email": user.email,
                "stripe_session_id": i.stripe_session_id,
                "stripe_payment_intent_id": i.stripe_payment_intent_id,
                "amount_total_pence": i.amount_total_pence,
                "currency": i.currency.upper() if i.currency else "AUD",
                "status": i.status,
                "job_ids": [str(jid) for jid in i.job_ids] if i.job_ids else [],
                "job_count": len(i.job_ids) if i.job_ids else 0,
                "invoice_metadata": i.invoice_metadata if hasattr(i, 'invoice_metadata') else None,
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "updated_at": i.updated_at.isoformat() if hasattr(i, 'updated_at') and i.updated_at else None
            }
            result.append(i_dict)
            
        return result
    except Exception as e:
        logger.error(f"Error listing invoices: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve invoices: {str(e)}"
        )
