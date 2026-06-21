# CLAUDE.md — pandora-monthly-report

Instruksi project-level untuk Claude Code. Baca ini dulu sebelum mengerjakan apa pun di repo ini.

## Ringkasan Project

Web app internal untuk generate **laporan bulanan (.docx)** dari data **Pandora FMS (Open Source/Community)**, per **tenant** (tenant = mapping ke Group di Pandora FMS). User pilih tenant + bulan di UI, sistem ambil data dari Pandora FMS REST API, lalu generate dokumen Word otomatis untuk didownload.

Konten report per tenant mencakup:
- Availability / uptime per agent
- Alerts / events (jumlah & severity)
- Performance metrics: CPU, RAM, disk usage

## Stack

- **Backend:** Python 3.11+, FastAPI
- **Doc generation:** `python-docx`
- **Chart:** `matplotlib` (render ke PNG, lalu di-embed ke docx)
- **HTTP client ke Pandora:** `httpx`
- **Frontend:** sederhana — server-rendered (Jinja2) atau React minimal. Jangan over-engineer; ini internal tool, bukan produk publik.
- **Scheduling (opsional, fase lanjutan):** APScheduler atau cron yang hit endpoint generate.

Jangan ganti stack ini tanpa didiskusikan dengan user dulu.

## Struktur Folder (target)

```
pandora-monthly-report/
├── CLAUDE.md
├── .env.example
├── .claude/
│   └── skills/
│       └── pandora-report/SKILL.md
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── pandora_client.py
│   ├── report_builder.py
│   ├── models.py
│   ├── templates/
│   │   └── monthly_report_template.docx
│   └── output/              # hasil generate, JANGAN di-commit (gitignore)
└── frontend/
    └── ...
```

## Pandora FMS API — Aturan Wajib

Base endpoint selalu: `{PANDORA_BASE_URL}/include/api.php`

Auth pakai query/POST param: `apipass`, `user`, `pass` — **bukan** Bearer token kecuali versi Pandora yang dipakai sudah support itu (cek dulu, jangan asumsi).

Operasi (`op=get`, `op2=...`) yang relevan untuk project ini:

| op2 | Fungsi | Catatan |
|---|---|---|
| `test` | Cek koneksi & versi | Pakai untuk health check |
| `all_agents` | List agent, bisa difilter `id_group` | Ini cara kita filter "per tenant" |
| `module_groups` | List grup module | Untuk referensi |
| `module_data` | Data historis 1 module dalam range waktu | Untuk uptime, CPU, RAM, disk per agent |
| `events` | List event/alert, filter by group & tanggal | Untuk bagian alerts di report |
| `license` / `license_remaining` | Info lisensi | Tidak prioritas, skip kecuali diminta |

Aturan keras:
1. **Tenant = Pandora Group.** Setiap kali query agent/event, selalu filter berdasarkan `id_group` milik tenant yang dipilih user. Jangan pernah return data lintas-tenant tanpa filter eksplisit.
2. **Selalu pakai `return_type=json`** kecuali ada alasan kuat pakai CSV.
3. **Jangan hardcode** `apipass`, `user`, `pass`, atau `PANDORA_BASE_URL` di kode. Semua dari environment variable (lihat `.env.example`).
4. Sebelum implementasi operasi API baru yang belum ada di tabel di atas, cek dulu dokumentasi resmi Pandora FMS (`External API` technical reference) — jangan menebak nama parameter `other` karena formatnya pakai separator `|` yang urutannya kaku dan beda-beda per operasi.
5. Tangani kemungkinan API mengembalikan `"auth error"` (plain string, bukan JSON) — selalu validasi response sebelum `json.loads`.

## Konvensi Kode

- Python: ikuti PEP8, type hints di semua fungsi public.
- Semua fungsi yang call Pandora API harus async (`httpx.AsyncClient`) dan punya timeout eksplisit (jangan biarkan default infinite).
- Logic "ambil data dari Pandora" (`pandora_client.py`) harus terpisah total dari logic "susun docx" (`report_builder.py`). Jangan campur HTTP call dengan manipulasi `python-docx` di file/fungsi yang sama.
- Setiap fungsi yang mengambil data numerik dari Pandora harus eksplisit menangani kemungkinan `null`/missing data (sensor down, agent belum pernah lapor, dll) — jangan biarkan report crash atau menampilkan angka palsu (misal 0 padahal harusnya "no data").

## Yang TIDAK boleh dilakukan tanpa konfirmasi user

- Jangan ubah skema/struktur docx template tanpa menunjukkan preview/perubahan ke user dulu.
- Jangan menambah dependency besar (database baru, message queue, dll) di luar yang disebut di Stack tanpa diskusi.
- Jangan commit file `.env`, file di `backend/output/`, atau kredensial apa pun.
- Jangan mengganggu/menulis ke instance Pandora FMS (semua call ke Pandora di project ini harus `op=get`, tidak ada `op=set`).

## Workflow yang Diharapkan

1. Baca `.claude/skills/pandora-report/SKILL.md` untuk detail format report & contoh payload API sebelum mulai coding fitur baru terkait Pandora atau docx.
2. Saat menambah field baru ke report, cek dulu apakah datanya tersedia dari operasi API yang sudah ada di tabel di atas sebelum mengusulkan operasi baru.
3. Tulis kode secara modular per langkah di atas (jangan satu file raksasa).
4. Setelah implementasi fitur, jalankan test manual terhadap Pandora API sungguhan (kredensial ada di `.env`, sudah disiapkan user) sebelum melaporkan selesai.