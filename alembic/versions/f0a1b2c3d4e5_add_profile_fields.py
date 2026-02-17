"""Add profile fields to clients and droppers tables

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2025-01-15 10:00:00.000000

This migration adds new profile fields to support user profile editing:
- Clients: website, description, address fields (street, city, state, zip_code)
- Both: notification preferences (email_notifications, sms_notifications)
- Both: timezone and language fields
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f0a1b2c3d4e5'
down_revision = 'e9f0a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add new profile fields to clients and droppers tables.
    """
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    
    # Add fields to clients table
    if 'clients' in existing_tables:
        clients_columns = [col['name'] for col in inspector.get_columns('clients')]
        
        # Add website field
        if 'website' not in clients_columns:
            op.add_column('clients', sa.Column('website', sa.String(length=500), nullable=True))
        
        # Add description field
        if 'description' not in clients_columns:
            op.add_column('clients', sa.Column('description', sa.Text(), nullable=True))
        
        # Add address fields
        if 'street' not in clients_columns:
            op.add_column('clients', sa.Column('street', sa.String(length=255), nullable=True))
        
        if 'city' not in clients_columns:
            op.add_column('clients', sa.Column('city', sa.String(length=100), nullable=True))
        
        if 'state' not in clients_columns:
            op.add_column('clients', sa.Column('state', sa.String(length=100), nullable=True))
        
        if 'zip_code' not in clients_columns:
            op.add_column('clients', sa.Column('zip_code', sa.String(length=20), nullable=True))
        
        # Add notification preferences
        if 'email_notifications' not in clients_columns:
            op.add_column('clients', sa.Column('email_notifications', sa.Boolean(), nullable=False, server_default='true'))
        
        if 'sms_notifications' not in clients_columns:
            op.add_column('clients', sa.Column('sms_notifications', sa.Boolean(), nullable=False, server_default='false'))
        
        # Add timezone and language
        if 'timezone' not in clients_columns:
            op.add_column('clients', sa.Column('timezone', sa.String(length=50), nullable=False, server_default='Europe/London'))
        
        if 'language' not in clients_columns:
            op.add_column('clients', sa.Column('language', sa.String(length=10), nullable=False, server_default='en'))
    
    # Add fields to droppers table
    if 'droppers' in existing_tables:
        droppers_columns = [col['name'] for col in inspector.get_columns('droppers')]
        
        # Add notification preferences
        if 'email_notifications' not in droppers_columns:
            op.add_column('droppers', sa.Column('email_notifications', sa.Boolean(), nullable=False, server_default='true'))
        
        if 'sms_notifications' not in droppers_columns:
            op.add_column('droppers', sa.Column('sms_notifications', sa.Boolean(), nullable=False, server_default='false'))
        
        # Add timezone and language
        if 'timezone' not in droppers_columns:
            op.add_column('droppers', sa.Column('timezone', sa.String(length=50), nullable=False, server_default='Europe/London'))
        
        if 'language' not in droppers_columns:
            op.add_column('droppers', sa.Column('language', sa.String(length=10), nullable=False, server_default='en'))


def downgrade() -> None:
    """
    Remove profile fields from clients and droppers tables.
    """
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    
    # Remove fields from clients table
    if 'clients' in existing_tables:
        clients_columns = [col['name'] for col in inspector.get_columns('clients')]
        
        # Remove client-specific fields
        if 'website' in clients_columns:
            op.drop_column('clients', 'website')
        
        if 'description' in clients_columns:
            op.drop_column('clients', 'description')
        
        if 'street' in clients_columns:
            op.drop_column('clients', 'street')
        
        if 'city' in clients_columns:
            op.drop_column('clients', 'city')
        
        if 'state' in clients_columns:
            op.drop_column('clients', 'state')
        
        if 'zip_code' in clients_columns:
            op.drop_column('clients', 'zip_code')
        
        # Remove shared fields
        if 'email_notifications' in clients_columns:
            op.drop_column('clients', 'email_notifications')
        
        if 'sms_notifications' in clients_columns:
            op.drop_column('clients', 'sms_notifications')
        
        if 'timezone' in clients_columns:
            op.drop_column('clients', 'timezone')
        
        if 'language' in clients_columns:
            op.drop_column('clients', 'language')
    
    # Remove fields from droppers table
    if 'droppers' in existing_tables:
        droppers_columns = [col['name'] for col in inspector.get_columns('droppers')]
        
        if 'email_notifications' in droppers_columns:
            op.drop_column('droppers', 'email_notifications')
        
        if 'sms_notifications' in droppers_columns:
            op.drop_column('droppers', 'sms_notifications')
        
        if 'timezone' in droppers_columns:
            op.drop_column('droppers', 'timezone')
        
        if 'language' in droppers_columns:
            op.drop_column('droppers', 'language')
