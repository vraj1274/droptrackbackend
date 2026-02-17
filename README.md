# DropVerify Backend API

FastAPI backend for the DropVerify package delivery and verification platform.

## 🚀 Quick Start

### Local Development

1. **Setup Environment**
   ```bash
   cp env.example .env
   # Edit .env with your configuration (database, Cognito, Stripe, etc.)
   ```

2. **Start Application**
   ```bash
   ./start.sh
   ```

### Production Deployment

1. **Configure Environment**
   ```bash
   cp .env.production .env
   # Edit .env with production settings
   ```

2. **Deploy to Server**
   ```bash
   ./deploy.sh
   ```

## 📋 Environment Configuration

### Required Environment Variables

```env
# Database (pre-configured for local Postgres db `droptrack` with password `root`)
DATABASE_URL=postgresql://postgres:root@localhost:5432/droptrack

# AWS Cognito
COGNITO_REGION=us-east-1
COGNITO_USER_POOL_ID=your-user-pool-id
COGNITO_APP_CLIENT_ID=your-app-client-id

# Stripe
STRIPE_SECRET_KEY=sk_live_your_key
STRIPE_WEBHOOK_SECRET=whsec_your_secret

# Security
SECRET_KEY=your-secret-key

# Superadmin Accounts (for deployment)
SUPERADMIN_1_EMAIL=vraj.suthar+admin@thelinetech.uk
SUPERADMIN_1_PASSWORD=Thelinetech@drop1
SUPERADMIN_2_EMAIL=info@thelinetech.uk
SUPERADMIN_2_PASSWORD=Thelinetech@drop
```

**Note**: See [deployment/SUPERADMIN_CONFIGURATION.md](deployment/SUPERADMIN_CONFIGURATION.md) for detailed superadmin setup instructions.

## 🏗️ Architecture

### Core Components
- **FastAPI**: Modern async web framework
- **SQLModel**: Type-safe ORM with Pydantic integration
- **PostgreSQL**: Primary database with PostGIS support
- **Alembic**: Database migration management
- **AWS Cognito**: Authentication and user management
- **Stripe**: Payment processing

### API Structure
```
/api/v1/
├── health/         # Health checks
├── auth/           # Authentication
├── jobs/           # Job management
├── payments/       # Payment processing
├── client/         # Client endpoints
├── dropper/        # Dropper endpoints
├── admin/          # Admin functions
├── map/            # Geographic services
├── pricing/        # Pricing calculations
└── disputes/       # Dispute resolution
```

## 🔧 Development

### Prerequisites
- Python 3.11+
- PostgreSQL 14+
- Virtual environment

### Setup
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Setup database
createdb droptrack
alembic upgrade head

# Start development server
python run.py
```

### Database Migrations
```bash
# Create migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1
```

## 🏥 Health Checks

- **Basic Health**: `GET /api/v1/health`
- **Detailed Health**: `GET /api/v1/health/detailed`
- **Readiness Probe**: `GET /api/v1/ready`

## 🔒 Security

### Authentication Flow
1. User authenticates with AWS Cognito
2. Frontend receives JWT tokens
3. Backend validates JWT on each request
4. Role-based access control applied

### Security Features
- JWT token validation
- Role-based permissions
- CORS configuration
- Input validation and sanitization
- SQL injection prevention

## 📊 Monitoring

### Logging
- Structured JSON logging
- Request/response logging
- Error tracking
- Performance metrics

### Metrics Endpoints
- Application health status
- Database connectivity
- Service dependencies

## 🚀 Production Deployment

### System Requirements
- Ubuntu 20.04+ or similar
- Python 3.11+
- PostgreSQL 14+
- Nginx (reverse proxy)
- SSL certificate

### Deployment Process
1. Configure production environment
2. Run deployment script: `./deploy.sh`
3. Verify health checks
4. Configure Nginx reverse proxy

### Service Management
```bash
# Check service status
sudo systemctl status dropverify-backend

# View logs
sudo journalctl -u dropverify-backend -f

# Restart service
sudo systemctl restart dropverify-backend
```

## 🔧 Troubleshooting

### Common Issues

**Database Connection Failed**
- Check DATABASE_URL configuration
- Verify PostgreSQL is running
- Check network connectivity

**Authentication Errors**
- Verify Cognito configuration
- Check JWT token format
- Validate user pool settings

**Payment Processing Issues**
- Check Stripe API keys
- Verify webhook endpoints
- Review Stripe dashboard logs

### Debug Mode
Set `DEBUG=true` in environment for detailed error messages and request logging.

## 📚 API Documentation

When running, visit:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

## 🤝 Contributing

1. Follow PEP 8 style guidelines
2. Add type hints to all functions
3. Write tests for new features
4. Update documentation
5. Run linting before commits

## 📄 License

Proprietary - DropVerify Platform
