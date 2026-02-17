"""Add role column to clients table

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2025-01-XX XX:XX:XX.XXXXXX

This migration adds a role column to the clients table to support admin/client role distinction.
Only the configured superadmin emails ('vraj.suthar+admin@thelinetech.uk' and 'info@thelinetech.uk')
can have 'admin' role, all others are 'client'.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e9f0a1b2c3d4'
down_revision = 'd8e9f0a1b2c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add role column to clients table.
    Set default to 'client' for all existing records.
    Only configured superadmin emails should have 'admin' role.
    """
    # Get connection to check if column exists
    try:
        from sqlalchemy import inspect
        conn = op.get_bind()
        inspector = inspect(conn)
        existing_tables = inspector.get_table_names()
    except Exception as e:
        existing_tables = []
    
    if 'clients' in existing_tables:
        clients_columns = [col['name'] for col in inspector.get_columns('clients')]
        
        # Add role column if it doesn't exist
        if 'role' not in clients_columns:
            # Add column with default 'client'
            op.add_column('clients', sa.Column('role', sa.String(length=10), nullable=False, server_default='client'))
            
            # Update existing records: set 'admin' for the specific emails, 'client' for all others
            connection = op.get_bind()
            superadmin_emails = [
                'vraj.suthar+admin@thelinetech.uk',
                'info@thelinetech.uk'
            ]
            
            for email in superadmin_emails:
                result = connection.execute(
                    sa.text("SELECT id FROM users WHERE lower(email) = :email"),
                    {"email": email.lower()}
                )
                admin_user_row = result.fetchone()
                
                if admin_user_row:
                    admin_user_id = admin_user_row[0]
                    # Update the client record for this user to have 'admin' role
                    connection.execute(
                        sa.text("UPDATE clients SET role = 'admin' WHERE user_id = :user_id"),
                        {"user_id": admin_user_id}
                    )
                    connection.commit()
            
            # Ensure all other clients have 'client' role (should already be default, but explicit)
            connection.execute(
                sa.text("UPDATE clients SET role = 'client' WHERE role IS NULL OR role != 'admin'")
            )
            connection.commit()


def downgrade() -> None:
    """
    Remove role column from clients table.
    """
    try:
        from sqlalchemy import inspect
        conn = op.get_bind()
        inspector = inspect(conn)
        existing_tables = inspector.get_table_names()
        
        if 'clients' in existing_tables:
            clients_columns = [col['name'] for col in inspector.get_columns('clients')]
            
            if 'role' in clients_columns:
                op.drop_column('clients', 'role')
    except Exception:
        # If table doesn't exist or column doesn't exist, ignore
        pass

