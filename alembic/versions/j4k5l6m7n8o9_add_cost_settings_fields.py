"""Add price_per_household_pence and platform_fee_percentage to cost_settings

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2025-12-01 13:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'j4k5l6m7n8o9'
down_revision = 'i3j4k5l6m7n8'
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    # Check if cost_settings table exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()
    
    if 'cost_settings' not in existing_tables:
        # Table doesn't exist, create it with all columns
        op.create_table(
            'cost_settings',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('price_per_household_pence', sa.Integer(), nullable=False, server_default='50'),
            sa.Column('platform_fee_percentage', sa.Float(), nullable=False, server_default='15.0'),
            sa.Column('platform_fee_pence', sa.Integer(), nullable=False, server_default='100'),
            sa.Column('processing_fee_pence', sa.Integer(), nullable=False, server_default='30'),
            sa.Column('cancellation_fee_pence', sa.Integer(), nullable=False, server_default='250'),
            sa.Column('dispute_fee_pence', sa.Integer(), nullable=False, server_default='500'),
            sa.Column('refund_processing_fee_pence', sa.Integer(), nullable=False, server_default='150'),
            sa.Column('late_fee_pence', sa.Integer(), nullable=False, server_default='300'),
            sa.Column('last_updated', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id', name='pk_cost_settings')
        )
    else:
        # Table exists, add missing columns if they don't exist
        if not column_exists('cost_settings', 'price_per_household_pence'):
            op.add_column('cost_settings', 
                sa.Column('price_per_household_pence', sa.Integer(), nullable=False, server_default='50'))
        
        if not column_exists('cost_settings', 'platform_fee_percentage'):
            op.add_column('cost_settings', 
                sa.Column('platform_fee_percentage', sa.Float(), nullable=False, server_default='15.0'))


def downgrade() -> None:
    # Remove columns if they exist
    if column_exists('cost_settings', 'platform_fee_percentage'):
        op.drop_column('cost_settings', 'platform_fee_percentage')
    
    if column_exists('cost_settings', 'price_per_household_pence'):
        op.drop_column('cost_settings', 'price_per_household_pence')

