#!/bin/bash
# ══════════════════════════════════════════════════════════
# setup_google_sheets.sh
# Fully automated Google Sheets setup for TicketScout v2
# Run: bash setup_google_sheets.sh
# ══════════════════════════════════════════════════════════

set -e

GCLOUD="/Users/petartomsic/google-cloud-sdk/google-cloud-sdk/bin/gcloud"
export CLOUDSDK_PYTHON=/Library/Developer/CommandLineTools/usr/bin/python3

ENV_FILE="/Users/petartomsic/Desktop/ticketscout_v2/.env"
KEY_FILE="/Users/petartomsic/Desktop/ticketscout_v2/google-service-key.json"

PROJECT_ID="ticketscout-sheets-$(date +%s | tail -c 6)"
SA_NAME="ticketscout-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SPREADSHEET_ID="1gNm3x6PZy0Ynn_Pbp_CwQhNy2cZhcvmV"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   TicketScout — Google Sheets Auto-Setup     ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Step 1: Auth ──────────────────────────────────────────
echo "▶ Step 1/7: Google login"
echo "  A browser window will open. Sign in with your Google account."
echo ""
$GCLOUD auth login --quiet

echo ""
echo "✅ Logged in as: $($GCLOUD auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"

# ── Step 2: Create project ────────────────────────────────
echo ""
echo "▶ Step 2/7: Creating Google Cloud project: ${PROJECT_ID}"
$GCLOUD projects create "$PROJECT_ID" --name="TicketScout Sheets" --quiet 2>/dev/null || true
$GCLOUD config set project "$PROJECT_ID" --quiet

echo "✅ Project: ${PROJECT_ID}"

# ── Step 3: Enable APIs ───────────────────────────────────
echo ""
echo "▶ Step 3/7: Enabling Google Sheets + Drive APIs..."
$GCLOUD services enable sheets.googleapis.com --quiet
$GCLOUD services enable drive.googleapis.com  --quiet
echo "✅ APIs enabled"

# ── Step 4: Create service account ───────────────────────
echo ""
echo "▶ Step 4/7: Creating service account..."
$GCLOUD iam service-accounts create "$SA_NAME" \
  --display-name="TicketScout Sheets Bot" \
  --quiet 2>/dev/null || true
echo "✅ Service account: ${SA_EMAIL}"

# ── Step 5: Download key ──────────────────────────────────
echo ""
echo "▶ Step 5/7: Downloading JSON key..."
$GCLOUD iam service-accounts keys create "$KEY_FILE" \
  --iam-account="$SA_EMAIL" \
  --quiet
echo "✅ Key saved to: ${KEY_FILE}"

# ── Step 6: Write .env ────────────────────────────────────
echo ""
echo "▶ Step 6/7: Writing credentials to .env..."

# Parse JSON key with Python
python3 - <<PYEOF
import json, re

KEY_FILE = "${KEY_FILE}"
ENV_FILE = "${ENV_FILE}"
SPREADSHEET_ID = "${SPREADSHEET_ID}"

with open(KEY_FILE) as f:
    key = json.load(f)

project_id    = key.get("project_id", "")
private_key_id = key.get("private_key_id", "")
client_email  = key.get("client_email", "")
# Escape newlines so they fit on one .env line
private_key   = key.get("private_key", "").replace("\n", "\\n")

# Read current .env
with open(ENV_FILE) as f:
    content = f.read()

# Remove any existing Google Sheets block
content = re.sub(r'\n# ── Google Sheets.*?(?=\n# ──|\Z)', '', content, flags=re.DOTALL)

# Append new block
block = f"""
# ── Google Sheets (Ticket Management) ─────────────────────────────────────────
GOOGLE_PROJECT_ID={project_id}
GOOGLE_PRIVATE_KEY_ID={private_key_id}
GOOGLE_CLIENT_EMAIL={client_email}
GOOGLE_PRIVATE_KEY="{private_key}"
GOOGLE_SHEETS_SPREADSHEET_ID={SPREADSHEET_ID}
GOOGLE_SHEETS_TICKETS_TAB=Ticket Data
GOOGLE_SHEETS_EXPENSES_TAB=Expenses
GOOGLE_SHEETS_SUMMARY_TAB=Financial Summary
"""

with open(ENV_FILE, 'a') as f:
    f.write(block)

print(f"  client_email : {client_email}")
print(f"  project_id   : {project_id}")
print("  .env updated ✅")
PYEOF

# ── Step 7: Share the sheet ───────────────────────────────
echo ""
echo "▶ Step 7/7: Instructions to share your Google Sheet"
echo ""
SA_EMAIL_RESOLVED=$($GCLOUD iam service-accounts list --format='value(email)' --filter="name:${SA_NAME}" 2>/dev/null | head -1)
echo "  ⚠️  One manual step required:"
echo ""
echo "  1. Open your Google Sheet:"
echo "     https://docs.google.com/spreadsheets/d/${SPREADSHEET_ID}"
echo ""
echo "  2. Click Share (top right)"
echo ""
echo "  3. Add this email with Editor access:"
echo "     ${SA_EMAIL_RESOLVED:-${SA_EMAIL}}"
echo ""
echo "  4. Click Send"
echo ""
echo "  Then restart the app: python3 app.py"
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   ✅ Setup complete! Do Step 7 manually.     ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
