#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# TicketScout VPS Setup Script
# Tested on: Ubuntu 22.04 / 24.04
#
# Run as root on a fresh VPS:
#   curl -o setup_vps.sh https://your-server/setup_vps.sh
#   chmod +x setup_vps.sh
#   ./setup_vps.sh
#
# OR copy this file to your VPS and run it.
# ═══════════════════════════════════════════════════════════════════════════════

set -e   # exit on any error

# ── Config — edit these before running ─────────────────────────────────────────
APP_USER="ticketscout"
APP_DIR="/home/$APP_USER/ticketscout_v2"
DOMAIN="YOUR_DOMAIN"          # e.g. scout.yourdomain.com
YOUR_EMAIL="YOUR_EMAIL"       # for Let's Encrypt cert expiry alerts

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║        TicketScout VPS Setup — Starting              ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. System update ───────────────────────────────────────────────────────────
echo "[1/10] Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    nginx certbot python3-certbot-nginx \
    ufw fail2ban git curl unzip \
    supervisor

# ── 2. Create dedicated app user (no login shell, no sudo) ────────────────────
echo "[2/10] Creating app user: $APP_USER..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$APP_USER"
    echo "  Created user: $APP_USER"
else
    echo "  User $APP_USER already exists — skipping"
fi

# ── 3. Copy app files ─────────────────────────────────────────────────────────
echo "[3/10] Copying app files to $APP_DIR..."
# If running from the project directory, copy it:
if [ -f "app.py" ]; then
    mkdir -p "$APP_DIR"
    cp -r . "$APP_DIR/"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    echo "  Copied from current directory"
else
    echo "  NOTE: Copy your project files to $APP_DIR manually, then re-run from step 4."
fi

# ── 4. Python virtual environment & dependencies ──────────────────────────────
echo "[4/10] Installing Python dependencies..."
sudo -u "$APP_USER" bash -c "
    cd $APP_DIR
    python3 -m venv venv
    venv/bin/pip install --upgrade pip -q
    venv/bin/pip install -r requirements.txt -q
"

# ── 5. Firewall (UFW) ─────────────────────────────────────────────────────────
echo "[5/10] Configuring firewall..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp     comment "SSH"
ufw allow 80/tcp     comment "HTTP (redirects to HTTPS)"
ufw allow 443/tcp    comment "HTTPS"
# Port 5001 is NOT opened — Flask only listens on 127.0.0.1, Nginx proxies to it
ufw --force enable
echo "  Firewall rules set. Open ports: 22, 80, 443"

# ── 6. Fail2Ban ───────────────────────────────────────────────────────────────
echo "[6/10] Configuring Fail2Ban..."
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5
ignoreip = 127.0.0.1/8

[sshd]
enabled = true
port    = 22
filter  = sshd
logpath = /var/log/auth.log

[nginx-http-auth]
enabled = true
filter  = nginx-http-auth
logpath = /var/log/nginx/error.log

[nginx-limit-req]
enabled  = true
filter   = nginx-limit-req
logpath  = /var/log/nginx/error.log
maxretry = 10
EOF
systemctl enable fail2ban
systemctl restart fail2ban
echo "  Fail2Ban active — bans IPs after 5 failed attempts"

# ── 7. Nginx config ───────────────────────────────────────────────────────────
echo "[7/10] Setting up Nginx..."

# Add rate limit zone to nginx.conf http block
if ! grep -q "ticketscout_login" /etc/nginx/nginx.conf; then
    sed -i '/http {/a\\tlimit_req_zone $binary_remote_addr zone=ticketscout_login:10m rate=5r/m;' /etc/nginx/nginx.conf
fi

# Copy site config
sed "s/YOUR_DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/ticketscout
ln -sf /etc/nginx/sites-available/ticketscout /etc/nginx/sites-enabled/ticketscout
rm -f /etc/nginx/sites-enabled/default

# Test config (will fail before SSL cert exists — that's ok)
nginx -t 2>/dev/null && systemctl reload nginx || true

# ── 8. SSL Certificate (Let's Encrypt) ───────────────────────────────────────
echo "[8/10] Getting SSL certificate for $DOMAIN..."
echo "  Make sure your domain DNS points to this server's IP first!"
echo ""
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$YOUR_EMAIL" \
    --redirect || {
    echo "  SSL cert failed — run manually after DNS is set:"
    echo "  certbot --nginx -d $DOMAIN -m $YOUR_EMAIL --agree-tos --redirect"
}

# Auto-renew SSL (certbot timer usually installs itself, but make sure)
systemctl enable certbot.timer 2>/dev/null || true

# ── 9. Systemd service ────────────────────────────────────────────────────────
echo "[9/10] Installing systemd service..."
sed "s|/home/ticketscout/ticketscout_v2|$APP_DIR|g" \
    "$APP_DIR/deploy/ticketscout.service" > /etc/systemd/system/ticketscout.service

systemctl daemon-reload
systemctl enable ticketscout
systemctl start ticketscout
sleep 3
systemctl is-active ticketscout && echo "  Service started OK" || echo "  Service failed — check: journalctl -u ticketscout -n 50"

# ── 10. SSH hardening ─────────────────────────────────────────────────────────
echo "[10/10] Hardening SSH..."
SSHD=/etc/ssh/sshd_config

# Disable root login
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' "$SSHD"
# Disable password auth (key-based only)
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD"
# Disable empty passwords
sed -i 's/^#*PermitEmptyPasswords.*/PermitEmptyPasswords no/' "$SSHD"

systemctl reload sshd
echo "  SSH: root login disabled, password auth disabled (keys only)"
echo "  IMPORTANT: Make sure you have an SSH key added before logging out!"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║           Setup Complete!                            ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  App running at: https://$DOMAIN"
echo "║  Service:  systemctl status ticketscout"
echo "║  Logs:     journalctl -u ticketscout -f"
echo "║  Nginx:    tail -f /var/log/nginx/error.log"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Upload your .env file to $APP_DIR/.env"
echo "  2. sudo systemctl restart ticketscout"
echo "  3. Open https://$DOMAIN — log in and change the password"
echo ""
