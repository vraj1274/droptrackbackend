"""Add job broadcasting fields to drop_jobs table

Revision ID: g1h2i3j4k5l6
Revises: f0a1b2c3d4e5
Create Date: 2025-01-15 12:00:00.000000

This migration adds job broadcasting functionality:
- is_broadcasted: Boolean flag to indicate if job is broadcasted to all droppers
- broadcasted_at: Timestamp when job was broadcasted
- Index on (is_broadcasted, status) for query optimization
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'g1h2i3j4k5l6'
down_revision = 'f0a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add broadcasting fields to drop_jobs table and create index.
    """
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    
    # Add fields to drop_jobs table
    if 'drop_jobs' in existing_tables:
        drop_jobs_columns = [col['name'] for col in inspector.get_columns('drop_jobs')]
        
        # Add is_broadcasted field
        if 'is_broadcasted' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('is_broadcasted', sa.Boolean(), nullable=False, server_default='false'))
        
        # Add broadcasted_at field
        if 'broadcasted_at' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('broadcasted_at', sa.DateTime(timezone=True), nullable=True))
        
        # Create index for (is_broadcasted, status) query optimization
        existing_indexes = [idx['name'] for idx in inspector.get_indexes('drop_jobs')]
        if 'idx_drop_jobs_broadcasted_status' not in existing_indexes:
            op.create_index('idx_drop_jobs_broadcasted_status', 'drop_jobs', ['is_broadcasted', 'status'])


def downgrade() -> None:
    """
    Remove broadcasting fields from drop_jobs table and drop index.
    """
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    
    # Remove fields from drop_jobs table
    if 'drop_jobs' in existing_tables:
        # Drop index first
        existing_indexes = [idx['name'] for idx in inspector.get_indexes('drop_jobs')]
        if 'idx_drop_jobs_broadcasted_status' in existing_indexes:
            op.drop_index('idx_drop_jobs_broadcasted_status', table_name='drop_jobs')
        
        drop_jobs_columns = [col['name'] for col in inspector.get_columns('drop_jobs')]
        
        # Remove broadcasted_at field
        if 'broadcasted_at' in drop_jobs_columns:
            op.drop_column('drop_jobs', 'broadcasted_at')
        
        # Remove is_broadcasted field
        if 'is_broadcasted' in drop_jobs_columns:
            op.drop_column('drop_jobs', 'is_broadcasted')
