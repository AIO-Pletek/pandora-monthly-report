# Pandora Monthly Report — Ubuntu VPS Deployment

## Ringkasan Kebutuhan

| Kategori | Package / Tool | Kenapa |
|---|---|---|
| **Runtime** | Python 3.11+ | Versi Python yang dipakai |
| **APT** | `python3-venv`, `python3-dev`, `build-essential`, `pkg-config` | Virtual env + kompilasi package |
| **APT** | `libfreetype6-dev`, `libpng-dev`, `libjpeg-dev`, `libtiff5` | matplotlib render chart PNG |
| **APT** | `fonts-dejavu-core`, `fonts-liberation`, `fontconfig` | Font untuk label chart (wajib!) |
| **Reverse proxy** | `nginx` | Proxy HTTP ke uvicorn |
| **HTTPS (opsional)** | `certbot`, `python3-certbot-nginx` | Gratis SSL dari Let's Encrypt |
| **Python** | `fastapi`, `uvicorn[standard]`, `httpx`, `python-docx`, `matplotlib`, `jinja2` | (`requirements.txt`) |

## Quick Deploy

### 1. Clone project ke VPS

```bash
git clone <repo-url> /opt/pandora-monthly-report
cd /opt/pandora-monthly-report
```

### 2. Jalankan setup script

```bash
chmod +x deploy/ubuntu-setup.sh
sudo ./deploy/ubuntu-setup.sh
```

Ini akan otomatis: install system packages → buat virtual env → install pip → konfigurasi systemd → konfigurasi nginx.

### 3. Isi .env

```bash
sudo nano /opt/pandora-monthly-report/.env
```

### 4. Start service

```bash
sudo systemctl start pandora-report
sudo systemctl status pandora-report
```

### 5. Akses

```
http://<ip-vps>
```

## File Layout di VPS

```
/opt/pandora-monthly-report/
├── .env                          # → ISI MANUAL (kredensial Pandora)
├── .venv/                        # → auto-dibuat oleh setup script
├── backend/
│   ├── main.py                   # FastAPI entry point
│   ├── config.py
│   ├── pandora_client.py
│   ├── report_builder.py
│   ├── models.py
│   ├── output/                   # hasil generate .docx
│   └── templates/
├── deploy/
│   ├── ubuntu-setup.sh
│   ├── pandora-report.service
│   └── nginx-pandora-report.conf
└── requirements.txt

/etc/systemd/system/pandora-report.service  → dari deploy/
/etc/nginx/sites-enabled/pandora-report     → dari deploy/
/var/log/pandora-report/                    → log aplikasi
```

## Manajemen Service

```bash
sudo systemctl start pandora-report     # Start
sudo systemctl stop pandora-report      # Stop
sudo systemctl restart pandora-report   # Restart
sudo systemctl status pandora-report    # Status
sudo journalctl -u pandora-report -f    # Log real-time
```

## Troubleshooting

**matplotlib crash / "no display":**
```python
# Pastikan ini dipanggil SEBELUM import matplotlib.pyplot:
import matplotlib
matplotlib.use("Agg")  # backend non-interaktif
```

**Font error:**
```bash
sudo apt-get install -y fonts-dejavu-core fonts-liberation
fc-cache -fv
```

**Nginx 502 Bad Gateway:**
```bash
sudo systemctl status pandora-report
sudo journalctl -u pandora-report -n 50
```

**Auth error dari Pandora:**
Cek `.env` — `PANDORA_API_USER`, `PANDORA_API_USER_PASS`, `PANDORA_API_PASSWORD` harus diisi dari Pandora Console (Setup → General Setup).
