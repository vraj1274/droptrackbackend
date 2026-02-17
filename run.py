"""
Run script for DropVerify FastAPI Backend Application.
This script is the SINGLE SOURCE OF TRUTH for starting the application.

It handles:
1. Environment detection (Development vs Production)
2. Binding address (0.0.0.0 for Linux/Prod, 127.0.0.1 for Win/Dev)
3. Port management (Strict in Prod, Auto-switch in Dev)
4. Worker process management (Gunicorn in Prod, Uvicorn in Dev)
"""

import os
import sys
import socket
import signal
import uvicorn
from app.config import settings

# Fix Windows console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def is_port_available(host: str, port: int) -> bool:
    """Check if a port is available for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_available_port(host: str, start_port: int, max_attempts: int = 10) -> int:
    """Find an available port starting from start_port."""
    for i in range(max_attempts):
        port = start_port + i
        if is_port_available(host, port):
            return port
    raise RuntimeError(f"Could not find an available port starting from {start_port}")


def handle_keyboard_interrupt(signum, frame):
    """Handle KeyboardInterrupt gracefully."""
    print("\n⚠️  Received interrupt signal. Shutting down...")
    sys.exit(0)


def main():
    """Main entry point."""
    
    # 1. Environment & Platform Detection
    # -----------------------------------
    # We default to 'production' if not specified, to be safe on servers.
    # UNLESS we are on Windows, where we default to 'development'.
    default_env = "development" if sys.platform == "win32" else "production"
    env_var = os.getenv("ENVIRONMENT", default_env).lower()
    is_production = env_var == "production"
    
    # 2. Host Binding
    # ---------------
    # CRITICAL: Linux/EC2 MUST bind to 0.0.0.0 to be reachable from outside (Load Balancers, Frontend).
    # Windows devs can use 127.0.0.1.
    if sys.platform == "win32":
        default_host = "127.0.0.1"
    else:
        default_host = "0.0.0.0"
    
    host = os.getenv("HOST", default_host)
    port = int(os.getenv("PORT", 8000))
    
    # Register signal handlers (Linux only)
    if sys.platform != "win32":
        signal.signal(signal.SIGINT, handle_keyboard_interrupt)
        signal.signal(signal.SIGTERM, handle_keyboard_interrupt)

    # 3. Mode Branching
    # -----------------
    if is_production:
        # --- PRODUCTION MODE ---
        workers = int(os.getenv("WORKERS", 4))
        
        print("\n" + "="*60)
        print(f"🚀 STARTING PROPVERIFY BACKEND : PRODUCTION MODE")
        print("="*60)
        print(f"✅ Host Binding: {host} (MUST be 0.0.0.0 for EC2)")
        print(f"✅ Port:         {port} (Strict)")
        print(f"✅ Workers:      {workers}")
        print(f"✅ Platform:     {sys.platform}")
        print("="*60 + "\n")
        
        # Safety Check: Do not bind to localhost in production on Linux
        if host in ["127.0.0.1", "localhost"] and sys.platform != "win32":
            print("❌ CRITICAL ERROR: Production refused to start on localhost.")
            print("   You are on Linux/EC2. You MUST bind to 0.0.0.0.")
            print("   Fix: Unset HOST env var or set HOST=0.0.0.0")
            sys.exit(1)

        # Port Check: Fail fast if port is taken (no magic switching in prod)
        if not is_port_available(host, port):
            print(f"❌ CRITICAL ERROR: Port {port} is already in use.")
            print("   Production requires a fixed port. Please kill the blocking process.")
            sys.exit(1)

        # Handover to Gunicorn
        # We replace the current process (os.execvp) so systemd sees Gunicorn as the main process
        import shutil
        gunicorn_path = shutil.which("gunicorn")
        if not gunicorn_path:
             # Fallback if gunicorn not found in PATH (e.g. windows or missing venv activation)
             print("⚠️  Gunicorn not found in PATH. Falling back to Uvicorn (NOT RECOMMENDED).")
        else:
            print(">> Executing Gunicorn...")
            gunicorn_cmd = [
                gunicorn_path,
                "app.main:socketio_app",
                "-w", str(workers),
                "-k", "uvicorn.workers.UvicornWorker",
                "-b", f"{host}:{port}",
                "--access-logfile", "-",
                "--error-logfile", "-"
            ]
            if sys.platform != "win32":
                os.execvp(gunicorn_path, gunicorn_cmd)
            else:
                # Windows doesn't support execvp properly for this use case
                os.system(" ".join(gunicorn_cmd))
                return

    else:
        # --- DEVELOPMENT MODE ---
        reload = os.getenv("RELOAD", "true").lower() in ("true", "1", "yes")
        
        print("\n" + "-"*60)
        print(f"🔧 STARTING PROPVERIFY BACKEND : DEVELOPMENT MODE")
        print("-"*60)
        print(f"ℹ️  Host:   {host}")
        
        # Auto-switch port if taken
        if not is_port_available(host, port):
            print(f"⚠️  Port {port} in use.")
            try:
                new_port = find_available_port(host, port)
                print(f"   ➜ Switched to: {new_port}")
                port = new_port
            except RuntimeError as e:
                print(f"❌ {e}")
                sys.exit(1)
        else:
            print(f"✅ Port:   {port}")

        print(f"✅ Reload: {reload}")
        print("-"*60 + "\n")

        # Start Uvicorn directly
        try:
            uvicorn.run(
                "app.main:socketio_app",
                host=host,
                port=port,
                reload=reload,
                log_level="debug"
            )
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"❌ Server Crashed: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
