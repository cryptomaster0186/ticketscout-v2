"""
backup_db.py — Daily SQLite backup to Google Drive.

Uses the existing service account (google-service-key.json) to upload
a timestamped backup of ticketscout.db to a Google Drive folder.

Setup (one-time):
  1. Go to Google Drive
  2. Create a folder named "TicketScout Backups"
  3. Share it with: ticketscout-sa@ticketscout-sheets-35735.iam.gserviceaccount.com
     (give Editor access)
  4. Copy the folder ID from the URL:
     https://drive.google.com/drive/folders/FOLDER_ID_HERE
  5. Set GDRIVE_BACKUP_FOLDER_ID=FOLDER_ID_HERE in your .env

Run manually:   python backup_db.py
Run via cron:   scheduled via Task Scheduler (see setup_backup_task.bat)
"""

import os
import shutil
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR          = Path(__file__).parent
DB_PATH           = BASE_DIR / os.getenv("DB_PATH", "ticketscout.db")
SERVICE_KEY_PATH  = BASE_DIR / "google-service-key.json"
FOLDER_ID         = os.getenv("GDRIVE_BACKUP_FOLDER_ID", "")
KEEP_DAYS         = int(os.getenv("BACKUP_KEEP_DAYS", "7"))
BACKUP_PREFIX     = "ticketscout_backup_"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "backup.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("backup")


def _get_drive_service():
    """Build authenticated Google Drive service using service account."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/drive"]
    creds  = service_account.Credentials.from_service_account_file(
        str(SERVICE_KEY_PATH), scopes=scopes
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _make_backup_copy() -> Path:
    """Create a safe offline copy of the SQLite DB using sqlite3 backup API."""
    import sqlite3
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest    = BASE_DIR / f"{BACKUP_PREFIX}{ts}.db"
    src_con = sqlite3.connect(str(DB_PATH))
    dst_con = sqlite3.connect(str(dest))
    src_con.backup(dst_con)
    src_con.close()
    dst_con.close()
    size_mb = dest.stat().st_size / 1024 / 1024
    logger.info(f"Backup created: {dest.name}  ({size_mb:.2f} MB)")
    return dest


def _upload_to_drive(service, local_path: Path) -> str:
    """Upload file to the configured Google Drive folder. Returns file ID."""
    from googleapiclient.http import MediaFileUpload

    file_metadata = {
        "name":    local_path.name,
        "parents": [FOLDER_ID],
    }
    media = MediaFileUpload(str(local_path), mimetype="application/x-sqlite3", resumable=False)
    f = service.files().create(
        body=file_metadata, media_body=media, fields="id,name,size"
    ).execute()
    logger.info(f"Uploaded to Drive: {f['name']}  (id={f['id']}  size={int(f.get('size',0))//1024}KB)")
    return f["id"]


def _prune_old_backups(service):
    """Delete backups older than KEEP_DAYS from the Drive folder."""
    cutoff = datetime.utcnow() - timedelta(days=KEEP_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    query = (
        f"'{FOLDER_ID}' in parents"
        f" and name contains '{BACKUP_PREFIX}'"
        f" and createdTime < '{cutoff_str}'"
        f" and trashed = false"
    )
    results = service.files().list(
        q=query, fields="files(id,name,createdTime)", pageSize=50
    ).execute()

    old_files = results.get("files", [])
    for f in old_files:
        service.files().delete(fileId=f["id"]).execute()
        logger.info(f"Deleted old backup: {f['name']} (created {f['createdTime']})")

    if not old_files:
        logger.info("No old backups to prune.")


def run():
    if not DB_PATH.exists():
        logger.error(f"Database not found: {DB_PATH}")
        sys.exit(1)

    if not SERVICE_KEY_PATH.exists():
        logger.error(f"Service key not found: {SERVICE_KEY_PATH}")
        sys.exit(1)

    if not FOLDER_ID:
        logger.error(
            "GDRIVE_BACKUP_FOLDER_ID not set in .env\n"
            "  1. Create a folder 'TicketScout Backups' in Google Drive\n"
            "  2. Share it with: ticketscout-sa@ticketscout-sheets-35735.iam.gserviceaccount.com\n"
            "  3. Copy the folder ID from the URL and add to .env:\n"
            "     GDRIVE_BACKUP_FOLDER_ID=your_folder_id_here"
        )
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("Starting TicketScout database backup")

    try:
        backup_path = _make_backup_copy()
        service     = _get_drive_service()
        _upload_to_drive(service, backup_path)
        _prune_old_backups(service)
        backup_path.unlink(missing_ok=True)   # clean up local temp copy
        logger.info("Backup completed successfully.")
        logger.info("=" * 50)
    except Exception as e:
        logger.error(f"Backup FAILED: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run()
