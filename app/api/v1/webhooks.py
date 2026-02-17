"""
Stripe webhook endpoints for DropTrack platform.
Handles payment events and updates job status accordingly.
"""

import logging
from typing import Dict, Any
from fastapi import APIRouter, Request, HTTPException, Depends, status
from sqlmodel import Session, select
from uuid import UUID

from app.database import get_session
from app.models import DropJob, Transaction, JobStatus, PaymentStatus, Invoice
from app.services.stripe_service import stripe_service
from datetime import datetime

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Handle Stripe webhook events.
    
    Processes payment_intent.succeeded events to update job status
    and create transaction records.
    """
    try:
        # Get raw payload and signature
        payload = await request.body()
        signature = request.headers.get("stripe-signature")
        
        if not signature:
            logger.error("Missing Stripe signature header")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing Stripe signature"
            )
        
        # Verify webhook signature and construct event
        try:
            event = await stripe_service.construct_webhook_event(payload, signature)
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid webhook signature"
            )
        
        # Handle different event types
        event_type = event["type"]
        logger.info(f"Received Stripe webhook event: {event_type}")
        
        if event_type == "checkout.session.completed":
            await handle_checkout_session_completed(event["data"]["object"], session)
        elif event_type == "payment_intent.succeeded":
            await handle_payment_intent_succeeded(event["data"]["object"], session)
        elif event_type == "payment_intent.payment_failed":
            await handle_payment_intent_failed(event["data"]["object"], session)
        elif event_type == "transfer.created":
            await handle_transfer_created(event["data"]["object"], session)
        elif event_type == "transfer.paid":
            await handle_transfer_paid(event["data"]["object"], session)
        else:
            logger.info(f"Unhandled webhook event type: {event_type}")
        
        return {"status": "success"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error processing webhook"
        )


async def handle_checkout_session_completed(
    checkout_session: Dict[str, Any],
    session: Session
):
    """
    Handle checkout.session.completed webhook event.
    
    Creates invoice record and updates all jobs to PAID status atomically.
    Implements idempotency to handle duplicate webhook events.
    """
    try:
        session_id = checkout_session["id"]
        payment_intent_id = checkout_session.get("payment_intent")
        amount_total = checkout_session["amount_total"]
        currency = checkout_session.get("currency", "AUD")
        metadata = checkout_session.get("metadata", {})
        
        logger.info(f"Processing checkout.session.completed for session {session_id}")
        
        # Extract user_id and job_ids from metadata
        user_id_str = metadata.get("user_id")
        job_ids_str = metadata.get("job_ids")
        
        if not user_id_str or not job_ids_str:
            logger.error(f"Missing user_id or job_ids in session {session_id} metadata")
            return
        
        # Idempotency check: Skip if invoice already exists
        from uuid import UUID
        existing_invoice = session.exec(
            select(Invoice).where(Invoice.stripe_session_id == session_id)
        ).first()
        
        if existing_invoice:
            logger.info(f"Invoice already exists for session {session_id}, skipping (idempotent)")
            return
        
        # Parse user_id
        try:
            user_uuid = UUID(user_id_str)
        except ValueError:
            logger.error(f"Invalid user_id format in session {session_id}: {user_id_str}")
            return
        
        # Parse comma-separated job_ids into UUID list
        job_id_strings = job_ids_str.split(",")
        job_uuids = []
        
        for job_id_str in job_id_strings:
            try:
                job_uuids.append(UUID(job_id_str.strip()))
            except ValueError:
                logger.error(f"Invalid job_id format in session {session_id}: {job_id_str}")
                continue
        
        if not job_uuids:
            logger.error(f"No valid job IDs found in session {session_id}")
            return
        
        # Begin database transaction
        try:
            # Fetch all jobs
            statement = select(DropJob).where(DropJob.id.in_(job_uuids))
            jobs = session.exec(statement).all()
            
            if len(jobs) != len(job_uuids):
                found_ids = {job.id for job in jobs}
                missing_ids = [job_id for job_id in job_uuids if job_id not in found_ids]
                logger.error(f"Some jobs not found for session {session_id}: {missing_ids}")
                # Continue with found jobs
            
            if not jobs:
                logger.error(f"No jobs found for session {session_id}")
                return
            
            # Fetch Invoice to get PDF URL
            invoice_pdf_url = None
            invoice_id = checkout_session.get("invoice")
            if invoice_id:
                try:
                    # Retrieve invoice from Stripe to get PDF URL
                    # We can't use stripe_service here easily without async wrapper or duplicate logic
                    # So we use stripe library directly, ensuring API key is set
                    import stripe
                    from app.config import settings
                    stripe.api_key = settings.stripe_secret_key
                    
                    stripe_invoice = stripe.Invoice.retrieve(invoice_id)
                    invoice_pdf_url = stripe_invoice.get("invoice_pdf") or stripe_invoice.get("hosted_invoice_url")
                    logger.info(f"Retrieved invoice PDF URL for session {session_id}")
                except Exception as inv_err:
                    logger.warning(f"Failed to retrieve invoice PDF for session {session_id}: {inv_err}")
            
            # Create Invoice record
            now = datetime.utcnow()
            invoice = Invoice(
                user_id=user_uuid,
                stripe_session_id=session_id,
                stripe_payment_intent_id=payment_intent_id,
                amount_total_pence=amount_total,
                currency=currency,
                status="paid",
                job_ids=[str(jid) for jid in job_uuids],  # Fix: Convert UUIDs to strings for JSON serialization
                invoice_pdf_url=invoice_pdf_url,
                invoice_metadata={
                    "job_count": len(jobs),
                    "household_count": sum(job.household_count for job in jobs),
                    "checkout_session_metadata": metadata,
                    "job_ids": [str(jid) for jid in job_uuids]
                },
                created_at=now
            )
            session.add(invoice)
            
            # Update all jobs to PAID status with paid_at timestamp
            for job in jobs:
                # Calculate platform fee and dropper payout for each job
                platform_fee = await stripe_service.calculate_platform_fee(job.cost_total_pence)
                dropper_payout = await stripe_service.calculate_dropper_payout(job.cost_total_pence)
                
                # Update job status and payment info (skip PENDING_APPROVAL)
                job.status = JobStatus.PAID
                job.payment_intent_id = payment_intent_id
                job.paid_at = now
                job.platform_fee_pence = platform_fee
                job.dropper_payout_pence = dropper_payout
                job.updated_at = now
                
                # Create Transaction record for each job
                transaction = Transaction(
                    user_id=user_uuid,
                    job_id=job.id,
                    transaction_type="payment",
                    amount_pence=job.cost_total_pence,
                    currency=currency,
                    status=PaymentStatus.COMPLETED,
                    stripe_payment_intent_id=payment_intent_id,
                    description=f"Payment for job: {job.title}",
                    transaction_metadata={
                        "job_title": job.title,
                        "household_count": job.household_count,
                        "platform_fee_pence": platform_fee,
                        "dropper_payout_pence": dropper_payout,
                        "checkout_session": True,
                        "stripe_session_id": session_id,
                        "total_jobs": len(jobs),
                        "total_amount_pence": amount_total
                    },
                    processed_at=now
                )
                
                session.add(job)
                session.add(transaction)
            
            # Commit transaction atomically
            session.commit()
            
            # Refresh all objects
            session.refresh(invoice)
            for job in jobs:
                session.refresh(job)
            
            logger.info(
                f"Successfully processed checkout session {session_id}: "
                f"created invoice {invoice.id}, updated {len(jobs)} jobs to PAID, "
                f"total amount: £{amount_total/100:.2f}"
            )
            
        except Exception as db_error:
            # Rollback transaction on any error
            session.rollback()
            logger.error(f"Database error processing checkout session {session_id}: {str(db_error)}")
            # Re-raise to trigger webhook retry
            raise
        
    except Exception as e:
        logger.error(f"Error handling checkout.session.completed: {str(e)}")
        session.rollback()
        raise


async def handle_payment_intent_succeeded(
    payment_intent: Dict[str, Any],
    session: Session
):
    """
    Handle successful payment intent webhook.
    
    Updates job status to 'paid' and creates transaction record.
    Supports both single-job and multi-job payments.
    """
    try:
        payment_intent_id = payment_intent["id"]
        amount = payment_intent["amount"]
        metadata = payment_intent.get("metadata", {})
        
        logger.info(f"Processing payment_intent.succeeded for {payment_intent_id}")
        
        # Check if this is a multi-job payment
        job_ids_str = metadata.get("job_ids")
        
        if job_ids_str:
            # Multi-job payment
            await handle_multi_job_payment_succeeded(
                payment_intent_id, amount, job_ids_str, metadata, session
            )
        else:
            # Single job payment (legacy)
            job_id = metadata.get("job_id")
            if not job_id:
                logger.error(f"No job_id or job_ids in PaymentIntent {payment_intent_id} metadata")
                return
            
            await handle_single_job_payment_succeeded(
                payment_intent_id, amount, job_id, metadata, session
            )
        
    except Exception as e:
        logger.error(f"Error handling payment_intent.succeeded: {str(e)}")
        session.rollback()
        raise


async def handle_single_job_payment_succeeded(
    payment_intent_id: str,
    amount: int,
    job_id: str,
    metadata: Dict[str, Any],
    session: Session
):
    """Handle successful payment for a single job."""
    try:
        # Find the job
        from uuid import UUID
        try:
            job_uuid = UUID(job_id) if isinstance(job_id, str) else job_id
        except ValueError:
            logger.error(f"Invalid job_id format: {job_id}")
            return
            
        statement = select(DropJob).where(DropJob.id == job_uuid)
        job = session.exec(statement).first()
        
        if not job:
            logger.error(f"Job {job_id} not found for PaymentIntent {payment_intent_id}")
            return
        
        # Calculate platform fee and dropper payout first
        platform_fee = await stripe_service.calculate_platform_fee(amount)
        dropper_payout = await stripe_service.calculate_dropper_payout(amount)
        
        # Update job status and payment info (skip PENDING_APPROVAL)
        job.status = JobStatus.PAID
        job.payment_intent_id = payment_intent_id
        job.paid_at = datetime.utcnow()
        job.platform_fee_pence = platform_fee
        job.dropper_payout_pence = dropper_payout
        job.updated_at = datetime.utcnow()
        
        # Create transaction record
        transaction = Transaction(
            user_id=job.client_id,
            job_id=job.id,
            transaction_type="payment",
            amount_pence=amount,
            currency="AUD",
            status=PaymentStatus.COMPLETED,
            stripe_payment_intent_id=payment_intent_id,
            description=f"Payment for leaflet distribution job: {job.title}",
            transaction_metadata={
                "job_title": job.title,
                "household_count": job.household_count,
                "platform_fee_pence": platform_fee,
                "dropper_payout_pence": dropper_payout
            },
            processed_at=datetime.utcnow()
        )
        
        session.add(job)
        session.add(transaction)
        session.commit()
        session.refresh(job)
        session.refresh(transaction)
        
        logger.info(f"Successfully processed payment for job {job_id}, amount: £{amount/100:.2f}")
        
    except Exception as update_error:
        # Rollback: If job update fails, rollback all changes
        session.rollback()
        logger.error(f"Failed to update job {job_id} after payment success: {update_error}")
        # Re-raise to trigger webhook retry
        raise


async def handle_multi_job_payment_succeeded(
    payment_intent_id: str,
    total_amount: int,
    job_ids_str: str,
    metadata: Dict[str, Any],
    session: Session
):
    """Handle successful payment for multiple jobs (cart checkout)."""
    try:
        from uuid import UUID
        
        # Parse job IDs from comma-separated string
        job_id_strings = job_ids_str.split(",")
        job_uuids = []
        
        for job_id_str in job_id_strings:
            try:
                job_uuids.append(UUID(job_id_str.strip()))
            except ValueError:
                logger.error(f"Invalid job_id format in multi-job payment: {job_id_str}")
                continue
        
        if not job_uuids:
            logger.error(f"No valid job IDs found in PaymentIntent {payment_intent_id}")
            return
        
        # Fetch all jobs
        statement = select(DropJob).where(DropJob.id.in_(job_uuids))
        jobs = session.exec(statement).all()
        
        if len(jobs) != len(job_uuids):
            found_ids = {job.id for job in jobs}
            missing_ids = [job_id for job_id in job_uuids if job_id not in found_ids]
            logger.error(f"Some jobs not found for PaymentIntent {payment_intent_id}: {missing_ids}")
            # Continue with found jobs
        
        if not jobs:
            logger.error(f"No jobs found for PaymentIntent {payment_intent_id}")
            return
        
        # Update all jobs atomically
        now = datetime.utcnow()
        
        for job in jobs:
            # Calculate platform fee and dropper payout for each job
            platform_fee = await stripe_service.calculate_platform_fee(job.cost_total_pence)
            dropper_payout = await stripe_service.calculate_dropper_payout(job.cost_total_pence)
            
            # Update job status and payment info (skip PENDING_APPROVAL)
            job.status = JobStatus.PAID
            job.payment_intent_id = payment_intent_id
            job.paid_at = now
            job.platform_fee_pence = platform_fee
            job.dropper_payout_pence = dropper_payout
            job.updated_at = now
            
            # Create transaction record for each job
            transaction = Transaction(
                user_id=job.client_id,
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
                    "platform_fee_pence": platform_fee,
                    "dropper_payout_pence": dropper_payout,
                    "multi_job_checkout": True,
                    "total_jobs": len(jobs),
                    "total_amount_pence": total_amount
                },
                processed_at=now
            )
            
            session.add(job)
            session.add(transaction)
        
        session.commit()
        
        # Refresh all jobs
        for job in jobs:
            session.refresh(job)
        
        logger.info(f"Successfully processed multi-job payment for {len(jobs)} jobs, total: £{total_amount/100:.2f}")
        
    except Exception as update_error:
        # Rollback: If any job update fails, rollback all changes
        session.rollback()
        logger.error(f"Failed to update jobs after multi-job payment success: {update_error}")
        # Re-raise to trigger webhook retry
        raise


async def handle_payment_intent_failed(
    payment_intent: Dict[str, Any],
    session: Session
):
    """
    Handle failed payment intent webhook.
    
    Creates a failed transaction record for tracking.
    Supports both single-job and multi-job payments.
    """
    try:
        payment_intent_id = payment_intent["id"]
        amount = payment_intent["amount"]
        metadata = payment_intent.get("metadata", {})
        
        logger.info(f"Processing payment_intent.payment_failed for {payment_intent_id}")
        
        # Get failure reason from last payment error
        failure_reason = "Payment failed"
        if payment_intent.get("last_payment_error"):
            failure_reason = payment_intent["last_payment_error"].get("message", failure_reason)
        
        # Check if this is a multi-job payment
        job_ids_str = metadata.get("job_ids")
        
        if job_ids_str:
            # Multi-job payment failure
            from uuid import UUID
            
            # Parse job IDs from comma-separated string
            job_id_strings = job_ids_str.split(",")
            job_uuids = []
            
            for job_id_str in job_id_strings:
                try:
                    job_uuids.append(UUID(job_id_str.strip()))
                except ValueError:
                    logger.error(f"Invalid job_id format in multi-job payment: {job_id_str}")
                    continue
            
            if job_uuids:
                # Fetch all jobs
                statement = select(DropJob).where(DropJob.id.in_(job_uuids))
                jobs = session.exec(statement).all()
                
                # Reset all jobs to DRAFT status
                for job in jobs:
                    if job.status != JobStatus.DRAFT:
                        logger.warning(f"Job {job.id} status was {job.status}, resetting to DRAFT after payment failure")
                        job.status = JobStatus.DRAFT
                        job.payment_intent_id = None
                        session.add(job)
                    
                    # Create failed transaction record for each job
                    transaction = Transaction(
                        user_id=job.client_id,
                        job_id=job.id,
                        transaction_type="payment",
                        amount_pence=job.cost_total_pence,
                        currency="AUD",
                        status=PaymentStatus.FAILED,
                        stripe_payment_intent_id=payment_intent_id,
                        description=f"Failed payment for job: {job.title}",
                        failure_reason=failure_reason,
                        transaction_metadata={
                            "job_title": job.title,
                            "household_count": job.household_count,
                            "multi_job_checkout": True,
                            "total_jobs": len(jobs),
                            "error": payment_intent.get("last_payment_error")
                        },
                        processed_at=datetime.utcnow()
                    )
                    session.add(transaction)
                
                session.commit()
                logger.info(f"Recorded failed multi-job payment for {len(jobs)} jobs: {failure_reason}")
        else:
            # Single job payment failure (legacy)
            job_id = metadata.get("job_id")
            if not job_id:
                logger.error(f"No job_id or job_ids in PaymentIntent {payment_intent_id} metadata")
                return
            
            # Find the job
            from uuid import UUID
            try:
                job_uuid = UUID(job_id) if isinstance(job_id, str) else job_id
            except ValueError:
                logger.error(f"Invalid job_id format: {job_id}")
                return
                
            statement = select(DropJob).where(DropJob.id == job_uuid)
            job = session.exec(statement).first()
            
            if not job:
                logger.error(f"Job {job_id} not found for PaymentIntent {payment_intent_id}")
                return
            
            # Ensure job status remains DRAFT if payment failed
            if job.status != JobStatus.DRAFT:
                logger.warning(f"Job {job_id} status was {job.status}, resetting to DRAFT after payment failure")
                job.status = JobStatus.DRAFT
                job.payment_intent_id = None
                session.add(job)
            
            # Create failed transaction record
            transaction = Transaction(
                user_id=job.client_id,
                job_id=job.id,
                transaction_type="payment",
                amount_pence=amount,
                currency="AUD",
                status=PaymentStatus.FAILED,
                stripe_payment_intent_id=payment_intent_id,
                description=f"Failed payment for leaflet distribution job: {job.title}",
                failure_reason=failure_reason,
                transaction_metadata={
                    "job_title": job.title,
                    "household_count": job.household_count,
                    "error": payment_intent.get("last_payment_error")
                },
                processed_at=datetime.utcnow()
            )
            
            session.add(transaction)
            session.commit()
            session.refresh(transaction)
            
            logger.info(f"Recorded failed payment for job {job_id}: {failure_reason}")
        
    except Exception as e:
        logger.error(f"Error handling payment_intent.payment_failed: {str(e)}")
        session.rollback()
        raise


async def handle_transfer_created(
    transfer: Dict[str, Any],
    session: Session
):
    """
    Handle transfer created webhook (payout to dropper).
    
    Creates a payout transaction record.
    """
    try:
        transfer_id = transfer["id"]
        amount = transfer["amount"]
        destination = transfer["destination"]
        metadata = transfer.get("metadata", {})
        job_id = metadata.get("job_id")
        
        logger.info(f"Processing transfer.created for {transfer_id}")
        
        if not job_id:
            logger.info(f"No job_id in transfer {transfer_id} metadata, skipping")
            return
        
        # Find the job
        from uuid import UUID
        try:
            job_uuid = UUID(job_id) if isinstance(job_id, str) else job_id
        except ValueError:
            logger.error(f"Invalid job_id format: {job_id}")
            return
            
        statement = select(DropJob).where(DropJob.id == job_uuid)
        job = session.exec(statement).first()
        
        if not job:
            logger.error(f"Job {job_id} not found for transfer {transfer_id}")
            return
        
        # Find the dropper from job assignment
        if not job.assignment:
            logger.error(f"No assignment found for job {job_id}")
            return
        
        # Create payout transaction record
        transaction = Transaction(
            user_id=job.assignment.dropper_id,
            job_id=job.id,
            transaction_type="payout",
            amount_pence=amount,
            currency="AUD",
            status=PaymentStatus.PENDING,
            stripe_transfer_id=transfer_id,
            description=f"Payout for completed job: {job.title}",
            transaction_metadata={
                "job_title": job.title,
                "household_count": job.household_count,
                "connect_account": destination
            },
            processed_at=datetime.utcnow()
        )
        
        session.add(transaction)
        session.commit()
        session.refresh(transaction)
        
        logger.info(f"Created payout transaction for job {job_id}, amount: £{amount/100:.2f}")
        
    except Exception as e:
        logger.error(f"Error handling transfer.created: {str(e)}")
        session.rollback()
        raise


async def handle_transfer_paid(
    transfer: Dict[str, Any],
    session: Session
):
    """
    Handle transfer paid webhook (payout completed).
    
    Updates payout transaction status to completed.
    """
    try:
        transfer_id = transfer["id"]
        
        logger.info(f"Processing transfer.paid for {transfer_id}")
        
        # Find the transaction record
        statement = select(Transaction).where(
            Transaction.stripe_transfer_id == transfer_id
        )
        transaction = session.exec(statement).first()
        
        if not transaction:
            logger.error(f"Transaction not found for transfer {transfer_id}")
            return
        
        # Update transaction status
        transaction.status = PaymentStatus.COMPLETED
        transaction.updated_at = datetime.utcnow()
        
        session.commit()
        session.refresh(transaction)
        
        logger.info(f"Updated payout transaction {transaction.id} to completed")
        
    except Exception as e:
        logger.error(f"Error handling transfer.paid: {str(e)}")
        session.rollback()
        raise