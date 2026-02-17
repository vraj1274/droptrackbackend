#!/bin/bash
# DropTrack Backend Deployment Script for macOS/Linux (Ubuntu EC2)
# This script deploys the FastAPI backend to Ubuntu EC2 instance

set -e  # Exit on error

# Configuration
EC2_HOST="ec2-54-153-137-21.ap-southeast-2.compute.amazonaws.com"  # Updated to match api.droptrack.com.au DNS
EC2_USER="ubuntu"
PEM_FILE="../droptrack-db-backend.pem"
REMOTE_DIR="/home/ubuntu/droptrack_backend"
LOCAL_DIR="."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "DropTrack Backend Deployment (V3.2 - macOS/Linux)"
echo "=========================================="
echo ""

# Check if PEM file exists
if [ ! -f "$PEM_FILE" ]; then
    echo -e "${RED}Error: PEM file '$PEM_FILE' not found.${NC}"
    exit 1
fi

# Set correct permissions for PEM file
chmod 400 "$PEM_FILE"

echo -e "${GREEN}Step 1: Testing SSH connection...${NC}"
if ! ssh -i "$PEM_FILE" -o StrictHostKeyChecking=no -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes -n "$EC2_USER@$EC2_HOST" "mkdir -p $REMOTE_DIR && echo 'SSH connection successful'"; then
    echo -e "${RED}Error: SSH connection failed.${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}Step 2: Uploading backend files...${NC}"
scp -i "$PEM_FILE" -o StrictHostKeyChecking=no -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=30 -o IdentitiesOnly=yes -r \
    "$LOCAL_DIR/app" \
    "$LOCAL_DIR/run.py" \
    "$LOCAL_DIR/run_worker.py" \
    "$LOCAL_DIR/requirements.txt" \
    "$LOCAL_DIR/.env.example" \
    "$LOCAL_DIR/alembic" \
    "$LOCAL_DIR/alembic.ini" \
    "$LOCAL_DIR/README.md" \
    "$EC2_USER@$EC2_HOST:$REMOTE_DIR/"

echo -e "${GREEN}Step 2.1: Uploading production .env file...${NC}"
scp -i "$PEM_FILE" -o StrictHostKeyChecking=no -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=30 -o IdentitiesOnly=yes \
    "$LOCAL_DIR/.env.production" \
    "$EC2_USER@$EC2_HOST:$REMOTE_DIR/.env"

echo ""
echo -e "${GREEN}Step 3: Creating and uploading setup script...${NC}"

# Create setup script
cat > setup_temp.sh << 'SETUP_SCRIPT'
#!/bin/bash
set -e

cd /home/ubuntu/droptrack_backend

echo "=========================================="
echo "Step 3.1: Updating system packages..."
echo "=========================================="
sudo apt-get update -y

echo ""
echo "=========================================="
echo "Step 3.2: Installing System Dependencies..."
echo "=========================================="
sudo apt-get install -y python3 python3-pip python3-venv python3-full libpq-dev python3-dev build-essential nginx

echo ""
echo "=========================================="
echo "Step 3.3: Setting up Virtual Environment..."
echo "=========================================="
rm -rf venv
python3 -m venv venv

echo ""
echo "=========================================="
echo "Step 3.4: Installing Python dependencies..."
echo "=========================================="
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
./venv/bin/pip install gunicorn uvicorn

echo ""
echo "=========================================="
echo "Step 3.5: Applying Database Schema Updates..."
echo "=========================================="
# Check if database schema update SQL exists
if [ -f "../complete-schema-update.sql" ]; then
    echo "Applying database schema updates from complete-schema-update.sql..."
    PGPASSWORD=droptrack_2026 psql -h api.droptrack.com.au -U droptrack_user -d droptrackdb -f ../complete-schema-update.sql || echo "Warning: Some schema updates may have already been applied"
else
    echo "No schema update file found, skipping..."
fi

echo ""
echo "=========================================="
echo "Step 3.6: Running Database Migrations..."
echo "=========================================="
if [ -f "alembic.ini" ]; then
    export $(grep -v '^#' .env | xargs)
    ./venv/bin/alembic upgrade head || echo "Warning: Migration may have already been applied"
fi

echo ""
echo "=========================================="
echo "Step 3.7: Configuring Backend Service..."
echo "=========================================="
sudo tee /etc/systemd/system/droptrack-backend.service > /dev/null <<'EOF'
[Unit]
Description=DropTrack Backend
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/droptrack_backend
Environment="PATH=/home/ubuntu/droptrack_backend/venv/bin"
Environment="ENVIRONMENT=production"
ExecStart=/home/ubuntu/droptrack_backend/venv/bin/gunicorn -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 app.main:socketio_app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo "=========================================="
echo "Step 3.8: Configuring Worker Service..."
echo "=========================================="
sudo tee /etc/systemd/system/droptrack-worker.service > /dev/null <<'EOF'
[Unit]
Description=DropTrack Background Worker
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/droptrack_backend
Environment="PATH=/home/ubuntu/droptrack_backend/venv/bin"
Environment="ENVIRONMENT=production"
ExecStart=/home/ubuntu/droptrack_backend/venv/bin/python run_worker.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo "=========================================="
echo "Step 3.9: Configuring Nginx..."
echo "=========================================="
sudo tee /etc/nginx/sites-available/droptrack_backend > /dev/null <<'EOF'
server {
    listen 80;
    server_name api.droptrack.com.au;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name api.droptrack.com.au;

    ssl_certificate /etc/letsencrypt/live/api.droptrack.com.au/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.droptrack.com.au/privkey.pem;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF

echo ""
echo "=========================================="
echo "Step 3.10: Restarting Services..."
echo "=========================================="
sudo ln -sf /etc/nginx/sites-available/droptrack_backend /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl daemon-reload
sudo systemctl restart nginx
sudo systemctl enable droptrack-backend droptrack-worker
sudo systemctl restart droptrack-backend droptrack-worker

echo ""
echo "=========================================="
echo "Step 3.11: Checking Service Status..."
echo "=========================================="
sleep 3
sudo systemctl status droptrack-backend --no-pager || true
sudo systemctl status droptrack-worker --no-pager || true
sudo systemctl status nginx --no-pager || true

echo ""
echo "=========================================="
echo "✅ DEPLOYMENT SUCCESSFUL!"
echo "=========================================="
echo "Backend URL: http://$EC2_HOST"
echo "API Docs: http://$EC2_HOST/docs"
echo ""
echo "To check logs:"
echo "  Backend: sudo journalctl -u droptrack-backend -f"
echo "  Worker: sudo journalctl -u droptrack-worker -f"
echo "  Nginx: sudo tail -f /var/log/nginx/error.log"
echo "=========================================="
SETUP_SCRIPT

echo -e "${GREEN}Step 4: Uploading database schema update file...${NC}"
if [ -f "../complete-schema-update.sql" ]; then
    scp -i "$PEM_FILE" -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=30 -o IdentitiesOnly=yes \
        "../complete-schema-update.sql" \
        "$EC2_USER@$EC2_HOST:/home/ubuntu/"
    echo -e "${GREEN}✅ Database schema update file uploaded${NC}"
else
    echo -e "${YELLOW}⚠️  Warning: complete-schema-update.sql not found, skipping database schema upload${NC}"
fi

echo ""
echo -e "${GREEN}Step 5: Uploading and running setup script...${NC}"
scp -i "$PEM_FILE" -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=30 -o IdentitiesOnly=yes \
    setup_temp.sh \
    "$EC2_USER@$EC2_HOST:/home/ubuntu/"

ssh -i "$PEM_FILE" -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes \
    "$EC2_USER@$EC2_HOST" \
    "chmod +x /home/ubuntu/setup_temp.sh && bash /home/ubuntu/setup_temp.sh && rm /home/ubuntu/setup_temp.sh"

# Clean up local temp file
rm -f setup_temp.sh

echo ""
echo "=========================================="
echo -e "${GREEN}✅ Deployment Process Finished${NC}"
echo "=========================================="
echo ""
echo "Backend is now running at: http://$EC2_HOST"
echo "API Documentation: http://$EC2_HOST/docs"
echo ""
echo "To check logs on the server:"
echo "  ssh -i $PEM_FILE $EC2_USER@$EC2_HOST"
echo "  sudo journalctl -u droptrack-backend -f"
echo ""
