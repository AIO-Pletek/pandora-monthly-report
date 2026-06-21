#!/usr/bin/env bash
# ============================================================================
# Pandora Monthly Report — Ubuntu VPS Setup
# ============================================================================
# Jalankan sebagai root atau user dengan sudo:
#   chmod +x ubuntu-setup.sh
#   sudo ./ubuntu-setup.sh
#
# Script ini mengasumsikan project sudah di-clone ke /opt/pandora-monthly-report
# (sesuaikan PROJECT_DIR di bawah jika berbeda).
# ============================================================================

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT_DIR="/opt/pandora-monthly-report"
APP_USER="${APP_USER:-www-data}"
VENV_DIR="${PROJECT_DIR}/.venv"
LOG_DIR="/var/log/pandora-report"

echo "=============================================="
echo " Pandora Monthly Report — Ubuntu VPS Setup"
echo "=============================================="
echo " Project dir : ${PROJECT_DIR}"
echo " App user    : ${APP_USER}"
echo ""

# ── 1. System packages ─────────────────────────────────────────────────────
echo " [1/7] Installing system packages..."

apt-get update -qq

# Python + build tools
apt-get install -y -qq \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    pkg-config

# matplotlib dependencies (required untuk render chart ke PNG)
apt-get install -y -qq \
    libfreetype6-dev \
    libpng-dev \
    libjpeg-dev \
    libopenjp2-7 \
    libtiff5

# Fonts — wajib, kalau tidak ada matplotlib crash saat render teks
apt-get install -y -qq \
    fonts-dejavu-core \
    fonts-liberation \
    fontconfig

# Nginx (reverse proxy)
apt-get install -y -qq nginx

echo " ✅ System packages installed."

# ── 2. Create directories ──────────────────────────────────────────────────
echo " [2/7] Creating directories..."

mkdir -p "${LOG_DIR}"
mkdir -p "${PROJECT_DIR}/backend/output"

echo " ✅ Directories created."

# ── 3. App user ────────────────────────────────────────────────────────────
echo " [3/7] Setting up app user..."

if ! id "${APP_USER}" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

echo " ✅ App user '${APP_USER}' ready."

# ── 4. Python virtual environment ──────────────────────────────────────────
echo " [4/7] Creating Python virtual environment..."

if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install -r "${PROJECT_DIR}/requirements.txt" -q

echo " ✅ Python venv + packages installed."

# ── 5. .env file ───────────────────────────────────────────────────────────
echo " [5/7] Checking .env file..."

if [ ! -f "${PROJECT_DIR}/.env" ]; then
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    echo " ⚠  .env created from .env.example — EDIT IT NOW:"
    echo "      nano ${PROJECT_DIR}/.env"
    echo "   Then re-run this script or skip to step 6 manually."
    echo ""
else
    echo " ✅ .env already exists."
fi

chmod 600 "${PROJECT_DIR}/.env" 2>/dev/null || true
chown -R "${APP_USER}:${APP_USER}" "${PROJECT_DIR}" "${LOG_DIR}"

# ── 6. systemd service ─────────────────────────────────────────────────────
echo " [6/7] Installing systemd service..."

cp "${PROJECT_DIR}/deploy/pandora-report.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable pandora-report

echo " ✅ systemd service installed (not started yet)."

# ── 7. Nginx config ────────────────────────────────────────────────────────
echo " [7/7] Installing Nginx config..."

cp "${PROJECT_DIR}/deploy/nginx-pandora-report.conf" /etc/nginx/sites-available/pandora-report

# Enable the site
if [ -f /etc/nginx/sites-enabled/default ]; then
    rm -f /etc/nginx/sites-enabled/default
fi
ln -sf /etc/nginx/sites-available/pandora-report /etc/nginx/sites-enabled/

# Test config
nginx -t

systemctl enable nginx
systemctl reload nginx

echo " ✅ Nginx configured."

# ── Permissions final ──────────────────────────────────────────────────────
chown -R "${APP_USER}:${APP_USER}" "${PROJECT_DIR}" "${LOG_DIR}"

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo " SETUP SELESAI"
echo "=============================================="
echo ""
echo " Sebelum start service, edit .env dulu:"
echo "   nano ${PROJECT_DIR}/.env"
echo ""
echo " Setelah .env benar, start aplikasi:"
echo "   sudo systemctl start pandora-report"
echo ""
echo " Cek status:"
echo "   sudo systemctl status pandora-report"
echo "   sudo journalctl -u pandora-report -f"
echo ""
echo " Akses:"
echo "   http://<ip-vps>  (port 80, diproxy ke uvicorn port 8000)"
echo ""
echo " Untuk HTTPS (opsional):"
echo "   sudo apt-get install -y certbot python3-certbot-nginx"
echo "   sudo certbot --nginx -d your-domain.com"
