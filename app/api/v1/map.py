"""
Map and location API endpoints.
Handles drop points, drop zones, and dropper location tracking.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Path, Body
from sqlmodel import Session, select, or_, desc
from pydantic import ValidationError

from app.database import get_session
from app.models import User, UserRole, DropPoint, DropZone, DroperLocation, VerificationStatus
from app.api.deps import (
    get_current_active_user,
    require_client_role,
    require_client_or_admin,
    AuthorizationError
)
from app.schemas.map_schemas import (
    DropPointResponse,
    DropZoneCreate,
    DropZoneResponse,
    DroperLocationResponse
)

# Initialize logger
logger = logging.getLogger(__name__)

router = APIRouter(tags=["map"])


@router.get(
    "/map-data",
    name="get_map_data",  # Explicit route name for debugging
    summary="Get map data for current user",
    description="Fetch droppers, drop points, and role information for the map view",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Map data retrieved successfully"},
        401: {"description": "Authentication required"},
        500: {"description": "Internal server error"}
    }
)
async def get_map_data(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """
    Get comprehensive map data for the current user based on their role.
    
    Returns:
    - **droppers**: List of droppers with their locations and active jobs (for clients/admins)
    - **drop_points**: List of drop points visible to the user
    - **role**: Current user's role
    """
    try:
        from app.config import settings
        if settings.debug:
            logger.debug(f"Map data request received (role: {current_user.role.value})")
    except AttributeError:
        # Handle case where current_user might not have email yet
        if settings.debug:
            logger.debug("Map data request received")
    
    try:
        from app.models import Dropper, DropJob, JobAssignment
        from sqlmodel import func
        
        role = current_user.role.value
        
        # Initialize response data
        droppers = []
        drop_points = []
        
        # Get droppers (only for clients and admins)
        if role in [UserRole.CLIENT, UserRole.ADMIN]:
            dropper_profiles = db.exec(select(Dropper)).all()
            
            # OPTIMIZATION: Fetch all dropper user info in a single query
            dropper_user_ids = [d.user_id for d in dropper_profiles]
            if dropper_user_ids:
                users = db.exec(
                    select(User).where(User.id.in_(dropper_user_ids))
                ).all()
                user_map = {u.id: u for u in users}
                
                # OPTIMIZATION: Count active jobs for ALL droppers in a single query
                job_counts = db.exec(
                    select(JobAssignment.dropper_id, func.count(JobAssignment.id))
                    .where(
                        JobAssignment.dropper_id.in_(dropper_user_ids),
                        or_(
                            JobAssignment.verification_status == VerificationStatus.PENDING,
                            JobAssignment.verification_status == VerificationStatus.APPROVED
                        )
                    )
                    .group_by(JobAssignment.dropper_id)
                ).all()
                job_count_map = {dropper_id: count for dropper_id, count in job_counts}
            else:
                user_map = {}
                job_count_map = {}
            
            for dropper in dropper_profiles:
                # Get dropper user info from pre-fetched map
                dropper_user = user_map.get(dropper.user_id)
                if not dropper_user:
                    continue
                
                # Get active jobs count from pre-calculated map
                active_jobs_count = job_count_map.get(dropper.user_id, 0)
                
                # Get latest location if available
                location = None
                if dropper.base_location_lat and dropper.base_location_lng:
                    location = {
                        "lat": dropper.base_location_lat,
                        "lng": dropper.base_location_lng
                    }
                
                droppers.append({
                    "dropper_id": str(dropper.user_id),
                    "name": dropper_user.name,
                    "location": location,
                    "active_jobs": active_jobs_count
                })
        
        # Get drop points based on role
        if role == UserRole.DROPPER:
            # Droppers see only their assigned/active drop points
            statement = select(DropPoint).where(
                DropPoint.dropper_id == current_user.id,
                or_(
                    DropPoint.status == "assigned",
                    DropPoint.status == "active"
                )
            )
        elif role == UserRole.CLIENT:
            # Clients see their own drop points
            statement = select(DropPoint).where(DropPoint.client_id == current_user.id)
        else:  # admin
            # Admins see all drop points
            statement = select(DropPoint)
        
        drop_points_data = db.exec(statement.order_by(desc(DropPoint.created_at))).all()
        
        for point in drop_points_data:
            drop_points.append({
                "id": str(point.id),
                "lat": point.lat,
                "lng": point.lng,
                "title": point.name,
                "status": point.status
            })
        
        result = {
            "droppers": droppers,
            "drop_points": drop_points,
            "role": role
        }
        
        logger.info(f"Map data returned successfully: {len(droppers)} droppers, {len(drop_points)} drop points")
        return result
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Error fetching map data: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch map data: {str(e)}"
        )


@router.get(
    "/drop-points/{role}/{user_id}",
    response_model=List[DropPointResponse],
    summary="Get drop points by role and user",
    description="Fetch drop points for a specific dropper, client, or admin"
)
async def get_drop_points(
    role: str = Path(..., description="User role: 'dropper', 'client', or 'admin'"),
    user_id: UUID = Path(..., description="User ID to fetch drop points for"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """
    Get drop points filtered by role and user ID.
    
    - **dropper**: Returns only assigned/active drop points for the dropper
    - **client**: Returns all drop points owned by the client
    - **admin**: Returns all drop points (full access)
    
    Access control:
    - Droppers can only see their own drop points
    - Clients can only see their own drop points
    - Admins can see all drop points
    """
    # Validate role
    if role not in ["dropper", "client", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be 'dropper', 'client', or 'admin'"
        )
    
    # Authorization checks
    if role == "dropper":
        # Droppers can only see their own drop points
        if current_user.id != user_id and current_user.role != UserRole.ADMIN:
            raise AuthorizationError("Access denied. You can only view your own drop points.")
        
        # Get drop points assigned to this dropper with assigned/active status
        statement = select(DropPoint).where(
            DropPoint.dropper_id == user_id,
            or_(
                DropPoint.status == "assigned",
                DropPoint.status == "active"
            )
        )
        
    elif role == "client":
        # Clients can only see their own drop points
        if current_user.id != user_id and current_user.role != UserRole.ADMIN:
            raise AuthorizationError("Access denied. You can only view your own drop points.")
        
        # Get all drop points owned by this client
        statement = select(DropPoint).where(DropPoint.client_id == user_id)
        
    else:  # admin
        # Admins can see all drop points
        if current_user.role != UserRole.ADMIN:
            raise AuthorizationError("Access denied. Admin role required.")
        
        # Get all drop points
        statement = select(DropPoint)
    
    # Execute query
    drop_points = db.exec(statement.order_by(desc(DropPoint.created_at))).all()
    
    return [
        DropPointResponse(
            id=point.id,
            lat=point.lat,
            lng=point.lng,
            name=point.name,
            status=point.status,
            client_id=point.client_id,
            dropper_id=point.dropper_id,
            created_at=point.created_at
        )
        for point in drop_points
    ]


@router.post(
    "/drop-zones/save",
    response_model=DropZoneResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save drop zone polygon",
    description="Store a polygon zone (drop zone) defined by a client on the map"
)
async def save_drop_zone(
    drop_zone: DropZoneCreate = Body(...),
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Save a drop zone polygon for the current client.
    
    The polygon_json should contain coordinates as an array of {lat, lng} objects.
    Only clients can create drop zones.
    """
    try:
        # Validate polygon_json structure
        polygon_data = drop_zone.polygon_json
        
        # Accept different formats: array of {lat, lng} or GeoJSON
        if isinstance(polygon_data, dict) and "coordinates" in polygon_data:
            # Format: {"coordinates": [{"lat": ..., "lng": ...}, ...]}
            coordinates = polygon_data.get("coordinates", [])
            if not isinstance(coordinates, list) or len(coordinates) < 3:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Polygon must have at least 3 coordinates"
                )
            # Validate each coordinate
            for coord in coordinates:
                if not isinstance(coord, dict) or "lat" not in coord or "lng" not in coord:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Each coordinate must have 'lat' and 'lng' keys"
                    )
        elif isinstance(polygon_data, list):
            # Format: [{"lat": ..., "lng": ...}, ...]
            if len(polygon_data) < 3:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Polygon must have at least 3 coordinates"
                )
            # Normalize to standard format
            polygon_data = {"coordinates": polygon_data}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="polygon_json must be an array of coordinates or an object with 'coordinates' key"
            )
        
        # Create drop zone
        new_drop_zone = DropZone(
            polygon_json=polygon_data,
            client_id=current_user.id,
            name=drop_zone.name
        )
        
        db.add(new_drop_zone)
        db.commit()
        db.refresh(new_drop_zone)
        
        logger.info(f"Drop zone created: {new_drop_zone.id} by client {current_user.id}")
        
        return DropZoneResponse(
            id=new_drop_zone.id,
            polygon_json=new_drop_zone.polygon_json,
            client_id=new_drop_zone.client_id,
            name=new_drop_zone.name,
            created_at=new_drop_zone.created_at,
            updated_at=new_drop_zone.updated_at
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error saving drop zone: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save drop zone: {str(e)}"
        )



@router.get(
    "/job/{job_id}/dropper-location",
    response_model=DroperLocationResponse,
    summary="Get dropper location for a job",
    description="Client or admin can track the dropper assigned to a job"
)
async def get_job_dropper_location(
    job_id: UUID = Path(..., description="Job ID to track"),
    current_user: User = Depends(require_client_or_admin()),
    db: Session = Depends(get_session)
):
    """
    Get the current location of the dropper assigned to a specific job.
    
    - Clients can only track jobs they own
    - Admins can track any job
    """
    from app.models import DropJob, JobAssignment
    
    try:
        # Get the job
        job = db.exec(select(DropJob).where(DropJob.id == job_id)).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )
        
        # Authorization: clients can only track their own jobs
        if current_user.role == UserRole.CLIENT and job.client_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only track your own jobs"
            )
        
        # Get the job assignment to find the dropper
        assignment = db.exec(
            select(JobAssignment).where(JobAssignment.job_id == job_id)
        ).first()
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No dropper assigned to this job yet"
            )
        
        # Get dropper's latest location
        location = db.exec(
            select(DroperLocation)
            .where(DroperLocation.dropper_id == assignment.dropper_id)
            .order_by(desc(DroperLocation.timestamp))
            .limit(1)
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dropper location not available"
            )
        
        return DroperLocationResponse(
            id=location.id,
            dropper_id=location.dropper_id,
            lat=location.lat,
            lng=location.lng,
            timestamp=location.timestamp
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching job dropper location: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch dropper location: {str(e)}"
        )

