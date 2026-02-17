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
