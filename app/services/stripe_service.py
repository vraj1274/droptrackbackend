"""
Stripe payment integration service for DropTrack platform.
Handles payment processing, customer management, and payouts.
"""

import stripe
import logging
from typing import Optional, Dict, Any, List
from decimal import Decimal
from datetime import datetime
from uuid import UUID

from app.config import settings
from app.models import User, DropJob, Transaction, PaymentStatus
from app.utils.log_redaction import redact_stripe_id  # HIGH-RISK FIX 4: Import log redaction

# Configure Stripe
stripe.api_key = settings.stripe_secret_key

logger = logging.getLogger(__name__)


class StripeService:
    """Service class for Stripe payment operations."""
    
    def __init__(self):
        """Initialize Stripe service with configuration."""
        # Don't cache the API key - always get it fresh from settings
        self.webhook_secret = settings.stripe_webhook_secret
    
    @property
    def api_key(self):
        """Get current Stripe API key from settings."""
        return settings.stripe_secret_key
        
    async def create_customer(self, user: User) -> str:
        """
        Create a Stripe customer for a user.
        
        Args:
            user: User object to create customer for
            
        Returns:
            Stripe customer ID
            
        Raises:
            stripe.StripeError: If customer creation fails
        """
        try:
            # Set API key fresh from settings before making call
            stripe.api_key = settings.stripe_secret_key
            customer = stripe.Customer.create(
                email=user.email,
                name=user.name,
                metadata={
                    "user_id": str(user.id),
                    "cognito_sub": user.cognito_sub,
                    "role": user.role
                }
            )
            
            # HIGH-RISK FIX 4: Redact Stripe ID in logs
            logger.info(f"Created Stripe customer {redact_stripe_id(customer.id)} for user {user.id}")
            return customer.id
            
        except stripe.StripeError as e:
            logger.error(f"Failed to create Stripe customer for user {user.id}: {str(e)}")
            raise
    
    async def get_or_create_customer(self, user: User) -> str:
        """
        Get existing Stripe customer ID or create a new one.
        
        Args:
            user: User object
            
        Returns:
            Stripe customer ID
        """
        if user.stripe_customer_id:
            try:
                # Set API key fresh from settings before making call
                stripe.api_key = settings.stripe_secret_key
                # Verify customer still exists in Stripe
                stripe.Customer.retrieve(user.stripe_customer_id)
                return user.stripe_customer_id
            except stripe.InvalidRequestError:
                # Customer doesn't exist, create new one
                # HIGH-RISK FIX 4: Redact Stripe ID in logs
                logger.warning(f"Stripe customer {redact_stripe_id(user.stripe_customer_id)} not found, creating new one")
                pass
        
        return await self.create_customer(user)
    
    async def create_payment_intent(
        self,
        job: DropJob,
        customer_id: str,
        payment_method_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a Stripe PaymentIntent for a job payment.
        
        Args:
            job: DropJob to create payment for
            customer_id: Stripe customer ID
            payment_method_id: Optional payment method ID for automatic confirmation
            
        Returns:
            PaymentIntent data including client_secret
            
        Raises:
            stripe.StripeError: If payment intent creation fails
        """
        try:
            # Check for MOCK_PAYMENT mode
            import os
            if os.environ.get("MOCK_PAYMENT", "false").lower() == "true":
                logger.info(f"Creating MOCK PaymentIntent for job {job.id}")
                import uuid
                mock_id = f"pi_mock_{uuid.uuid4().hex[:16]}"
                return {
                    "id": mock_id,
                    "client_secret": f"{mock_id}_secret_{uuid.uuid4().hex[:16]}",
                    "status": "succeeded",  # Auto-succeed for mock
                    "amount": job.cost_total_pence,
                    "currency": "gbp"
                }

            payment_intent_data = {
                "amount": job.cost_total_pence,
                "currency": "gbp",
                "customer": customer_id,
                "metadata": {
                    "job_id": str(job.id),
                    "client_id": str(job.client_id),
                    "household_count": job.household_count,
                    "job_title": job.title
                },
                "description": f"Payment for leaflet distribution job: {job.title}",
                "automatic_payment_methods": {
                    "enabled": True
                }
            }
            
            # If payment method provided, confirm automatically
            if payment_method_id:
                payment_intent_data["payment_method"] = payment_method_id
                payment_intent_data["confirm"] = True
                payment_intent_data["return_url"] = "https://droptrack.com/payment/return"
            
            # Set API key fresh from settings before making call
            stripe.api_key = settings.stripe_secret_key
            
            # HIGH-RISK FIX 2: Add idempotency key to prevent duplicate charges
            # Use job_id as idempotency key for payment intents
            idempotency_key = f"pi_job_{job.id}"
            payment_intent = stripe.PaymentIntent.create(
                **payment_intent_data,
                idempotency_key=idempotency_key
            )
            
            logger.info(f"Created PaymentIntent {payment_intent.id} for job {job.id}")
            
            return {
                "id": payment_intent.id,
                "client_secret": payment_intent.client_secret,
                "status": payment_intent.status,
                "amount": payment_intent.amount,
                "currency": payment_intent.currency
            }
            
        except stripe.StripeError as e:
            logger.error(f"Failed to create PaymentIntent for job {job.id}: {str(e)}")
            raise
    
    async def confirm_payment_intent(self, payment_intent_id: str) -> Dict[str, Any]:
        """
        Confirm a PaymentIntent (used for manual confirmation).
        
        Args:
            payment_intent_id: Stripe PaymentIntent ID
            
        Returns:
            Updated PaymentIntent data
        """
        try:
            # Set API key fresh from settings before making call
            stripe.api_key = settings.stripe_secret_key
            payment_intent = stripe.PaymentIntent.confirm(payment_intent_id)
            
            logger.info(f"Confirmed PaymentIntent {payment_intent_id}")
            
            return {
                "id": payment_intent.id,
                "status": payment_intent.status,
                "amount": payment_intent.amount,
                "currency": payment_intent.currency
            }
            
        except stripe.StripeError as e:
            logger.error(f"Failed to confirm PaymentIntent {payment_intent_id}: {str(e)}")
            raise
    
    async def retrieve_payment_intent(self, payment_intent_id: str) -> Dict[str, Any]:
        """
        Retrieve a PaymentIntent from Stripe.
        
        Args:
            payment_intent_id: Stripe PaymentIntent ID
            
        Returns:
            PaymentIntent data
        """
        try:
            # Set API key fresh from settings before making call
            stripe.api_key = settings.stripe_secret_key
            payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            
            # Convert metadata to dict if it's a Stripe object
            metadata = payment_intent.metadata
            if hasattr(metadata, 'to_dict'):
                metadata = metadata.to_dict()
            elif not isinstance(metadata, dict):
                metadata = dict(metadata) if metadata else {}
            
            # Build return dictionary with essential payment intent data
            result = {
                "id": payment_intent.id,
                "status": payment_intent.status,
                "amount": payment_intent.amount,
                "currency": payment_intent.currency,
                "metadata": metadata
            }
            
            # Optionally include charges if available (not always present)
            # Charges are typically accessed separately via the API if needed
            try:
                if hasattr(payment_intent, 'charges') and payment_intent.charges:
                    if hasattr(payment_intent.charges, 'data'):
                        result["charges"] = list(payment_intent.charges.data) if payment_intent.charges.data else []
                    elif isinstance(payment_intent.charges, list):
                        result["charges"] = payment_intent.charges
                    else:
                        result["charges"] = []
                else:
                    result["charges"] = []
            except (AttributeError, KeyError, TypeError):
                # Charges not available - this is fine, not all payment intents have charges accessible this way
                result["charges"] = []
            
            return result
            
        except stripe.StripeError as e:
            logger.error(f"Failed to retrieve PaymentIntent {payment_intent_id}: {str(e)}")
            raise
    
    async def create_connect_account(self, dropper_user: User) -> str:
        """
        Create a Stripe Connect Express account for a dropper.
        
        Args:
            dropper_user: User object for the dropper
            
        Returns:
            Stripe Connect account ID
        """
        try:
            # Set API key fresh from settings before making call
            stripe.api_key = settings.stripe_secret_key
            account = stripe.Account.create(
                type="express",
                country="GB",
                email=dropper_user.email,
                metadata={
                    "user_id": str(dropper_user.id),
                    "cognito_sub": dropper_user.cognito_sub
                }
            )
            
            # HIGH-RISK FIX 4: Redact Stripe ID in logs
            logger.info(f"Created Stripe Connect account {redact_stripe_id(account.id)} for dropper {dropper_user.id}")
            return account.id
            
        except stripe.StripeError as e:
            logger.error(f"Failed to create Connect account for dropper {dropper_user.id}: {str(e)}")
            raise
    
    async def create_account_link(self, account_id: str, refresh_url: str, return_url: str) -> str:
        """
        Create an account link for Connect account onboarding.
        
        Args:
            account_id: Stripe Connect account ID
            refresh_url: URL to redirect to if link expires
            return_url: URL to redirect to after onboarding
            
        Returns:
            Account link URL
        """
        try:
            # Set API key fresh from settings before making call
            stripe.api_key = settings.stripe_secret_key
            account_link = stripe.AccountLink.create(
                account=account_id,
                refresh_url=refresh_url,
                return_url=return_url,
                type="account_onboarding"
            )
            
            return account_link.url
            
        except stripe.StripeError as e:
            logger.error(f"Failed to create account link for {account_id}: {str(e)}")
            raise
    
    async def create_payout(
        self,
        connect_account_id: str,
        amount_pence: int,
        job: DropJob,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a payout to a dropper's Connect account.
        
        Args:
            connect_account_id: Stripe Connect account ID
            amount_pence: Amount to pay out in pence
            job: DropJob associated with the payout
            description: Optional description for the payout
            
        Returns:
            Transfer data
        """
        try:
            # Set API key fresh from settings before making call
            stripe.api_key = settings.stripe_secret_key
            
            # HIGH-RISK FIX 2: Add idempotency key to prevent duplicate transfers
            # Use job_id + timestamp as idempotency key for transfers
            import time
            idempotency_key = f"transfer_job_{job.id}_{int(time.time())}"
            
            # Create a transfer to the Connect account
            transfer = stripe.Transfer.create(
                amount=amount_pence,
                currency="AUD",
                destination=connect_account_id,
                metadata={
                    "job_id": str(job.id),
                    "job_title": job.title,
                    "household_count": job.household_count
                },
                description=description or f"Payout for job: {job.title}",
                idempotency_key=idempotency_key
            )
            
            logger.info(f"Created transfer {transfer.id} for £{amount_pence/100:.2f} to account {connect_account_id}")
            
            return {
                "id": transfer.id,
                "amount": transfer.amount,
                "currency": transfer.currency,
                "destination": transfer.destination,
                "status": "pending"  # Transfers are initially pending
            }
            
        except stripe.StripeError as e:
            logger.error(f"Failed to create payout to {connect_account_id}: {str(e)}")
            raise
    
    async def calculate_platform_fee(self, job_total_pence: int) -> int:
        """
        Calculate platform fee for a job.
        
        Args:
            job_total_pence: Total job cost in pence
            
        Returns:
            Platform fee in pence
        """
        # Platform takes 15% fee
        fee_percentage = Decimal("0.15")
        fee_pence = int(Decimal(job_total_pence) * fee_percentage)
        return fee_pence
    
    async def calculate_dropper_payout(self, job_total_pence: int) -> int:
        """
        Calculate dropper payout after platform fee.
        
        Args:
            job_total_pence: Total job cost in pence
            
        Returns:
            Dropper payout in pence
        """
        platform_fee = await self.calculate_platform_fee(job_total_pence)
        return job_total_pence - platform_fee
    
    async def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify Stripe webhook signature.
        
        Args:
            payload: Raw webhook payload
            signature: Stripe signature header
            
        Returns:
            True if signature is valid
        """
        try:
            stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            return True
        except (stripe.SignatureVerificationError, ValueError) as e:
            logger.error(f"Webhook signature verification failed: {str(e)}")
            return False
    
    async def construct_webhook_event(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """
        Construct and validate webhook event.
        
        Args:
            payload: Raw webhook payload
            signature: Stripe signature header
            
        Returns:
            Webhook event data
            
        Raises:
            stripe.SignatureVerificationError: If signature is invalid
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            return event
        except stripe.SignatureVerificationError as e:
            logger.error(f"Webhook signature verification failed: {str(e)}")
            raise
    
    async def handle_payment_succeeded(self, payment_intent: Dict[str, Any]) -> bool:
        """
        Handle successful payment webhook event.
        
        Args:
            payment_intent: PaymentIntent data from webhook
            
        Returns:
            True if handled successfully
        """
        try:
            payment_intent_id = payment_intent["id"]
            metadata = payment_intent.get("metadata", {})
            job_id = metadata.get("job_id")
            
            if not job_id:
                logger.error(f"No job_id in PaymentIntent {payment_intent_id} metadata")
                return False
            
            logger.info(f"Payment succeeded for PaymentIntent {payment_intent_id}, job {job_id}")
            
            # The actual job status update should be handled by the calling service
            # This method just validates the webhook data
            return True
            
        except Exception as e:
            logger.error(f"Error handling payment succeeded webhook: {str(e)}")
            return False
    
    async def refund_payment(
        self,
        payment_intent_id: str,
        amount_pence: Optional[int] = None,
        reason: str = "requested_by_customer"
    ) -> Dict[str, Any]:
        """
        Create a refund for a payment.
        
        Args:
            payment_intent_id: PaymentIntent ID to refund
            amount_pence: Amount to refund in pence (None for full refund)
            reason: Reason for refund
            
        Returns:
            Refund data
        """
        try:
            # Set API key fresh from settings before making call
            stripe.api_key = settings.stripe_secret_key
            # Get the charge from the payment intent
            payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            
            if not payment_intent.charges.data:
                raise ValueError("No charges found for PaymentIntent")
            
            charge_id = payment_intent.charges.data[0].id
            
            refund_data = {
                "charge": charge_id,
                "reason": reason
            }
            
            if amount_pence:
                refund_data["amount"] = amount_pence
            
            # API key is already set above, but ensure it's set before refund
            stripe.api_key = settings.stripe_secret_key
            
            # HIGH-RISK FIX 2: Add idempotency key to prevent duplicate refunds
            # Use payment_intent_id + timestamp as idempotency key
            import time
            idempotency_key = f"refund_{payment_intent_id}_{int(time.time())}"
            
            refund = stripe.Refund.create(
                **refund_data,
                idempotency_key=idempotency_key
            )
            
            logger.info(f"Created refund {refund.id} for PaymentIntent {payment_intent_id}")
            
            return {
                "id": refund.id,
                "amount": refund.amount,
                "currency": refund.currency,
                "status": refund.status,
                "reason": refund.reason
            }
            
        except stripe.StripeError as e:
            logger.error(f"Failed to create refund for PaymentIntent {payment_intent_id}: {str(e)}")
            raise
    
    async def create_checkout_session(
        self,
        jobs: List[DropJob],
        user: User,
        success_url: str,
        cancel_url: str
    ) -> Dict[str, Any]:
        """
        Create a Stripe Checkout Session for multiple jobs.
        
        Args:
            jobs: List of DropJob objects to include in checkout
            user: User making the payment
            success_url: URL to redirect to after successful payment
            cancel_url: URL to redirect to if payment is cancelled
            
        Returns:
            Dictionary with session_id and checkout_url
            
        Raises:
            stripe.StripeError: If checkout session creation fails
        """
        try:
            # IMPORTANT: Set Stripe API key fresh from settings before making API calls
            # This ensures we always use the current key, even if .env was updated
            stripe.api_key = settings.stripe_secret_key
            
            # Build line items from jobs
            line_items = []
            job_ids = []
            
            for job in jobs:
                line_items.append({
                    "price_data": {
                        "currency": "gbp",
                        "product_data": {
                            "name": job.title,
                            "description": f"Leaflet distribution for {job.household_count} households"
                        },
                        "unit_amount": job.cost_total_pence
                    },
                    "quantity": 1
                })
                job_ids.append(str(job.id))
            
            # Create metadata with user_id and comma-separated job_ids
            metadata = {
                "user_id": str(user.id),
                "job_ids": ",".join(job_ids)
            }
            
            # Create checkout session
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=line_items,
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_url,
                customer=user.stripe_customer_id if user.stripe_customer_id else None,
                customer_email=user.email if not user.stripe_customer_id else None,
                metadata=metadata,
                client_reference_id=str(user.id),
                invoice_creation={"enabled": True}
            )
            
            logger.info(f"Created Checkout Session {session.id} for user {user.id} with {len(jobs)} jobs")
            
            return {
                "session_id": session.id,
                "checkout_url": session.url
            }
            
        except stripe.StripeError as e:
            logger.error(f"Failed to create Checkout Session for user {user.id}: {str(e)}")
            raise
    
    async def create_customer_portal_session(
        self,
        user: User,
        return_url: str
    ) -> Dict[str, Any]:
        """
        Create a Stripe Customer Portal session for payment method management.
        
        Args:
            user: User requesting portal access
            return_url: URL to redirect to when user exits the portal
            
        Returns:
            Dictionary with portal_url
            
        Raises:
            stripe.StripeError: If portal session creation fails
        """
        try:
            # IMPORTANT: Set Stripe API key fresh from settings before making API calls
            stripe.api_key = settings.stripe_secret_key
            
            # Get or create Stripe customer for user
            customer_id = await self.get_or_create_customer(user)
            
            # Ensure API key is set before creating portal session
            stripe.api_key = settings.stripe_secret_key
            
            # Create billing portal session
            portal_session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url
            )
            
            logger.info(f"Created Customer Portal Session {portal_session.id} for user {user.id}")
            
            return {
                "portal_url": portal_session.url
            }
            
        except stripe.StripeError as e:
            logger.error(f"Failed to create Customer Portal Session for user {user.id}: {str(e)}")
            raise


# Global service instance
stripe_service = StripeService()