"""Add drop points, drop zones, and droper locations

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2025-01-XX XX:XX:XX.XXXXXX

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c7d8e9f0a1b2'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create drop_points table
    op.create_table(
        'drop_points',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lng', sa.Float(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('dropper_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='draft'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['users.id'], name='fk_drop_points_client_id'),
        sa.ForeignKeyConstraint(['dropper_id'], ['users.id'], name='fk_drop_points_dropper_id'),
        sa.PrimaryKeyConstraint('id', name='pk_drop_points')
    )
    
    # Create drop_zones table
    op.create_table(
        'drop_zones',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('polygon_json', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['users.id'], name='fk_drop_zones_client_id'),
        sa.PrimaryKeyConstraint('id', name='pk_drop_zones')
    )
    
    # Create droper_locations table
    op.create_table(
        'droper_locations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('dropper_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lng', sa.Float(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['dropper_id'], ['users.id'], name='fk_droper_locations_dropper_id'),
        sa.PrimaryKeyConstraint('id', name='pk_droper_locations')
    )
    
    # Create indexes for drop_points
    op.create_index('idx_drop_points_client_id', 'drop_points', ['client_id'], unique=False)
    op.create_index('idx_drop_points_dropper_id', 'drop_points', ['dropper_id'], unique=False)
    op.create_index('idx_drop_points_status', 'drop_points', ['status'], unique=False)
    op.create_index('idx_drop_points_location', 'drop_points', ['lat', 'lng'], unique=False)
    op.create_index('idx_drop_points_created_at', 'drop_points', ['created_at'], unique=False)
    
    # Create indexes for drop_zones
    op.create_index('idx_drop_zones_client_id', 'drop_zones', ['client_id'], unique=False)
    op.create_index('idx_drop_zones_created_at', 'drop_zones', ['created_at'], unique=False)
    
    # Create indexes for droper_locations
    op.create_index('idx_droper_locations_dropper_id', 'droper_locations', ['dropper_id'], unique=False)
    op.create_index('idx_droper_locations_timestamp', 'droper_locations', ['timestamp'], unique=False)
    op.create_index('idx_droper_locations_dropper_timestamp', 'droper_locations', ['dropper_id', 'timestamp'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_droper_locations_dropper_timestamp', table_name='droper_locations')
    op.drop_index('idx_droper_locations_timestamp', table_name='droper_locations')
    op.drop_index('idx_droper_locations_dropper_id', table_name='droper_locations')
    op.drop_index('idx_drop_zones_created_at', table_name='drop_zones')
    op.drop_index('idx_drop_zones_client_id', table_name='drop_zones')
    op.drop_index('idx_drop_points_created_at', table_name='drop_points')
    op.drop_index('idx_drop_points_location', table_name='drop_points')
    op.drop_index('idx_drop_points_status', table_name='drop_points')
    op.drop_index('idx_drop_points_dropper_id', table_name='drop_points')
    op.drop_index('idx_drop_points_client_id', table_name='drop_points')
    
    # Drop tables
    op.drop_table('droper_locations')
    op.drop_table('drop_zones')
    op.drop_table('drop_points')

