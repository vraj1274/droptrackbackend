"""Add job_id and order columns to drop_points

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2025-11-29 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'i3j4k5l6m7n8'
down_revision = 'h2i3j4k5l6m7'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    # Check if drop_points table exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()
    
    if 'drop_points' not in existing_tables:
        # Table doesn't exist, create it with all columns
        op.create_table(
            'drop_points',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('lat', sa.Float(), nullable=False),
            sa.Column('lng', sa.Float(), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('client_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('dropper_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('status', sa.String(length=50), nullable=False, server_default='draft'),
            sa.Column('order', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['users.id'], name='fk_drop_points_client_id'),
            sa.ForeignKeyConstraint(['dropper_id'], ['users.id'], name='fk_drop_points_dropper_id'),
            sa.ForeignKeyConstraint(['job_id'], ['drop_jobs.id'], name='fk_drop_points_job_id'),
            sa.PrimaryKeyConstraint('id', name='pk_drop_points')
        )
    else:
        # Table exists, add missing columns
        if not column_exists('drop_points', 'job_id'):
            op.add_column('drop_points', 
                sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=True))
            # Add foreign key constraint
            op.create_foreign_key(
                'fk_drop_points_job_id',
                'drop_points', 'drop_jobs',
                ['job_id'], ['id']
            )
        
        if not column_exists('drop_points', 'order'):
            op.add_column('drop_points', 
                sa.Column('order', sa.Integer(), nullable=True))
        
        # Ensure name column exists (it might be missing)
        if not column_exists('drop_points', 'name'):
            op.add_column('drop_points', 
                sa.Column('name', sa.String(length=255), nullable=True))
            # Update existing rows with a default name
            op.execute("UPDATE drop_points SET name = 'Drop Point' WHERE name IS NULL")
            # Make it NOT NULL after updating
            op.alter_column('drop_points', 'name', nullable=False)
    
    # Create index on job_id for better query performance
    try:
        op.create_index('idx_drop_points_job_id', 'drop_points', ['job_id'], unique=False)
    except Exception:
        # Index might already exist, ignore
        pass


def downgrade() -> None:
    # Remove index
    try:
        op.drop_index('idx_drop_points_job_id', table_name='drop_points')
    except Exception:
        pass
    
    # Remove columns if they exist
    if column_exists('drop_points', 'order'):
        op.drop_column('drop_points', 'order')
    
    if column_exists('drop_points', 'job_id'):
        # Drop foreign key first
        try:
            op.drop_constraint('fk_drop_points_job_id', 'drop_points', type_='foreignkey')
        except Exception:
            pass
        op.drop_column('drop_points', 'job_id')



