"""
Job service for managing job creation, updates, and business logic.
Handles job lifecycle, cost calculations, and validation.
"""

# pylint: disable=no-member,not-callable,too-many-lines
# SQLModel/SQLAlchemy dynamic attributes (in_, desc, notin_) are valid at runtime

from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal
from uuid import UUID
import math
import logging
from sqlalchemy import desc, exists
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, and_, or_
from app.models import (
    DropJob, JobArea, JobStatus, User, UserRole, Dropper,
    JobAssignment, VerificationStatus, DropPoint
)
from app.schemas.job_schemas import (
    JobCreate, JobUpdate, JobAreaCreate, JobCostCalculation
)
from app.schemas.dropper_schemas import (
    JobAcceptanceRequest, JobCompletionRequest
)
from app.security import is_superadmin_email


class JobServiceError(Exception):
    """Custom exception for job service errors."""


logger = logging.getLogger(__name__)


class JobService:
    """Service for job management operations."""

    def __init__(self, db: Session):
        self.db = db
        # Cost configuration - will be loaded from CostSettings
        self._load_cost_settings()

    def _load_cost_settings(self):
        """Load cost settings from database."""
        from app.models import CostSettings  # pylint: disable=import-outside-toplevel

        settings = self.db.exec(select(CostSettings).limit(1)).first()
        if settings:
            self.cost_per_household_pence = settings.price_per_household_pence
            self.platform_fee_percentage = Decimal(str(settings.platform_fee_percentage / 100))
        else:
            # Default values if no settings exist
            self.cost_per_household_pence = 50  # 50 pence per household
            self.platform_fee_percentage = Decimal("0.15")  # 15% platform fee

    def calculate_job_cost(self, household_count: int) -> JobCostCalculation:
        """
        Calculate total job cost including platform fees.
        Uses current cost settings from database.

        Args:
            household_count: Number of households for leaflet distribution

        Returns:
            JobCostCalculation: Detailed cost breakdown
        """
        # Reload cost settings to ensure we have the latest values
        self._load_cost_settings()

        subtotal_pence = household_count * self.cost_per_household_pence
        platform_fee_pence = int(Decimal(subtotal_pence) * self.platform_fee_percentage)
        total_cost_pence = subtotal_pence + platform_fee_pence
        dropper_payout_pence = subtotal_pence  # Dropper gets the subtotal, platform keeps the fee

        return JobCostCalculation(
            household_count=household_count,
            cost_per_household_pence=self.cost_per_household_pence,
            subtotal_pence=subtotal_pence,
            platform_fee_pence=platform_fee_pence,
            total_cost_pence=total_cost_pence,
            dropper_payout_pence=dropper_payout_pence
        )

    def create_job(self, job_data: JobCreate, client_user: User) -> DropJob:
        """
        Create a new draft job for a client.

        Args:
            job_data: Job creation data
            client_user: Client user creating the job

        Returns:
            DropJob: Created job

        Raises:
            JobServiceError: If job creation fails
        """
        try:
            # Validate client role - allow regular clients or configured superadmin emails
            is_allowed_admin = (
                client_user.role == UserRole.ADMIN
                and is_superadmin_email(client_user.email)
            )

            # Robust role check
            user_role_str = str(client_user.role.value if hasattr(client_user.role, 'value') else client_user.role).upper()
            if user_role_str != UserRole.CLIENT and not is_allowed_admin:
                raise JobServiceError(f"Only clients can create jobs (current role: {user_role_str})")

            # Calculate job costs
            cost_calc = self.calculate_job_cost(job_data.household_count)

            # Create job record
            job = DropJob(
                client_id=client_user.id,
                status=JobStatus.DRAFT,
                title=job_data.title,
                description=job_data.description,
                leaflet_file_url=job_data.leaflet_file_url or "https://example.com/leaflets/placeholder.pdf",
                household_count=job_data.household_count,
                cost_per_household_pence=self.cost_per_household_pence,
                cost_total_pence=cost_calc.total_cost_pence,
                platform_fee_pence=cost_calc.platform_fee_pence,
                dropper_payout_pence=cost_calc.dropper_payout_pence,
                scheduled_date=job_data.scheduled_date,
                special_instructions=job_data.special_instructions,
                created_at=datetime.utcnow()
            )

            self.db.add(job)
            self.db.flush()  # Flush to get job ID

            # Create job area
            self._create_job_area(job.id, job_data.job_area)

            # Create drop points if provided
            if job_data.drop_points:
                self._create_drop_points(job.id, client_user.id, job_data.drop_points)

            # Explicit commit with verification
            self.db.commit()
            self.db.refresh(job)

            # Verify job was saved (ID should be set after commit)
            if not job.id:
                raise JobServiceError("Job creation failed - ID not generated after commit")

            logger.info("Job created successfully: %s for client %s", job.id, client_user.id)
            return job

        except Exception as e:
            self.db.rollback()
            logger.error("Job creation failed: %s", e, exc_info=True)
            if isinstance(e, JobServiceError):
                raise
            raise JobServiceError(f"Failed to create job: {str(e)}") from e

    def _create_job_area(self, job_id: UUID, area_data: JobAreaCreate) -> JobArea:
        """
        Create job area for a job.

        Args:
            job_id: ID of the job
            area_data: Job area creation data

        Returns:
            JobArea: Created job area
        """
        # Calculate center and radius if not provided
        center_lat = area_data.center_lat
        center_lng = area_data.center_lng
        radius_km = area_data.radius_km

        if area_data.area_type == "polygon" and area_data.geojson:
            # Extract center from polygon if not provided
            if not center_lat or not center_lng:
                center_lat, center_lng = self._calculate_polygon_center(area_data.geojson)

            # Estimate radius from polygon bounds
            if not radius_km:
                radius_km = self._estimate_polygon_radius(area_data.geojson)

        job_area = JobArea(
            job_id=job_id,
            area_type=area_data.area_type,
            geojson=area_data.geojson,
            postcodes=area_data.postcodes,
            center_lat=center_lat,
            center_lng=center_lng,
            radius_km=radius_km,
            created_at=datetime.utcnow()
        )

        self.db.add(job_area)
        return job_area

    def _calculate_polygon_center(self, geojson: Dict[str, Any]) -> tuple[float, float]:
        """
        Calculate approximate center of a polygon from GeoJSON.

        Args:
            geojson: GeoJSON polygon object

        Returns:
            tuple: (latitude, longitude) of center
        """
        try:
            coordinates = geojson["coordinates"][0]  # First ring of polygon

            # Calculate centroid
            lat_sum = sum(coord[1] for coord in coordinates)
            lng_sum = sum(coord[0] for coord in coordinates)
            count = len(coordinates)

            center_lat = lat_sum / count
            center_lng = lng_sum / count

            return center_lat, center_lng

        except (KeyError, IndexError, TypeError):
            # Fallback to London center if calculation fails
            return 51.5074, -0.1278

    def _estimate_polygon_radius(self, geojson: Dict[str, Any]) -> float:
        """
        Estimate radius of a polygon from GeoJSON.

        Args:
            geojson: GeoJSON polygon object

        Returns:
            float: Estimated radius in kilometers
        """
        try:
            coordinates = geojson["coordinates"][0]  # First ring of polygon

            # Calculate bounding box
            lats = [coord[1] for coord in coordinates]
            lngs = [coord[0] for coord in coordinates]

            lat_range = max(lats) - min(lats)
            lng_range = max(lngs) - min(lngs)

            # Rough approximation: 1 degree ≈ 111 km
            lat_km = lat_range * 111
            lng_km = lng_range * 111 * 0.7  # Adjust for longitude at UK latitude

            # Return half of the maximum dimension as radius
            return max(lat_km, lng_km) / 2

        except (KeyError, IndexError, TypeError):
            # Fallback radius
            return 5.0

    def _create_drop_points(
            self, job_id: UUID, client_id: UUID,
            drop_points_data: List
    ) -> List[DropPoint]:
        """
        Create drop points for a job.

        Args:
            job_id: ID of the job
            client_id: ID of the client creating the job
            drop_points_data: List of drop point creation data

        Returns:
            List[DropPoint]: Created drop points
        """
        drop_points = []
        for dp_data in drop_points_data:
            drop_point = DropPoint(
                job_id=job_id,
                client_id=client_id,
                lat=dp_data.lat,
                lng=dp_data.lng,
                name=dp_data.name or f"Drop Point {dp_data.order or len(drop_points) + 1}",
                order=dp_data.order,
                status="draft",
                created_at=datetime.utcnow()
            )
            self.db.add(drop_point)
            drop_points.append(drop_point)

        return drop_points

    def get_job_by_id(
            self, job_id: UUID, user: User,
            allow_public: bool = False
    ) -> Optional[DropJob]:
        """
        Get job by ID with access control.

        Args:
            job_id: Job ID
            user: User requesting the job
            allow_public: If True, allow viewing other users' published jobs (read-only)

        Returns:
            Optional[DropJob]: Job if found and accessible
        """
        statement = select(DropJob).where(DropJob.id == job_id)
        job = self.db.exec(statement).first()

        if not job:
            return None

        # Owner can always see their jobs
        if job.client_id == user.id:
            return job

        # If allow_public, allow viewing published jobs from others (read-only)
        public_statuses = [
            JobStatus.PENDING_APPROVAL, JobStatus.PAID,
            JobStatus.ASSIGNED, JobStatus.COMPLETED
        ]
        if allow_public and job.status in public_statuses:
            return job

        # Admins can see all jobs
        if user.role == UserRole.ADMIN:
            return job

        # Access control: clients can only see their own jobs (or published if allow_public)
        if user.role == UserRole.CLIENT:
            return None

        return job

    def get_public_jobs(
        self,
        status: Optional[JobStatus] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[DropJob]:
        """
        Get all published jobs from all users (public listing).
        Excludes draft jobs (only visible to owner).

        Args:
            status: Optional status filter (must be one of PAID, ASSIGNED, COMPLETED)
            limit: Maximum number of jobs to return
            offset: Number of jobs to skip

        Returns:
            List[DropJob]: List of published jobs
        """
        # Only show published jobs (exclude DRAFT and REJECTED)
        allowed_statuses = [
            JobStatus.PENDING_APPROVAL, JobStatus.PAID,
            JobStatus.ASSIGNED, JobStatus.COMPLETED
        ]

        statement = (
            select(DropJob, JobArea)
            .outerjoin(JobArea, JobArea.job_id == DropJob.id)
            .where(DropJob.status.in_(allowed_statuses))  # type: ignore[attr-defined]
        )

        # Apply status filter if provided (must be one of allowed statuses)
        if status:
            if status not in allowed_statuses:
                # If invalid status provided, return empty list
                return []
            statement = statement.where(DropJob.status == status)

        statement = statement.order_by(desc(DropJob.created_at))
        statement = statement.offset(offset).limit(limit)

        # Execute query and attach job_area to jobs
        results = self.db.exec(statement).all()
        jobs = []
        for result in results:
            try:
                is_row = hasattr(result, '_fields') or hasattr(result, '_mapping') or (
                    hasattr(result, '__getitem__') and not isinstance(result, tuple)
                )

                if is_row:
                    job = result[0] if len(result) > 0 else None
                    job_area = result[1] if len(result) > 1 else None
                elif isinstance(result, tuple):
                    job = result[0] if len(result) > 0 else None
                    job_area = result[1] if len(result) > 1 else None
                else:
                    job = result
                    job_area = None

                if job is None or not hasattr(job, 'id'):
                    continue

                if job_area and hasattr(job_area, 'job_id'):
                    job.job_area = job_area

                jobs.append(job)
            except (IndexError, TypeError, AttributeError) as e:
                logger.warning("Error processing public job result: %s, error: %s", type(result), e)
                continue

        return jobs

    def get_client_jobs(
        self,
        client_user: User,
        status: Optional[JobStatus] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[DropJob]:
        """
        Get jobs for a client with optional filtering.

        Args:
            client_user: Client user
            status: Optional status filter
            limit: Maximum number of jobs to return
            offset: Number of jobs to skip

        Returns:
            List[DropJob]: List of jobs
        """
        statement = (
            select(DropJob, JobArea)
            .outerjoin(JobArea, JobArea.job_id == DropJob.id)
            .where(DropJob.client_id == client_user.id)
        )

        if status:
            statement = statement.where(DropJob.status == status)

        statement = statement.order_by(desc(DropJob.created_at))
        statement = statement.offset(offset).limit(limit)

        # Execute query and attach job_area to jobs
        results = self.db.exec(statement).all()
        jobs = []
        for result in results:
            # Handle Row objects from SQLModel/SQLAlchemy joins
            # Row objects are returned when selecting multiple models
            try:
                # Check if it's a Row object (from sqlalchemy.engine.row)
                has_fields = hasattr(result, '_fields')
                has_mapping = hasattr(result, '_mapping')
                has_getitem = (
                    hasattr(result, '__getitem__') and
                    not isinstance(result, tuple)
                )
                is_row = has_fields or has_mapping or has_getitem

                if is_row:
                    # Row objects support indexing: result[0] = DropJob, result[1] = JobArea
                    job = result[0] if len(result) > 0 else None
                    job_area = result[1] if len(result) > 1 else None
                elif isinstance(result, tuple):
                    # Tuple from older SQLAlchemy versions
                    job = result[0] if len(result) > 0 else None
                    job_area = result[1] if len(result) > 1 else None
                else:
                    # Already a DropJob instance (shouldn't happen with join, but handle it)
                    job = result
                    job_area = None

                # Validate we have a proper DropJob instance
                if job is None:
                    continue

                if not hasattr(job, 'id') or not hasattr(job, 'client_id'):
                    # Not a valid DropJob instance, skip it
                    logger.warning("Skipping invalid job result: %s", type(job))
                    continue

                # Attach job_area if available
                if job_area and hasattr(job_area, 'job_id'):
                    job.job_area = job_area

                jobs.append(job)

            except (IndexError, TypeError, AttributeError) as e:
                logger.warning("Error processing job result: %s, error: %s", type(result), e)
                continue

        return jobs

    def get_jobs_for_feed(
        self,
        user: User,
        status: Optional[JobStatus] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[DropJob]:
        """
        Get jobs tailored for the unified job feed.

        Clients: Only their own jobs.
        Superadmin clients (role='ADMIN' in clients table): All jobs.
        Droppers: Broadcasted jobs and jobs assigned to them.
        Admins: All jobs.
        """
        from app.models import Client  # pylint: disable=import-outside-toplevel

        logger.info(
            "get_jobs_for_feed called: user.id=%s, user.role=%s, status=%s",
            user.id, user.role, status
        )

        statement = (
            select(DropJob, JobArea)
            .outerjoin(JobArea, JobArea.job_id == DropJob.id)
        )

        if status:
            statement = statement.where(DropJob.status == status)

        # Check if user is superadmin (by Client.role = 'ADMIN')
        is_superadmin_client = False
        if user.role == UserRole.CLIENT:
            client_profile = self.db.exec(
                select(Client).where(Client.user_id == user.id)
            ).first()
            if client_profile and client_profile.role == 'ADMIN':
                is_superadmin_client = True

        if user.role == UserRole.CLIENT and not is_superadmin_client:
            # Regular clients see ONLY their own jobs
            statement = statement.where(DropJob.client_id == user.id)
        elif user.role == UserRole.DROPPER:
            # OPTIMIZATION: Use EXISTS subquery instead of IN for better performance
            # Use scalar select for EXISTS - SQLAlchemy exists() requires a selectable
            assignment_subquery = select(JobAssignment).where(
                and_(
                    JobAssignment.job_id == DropJob.id,
                JobAssignment.dropper_id == user.id
            )
            )
            assignment_exists = exists(assignment_subquery)
            statement = statement.where(
                or_(
                    DropJob.is_broadcasted.is_(True),  # noqa: E712
                    assignment_exists  # Use EXISTS instead of IN for better performance
                )
            ).where(
                DropJob.status.in_([  # type: ignore[attr-defined]
                    JobStatus.PENDING_APPROVAL, JobStatus.PAID,
                    JobStatus.ASSIGNED, JobStatus.COMPLETED
                ])
            )

        statement = statement.order_by(desc(DropJob.created_at))
        statement = statement.offset(offset).limit(limit)

        results = self.db.exec(statement).all()
        jobs = []

        for result in results:
            try:
                is_row = hasattr(result, '_fields') or hasattr(result, '_mapping') or (
                    hasattr(result, '__getitem__') and not isinstance(result, tuple)
                )

                if is_row:
                    job = result[0] if len(result) > 0 else None
                    job_area = result[1] if len(result) > 1 else None
                elif isinstance(result, tuple):
                    job = result[0] if len(result) > 0 else None
                    job_area = result[1] if len(result) > 1 else None
                else:
                    job = result
                    job_area = None

                if job is None or not hasattr(job, 'id'):
                    continue

                if job_area and hasattr(job_area, 'job_id'):
                    job.job_area = job_area

                jobs.append(job)
            except (IndexError, TypeError, AttributeError) as e:
                logger.warning("Error processing job feed result: %s, error: %s", type(result), e)
                continue

        return jobs

    def update_job(
        self,
        job_id: UUID,
        job_data: JobUpdate,
        client_user: User
    ) -> Optional[DropJob]:
        """
        Update a job.

        Jobs in any status can be updated, but with restrictions:
        - Paid/Active jobs: Only certain fields can be updated
          (description, special_instructions, scheduled_date)
        - Draft jobs: All fields can be updated

        Args:
            job_id: Job ID to update
            job_data: Update data
            client_user: Client user updating the job

        Returns:
            Optional[DropJob]: Updated job if successful

        Raises:
            JobServiceError: If update fails
        """
        try:
            job = self.get_job_by_id(job_id, client_user)

            if not job:
                return None

            # Determine if job is in a state that restricts certain updates
            is_restricted = job.status not in [JobStatus.DRAFT, JobStatus.REJECTED]

            # Update fields if provided
            updated = False

            # Title can be updated for all jobs
            if job_data.title is not None:
                job.title = job_data.title
                updated = True

            # Description can be updated for all jobs
            if job_data.description is not None:
                job.description = job_data.description
                updated = True

            # Leaflet file URL can be updated for all jobs
            if job_data.leaflet_file_url is not None:
                job.leaflet_file_url = job_data.leaflet_file_url
                updated = True

            # Household count and cost - can only be updated for draft/rejected jobs
            if job_data.household_count is not None:
                if is_restricted:
                    # For paid/active jobs, don't allow cost changes
                    raise JobServiceError(
                        f"Cannot update household count for jobs with "
                        f"status '{job.status.value}'. Only draft/rejected "
                        f"jobs can have cost changes."
                    )
                # Recalculate costs if household count changes
                cost_calc = self.calculate_job_cost(job_data.household_count)
                job.household_count = job_data.household_count
                job.cost_total_pence = cost_calc.total_cost_pence
                job.platform_fee_pence = cost_calc.platform_fee_pence
                job.dropper_payout_pence = cost_calc.dropper_payout_pence
                updated = True

            # Scheduled date can be updated for all jobs
            if job_data.scheduled_date is not None:
                job.scheduled_date = job_data.scheduled_date
                updated = True

            # Special instructions can be updated for all jobs
            if job_data.special_instructions is not None:
                job.special_instructions = job_data.special_instructions
                updated = True

            if updated:
                job.updated_at = datetime.utcnow()
                self.db.add(job)
                self.db.commit()
                self.db.refresh(job)

            return job

        except Exception as e:
            self.db.rollback()
            if isinstance(e, JobServiceError):
                raise
            raise JobServiceError(f"Failed to update job: {str(e)}") from e

    def mark_job_as_paid(self, job_id: UUID, payment_intent_id: str) -> bool:
        """
        Mark a job as paid after successful payment.

        Args:
            job_id: Job ID
            payment_intent_id: Stripe PaymentIntent ID

        Returns:
            bool: True if successful

        Raises:
            JobServiceError: If job update fails
        """
        try:
            statement = select(DropJob).where(DropJob.id == job_id)
            job = self.db.exec(statement).first()

            if not job:
                raise JobServiceError(f"Job {job_id} not found")

            if job.status != JobStatus.DRAFT:
                raise JobServiceError(f"Job {job_id} is not in draft status")

            job.status = JobStatus.PENDING_APPROVAL
            job.payment_intent_id = payment_intent_id
            job.paid_at = datetime.utcnow()
            job.updated_at = datetime.utcnow()

            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)

            logger.info("Job %s marked as paid with payment intent %s", job_id, payment_intent_id)
            return True

        except Exception as e:
            self.db.rollback()
            if isinstance(e, JobServiceError):
                raise
            raise JobServiceError(f"Failed to mark job as paid: {str(e)}") from e

    def get_available_jobs_for_dropper(
        self,
        dropper_user: User,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get available paid jobs for a dropper based on their service area.
        Includes jobs that are:
        1. Assigned to this dropper
        2. Broadcasted to all droppers (is_broadcasted=True AND status=PAID)

        Excludes jobs assigned to other droppers.

        Args:
            dropper_user: Dropper user
            limit: Maximum number of jobs to return
            offset: Number of jobs to skip

        Returns:
            List[Dict[str, Any]]: List of available jobs with distance and assignment information
        """
        try:
            # Get dropper profile
            dropper_statement = select(Dropper).where(Dropper.user_id == dropper_user.id)
            dropper = self.db.exec(dropper_statement).first()

            if not dropper:
                raise JobServiceError("Dropper profile not found")

            # Get jobs assigned to this dropper (regardless of scheduled date)
            # Exclude completed jobs - they should only appear in the completed jobs endpoint
            assigned_to_me_statement = (
                select(DropJob)
                .join(JobAssignment, JobAssignment.job_id == DropJob.id)
                .where(
                    and_(
                        JobAssignment.dropper_id == dropper_user.id,
                        DropJob.status.in_([  # type: ignore[attr-defined]
                            JobStatus.PENDING_APPROVAL, JobStatus.PAID,
                            JobStatus.APPROVED, JobStatus.ASSIGNED
                        ])
                    )
                )
            )
            assigned_jobs = list(self.db.exec(assigned_to_me_statement).all())

            # Get broadcasted jobs that are not assigned to anyone yet
            assigned_jobs_subquery = select(JobAssignment.job_id)
            broadcasted_statement = (
                select(DropJob)
                .where(
                    and_(
                        DropJob.is_broadcasted.is_(True),  # noqa: E712
                        DropJob.status.in_([  # type: ignore[attr-defined]
                            JobStatus.PENDING_APPROVAL, JobStatus.PAID,
                            JobStatus.APPROVED, JobStatus.ASSIGNED
                        ]),
                        DropJob.id.notin_(assigned_jobs_subquery)  # type: ignore[attr-defined]
                    )
                )
                .order_by(desc(DropJob.created_at))
            )
            broadcasted_jobs = list(self.db.exec(broadcasted_statement).all())

            # Combine both lists, removing duplicates
            all_jobs = assigned_jobs + broadcasted_jobs
            seen_ids = set()
            unique_jobs = []
            for job in all_jobs:
                if job.id not in seen_ids:
                    seen_ids.add(job.id)
                    unique_jobs.append(job)

            # Filter jobs by distance if dropper has location set
            available_jobs = []

            for job in unique_jobs:
                # Get job area
                job_area_statement = select(JobArea).where(JobArea.job_id == job.id)
                job_area = self.db.exec(job_area_statement).first()

                # Check if job is assigned to this dropper
                assignment_statement = select(JobAssignment).where(
                    and_(
                        JobAssignment.job_id == job.id,
                        JobAssignment.dropper_id == dropper_user.id
                    )
                )
                assignment = self.db.exec(assignment_statement).first()
                is_assigned_to_me = assignment is not None

                distance_km = None
                within_service_area = True

                if (dropper.base_location_lat and dropper.base_location_lng and
                    job_area and job_area.center_lat and job_area.center_lng):

                    # Calculate distance between dropper and job
                    distance_km = self._calculate_distance(
                        dropper.base_location_lat, dropper.base_location_lng,
                        job_area.center_lat, job_area.center_lng
                    )

                    # For assigned jobs, always include regardless of distance
                    # For broadcasted jobs: always include them
                    # (broadcast means available to all droppers)
                    # Distance is still calculated and shown, but doesn't filter out broadcast jobs
                    if not is_assigned_to_me and not job.is_broadcasted:
                        # Only filter by distance for non-broadcasted, non-assigned jobs
                        within_service_area = distance_km <= dropper.service_radius_km

                if within_service_area:
                    job_dict = {
                        "job": job,
                        "job_area": job_area,
                        "distance_km": distance_km,
                        "is_assigned_to_me": is_assigned_to_me,
                        "is_broadcasted": job.is_broadcasted
                    }
                    available_jobs.append(job_dict)

            # Sort: assigned jobs first, then by distance if available, otherwise by scheduled date
            def sort_key(job_dict):
                # Assigned jobs come first (0), broadcasted jobs second (1)
                assignment_priority = 0 if job_dict["is_assigned_to_me"] else 1
                # Then sort by distance (or infinity if no distance)
                distance = (
                    job_dict["distance_km"]
                    if job_dict["distance_km"] is not None
                    else float('inf')
                )
                return (assignment_priority, distance)

            available_jobs.sort(key=sort_key)

            # Apply pagination
            paginated_jobs = available_jobs[offset:offset + limit]

            return paginated_jobs

        except Exception as e:
            if isinstance(e, JobServiceError):
                raise
            raise JobServiceError(f"Failed to get available jobs: {str(e)}") from e

    def _calculate_distance(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """
        Calculate distance between two points using Haversine formula.

        Args:
            lat1, lng1: First point coordinates
            lat2, lng2: Second point coordinates

        Returns:
            float: Distance in kilometers
        """
        # Convert latitude and longitude from degrees to radians
        lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])

        # Haversine formula
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng/2)**2
        c = 2 * math.asin(math.sqrt(a))

        # Radius of earth in kilometers
        r = 6371

        return c * r

    def accept_job(
        self,
        job_id: UUID,
        dropper_user: User,
        acceptance_data: JobAcceptanceRequest
    ) -> JobAssignment:
        """
        Accept a job for a dropper.

        Args:
            job_id: Job ID to accept
            dropper_user: Dropper user accepting the job
            acceptance_data: Acceptance request data

        Returns:
            JobAssignment: Created job assignment

        Raises:
            JobServiceError: If job acceptance fails
        """
        try:
            logger.info("AUDIT FINGERPRINT: Executing LOCAL accept_job logic for job %s", job_id)
            # Get dropper profile
            dropper_statement = select(Dropper).where(Dropper.user_id == dropper_user.id)
            dropper = self.db.exec(dropper_statement).first()

            if not dropper:
                raise JobServiceError("Dropper profile not found")

            # Check dropper availability
            if not dropper.is_available:
                raise JobServiceError(
                    "You are not available for new jobs. "
                    "Please update your availability in your profile."
                )

            # Get job with row-level lock to prevent race conditions
            # Use with_for_update() to lock the row until transaction completes
            
            # FIX: Use SQLModel select to ensure we get a full ORM object
            job_statement = select(DropJob).where(DropJob.id == job_id).with_for_update()
            job = self.db.exec(job_statement).first()
            
            # Optional hardening: Log job type
            logger.debug(f"accept_job job type: {type(job)}")

            if not job:
                raise JobServiceError("Job not found")
                
            # Optional hardening: Assert attribute existence
            assert hasattr(job, "status"), "Invalid job object loaded - missing status attribute"

            # Allow jobs that are paid, pending approval, or approved
            # These statuses indicate the job is ready for assignment
            # Also allow 'active' as a valid status for assignment (alias for PAID)
            allowed_statuses = [
                JobStatus.PAID, 
                JobStatus.PENDING_APPROVAL, 
                JobStatus.APPROVED,
                JobStatus.DRAFT  # TEMPORARY: Allow DRAFT jobs to be accepted for testing flow
            ]
            
            if job.status not in allowed_statuses and str(job.status).lower() != 'active':
                raise JobServiceError(
                    f"Job is not available for assignment. Current status: '{job.status}'. "
                    f"Expected one of: PAID, PENDING_APPROVAL, APPROVED."
                )

            # Check if job is already assigned with row-level lock to prevent race conditions
            # Lock the assignment row to prevent concurrent acceptance
            existing_assignment_statement = select(JobAssignment).where(
                JobAssignment.job_id == job_id
            ).with_for_update()
            existing_assignment = self.db.exec(existing_assignment_statement).first()
            if existing_assignment:
                raise JobServiceError("Job is already assigned to another dropper")

            # Validate dropper is within service radius if location provided
            if (acceptance_data.dropper_location_lat and acceptance_data.dropper_location_lng):
                job_area_statement = select(JobArea).where(JobArea.job_id == job_id)
                job_area = self.db.exec(job_area_statement).first()

                if (job_area and job_area.center_lat and job_area.center_lng):
                    distance = self._calculate_distance(
                        acceptance_data.dropper_location_lat,
                        acceptance_data.dropper_location_lng,
                        job_area.center_lat,
                        job_area.center_lng
                    )

                    if distance > dropper.service_radius_km:
                        raise JobServiceError(
                            f"Job is outside your service radius. "
                            f"Distance: {distance:.1f}km, "
                            f"Service radius: {dropper.service_radius_km}km"
                        )

            # Create job assignment
            assignment = JobAssignment(
                job_id=job_id,
                dropper_id=dropper_user.id,
                accepted_at=datetime.utcnow(),
                verification_status=VerificationStatus.PENDING
            )

            # Update job status
            job.status = JobStatus.ASSIGNED
            job.updated_at = datetime.utcnow()

            try:
                self.db.add(assignment)
                self.db.add(job)
                self.db.commit()
                self.db.refresh(assignment)
            except IntegrityError as e:
                self.db.rollback()
                # Check if it's a unique constraint violation on job_id
                error_str = str(e.orig).lower() if hasattr(e, 'orig') else str(e).lower()
                if ("job_assignments_job_id_key" in error_str or
                        "unique constraint" in error_str or
                        "duplicate key" in error_str):
                    logger.warning(
                        "Job assignment failed due to unique constraint "
                        "violation (race condition): job_id=%s, dropper_id=%s",
                        job_id, dropper_user.id
                    )
                    raise JobServiceError(
                        "Job is already assigned to another dropper. "
                        "Please refresh the page to see the updated status."
                    ) from e
                # Re-raise if it's a different integrity error
                logger.error("IntegrityError during job acceptance: %s", str(e), exc_info=True)
                raise JobServiceError(
                    f"Failed to accept job due to database constraint: "
                    f"{str(e)}"
                ) from e

            logger.info("Job %s accepted successfully by dropper %s", job_id, dropper_user.id)
            return assignment

        except JobServiceError:
            # Re-raise JobServiceError without wrapping
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(
                "Unexpected error in accept_job: job_id=%s, "
                "dropper_id=%s, error=%s",
                job_id, dropper_user.id, str(e), exc_info=True
            )
            # Prevent hiding AttributeError logic bugs behind generic messages
            if isinstance(e, AttributeError):
                raise
                
            raise JobServiceError(f"Failed to accept job: {str(e)}") from e

    def start_job(
        self,
        job_id: UUID,
        dropper_user: User,
        _start_data: Optional[Dict[str, Any]] = None
    ) -> JobAssignment:
        """
        Start a job (mark as in progress).

        Args:
            job_id: Job ID to start
            dropper_user: Dropper user starting the job
            start_data: Optional start data (e.g., start location)

        Returns:
            JobAssignment: Updated job assignment

        Raises:
            JobServiceError: If job start fails
        """
        try:
            # Get job assignment
            assignment_statement = select(JobAssignment).where(
                and_(
                    JobAssignment.job_id == job_id,
                    JobAssignment.dropper_id == dropper_user.id
                )
            )
            assignment = self.db.exec(assignment_statement).first()

            if not assignment:
                raise JobServiceError("Job assignment not found or not assigned to you")

            # Get job
            job_statement = select(DropJob).where(DropJob.id == job_id)
            job = self.db.exec(job_statement).first()

            if not job:
                raise JobServiceError("Job not found")

            if job.status != JobStatus.ASSIGNED:
                raise JobServiceError("Job is not in assigned status")

            if assignment.started_at is not None:
                raise JobServiceError("Job has already been started")

            # Update assignment with start time
            assignment.started_at = datetime.utcnow()
            assignment.status = "in_progress"
            
            # Update job status if needed - keeping it as ASSIGNED for now as per existing logic
            # but assignment itself is now in_progress

            self.db.add(assignment)
            self.db.commit()
            self.db.refresh(assignment)

            return assignment

        except JobServiceError:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            logger.error("Error starting job %s: %s", job_id, str(e), exc_info=True)
            if isinstance(e, JobServiceError):
                raise
            raise JobServiceError(f"Failed to start job: {str(e)}") from e

    def pause_job(
        self,
        job_id: UUID,
        dropper_user: User
    ) -> JobAssignment:
        """
        Pause a job.
        
        Args:
            job_id: Job ID to pause
            dropper_user: Dropper user pausing the job
            
        Returns:
            JobAssignment: Updated job assignment
        """
        try:
            # Get job assignment
            assignment_statement = select(JobAssignment).where(
                and_(
                    JobAssignment.job_id == job_id,
                    JobAssignment.dropper_id == dropper_user.id
                )
            )
            assignment = self.db.exec(assignment_statement).first()

            if not assignment:
                raise JobServiceError("Job assignment not found or not assigned to you")

            if assignment.status not in ["in_progress", "active"]:
                raise JobServiceError(f"Cannot pause job. Current status: {assignment.status}")

            assignment.status = "paused"
            
            self.db.add(assignment)
            self.db.commit()
            self.db.refresh(assignment)

            return assignment

        except JobServiceError:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            logger.error("Error pausing job %s: %s", job_id, str(e), exc_info=True)
            raise JobServiceError(f"Failed to pause job: {str(e)}") from e

    def resume_job(
        self,
        job_id: UUID,
        dropper_user: User
    ) -> JobAssignment:
        """
        Resume a paused job.
        
        Args:
            job_id: Job ID to resume
            dropper_user: Dropper user resuming the job
            
        Returns:
            JobAssignment: Updated job assignment
        """
        try:
            # Get job assignment
            assignment_statement = select(JobAssignment).where(
                and_(
                    JobAssignment.job_id == job_id,
                    JobAssignment.dropper_id == dropper_user.id
                )
            )
            assignment = self.db.exec(assignment_statement).first()

            if not assignment:
                raise JobServiceError("Job assignment not found or not assigned to you")

            if assignment.status != "paused":
                raise JobServiceError(f"Cannot resume job. Current status: {assignment.status}")

            assignment.status = "in_progress"
            
            self.db.add(assignment)
            self.db.commit()
            self.db.refresh(assignment)

            return assignment

        except JobServiceError:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            logger.error("Error resuming job %s: %s", job_id, str(e), exc_info=True)
            raise JobServiceError(f"Failed to resume job: {str(e)}") from e

    def complete_job(
        self,
        job_id: UUID,
        dropper_user: User,
        completion_data: JobCompletionRequest
    ) -> JobAssignment:
        """
        Complete a job with proof submission.

        Args:
            job_id: Job ID to complete
            dropper_user: Dropper user completing the job
            completion_data: Completion proof data

        Returns:
            JobAssignment: Updated job assignment

        Raises:
            JobServiceError: If job completion fails
        """
        try:
            # Get job assignment
            assignment_statement = select(JobAssignment).where(
                and_(
                    JobAssignment.job_id == job_id,
                    JobAssignment.dropper_id == dropper_user.id
                )
            )
            assignment = self.db.exec(assignment_statement).first()

            if not assignment:
                raise JobServiceError("Job assignment not found or not assigned to you")

            # Get job
            job_statement = select(DropJob).where(DropJob.id == job_id)
            job = self.db.exec(job_statement).first()

            if not job:
                raise JobServiceError("Job not found")

            if job.status != JobStatus.ASSIGNED:
                raise JobServiceError("Job is not in assigned status")

            # Validate minimum time requirement
            min_time_required = job.min_time_per_segment_sec
            if completion_data.time_spent_sec < (min_time_required * 0.8):  # 80% of minimum time
                raise JobServiceError(
                    f"Time spent ({completion_data.time_spent_sec}s) is less than "
                    f"80% of minimum required time ({min_time_required}s)"
                )

            # Update assignment with completion data
            assignment.completed_at = datetime.utcnow()
            assignment.time_spent_sec = completion_data.time_spent_sec
            assignment.proof_photos = completion_data.proof_photos
            assignment.gps_log = completion_data.gps_log
            assignment.verification_status = VerificationStatus.PENDING

            # Update job status
            job.status = JobStatus.COMPLETED
            job.updated_at = datetime.utcnow()

            self.db.add(assignment)
            self.db.add(job)
            self.db.commit()
            self.db.refresh(assignment)

            return assignment

        except Exception as e:
            self.db.rollback()
            if isinstance(e, JobServiceError):
                raise
            raise JobServiceError(f"Failed to complete job: {str(e)}") from e

    def get_dropper_jobs(
        self,
        dropper_user: User,
        status_filter: Optional[JobStatus] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get jobs assigned to a dropper.

        Args:
            dropper_user: Dropper user
            status_filter: Optional status filter
            limit: Maximum number of jobs to return
            offset: Number of jobs to skip

        Returns:
            List[Dict[str, Any]]: List of assigned jobs with assignment details
        """
        try:
            # Get assignments for this dropper
            assignments_statement = select(JobAssignment).where(
                JobAssignment.dropper_id == dropper_user.id
            )
            assignments_statement = assignments_statement.order_by(desc(JobAssignment.accepted_at))
            assignments = list(self.db.exec(assignments_statement).all())

            # Get jobs for these assignments
            job_ids = [assignment.job_id for assignment in assignments]
            if not job_ids:
                return []

            jobs_statement = select(DropJob).where(  # type: ignore[attr-defined]
                DropJob.id.in_(job_ids)
            )

            if status_filter:
                jobs_statement = jobs_statement.where(DropJob.status == status_filter)

            jobs = list(self.db.exec(jobs_statement).all())

            # Combine job and assignment data
            assignment_map = {assignment.job_id: assignment for assignment in assignments}

            result = []
            for job in jobs:
                assignment = assignment_map.get(job.id)
                if assignment:
                    # Get job area
                    job_area_statement = select(JobArea).where(JobArea.job_id == job.id)
                    job_area = self.db.exec(job_area_statement).first()

                    job_dict = {
                        "job": job,
                        "assignment": assignment,
                        "job_area": job_area
                    }
                    result.append(job_dict)

            # Apply pagination
            paginated_result = result[offset:offset + limit]

            return paginated_result

        except Exception as e:
            raise JobServiceError(f"Failed to get dropper jobs: {str(e)}") from e

    def reject_job(
        self,
        job_id: UUID,
        dropper_user: User,
        rejection_reason: Optional[str] = None
    ) -> JobAssignment:
        """
        Reject a job assigned to a dropper.

        Args:
            job_id: Job ID to reject
            dropper_user: Dropper user rejecting the job
            rejection_reason: Optional reason for rejection

        Returns:
            JobAssignment: Updated job assignment with rejection details

        Raises:
            JobServiceError: If job rejection fails
        """
        try:
            # Get job assignment
            assignment_statement = select(JobAssignment).where(
                and_(
                    JobAssignment.job_id == job_id,
                    JobAssignment.dropper_id == dropper_user.id
                )
            )
            assignment = self.db.exec(assignment_statement).first()

            if not assignment:
                raise JobServiceError("Job assignment not found or not assigned to you")

            # Get job
            job_statement = select(DropJob).where(DropJob.id == job_id)
            job = self.db.exec(job_statement).first()

            if not job:
                raise JobServiceError("Job not found")

            # Validate job can be rejected (must be in assigned status)
            if job.status != JobStatus.ASSIGNED:
                raise JobServiceError(f"Job cannot be rejected. Current status: {job.status.value}")

            # Update assignment with rejection details
            assignment.verification_status = VerificationStatus.REJECTED
            assignment.rejection_reason = rejection_reason
            assignment.verified_at = datetime.utcnow()

            # Update job status to rejected
            job.status = JobStatus.REJECTED
            job.updated_at = datetime.utcnow()

            # Commit changes atomically
            self.db.add(assignment)
            self.db.add(job)
            self.db.commit()
            self.db.refresh(assignment)

            logger.info("Job %s rejected by dropper %s", job_id, dropper_user.id)
            return assignment

        except Exception as e:
            self.db.rollback()
            if isinstance(e, JobServiceError):
                raise
            raise JobServiceError(f"Failed to reject job: {str(e)}") from e


def get_job_service(db: Session) -> JobService:
    """
    Factory function to create JobService instance.

    Args:
        db: Database session

    Returns:
        JobService: Service instance
    """
    return JobService(db)




