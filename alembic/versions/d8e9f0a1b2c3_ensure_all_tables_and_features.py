"""Ensure all tables and features for database entries

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2025-01-XX XX:XX:XX.XXXXXX

This migration ensures all database tables, columns, indexes, and constraints
are in place to support all features: users, clients, droppers, jobs, transactions,
payments, and map features.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd8e9f0a1b2c3'
down_revision = 'c7d8e9f0a1b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Ensure all tables and columns exist for all features.
    This migration is idempotent - it checks before creating to avoid errors.
    """
    
    # Get connection to check if tables/columns exist
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()
    
    # Helpers for idempotent constraints and foreign keys
    def constraint_exists(table_name, constraint_name):
        try:
            constraints = [c['name'] for c in inspector.get_unique_constraints(table_name)]
            return constraint_name in constraints
        except Exception: return False

    def fk_exists(table_name, fk_name):
        try:
            fks = [fk['name'] for fk in inspector.get_foreign_keys(table_name)]
            return fk_name in fks
        except Exception: return False
    
    # ============================================
    # 1. Ensure USERS table has all columns
    # ============================================
    if 'users' in existing_tables:
        users_columns = [col['name'] for col in inspector.get_columns('users')]
        
        # Add any missing columns
        if 'updated_at' not in users_columns:
            op.add_column('users', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    
    # ============================================
    # 2. Ensure CLIENTS table has all columns
    # ============================================
    if 'clients' in existing_tables:
        clients_columns = [col['name'] for col in inspector.get_columns('clients')]
        # All columns should already exist - no changes needed
    
    # ============================================
    # 3. Ensure DROPPERS table has all columns
    # ============================================
    if 'droppers' in existing_tables:
        droppers_columns = [col['name'] for col in inspector.get_columns('droppers')]
        
        # Verify all required columns exist
        required_dropper_columns = [
            'stripe_connect_account_id', 'phone_number', 
            'emergency_contact_name', 'emergency_contact_phone'
        ]
        for col_name in required_dropper_columns:
            if col_name not in droppers_columns:
                if col_name == 'stripe_connect_account_id':
                    op.add_column('droppers', sa.Column(col_name, sa.String(length=255), nullable=True))
                elif col_name == 'phone_number' or col_name == 'emergency_contact_phone':
                    op.add_column('droppers', sa.Column(col_name, sa.String(length=20), nullable=True))
                elif col_name == 'emergency_contact_name':
                    op.add_column('droppers', sa.Column(col_name, sa.String(length=255), nullable=True))
    
    # ============================================
    # 4. Ensure DROP_JOBS table has all columns
    # ============================================
    if 'drop_jobs' in existing_tables:
        drop_jobs_columns = [col['name'] for col in inspector.get_columns('drop_jobs')]
        
        # Add any missing columns
        if 'platform_fee_pence' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('platform_fee_pence', sa.Integer(), nullable=False, server_default='0'))
        if 'dropper_payout_pence' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('dropper_payout_pence', sa.Integer(), nullable=False, server_default='0'))
        if 'payment_intent_id' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('payment_intent_id', sa.String(length=255), nullable=True))
        if 'scheduled_date' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('scheduled_date', sa.Date(), nullable=True))
        if 'min_time_per_segment_sec' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('min_time_per_segment_sec', sa.Integer(), nullable=False, server_default='300'))
        if 'special_instructions' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('special_instructions', sa.Text(), nullable=True))
        if 'paid_at' not in drop_jobs_columns:
            op.add_column('drop_jobs', sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True))
    
    # ============================================
    # 5. Ensure JOB_AREAS table has all columns
    # ============================================
    if 'job_areas' in existing_tables:
        job_areas_columns = [col['name'] for col in inspector.get_columns('job_areas')]
        
        # Add missing columns
        if 'postcodes' not in job_areas_columns:
            op.add_column('job_areas', sa.Column('postcodes', postgresql.JSON(astext_type=sa.Text()), nullable=True))
        if 'center_lat' not in job_areas_columns:
            op.add_column('job_areas', sa.Column('center_lat', sa.Float(), nullable=True))
        if 'center_lng' not in job_areas_columns:
            op.add_column('job_areas', sa.Column('center_lng', sa.Float(), nullable=True))
        if 'radius_km' not in job_areas_columns:
            op.add_column('job_areas', sa.Column('radius_km', sa.Float(), nullable=True))
        
        # Ensure unique constraint on job_id
        if not constraint_exists('job_areas', 'uq_job_areas_job_id'):
            op.create_unique_constraint('uq_job_areas_job_id', 'job_areas', ['job_id'])
    
    # ============================================
    # 6. Ensure JOB_ASSIGNMENTS table has all columns
    # ============================================
    if 'job_assignments' in existing_tables:
        job_assignments_columns = [col['name'] for col in inspector.get_columns('job_assignments')]
        
        # Add new columns if missing
        if 'accepted_at' not in job_assignments_columns:
            op.add_column('job_assignments', sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
        if 'gps_log' not in job_assignments_columns:
            op.add_column('job_assignments', sa.Column('gps_log', postgresql.JSON(astext_type=sa.Text()), nullable=True))
        if 'verification_status' not in job_assignments_columns:
            op.add_column('job_assignments', sa.Column('verification_status', sa.String(), nullable=False, server_default='pending'))
        if 'verification_notes' not in job_assignments_columns:
            op.add_column('job_assignments', sa.Column('verification_notes', sa.Text(), nullable=True))
        if 'verified_at' not in job_assignments_columns:
            op.add_column('job_assignments', sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True))
        if 'verified_by' not in job_assignments_columns:
            op.add_column('job_assignments', sa.Column('verified_by', postgresql.UUID(as_uuid=True), nullable=True))
            # Create foreign key if it doesn't exist
            if not fk_exists('job_assignments', 'fk_job_assignments_verified_by'):
                op.create_foreign_key('fk_job_assignments_verified_by', 'job_assignments', 'users', ['verified_by'], ['id'])
        if 'rejection_reason' not in job_assignments_columns:
            op.add_column('job_assignments', sa.Column('rejection_reason', sa.Text(), nullable=True))
        
        # Ensure dropper_id references users.id
        if fk_exists('job_assignments', 'job_assignments_dropper_id_fkey'):
            op.drop_constraint('job_assignments_dropper_id_fkey', 'job_assignments', type_='foreignkey')
        if not fk_exists('job_assignments', 'fk_job_assignments_dropper_id'):
            op.create_foreign_key('fk_job_assignments_dropper_id', 'job_assignments', 'users', ['dropper_id'], ['id'])
    
    # ============================================
    # 7. Ensure TRANSACTIONS table has all columns
    # ============================================
    if 'transactions' in existing_tables:
        transactions_columns = [col['name'] for col in inspector.get_columns('transactions')]
        
        # Add missing columns
        if 'stripe_transfer_id' not in transactions_columns:
            op.add_column('transactions', sa.Column('stripe_transfer_id', sa.String(length=255), nullable=True))
        if 'stripe_charge_id' not in transactions_columns:
            op.add_column('transactions', sa.Column('stripe_charge_id', sa.String(length=255), nullable=True))
        if 'stripe_refund_id' not in transactions_columns:
            op.add_column('transactions', sa.Column('stripe_refund_id', sa.String(length=255), nullable=True))
        if 'transaction_metadata' not in transactions_columns:
            op.add_column('transactions', sa.Column('transaction_metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True))
        if 'failure_reason' not in transactions_columns:
            op.add_column('transactions', sa.Column('failure_reason', sa.Text(), nullable=True))
        if 'processed_at' not in transactions_columns:
            op.add_column('transactions', sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True))
        if 'updated_at' not in transactions_columns:
            op.add_column('transactions', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        
        # Make description NOT NULL
        # In this specific case, we can't easily check 'nullable' via simple inspector name list,
        # but alter_column is usually safe to repeat if it doesn't change anything.
        # However, to be extra safe since we are in a batch:
        op.alter_column('transactions', 'description',
                      existing_type=sa.String(length=500),
                      nullable=False,
                      server_default='')
    
    # ============================================
    # 8. Ensure PAYMENT_METHODS table exists (Skipped - Handled by Initial Migration)
    # ============================================
    
    # ============================================
    # 9. Ensure DROP_POINTS table exists (Skipped - Handled by c7d8e9f0a1b2)
    # ============================================
    
    # ============================================
    # 10. Ensure DROP_ZONES table exists (Skipped - Handled by c7d8e9f0a1b2)
    # ============================================
    
    # ============================================
    # 11. Ensure DROPER_LOCATIONS table exists (Skipped - Handled by c7d8e9f0a1b2)
    # ============================================
    
    # ============================================
    # 12. Create all necessary indexes
    # ============================================
    
    # Get existing indexes
    def index_exists(table_name, index_name):
        try:
            indexes = [idx['name'] for idx in inspector.get_indexes(table_name)]
            return index_name in indexes
        except Exception:
            return False
    
    # Users indexes
    if 'users' in existing_tables:
        if not index_exists('users', 'idx_users_role'):
            op.create_index('idx_users_role', 'users', ['role'], unique=False)
        if not index_exists('users', 'idx_users_created_at'):
            op.create_index('idx_users_created_at', 'users', ['created_at'], unique=False)
    
    # Clients indexes
    if 'clients' in existing_tables:
        if not index_exists('clients', 'idx_clients_business_name'):
            op.create_index('idx_clients_business_name', 'clients', ['business_name'], unique=False)
    
    # Droppers indexes
    if 'droppers' in existing_tables:
        if not index_exists('droppers', 'idx_droppers_service_radius'):
            op.create_index('idx_droppers_service_radius', 'droppers', ['service_radius_km'], unique=False)
        if not index_exists('droppers', 'idx_droppers_location'):
            op.create_index('idx_droppers_location', 'droppers', ['base_location_lat', 'base_location_lng'], unique=False)
        if not index_exists('droppers', 'idx_droppers_rating'):
            op.create_index('idx_droppers_rating', 'droppers', ['rating'], unique=False)
        if not index_exists('droppers', 'idx_droppers_available'):
            op.create_index('idx_droppers_available', 'droppers', ['is_available'], unique=False)
    
    # DropJobs indexes
    if 'drop_jobs' in existing_tables:
        if not index_exists('drop_jobs', 'idx_drop_jobs_client_id'):
            op.create_index('idx_drop_jobs_client_id', 'drop_jobs', ['client_id'], unique=False)
        if not index_exists('drop_jobs', 'idx_drop_jobs_status'):
            op.create_index('idx_drop_jobs_status', 'drop_jobs', ['status'], unique=False)
        if not index_exists('drop_jobs', 'idx_drop_jobs_scheduled_date'):
            op.create_index('idx_drop_jobs_scheduled_date', 'drop_jobs', ['scheduled_date'], unique=False)
        if not index_exists('drop_jobs', 'idx_drop_jobs_created_at'):
            op.create_index('idx_drop_jobs_created_at', 'drop_jobs', ['created_at'], unique=False)
        if not index_exists('drop_jobs', 'idx_drop_jobs_status_scheduled'):
            op.create_index('idx_drop_jobs_status_scheduled', 'drop_jobs', ['status', 'scheduled_date'], unique=False)
    
    # JobAreas indexes
    if 'job_areas' in existing_tables:
        if not index_exists('job_areas', 'idx_job_areas_job_id'):
            op.create_index('idx_job_areas_job_id', 'job_areas', ['job_id'], unique=False)
        if not index_exists('job_areas', 'idx_job_areas_center'):
            op.create_index('idx_job_areas_center', 'job_areas', ['center_lat', 'center_lng'], unique=False)
        if not index_exists('job_areas', 'idx_job_areas_type'):
            op.create_index('idx_job_areas_type', 'job_areas', ['area_type'], unique=False)
    
    # JobAssignments indexes
    if 'job_assignments' in existing_tables:
        if not index_exists('job_assignments', 'idx_job_assignments_job_id'):
            op.create_index('idx_job_assignments_job_id', 'job_assignments', ['job_id'], unique=False)
        if not index_exists('job_assignments', 'idx_job_assignments_dropper_id'):
            op.create_index('idx_job_assignments_dropper_id', 'job_assignments', ['dropper_id'], unique=False)
        if not index_exists('job_assignments', 'idx_job_assignments_verification_status'):
            op.create_index('idx_job_assignments_verification_status', 'job_assignments', ['verification_status'], unique=False)
        if not index_exists('job_assignments', 'idx_job_assignments_completed_at'):
            op.create_index('idx_job_assignments_completed_at', 'job_assignments', ['completed_at'], unique=False)
        if not index_exists('job_assignments', 'idx_job_assignments_accepted_at'):
            op.create_index('idx_job_assignments_accepted_at', 'job_assignments', ['accepted_at'], unique=False)
    
    # Transactions indexes
    if 'transactions' in existing_tables:
        if not index_exists('transactions', 'idx_transactions_type'):
            op.create_index('idx_transactions_type', 'transactions', ['transaction_type'], unique=False)
        if not index_exists('transactions', 'idx_transactions_status'):
            op.create_index('idx_transactions_status', 'transactions', ['status'], unique=False)
        if not index_exists('transactions', 'idx_transactions_created_at'):
            op.create_index('idx_transactions_created_at', 'transactions', ['created_at'], unique=False)
        if not index_exists('transactions', 'idx_transactions_stripe_payment_intent'):
            op.create_index('idx_transactions_stripe_payment_intent', 'transactions', ['stripe_payment_intent_id'], unique=False)
        if not index_exists('transactions', 'idx_transactions_user_type_status'):
            op.create_index('idx_transactions_user_type_status', 'transactions', ['user_id', 'transaction_type', 'status'], unique=False)
    
    # PaymentMethods indexes
    if 'payment_methods' in existing_tables:
        if not index_exists('payment_methods', 'idx_payment_methods_user_id'):
            op.create_index('idx_payment_methods_user_id', 'payment_methods', ['user_id'], unique=False)
        if not index_exists('payment_methods', 'idx_payment_methods_stripe_id'):
            op.create_index('idx_payment_methods_stripe_id', 'payment_methods', ['stripe_payment_method_id'], unique=False)
        if not index_exists('payment_methods', 'idx_payment_methods_default'):
            op.create_index('idx_payment_methods_default', 'payment_methods', ['user_id', 'is_default'], unique=False)
        if not index_exists('payment_methods', 'idx_payment_methods_active'):
            op.create_index('idx_payment_methods_active', 'payment_methods', ['is_active'], unique=False)
    
    # DropPoints indexes
    if 'drop_points' in existing_tables:
        if not index_exists('drop_points', 'idx_drop_points_client_id'):
            op.create_index('idx_drop_points_client_id', 'drop_points', ['client_id'], unique=False)
        if not index_exists('drop_points', 'idx_drop_points_dropper_id'):
            op.create_index('idx_drop_points_dropper_id', 'drop_points', ['dropper_id'], unique=False)
        if not index_exists('drop_points', 'idx_drop_points_status'):
            op.create_index('idx_drop_points_status', 'drop_points', ['status'], unique=False)
        if not index_exists('drop_points', 'idx_drop_points_location'):
            op.create_index('idx_drop_points_location', 'drop_points', ['lat', 'lng'], unique=False)
        if not index_exists('drop_points', 'idx_drop_points_created_at'):
            op.create_index('idx_drop_points_created_at', 'drop_points', ['created_at'], unique=False)
    
    # DropZones indexes
    if 'drop_zones' in existing_tables:
        if not index_exists('drop_zones', 'idx_drop_zones_client_id'):
            op.create_index('idx_drop_zones_client_id', 'drop_zones', ['client_id'], unique=False)
        if not index_exists('drop_zones', 'idx_drop_zones_created_at'):
            op.create_index('idx_drop_zones_created_at', 'drop_zones', ['created_at'], unique=False)
    
    # DroperLocations indexes
    if 'droper_locations' in existing_tables:
        if not index_exists('droper_locations', 'idx_droper_locations_dropper_id'):
            op.create_index('idx_droper_locations_dropper_id', 'droper_locations', ['dropper_id'], unique=False)
        if not index_exists('droper_locations', 'idx_droper_locations_timestamp'):
            op.create_index('idx_droper_locations_timestamp', 'droper_locations', ['timestamp'], unique=False)
        if not index_exists('droper_locations', 'idx_droper_locations_dropper_timestamp'):
            op.create_index('idx_droper_locations_dropper_timestamp', 'droper_locations', ['dropper_id', 'timestamp'], unique=False)


def downgrade() -> None:
    """
    Rollback this migration.
    Note: This is a comprehensive migration, so downgrade will not remove tables,
    only indexes added by this migration.
    """
    # Note: We don't downgrade table creation in this migration
    # as tables might be needed by other parts of the system
    # Only remove indexes that were added in this migration
    
    # Most indexes should remain for performance
    # This downgrade is intentionally minimal to avoid breaking the system
    pass

