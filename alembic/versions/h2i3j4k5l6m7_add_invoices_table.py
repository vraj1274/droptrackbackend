"""Add invoices table for Stripe payment tracking

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2025-01-15 14:00:00.000000

This migration adds the invoices table for tracking Stripe Checkout payments:
- id: Primary key UUID
- user_id: Foreign key to users table
- stripe_session_id: Unique Stripe Checkout Session ID
- stripe_payment_intent_id: Stripe PaymentIntent ID
- amount_total_pence: Total amount paid in pence
- currency: Currency code (default: gbp)
- status: Payment status (paid, refunded, failed)
- job_ids: JSON array of job UUIDs
- invoice_metadata: JSON metadata
- created_at: Timestamp when invoice was created
- updated_at: Timestamp when invoice was last updated
- Indexes on user_id, stripe_session_id, created_at, and status
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'h2i3j4k5l6m7'
down_revision = 'g1h2i3j4k5l6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create invoices table with indexes.
    """
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    
    # Create invoices table if it doesn't exist
    if 'invoices' not in existing_tables:
        op.create_table(
            'invoices',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('stripe_session_id', sa.String(length=255), nullable=False, unique=True),
            sa.Column('stripe_payment_intent_id', sa.String(length=255), nullable=True),
            sa.Column('amount_total_pence', sa.Integer(), nullable=False),
            sa.Column('currency', sa.String(length=3), nullable=False, server_default='gbp'),
            sa.Column('status', sa.String(length=50), nullable=False),
            sa.Column('job_ids', postgresql.JSON(astext_type=sa.Text()), nullable=False),
            sa.Column('invoice_metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        
        # Create indexes
        op.create_index('idx_invoices_user_id', 'invoices', ['user_id'])
        op.create_index('idx_invoices_stripe_session_id', 'invoices', ['stripe_session_id'])
        op.create_index('idx_invoices_created_at', 'invoices', ['created_at'])
        op.create_index('idx_invoices_status', 'invoices', ['status'])


def downgrade() -> None:
    """
    Drop invoices table and indexes.
    """
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    
    # Drop invoices table if it exists
    if 'invoices' in existing_tables:
        # Drop indexes first
        existing_indexes = [idx['name'] for idx in inspector.get_indexes('invoices')]
        
        if 'idx_invoices_status' in existing_indexes:
            op.drop_index('idx_invoices_status', table_name='invoices')
        if 'idx_invoices_created_at' in existing_indexes:
            op.drop_index('idx_invoices_created_at', table_name='invoices')
        if 'idx_invoices_stripe_session_id' in existing_indexes:
            op.drop_index('idx_invoices_stripe_session_id', table_name='invoices')
        if 'idx_invoices_user_id' in existing_indexes:
            op.drop_index('idx_invoices_user_id', table_name='invoices')
        
        # Drop table
        op.drop_table('invoices')
