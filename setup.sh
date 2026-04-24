#!/bin/bash
# ══════════════════════════════════════════════════════════════════
# setup.sh — TicketScout v2 setup script
# Pokretanje: bash setup.sh
# ══════════════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║    TicketScout v2 — Setup           ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Python check ────────────────────────────────────────────────────────────
PYTHON=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON" ]; then
    echo "❌ Python nije pronađen. Instaliraj Python 3.9+"
    exit 1
fi
echo "✅ Python: $($PYTHON --version)"

# ── 2. Install dependencies ────────────────────────────────────────────────────
echo "📦 Instaliranje dependencies..."
$PYTHON -m pip install -r requirements.txt --quiet
echo "✅ Dependencies instalirane"

# ── 3. .env setup ─────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "📋 .env fajl kreiran iz .env.example"
    echo ""
    echo "⚠️  OBAVEZNO popuni ova polja u .env:"
    echo "   ANTHROPIC_API_KEY  → https://console.anthropic.com"
    echo "   TM_API_KEY         → https://developer.ticketmaster.com"
    echo "   DISCORD_WEBHOOK_URL → Discord Server → Integrations → Webhooks"
    echo ""
else
    echo "✅ .env fajl već postoji"
fi

# ── 4. Init DB ─────────────────────────────────────────────────────────────────
echo "🗄  Inicijalizacija baze podataka..."
$PYTHON -c "import database; database.init_db()"
echo "✅ Baza inicijalizirana: ticketscout.db"

# ── 5. Test Discord webhook ────────────────────────────────────────────────────
echo ""
echo "Testiraj Discord webhook? (y/n)"
read -r TEST_DISCORD
if [ "$TEST_DISCORD" = "y" ]; then
    $PYTHON -c "
import os
from dotenv import load_dotenv
load_dotenv()
import requests
url = os.getenv('DISCORD_WEBHOOK_URL','')
if not url:
    print('❌ DISCORD_WEBHOOK_URL nije postavljen u .env')
else:
    r = requests.post(url, json={'content':'✅ TicketScout v2 webhook test uspješan!'})
    if r.status_code in (200,204):
        print('✅ Discord webhook radi!')
    else:
        print(f'❌ Discord webhook error: {r.status_code}')
"
fi

# ── 6. Systemd service (opciono za VPS) ───────────────────────────────────────
echo ""
echo "Instalirati systemd servis za auto-start? (y/n)"
read -r INSTALL_SYSTEMD
if [ "$INSTALL_SYSTEMD" = "y" ]; then
    SERVICE_FILE="/etc/systemd/system/ticketscout.service"
    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=TicketScout v2 — Ticket Resale Scout
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON $SCRIPT_DIR/main.py run
Restart=always
RestartSec=30
StandardOutput=append:$SCRIPT_DIR/ticketscout.log
StandardError=append:$SCRIPT_DIR/ticketscout.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable ticketscout
    echo "✅ Systemd servis instaliran."
    echo "   Start: sudo systemctl start ticketscout"
    echo "   Status: sudo systemctl status ticketscout"
    echo "   Logs: journalctl -u ticketscout -f"
fi

echo ""
echo "╔══════════════════════════════════════╗"
echo "║    Setup kompletan!                  ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Pokretanje:"
echo "  python main.py run          # 24/7 scheduler"
echo "  python main.py discover     # samo discovery"
echo "  python main.py restock      # samo restock check"
echo "  python main.py analyse      # samo Claude analiza"
echo "  python main.py status       # DB statistika"
echo "  python main.py run --dry-run # bez Discord"
echo ""
