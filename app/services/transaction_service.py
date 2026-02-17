"""
Transaction service for managing financial transactions and payouts.
Handles transaction creation, tracking, and payout processing with retry logic.
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from uuid import UUID
from sqlmodel import Session, select
from sqlalchemy import func
from decimal import Decimal

from app.models import (
    Transaction, User, DropJob, JobAssignment, 
    PaymentStatus, VerificationStatus
)
from app.services.stripe_service import stripe_service

logger = logging.getLogger(__name__)


class TransactionService:
    """Service for managing transactions and payouts."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_payout_transaction(
        self,
        dropper: User,
        job: DropJob,
        amount_pence: int,
        platform_fee_pence: int,
        verified_by: UUID
    ) -> Transaction:
        """
        Create a payout transaction record.
        
        Args:
            dropper: Dropper user receiving the payout
            job: Job associated with the payout
            amount_pence: Payout amount in pence
            platform_fee_pence: Platform fee in pence
            verified_by: Admin user who approved the job
            
        Returns:
            Created Transaction object
        """
        transaction = Transaction(
            user_id=dropper.id,
            job_id=job.id,
            transaction_type="payout",
            amount_pence=amount_pence,
            currency="AUD",
            status=PaymentStatus.PENDING,
            description=f"Payout for completed job: {job.title}",
            transaction_metadata={
                "job_id": str(job.id),
                "job_title": job.title,
                "household_count": job.household_count,
                "platform_fee_pence": platform_fee_pence,
                "verified_by": str(verified_by),
                "payout_calculation": {
                    "total_job_cost_pence": job.cost_total_pence,
                    "platform_fee_pence": platform_fee_pence,
                    "dropper_payout_pence": amount_pence
                }
            }
        )
        
        self.db.add(transaction)
        self.db.flush()  # Get the transaction ID
        
        logger.info(f"Created payout transaction {transaction.id} for dropper {dropper.id}, job {job.id}")
        return transaction
    
    async def process_payout(
        self,
        transaction: Transaction,
        connect_account_id: str,
        job: DropJob
    ) -> bool:
        """
        Process a payout transaction through Stripe.
        
        Args:
            transaction: Transaction to process
            connect_account_id: Stripe Connect account ID
            job: Associated job
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create Stripe payout
            payout_result = await stripe_service.create_payout(
                connect_account_id=connect_account_id,
                amount_pence=transaction.amount_pence,
                job=job,
                description=transaction.description
            )
            
            # Update transaction with success
            transaction.stripe_transfer_id = payout_result["id"]
            transaction.status = PaymentStatus.COMPLETED
            transaction.processed_at = datetime.utcnow()
            
            # Update metadata with Stripe response
            if transaction.transaction_metadata:
                transaction.transaction_metadata["stripe_transfer"] = payout_result
            else:
                transaction.transaction_metadata = {"stripe_transfer": payout_result}
            
            self.db.add(transaction)
            self.db.commit()
            
            logger.info(f"Successfully processed payout transaction {transaction.id}")
            return True
            
        except Exception as e:
            # Update transaction with failure
            transaction.status = PaymentStatus.FAILED
            transaction.failure_reason = str(e)
            
            # Update metadata with error details
            if transaction.transaction_metadata:
                transaction.transaction_metadata["error"] = {
                    "message": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
            else:
                transaction.transaction_metadata = {
                    "error": {
                        "message": str(e),
                        "timestamp": datetime.utcnow().isoformat()
                    }
                }
            
            self.db.add(transaction)
            self.db.commit()
            
            logger.error(f"Failed to process payout transaction {transaction.id}: {str(e)}")
            return False
    
    def get_failed_payouts(self, hours_ago: int = 24) -> List[Transaction]:
        """
        Get failed payout transactions for retry processing.
        
        Args:
            hours_ago: Look for failures within this many hours
            
        Returns:
            List of failed payout transactions
        """
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_ago)
        
        query = (
            select(Transaction)
            .where(Transaction.transaction_type == "payout")
            .where(Transaction.status == PaymentStatus.FAILED)
            .where(Transaction.created_at >= cutoff_time)
            .order_by(Transaction.created_at.desc())
        )
        
        return list(self.db.exec(query).all())
    
    async def retry_failed_payout(self, transaction_id: UUID) -> bool:
        """
        Retry a failed payout transaction.
        
        Args:
            transaction_id: ID of the transaction to retry
            
        Returns:
            True if retry successful, False otherwise
        """
        # Get transaction with related data
        transaction_query = (
            select(Transaction, User, DropJob)
            .join(User, Transaction.user_id == User.id)
            .join(DropJob, Transaction.job_id == DropJob.id)
            .where(Transaction.id == transaction_id)
            .where(Transaction.transaction_type == "payout")
            .where(Transaction.status == PaymentStatus.FAILED)
        )
        
        result = self.db.exec(transaction_query).first()
        
        if not result:
            logger.error(f"Failed payout transaction {transaction_id} not found")
            return False
        
        transaction, dropper, job = result
        
        # Check if dropper still has Connect account
        if not dropper.dropper_profile or not dropper.dropper_profile.stripe_connect_account_id:
            logger.error(f"Dropper {dropper.id} does not have Connect account for retry")
            return False
        
        # Reset transaction status for retry
        transaction.status = PaymentStatus.PENDING
        transaction.failure_reason = None
        
        # Add retry metadata
        retry_count = 0
        if transaction.transaction_metadata and "retry_count" in transaction.transaction_metadata:
            retry_count = transaction.transaction_metadata["retry_count"] + 1
        
        if transaction.transaction_metadata:
            transaction.transaction_metadata["retry_count"] = retry_count
            transaction.transaction_metadata["retry_at"] = datetime.utcnow().isoformat()
        else:
            transaction.transaction_metadata = {
                "retry_count": retry_count,
                "retry_at": datetime.utcnow().isoformat()
            }
        
        self.db.add(transaction)
        self.db.commit()
        
        # Process the payout
        success = await self.process_payout(
            transaction=transaction,
            connect_account_id=dropper.dropper_profile.stripe_connect_account_id,
            job=job
        )
        
        logger.info(f"Retry payout transaction {transaction_id}: {'success' if success else 'failed'}")
        return success
    
    def get_transaction_summary(self, user_id: UUID, limit: int = 50) -> List[Transaction]:
        """
        Get transaction summary for a user.
        
        Args:
            user_id: User ID to get transactions for
            limit: Maximum number of transactions to return
            
        Returns:
            List of user transactions
        """
        query = (
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )
        
        return list(self.db.exec(query).all())
    
    def calculate_platform_metrics(self, days: int = 30) -> Dict[str, Any]:
        """
        Calculate platform financial metrics.
        
        Args:
            days: Number of days to calculate metrics for
            
        Returns:
            Dictionary with platform metrics
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        # Use database aggregation for better performance
        
        # Get completed payouts count and sum using database aggregation
        payout_stats_query = (
            select(
                func.count(Transaction.id).label("count"),
                func.coalesce(func.sum(Transaction.amount_pence), 0).label("total")
            )
            .where(Transaction.transaction_type == "payout")
            .where(Transaction.status == PaymentStatus.COMPLETED)
            .where(Transaction.created_at >= cutoff_date)
        )
        payout_stats = self.db.exec(payout_stats_query).first()
        payout_count = payout_stats[0] if payout_stats else 0
        total_payouts_pence = int(payout_stats[1]) if payout_stats else 0
        
        # Get completed payments count and sum using database aggregation
        payment_stats_query = (
            select(
                func.count(Transaction.id).label("count"),
                func.coalesce(func.sum(Transaction.amount_pence), 0).label("total")
            )
            .where(Transaction.transaction_type == "payment")
            .where(Transaction.status == PaymentStatus.COMPLETED)
            .where(Transaction.created_at >= cutoff_date)
        )
        payment_stats = self.db.exec(payment_stats_query).first()
        payment_count = payment_stats[0] if payment_stats else 0
        total_payments_pence = int(payment_stats[1]) if payment_stats else 0
        
        # Get all payouts to calculate platform fees from metadata
        # (This requires loading objects to access metadata)
        payout_query = (
            select(Transaction)
            .where(Transaction.transaction_type == "payout")
            .where(Transaction.status == PaymentStatus.COMPLETED)
            .where(Transaction.created_at >= cutoff_date)
        )
        payouts = list(self.db.exec(payout_query).all())
        
        # Calculate platform fees from metadata
        total_fees_pence = 0
        for payout in payouts:
            if payout.transaction_metadata and "platform_fee_pence" in payout.transaction_metadata:
                total_fees_pence += payout.transaction_metadata["platform_fee_pence"]
        
        # Calculate total revenue (total payments from clients)
        # This is the revenue the platform receives
        total_revenue_pence = total_payments_pence
        
        # Calculate failed payouts count
        failed_payouts_query = (
            select(func.count(Transaction.id))
            .where(Transaction.transaction_type == "payout")
            .where(Transaction.status == PaymentStatus.FAILED)
            .where(Transaction.created_at >= cutoff_date)
        )
        failed_payouts = self.db.exec(failed_payouts_query).first() or 0
        
        return {
            "period_days": days,
            "total_revenue_pence": total_revenue_pence,  # Total revenue from client payments
            "total_payments_pence": total_payments_pence,
            "total_payouts_pence": total_payouts_pence,
            "total_platform_fees_pence": total_fees_pence,
            "payment_count": payment_count,
            "payout_count": payout_count,
            "average_payment_pence": total_payments_pence // payment_count if payment_count > 0 else 0,
            "average_payout_pence": total_payouts_pence // payout_count if payout_count > 0 else 0,
            "failed_payouts": failed_payouts
        }


def get_transaction_service(db: Session) -> TransactionService:
    """
    Get transaction service instance.
    
    Args:
        db: Database session
        
    Returns:
        TransactionService instance
    """
    return TransactionService(db)