"""
run_production.py — Production server for TicketScout (Linux VPS / Windows).

Uses Waitress (cross-platform WSGI server).
Do NOT use app.py --debug in production.

On VPS: runs behind Nginx on 127.0.0.1:5001 (never exposed directly).
On Windows: can bind 0.0.0.0 if no reverse proxy.

Usage:
    python run_production.py
    python run_production.py --port 5001
    python run_production.py --host 0.0.0.0 --port 5001
"""

import argparse
import logging
import os
import sys

# ── Ensure working directory is the project root ─────────────────────────────
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# Mark as production so Flask sets secure cookies, HSTS, etc.
os.environ.setdefault("PRODUCTION", "true")

# ── Logging ───────────────────────────────────────────────────────────────────
from logging.handlers import RotatingFileHandler

_file_handler = RotatingFileHandler(
    "ticketscout.log",
    maxBytes=5 * 1024 * 1024,   # 5 MB per file
    backupCount=5,               # keep last 5 rotated files
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"))

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler])
logger = logging.getLogger("production")

def main():
    parser = argparse.ArgumentParser(description="TicketScout production server")
    # Railway (and most cloud platforms) inject a PORT env var — honour it
    _default_port = int(os.getenv("PORT", 5001))
    # Bind to 0.0.0.0 on any cloud platform (Railway, Render, Fly, etc.)
    _on_cloud = any(os.getenv(v) for v in ("RAILWAY_ENVIRONMENT", "RENDER", "FLY_APP_NAME", "DYNO"))
    _default_host = "0.0.0.0" if _on_cloud else "127.0.0.1"

    parser.add_argument("--host",  default=_default_host, help="Bind host")
    parser.add_argument("--port",  default=_default_port, type=int, help="Port")
    parser.add_argument("--threads", default=4, type=int, help="Worker threads (default: 4)")
    args = parser.parse_args()

    try:
        from waitress import serve
    except ImportError:
        logger.error("waitress not installed. Run: pip install waitress")
        sys.exit(1)

    from app import app

    logger.info("=" * 60)
    logger.info("  TicketScout — Production Mode")
    logger.info(f"  Listening on http://{args.host}:{args.port}")
    logger.info(f"  Threads: {args.threads}")
    logger.info("=" * 60)

    serve(
        app,
        host=args.host,
        port=args.port,
        threads=args.threads,
        channel_timeout=120,
        cleanup_interval=30,
        connection_limit=200,
        ident="TicketScout",
    )

if __name__ == "__main__":
    main()
