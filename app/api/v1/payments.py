"""
Payment setup and management endpoints for DropTrack platform.
Handles Stripe customer creation and payment method management.
"""

import logging
import traceback
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlmodel import Session, select
from sqlalchemy import desc
from pydantic import BaseModel
import stripe

from app.database import get_session
from app.models import (
    User, Dropper, PaymentMethod, DropJob, JobStatus,
    Transaction, PaymentStatus, Invoice
)
from app.services.stripe_service import stripe_service
from app.api.deps import get_current_user, get_current_active_user, require_client_role
from app.config import settings
from app.schemas.job_schemas import MultiJobCheckoutRequest, MultiJobCheckoutResponse
from app.schemas.invoice_schemas import InvoiceResponse, InvoiceListResponse, InvoiceDetailResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter()
logger = logging.getLogger(__name__)

# SECURITY FIX 4: Initialize rate limiter for payment endpoints
limiter = Limiter(key_func=get_remote_address)


# Checkout Session schemas
class CheckoutSessionRequest(BaseModel):
    """Request model for creating Stripe Checkout Session."""
    job_ids: List[UUID]
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutSessionResponse(BaseModel):
    """Response model for Stripe Checkout Session."""
    session_id: str
    checkout_url: str
    expires_at: int


# Customer Portal schemas
class PortalSessionRequest(BaseModel):
    """Request model for creating Stripe Customer Portal Session."""
    return_url: str


class PortalSessionResponse(BaseModel):
    """Response model for Stripe Customer Portal Session."""
    portal_url: str


# Request/Response schemas
class PaymentSetupRequest(BaseModel):
    """Request model for payment setup."""
    return_url: Optional[str] = None


class PaymentSetupResponse(BaseModel):
    """Response model for payment setup."""
    customer_id: str
    setup_intent_client_secret: Optional[str] = None
    message: str


class ConnectAccountRequest(BaseModel):
    """Request model for Connect account setup."""
    refresh_url: str
    return_url: str


class ConnectAccountResponse(BaseModel):
    """Response model for Connect account setup."""
    account_id: str
    onboarding_url: str
    message: str


class PaymentIntentRequest(BaseModel):
    """Request model for creating payment intent."""
    job_id: str
    payment_method_id: Optional[str] = None


class PaymentIntentResponse(BaseModel):
    """Response model for payment intent creation."""
    payment_intent_id: str
    client_secret: str
    status: str
    amount: int
    currency: str


@router.post("/setup", response_model=PaymentSetupResponse)
async def setup_payment(
    request: PaymentSetupRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Set up payment for a user by creating or retrieving Stripe customer.
    
    Creates a Stripe customer if one doesn't exist and optionally
    creates a SetupIntent for saving payment methods.
    """
    try:
        # Get or create Stripe customer
        customer_id = await stripe_service.get_or_create_customer(current_user)
        
        # Update user record with customer ID if not already set
        # Note: current_user comes from get_current_user (different session), so we need to merge it
        if not current_user.stripe_customer_id:
            # Merge current_user into this session to avoid DetachedInstanceError
            merged_user = session.merge(current_user)
            merged_user.stripe_customer_id = customer_id
            session.add(merged_user)
            session.commit()
            session.refresh(merged_user)
        
        # Optionally create SetupIntent for saving payment methods
        setup_intent_client_secret = None
        if request.return_url:
            setup_intent = stripe.SetupIntent.create(
                customer=customer_id,
                payment_method_types=["card"],
                usage="off_session"
            )
            setup_intent_client_secret = setup_intent.client_secret
        
        logger.info("Payment setup completed for user %s", current_user.id)

        return PaymentSetupResponse(
            customer_id=customer_id,
            setup_intent_client_secret=setup_intent_client_secret,
            message="Payment setup completed successfully"
        )

    except Exception as e:
        logger.error("Error setting up payment for user %s: %s", current_user.id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set up payment"
        ) from e


@router.post("/connect-account", response_model=ConnectAccountResponse)
async def setup_connect_account(
    request: ConnectAccountRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Set up Stripe Connect account for a dropper to receive payouts.
    
    Creates a Stripe Connect Express account and returns onboarding URL.
    Only available to users with dropper role.
    """
    try:
        # Verify user is a dropper
        if current_user.role != "dropper":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only droppers can set up Connect accounts"
            )
        
        # Get dropper profile
        statement = select(Dropper).where(Dropper.user_id == current_user.id)
        dropper = session.exec(statement).first()
        
        if not dropper:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dropper profile not found"
            )
        
        # Create or get existing Connect account
        account_id = dropper.stripe_connect_account_id
        
        if not account_id:
            account_id = await stripe_service.create_connect_account(current_user)
            dropper.stripe_connect_account_id = account_id
            session.add(dropper)
            session.commit()
            session.refresh(dropper)
        
        # Create account link for onboarding
        onboarding_url = await stripe_service.create_account_link(
            account_id=account_id,
            refresh_url=request.refresh_url,
            return_url=request.return_url
        )
        
        logger.info("Connect account setup initiated for dropper %s", current_user.id)

        return ConnectAccountResponse(
            account_id=account_id,
            onboarding_url=onboarding_url,
            message="Connect account setup initiated"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error setting up Connect account for user %s: %s", current_user.id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set up Connect account"
        ) from e


@router.get("/customer")
async def get_customer_info(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get current user's Stripe customer information.
    
    Returns customer ID and payment setup status.
    """
    try:
        customer_info = {
            "user_id": str(current_user.id),
            "stripe_customer_id": current_user.stripe_customer_id,
            "has_payment_setup": bool(current_user.stripe_customer_id),
            "email": current_user.email,
            "name": current_user.name
        }
        
        # If user is a dropper, include Connect account info
        if current_user.role == "dropper" and current_user.dropper_profile:
            customer_info["stripe_connect_account_id"] = current_user.dropper_profile.stripe_connect_account_id
            customer_info["has_connect_account"] = bool(current_user.dropper_profile.stripe_connect_account_id)
        
        return customer_info

    except Exception as e:
        logger.error("Error getting customer info for user %s: %s", current_user.id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get customer information"
        ) from e


@router.post("/payment-intent", response_model=PaymentIntentResponse)
@limiter.limit("5/minute")  # SECURITY FIX 4: Rate limit to 5 requests per minute
async def create_payment_intent(
    request: Request,
    payment_data: PaymentIntentRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Create a PaymentIntent for a job payment.
    
    SECURITY FIX 4: Rate limited to 5 requests per minute per IP to prevent payment abuse.
    
    Creates a Stripe PaymentIntent for the specified job.
    Only the job owner (client) can create payment intents.
    """
    try:
        # Verify user is a client
        if current_user.role != "client":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only clients can create payment intents"
            )
        
        # Find the job
        try:
            job_uuid = UUID(payment_data.job_id) if isinstance(payment_data.job_id, str) else payment_data.job_id
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid job ID format"
            ) from exc
            
        statement = select(DropJob).where(DropJob.id == job_uuid)
        job = session.exec(statement).first()
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )
        
        # Verify user owns the job
        if job.client_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only create payment intents for your own jobs"
            )
        
        # Verify job is in draft status
        if job.status != JobStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only create payment intents for draft jobs"
            )
        
        # Ensure user has Stripe customer
        # Note: current_user comes from get_current_user (different session), so we need to merge it
        if not current_user.stripe_customer_id:
            customer_id = await stripe_service.get_or_create_customer(current_user)
            # Merge current_user into this session to avoid DetachedInstanceError
            merged_user = session.merge(current_user)
            merged_user.stripe_customer_id = customer_id
            session.add(merged_user)
            session.commit()
        else:
            customer_id = current_user.stripe_customer_id
        
        # Create PaymentIntent
        payment_intent_data = await stripe_service.create_payment_intent(
            job=job,
            customer_id=customer_id,
            payment_method_id=payment_data.payment_method_id
        )
        
        # Update job with payment intent ID
        job.payment_intent_id = payment_intent_data["id"]
        session.add(job)
        session.commit()
        session.refresh(job)
        
        logger.info("Created PaymentIntent for job %s", job.id)

        return PaymentIntentResponse(
            payment_intent_id=payment_intent_data["id"],
            client_secret=payment_intent_data["client_secret"],
            status=payment_intent_data["status"],
            amount=payment_intent_data["amount"],
            currency=payment_intent_data["currency"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating PaymentIntent: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment intent"
        ) from e


@router.post("/payment-intent/{payment_intent_id}/confirm")
async def confirm_payment_intent(
    request: Request,
    payment_intent_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Confirm a PaymentIntent for manual confirmation flow.
    
    Confirms the PaymentIntent and returns updated status.
    Also updates job status and creates transaction records if payment succeeds.
    """
    try:
        # Verify the payment intent belongs to user's job(s)
        # Fetch ALL jobs associated with this payment intent
        statement = select(DropJob).where(
            DropJob.payment_intent_id == payment_intent_id,
            DropJob.client_id == current_user.id
        )
        jobs = session.exec(statement).all()
        
        if not jobs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payment intent not found or not authorized"
            )
        
        # Check current status of the payment intent
        try:
            payment_intent = await stripe_service.retrieve_payment_intent(payment_intent_id)
            status_value = payment_intent.get("status")
        except Exception:
            # If retrieve fails, proceed to confirm (which might also fail, but we'll catch it)
            status_value = "unknown"

        # If already succeeded, use existing data
        if status_value == "succeeded":
            payment_intent_data = payment_intent
            logger.info("PaymentIntent %s is already succeeded, skipping confirmation", payment_intent_id)
        else:
            # Confirm the payment intent
            try:
                payment_intent_data = await stripe_service.confirm_payment_intent(payment_intent_id)
                logger.info("Confirmed PaymentIntent %s for %d jobs", payment_intent_id, len(jobs))
            except stripe.StripeError as e:
                # If error says it's already succeeded (race condition), treat as success
                if "already succeeded" in str(e) or (hasattr(e, 'code') and e.code == 'payment_intent_unexpected_state'):
                     logger.info("PaymentIntent %s already succeeded (race condition), treating as success", payment_intent_id)
                     payment_intent_data = await stripe_service.retrieve_payment_intent(payment_intent_id)
                     if payment_intent_data["status"] != "succeeded":
                         # If it's still not succeeded after retrieval, re-raise
                         raise e
                else:
                    raise e

        # If payment succeeded, update jobs and create transactions
        if payment_intent_data["status"] == "succeeded":
            now = datetime.utcnow()
            for job in jobs:
                # Only update if not already paid
                if job.status == JobStatus.DRAFT:
                    job.status = JobStatus.PENDING_APPROVAL
                    job.paid_at = now
                    session.add(job)
            
            # Create transaction records (check against duplicates if needed)
            existing_txn = session.exec(select(Transaction).where(Transaction.stripe_payment_intent_id == payment_intent_id)).first()
            if not existing_txn:
                 for job in jobs:
                    transaction = Transaction(
                        user_id=current_user.id,
                        job_id=job.id,
                        transaction_type="payment",
                        amount_pence=job.cost_total_pence,
                        currency="AUD",
                        status=PaymentStatus.COMPLETED,
                        stripe_payment_intent_id=payment_intent_id,
                        description=f"Payment for job: {job.title}",
                        transaction_metadata={
                            "job_title": job.title,
                            "household_count": job.household_count,
                            "multi_job_checkout": len(jobs) > 1,
                            "total_jobs": len(jobs)
                        },
                        processed_at=now
                    )
                    session.add(transaction)
            
            session.commit()
            logger.info("Updated %d jobs to paid status via manual confirmation", len(jobs))

        return {
            "payment_intent_id": payment_intent_data["id"],
            "status": payment_intent_data["status"],
            "amount": payment_intent_data["amount"],
            "currency": payment_intent_data["currency"]
        }

    except stripe.StripeError as e:
        session.rollback()
        logger.error(f"Stripe error confirming payment intent {payment_intent_id}: {e}")
        status_code = status.HTTP_400_BAD_REQUEST
        if hasattr(e, 'http_status') and e.http_status:
            status_code = e.http_status
        raise HTTPException(
            status_code=status_code,
            detail=f"Payment failed: {str(e)}"
        )
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error("Error confirming PaymentIntent %s: %s", payment_intent_id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to confirm payment intent: {str(e)}"
        ) from e


@router.get("/methods")
async def get_payment_methods(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get all payment methods for the current user.
    
    Returns a list of saved payment methods from Stripe and database.
    """
    try:
        # Get payment methods from database
        statement = select(PaymentMethod).where(
            PaymentMethod.user_id == current_user.id,
            PaymentMethod.is_active == True  # noqa: E712  # pylint: disable=no-member
        ).order_by(desc(PaymentMethod.is_default), desc(PaymentMethod.created_at))
        
        db_methods = session.exec(statement).all()
        
        # If user has Stripe customer ID, also fetch from Stripe
        stripe_methods = []
        if current_user.stripe_customer_id:
            try:
                # Set API key fresh from settings
                stripe.api_key = settings.stripe_secret_key
                payment_methods = stripe.PaymentMethod.list(
                    customer=current_user.stripe_customer_id,
                    type="card"
                )
                stripe_methods = payment_methods.data
            except Exception as stripe_error:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to fetch Stripe payment methods: %s", stripe_error)
        
        # Combine database and Stripe methods
        result = []
        
        # Add database methods
        for method in db_methods:
            result.append({
                "id": str(method.id),
                "stripe_payment_method_id": method.stripe_payment_method_id,
                "type": method.payment_method_type,
                "card_brand": method.card_brand,
                "card_last4": method.card_last4,
                "card_exp_month": method.card_exp_month,
                "card_exp_year": method.card_exp_year,
                "is_default": method.is_default,
                "is_active": method.is_active,
                "created_at": method.created_at.isoformat() if method.created_at else None
            })
        
        # Add Stripe methods that aren't in database
        db_stripe_ids = {m.stripe_payment_method_id for m in db_methods}
        for stripe_method in stripe_methods:
            if stripe_method.id not in db_stripe_ids:
                card = stripe_method.card if hasattr(stripe_method, 'card') else None
                result.append({
                    "id": None,  # Not in database yet
                    "stripe_payment_method_id": stripe_method.id,
                    "type": stripe_method.type,
                    "card_brand": card.brand if card else None,
                    "card_last4": card.last4 if card else None,
                    "card_exp_month": card.exp_month if card else None,
                    "card_exp_year": card.exp_year if card else None,
                    "is_default": False,
                    "is_active": True,
                    "created_at": None
                })
        
        return result

    except Exception as e:
        logger.error("Error getting payment methods for user %s: %s", current_user.id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get payment methods"
        ) from e


@router.post("/checkout", response_model=MultiJobCheckoutResponse)
@limiter.limit("5/minute")  # SECURITY FIX 4: Rate limit to 5 requests per minute
async def checkout_multiple_jobs(
    request: Request,
    checkout_data: MultiJobCheckoutRequest,
    current_user: User = Depends(require_client_role()),
    session: Session = Depends(get_session)
):
    """
    Create a single PaymentIntent for multiple jobs (cart checkout).
    
    SECURITY FIX 4: Rate limited to 5 requests per minute per IP to prevent payment abuse.
    
    Validates all jobs belong to the client and are in draft status,
    calculates total payment amount, creates Stripe PaymentIntent,
    and updates all jobs to 'paid' status on payment success.
    """
    try:
        # Validate request has jobs
        if not checkout_data.job_ids or len(checkout_data.job_ids) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one job ID is required"
            )
        
        # Fetch all jobs
        # pylint: disable=no-member
        statement = select(DropJob).where(DropJob.id.in_(checkout_data.job_ids))  # type: ignore[attr-defined]
        jobs = session.exec(statement).all()
        
        # Validate all jobs were found
        if len(jobs) != len(checkout_data.job_ids):
            found_ids = {job.id for job in jobs}
            missing_ids = [job_id for job_id in checkout_data.job_ids if job_id not in found_ids]
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Jobs not found: {missing_ids}"
            )
        
        # Validate all jobs belong to the authenticated client
        for job in jobs:
            if job.client_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Job {job.id} does not belong to you"
                )
        
        # Validate all jobs are in draft status
        non_draft_jobs = [job for job in jobs if job.status != JobStatus.DRAFT]
        if non_draft_jobs:
            non_draft_ids = [str(job.id) for job in non_draft_jobs]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"All jobs must be in draft status. Non-draft jobs: {non_draft_ids}"
            )
        
        # Calculate total payment amount
        total_amount_pence = sum(job.cost_total_pence for job in jobs)
        
        if total_amount_pence <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Total payment amount must be greater than zero"
            )
        
        # Ensure user has Stripe customer
        if not current_user.stripe_customer_id:
            customer_id = await stripe_service.get_or_create_customer(current_user)
            merged_user = session.merge(current_user)
            merged_user.stripe_customer_id = customer_id
            session.add(merged_user)
            session.commit()
        else:
            customer_id = current_user.stripe_customer_id
        
        # Create PaymentIntent with Stripe
        payment_intent_data = {
            "amount": total_amount_pence,
            "currency": "aud",
            "customer": customer_id,
            "metadata": {
                "client_id": str(current_user.id),
                "job_count": len(jobs),
                "job_ids": ",".join([str(job.id) for job in jobs])
            },
            "description": f"Payment for {len(jobs)} leaflet distribution job(s)",
            "automatic_payment_methods": {
                "enabled": True
            }
        }
        
        # If payment method provided, confirm automatically
        if checkout_data.payment_method_id:
            payment_intent_data["payment_method"] = checkout_data.payment_method_id
            payment_intent_data["confirm"] = True
            if checkout_data.return_url:
                payment_intent_data["return_url"] = checkout_data.return_url
            else:
                payment_intent_data["return_url"] = "https://droptrack.com/payment/return"
        
        # Set API key fresh from settings
        stripe.api_key = settings.stripe_secret_key
        
        # HIGH-RISK FIX 2: Add idempotency key to prevent duplicate charges
        # Use comma-separated job_ids as idempotency key for multi-job payments
        base_idempotency_key = f"pi_multi_{','.join([str(job.id) for job in jobs])}"
        idempotency_key = base_idempotency_key[:255]  # Stripe limit is 255 chars

        # Check for existing "dead" intent (canceled) to avoid returning it
        # If the jobs share a payment intent, check its status
        existing_intent_id = jobs[0].payment_intent_id if jobs else None
        if existing_intent_id:
            try:
                existing_intent = stripe.PaymentIntent.retrieve(existing_intent_id)
                if existing_intent.status == 'canceled':
                    # If canceled, we MUST create a new one. Force a new key.
                    # We can't reuse the old key because it maps to the canceled intent.
                     import time
                     idempotency_key = f"{base_idempotency_key}_retry_{int(time.time())}"[:255]
                     logger.info("Existing intent %s is canceled. Creating new one with key %s", existing_intent_id, idempotency_key)
            except Exception as e:
                logger.warning("Could not check existing intent status: %s", e)

        payment_intent = stripe.PaymentIntent.create(
            **payment_intent_data,
            idempotency_key=idempotency_key
        )

        total_amount = total_amount_pence / 100
        logger.info(
            "Created PaymentIntent %s for %d jobs, total £%.2f",
            payment_intent.id, len(jobs), total_amount
        )
        
        # Update all jobs with payment intent ID (but don't mark as paid yet)
        # Jobs will be marked as paid when webhook confirms payment success
        for job in jobs:
            job.payment_intent_id = payment_intent.id
            session.add(job)
        
        # If payment is already succeeded (auto-confirm), update jobs to paid
        if payment_intent.status == "succeeded":
            now = datetime.utcnow()
            for job in jobs:
                job.status = JobStatus.PENDING_APPROVAL
                job.paid_at = now
                session.add(job)
            
            # Create transaction records
            for job in jobs:
                transaction = Transaction(
                    user_id=current_user.id,
                    job_id=job.id,
                    transaction_type="payment",
                    amount_pence=job.cost_total_pence,
                    currency="AUD",
                    status=PaymentStatus.COMPLETED,
                    stripe_payment_intent_id=payment_intent.id,
                    description=f"Payment for job: {job.title}",
                    transaction_metadata={
                        "job_title": job.title,
                        "household_count": job.household_count,
                        "multi_job_checkout": True,
                        "total_jobs": len(jobs)
                    },
                    processed_at=now
                )
                session.add(transaction)
            
            logger.info(
                "Updated %d jobs to paid status and created transaction records",
                len(jobs)
            )

        session.commit()
        
        return MultiJobCheckoutResponse(
            payment_intent_id=payment_intent.id,
            client_secret=payment_intent.client_secret,
            status=payment_intent.status,
            total_amount_pence=total_amount_pence,
            currency="AUD",
            job_count=len(jobs),
            job_ids=[str(job.id) for job in jobs]  # Convert UUIDs to strings for JSON serialization
        )
        
    except HTTPException:
        session.rollback()
        raise
    except stripe.StripeError as e:
        session.rollback()
        # Enhanced error logging for debugging configuration issues
        error_code = e.code if hasattr(e, 'code') else 'unknown'
        error_type = type(e).__name__
        logger.error("Stripe error during checkout (Type: %s, Code: %s): %s", error_type, error_code, str(e))
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Payment processing failed: {str(e)}"
        ) from e
    except Exception as e:
        session.rollback()
        logger.error("Error during multi-job checkout: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process checkout"
        ) from e



@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
@limiter.limit("5/minute")  # SECURITY FIX 4: Rate limit to 5 requests per minute
async def create_checkout_session(
    request: Request,
    session_data: CheckoutSessionRequest,
    current_user: User = Depends(require_client_role()),
    session: Session = Depends(get_session)
):
    """
    Create a Stripe Checkout Session for multiple jobs.
    
    SECURITY FIX 4: Rate limited to 5 requests per minute per IP to prevent payment abuse.
    
    Creates a hosted Stripe Checkout page for secure payment processing.
    Validates all jobs belong to the client and are in draft status.
    """
    try:
        # Validate request has jobs
        if not session_data.job_ids or len(session_data.job_ids) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one job ID is required"
            )
        
        # Fetch all jobs
        # pylint: disable=no-member
        statement = select(DropJob).where(DropJob.id.in_(session_data.job_ids))  # type: ignore[attr-defined]
        jobs = session.exec(statement).all()

        # Validate all jobs were found
        if len(jobs) != len(session_data.job_ids):
            found_ids = {job.id for job in jobs}
            missing_ids = [str(job_id) for job_id in session_data.job_ids if job_id not in found_ids]
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Jobs not found: {missing_ids}"
            )
        
        # Validate all jobs belong to the authenticated client
        for job in jobs:
            if job.client_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Job {job.id} does not belong to you"
                )
        
        # Validate all jobs are in draft status
        non_draft_jobs = [job for job in jobs if job.status != JobStatus.DRAFT]
        if non_draft_jobs:
            non_draft_ids = [str(job.id) for job in non_draft_jobs]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"All jobs must be in draft status. Non-draft jobs: {non_draft_ids}"
            )
        
        # Set default URLs if not provided
        base_url = settings.cors_origins_list[0] if settings.cors_origins_list else ""
        success_url = (
            session_data.success_url or
            f"{base_url}/client/payment/success?session_id={{CHECKOUT_SESSION_ID}}"
        )
        cancel_url = session_data.cancel_url or f"{base_url}/client/payment/cancel"
        
        # Create Stripe Checkout Session
        checkout_data = await stripe_service.create_checkout_session(
            jobs=jobs,
            user=current_user,
            success_url=success_url,
            cancel_url=cancel_url
        )
        
        logger.info(
            "Created Checkout Session %s for user %s with %d jobs",
            checkout_data['session_id'], current_user.id, len(jobs)
        )

        # Get session expiration time (Stripe sessions expire after 24 hours)
        # Set API key fresh from settings
        stripe.api_key = settings.stripe_secret_key
        checkout_session = stripe.checkout.Session.retrieve(checkout_data['session_id'])
        
        return CheckoutSessionResponse(
            session_id=checkout_data['session_id'],
            checkout_url=checkout_data['checkout_url'],
            expires_at=checkout_session.expires_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating Checkout Session: %s", str(e))
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create checkout session: {str(e)}"
        ) from e


@router.post("/create-portal-session", response_model=PortalSessionResponse)
async def create_portal_session(
    request: PortalSessionRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Create a Stripe Customer Portal session for payment method management.
    
    Creates a hosted Stripe Customer Portal where users can manage their
    payment methods, view payment history, and update billing information.
    """
    try:
        # Ensure user has Stripe customer ID
        if not current_user.stripe_customer_id:
            customer_id = await stripe_service.get_or_create_customer(current_user)
            merged_user = session.merge(current_user)
            merged_user.stripe_customer_id = customer_id
            session.add(merged_user)
            session.commit()
        
        # Create Customer Portal Session
        portal_data = await stripe_service.create_customer_portal_session(
            user=current_user,
            return_url=request.return_url
        )
        
        logger.info("Created Customer Portal Session for user %s", current_user.id)

        return PortalSessionResponse(
            portal_url=portal_data['portal_url']
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating Customer Portal Session: %s", str(e))
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create customer portal session: {str(e)}"
        ) from e



@router.get("/invoices", response_model=InvoiceListResponse)
async def get_invoices(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=100, description="Maximum 100 invoices per request"),  # HIGH-RISK FIX 3: Add max limit
    offset: int = Query(0, ge=0, description="Number of invoices to skip")
):
    """
    Get invoice history for the authenticated user.
    
    Returns a paginated list of invoices with job titles included.
    Supports pagination through limit and offset query parameters.
    """
    try:
        # Query invoices for the authenticated user
        statement = (
            select(Invoice)
            .where(Invoice.user_id == current_user.id)
            .order_by(desc(Invoice.created_at))
            .limit(limit)
            .offset(offset)
        )
        invoices = session.exec(statement).all()
        
        # Self-healing: Check for missing PDF URLs and fetch from Stripe if needed
        # This handles race conditions where webhook processed before PDF was ready
        invoices_to_update = [inv for inv in invoices if not inv.invoice_pdf_url and inv.stripe_session_id]
        
        if invoices_to_update:
            try:
                # Set API key fresh from settings
                stripe.api_key = settings.stripe_secret_key
                updated_count = 0
                
                for invoice in invoices_to_update:
                    try:
                        # Retrieve session to get invoice ID
                        # We need to retrieve the session because we don't store invoice ID directly
                        session_obj = stripe.checkout.Session.retrieve(invoice.stripe_session_id)
                        
                        if session_obj.invoice:
                            # Retrieve the actual invoice to get the PDF URL
                            stripe_inv = stripe.Invoice.retrieve(session_obj.invoice)
                            pdf_url = stripe_inv.invoice_pdf or stripe_inv.hosted_invoice_url
                            
                            if pdf_url:
                                invoice.invoice_pdf_url = pdf_url
                                session.add(invoice)
                                updated_count += 1
                                logger.info(f"Self-healed missing PDF URL for invoice {invoice.id}")
                    except Exception as loop_e:
                        # Log but continue to next invoice so one failure doesn't break the whole list
                        logger.warning(f"Failed to fetch PDF for invoice {invoice.id}: {loop_e}")
                        continue
                
                if updated_count > 0:
                    session.commit()
                    # No need to refresh as we updated the objects in memory
                    
            except Exception as healing_error:
                logger.error(f"Error in invoice URL self-healing: {healing_error}")
                # Don't fail the request, just continue with what we have

        
        # Get total count for pagination
        count_statement = select(Invoice).where(Invoice.user_id == current_user.id)
        total_count = len(session.exec(count_statement).all())
        
        # Build response with job titles
        invoice_responses = []
        for invoice in invoices:
            # Fetch job titles for this invoice
            job_titles = []
            if invoice.job_ids:
                # pylint: disable=no-member
                job_statement = select(DropJob).where(DropJob.id.in_(invoice.job_ids))  # type: ignore[attr-defined]
                jobs = session.exec(job_statement).all()
                job_titles = [job.title for job in jobs]
            
            invoice_response = InvoiceResponse(
                id=invoice.id,
                user_id=invoice.user_id,
                stripe_session_id=invoice.stripe_session_id,
                stripe_payment_intent_id=invoice.stripe_payment_intent_id,
                amount_total_pence=invoice.amount_total_pence,
                currency=invoice.currency,
                status=invoice.status,
                job_ids=invoice.job_ids,
                job_titles=job_titles,
                invoice_metadata=invoice.invoice_metadata,
                created_at=invoice.created_at,
                updated_at=invoice.updated_at
            )
            invoice_responses.append(invoice_response)
        
        logger.info(
            "Retrieved %d invoices for user %s",
            len(invoice_responses), current_user.id
        )

        return InvoiceListResponse(
            invoices=invoice_responses,
            total_count=total_count
        )

    except Exception as e:
        logger.error("Error retrieving invoices for user %s: %s", current_user.id, str(e))
        logger.error(traceback.format_exc())  # pylint: disable=used-before-assignment
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve invoices"
        ) from e


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetailResponse)
async def get_invoice_detail(
    invoice_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Get detailed invoice information including associated job details.
    
    Returns detailed invoice information with full job details.
    Verifies that the invoice belongs to the authenticated user.
    """
    try:
        # Fetch the invoice
        statement = select(Invoice).where(Invoice.id == invoice_id)
        invoice = session.exec(statement).first()
        
        if not invoice:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invoice not found"
            )
        
        # Verify invoice belongs to authenticated user
        if invoice.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this invoice"
            )
        
        # Fetch detailed job information
        jobs_detail = []
        if invoice.job_ids:
            # pylint: disable=no-member
            job_statement = (
                select(DropJob).where(DropJob.id.in_(invoice.job_ids))  # type: ignore[attr-defined]
            )
            jobs = session.exec(job_statement).all()
            
            for job in jobs:
                jobs_detail.append({
                    "id": str(job.id),
                    "title": job.title,
                    "description": job.description,
                    "household_count": job.household_count,
                    "cost_total_pence": job.cost_total_pence,
                    "scheduled_date": job.scheduled_date.isoformat() if job.scheduled_date else None,
                    "status": job.status.value if hasattr(job.status, 'value') else job.status,
                    "paid_at": job.paid_at.isoformat() if job.paid_at else None
                })
        
        logger.info("Retrieved invoice detail %s for user %s", invoice_id, current_user.id)

        return InvoiceDetailResponse(
            id=invoice.id,
            user_id=invoice.user_id,
            stripe_session_id=invoice.stripe_session_id,
            stripe_payment_intent_id=invoice.stripe_payment_intent_id,
            amount_total_pence=invoice.amount_total_pence,
            currency=invoice.currency,
            status=invoice.status,
            job_ids=invoice.job_ids,
            jobs=jobs_detail,
            invoice_metadata=invoice.invoice_metadata,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error retrieving invoice detail %s: %s", invoice_id, str(e))
        logger.error(traceback.format_exc())  # pylint: disable=used-before-assignment
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve invoice detail"
        ) from e
