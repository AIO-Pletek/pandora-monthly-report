---
name: pandora-report
description: Gunakan skill ini setiap kali mengerjakan apa pun yang berkaitan dengan mengambil data dari Pandora FMS REST API dan/atau menyusun monthly report dalam format .docx per tenant. Termasuk menambah metric baru, mengubah cara filter tenant, memperbaiki bug di pandora_client.py atau report_builder.py, atau mendesain ulang struktur dokumen. Picu skill ini untuk task: "tambah data X ke report", "kenapa angka uptime salah", "generate report bulan Y untuk tenant Z", "ubah layout docx", "tambah chart baru".
---

# Skill: Pandora FMS Monthly Report Generation

Skill ini berisi detail teknis spesifik project yang tidak ada di pengetahuan umum — terutama soal kuirk Pandora FMS API dan struktur konten report yang disepakati.

## 1. Autentikasi & Request Dasar

Semua request ke Pandora FMS API:

```
GET/POST {PANDORA_BASE_URL}/include/api.php
```

Parameter wajib di setiap request:
- `op=get` (project ini read-only, tidak pernah `op=set`)
- `op2=<nama_operasi>`
- `apipass=<API_PASSWORD>` (dari Setup > General Setup di Pandora, bukan password user)
- `user=<API_USER>`
- `pass=<API_USER_PASSWORD>`
- `return_type=json`

Contoh wrapper dasar (`pandora_client.py`):

```python
import httpx

class PandoraClient:
    def __init__(self, base_url: str, api_user: str, api_pass: str, api_password: str):
        self.base_url = base_url.rstrip("/") + "/include/api.php"
        self.api_user = api_user
        self.api_pass = api_pass
        self.api_password = api_password

    async def _call(self, op2: str, extra_params: dict | None = None, id_: str | None = None) -> dict | list:
        params = {
            "op": "get",
            "op2": op2,
            "user": self.api_user,
            "pass": self.api_pass,
            "apipass": self.api_password,
            "return_type": "json",
        }
        if id_ is not None:
            params["id"] = id_
        if extra_params:
            params.update(extra_params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()

        text = resp.text.strip()
        if text == '"auth error"' or text == "auth error":
            raise PermissionError("Pandora API auth error — cek apipass/user/pass di .env")

        return resp.json()
```

**PENTING:** Pandora API kadang return plain string `"auth error"` bukan JSON kalau auth gagal. Selalu cek ini sebelum parse JSON, atau `json.loads`/`.json()` akan throw error yang membingungkan.

## 2. Mapping Tenant → Group

Tenant di project ini **selalu** dipetakan ke `id_group` Pandora. Tidak ada tabel mapping tambahan di luar Pandora — kita percaya struktur Group yang sudah dikonfigurasi di Pandora Console sebagai source of truth.

Ambil daftar tenant (= daftar group) untuk dropdown UI:

```python
# op2=all_agents bisa difilter id_group, tapi untuk DAFTAR GROUP itu sendiri,
# gunakan op2=module_groups atau (lebih umum) ambil dari tabel groups via
# op yang sesuai — KONFIRMASI dulu di instance Pandora yang dipakai user,
# karena nama operasi list-group bisa berbeda antar minor version.
# Jangan tebak — test langsung ke instance, lihat response-nya.
```

> Catatan untuk implementer: sebelum hardcode nama operasi list-group, jalankan `op=get&op2=test` dulu untuk pastikan koneksi & versi, baru explore operasi list group yang valid di versi tersebut.

Setelah dapat `id_group` tenant terpilih, ambil semua agent di group itu:

```python
async def get_agents_by_tenant(self, id_group: int) -> list[dict]:
    # other format: pipe-separated, urutan params HARUS sesuai dokumentasi
    # all_agents: other = id_os|id_group|status|search|policy|...
    other = f"|{id_group}||||"
    data = await self._call(
        "all_agents",
        extra_params={
            "other": other,
            "other_mode": "url_encode_separator_|",
        },
    )
    return data.get("data", []) if isinstance(data, dict) else data
```

**Aturan keras:** setiap fungsi yang mengambil agent/event/module data WAJIB menerima `id_group` sebagai parameter eksplisit dan menyertakannya di filter. Tidak ada fungsi yang "ambil semua lalu filter di Python" kecuali API tidak menyediakan filter server-side untuk kasus itu — dan itu harus didokumentasikan kenapa.

## 3. Mengambil Data per Metric

### Uptime / Availability

Gunakan `module_data` dengan range tanggal awal-akhir bulan report. Module uptime biasanya bernama mengandung "Host Alive" atau sejenis — **jangan asumsikan nama module sama di semua environment**, ambil dulu daftar module per agent, baru cocokkan berdasarkan nama atau type, dan dokumentasikan asumsi penamaan yang dipakai di komentar kode.

### Performance (CPU/RAM/Disk)

Sama, lewat `module_data` per agent per module yang relevan, dengan range waktu = 1 bulan penuh. Hitung agregat (avg, max, min) di sisi Python — Pandora API mengembalikan raw data points, bukan agregat siap pakai.

```python
async def get_module_data(self, module_id: int, date_start: str, date_end: str) -> list[dict]:
    other = f";|0|{date_start}|{date_end}"
    data = await self._call(
        "module_data",
        extra_params={"other": other, "other_mode": "url_encode_separator_|"},
        id_=str(module_id),
    )
    return data
```

> Format `other` di atas adalah pola umum dari dokumentasi resmi (`error_value|period|date_start|date_end` dengan variasi). **Selalu verifikasi urutan parameter `other` di dokumentasi resmi versi Pandora yang dipakai sebelum deploy** — parameter ini gampang salah urutan dan errornya silent (return data kosong, bukan error jelas).

### Alerts / Events

```python
async def get_events_by_tenant(self, id_group: int, date_start: str, date_end: str) -> list[dict]:
    # cek urutan parameter `other` untuk op2=events di dokumentasi —
    # ada banyak slot (severity, status, search, group, dst) yang harus
    # diisi sesuai posisi meski kosong (separator | tetap harus ada)
    ...
```

Hitung breakdown severity (critical/warning/normal/unknown) di sisi Python setelah data event didapat.

## 4. Struktur Konten Report (.docx)

Urutan section yang disepakati (ubah hanya dengan approval user):

1. **Cover** — nama tenant, periode (bulan-tahun), tanggal generate
2. **Executive Summary** — ringkasan singkat: jumlah agent, % avg uptime keseluruhan, total alert critical
3. **Availability** — tabel per agent: nama agent, % uptime, downtime (jam)
4. **Performance Metrics** — per agent: chart/tabel CPU avg/max, RAM avg/max, Disk usage avg/max
5. **Alerts & Events** — tabel breakdown severity + daftar event critical (kalau ada)
6. **Notes/Footer** — generated by, timestamp

## 5. Generate Docx

Pakai `python-docx`. Pola dasar:

```python
from docx import Document
from docx.shared import Inches

def build_report(tenant_name: str, period: str, summary: dict, agents_data: list[dict], events_summary: dict, chart_paths: dict) -> str:
    doc = Document()
    doc.add_heading(f"Monthly Report — {tenant_name}", level=0)
    doc.add_paragraph(f"Periode: {period}")

    doc.add_heading("Executive Summary", level=1)
    doc.add_paragraph(
        f"Total Agent: {summary['total_agents']} | "
        f"Avg Uptime: {summary['avg_uptime']:.2f}% | "
        f"Critical Alerts: {summary['critical_alerts']}"
    )

    doc.add_heading("Availability", level=1)
    table = doc.add_table(rows=1, cols=3)
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Agent", "Uptime (%)", "Downtime (h)"
    for agent in agents_data:
        row = table.add_row().cells
        row[0].text = agent["name"]
        row[1].text = f"{agent['uptime_pct']:.2f}"
        row[2].text = f"{agent['downtime_hours']:.1f}"

    doc.add_heading("Performance Metrics", level=1)
    if chart_paths.get("performance"):
        doc.add_picture(chart_paths["performance"], width=Inches(6))

    doc.add_heading("Alerts & Events", level=1)
    doc.add_paragraph(
        f"Critical: {events_summary['critical']} | "
        f"Warning: {events_summary['warning']} | "
        f"Normal: {events_summary['normal']}"
    )

    output_path = f"backend/output/{tenant_name}_{period}.docx"
    doc.save(output_path)
    return output_path
```

Untuk chart, render dengan `matplotlib` → `savefig(path, dpi=150)` → embed lewat `add_picture`. Selalu pakai `Agg` backend (`matplotlib.use("Agg")`) karena ini server-side, tanpa display.

## 6. Penanganan Data Kosong/Hilang

Kalau agent tidak punya data di rentang waktu tertentu (sensor down, baru ditambahkan pertengahan bulan, dll):
- Jangan tampilkan `0%` uptime jika sebenarnya "tidak ada data" — bedakan eksplisit antara "0% uptime" dan "no data", misal tampilkan "N/A" di tabel.
- Jangan biarkan chart generation crash karena array kosong — selalu cek `len(data) == 0` dulu dan skip/beri placeholder.

## 7. Checklist Sebelum Lapor "Selesai"

- [ ] Sudah test call ke instance Pandora asli (bukan mock) minimal untuk `test`, `all_agents`, `module_data`, `events`
- [ ] Auth error ditangani dengan jelas (bukan crash mentah)
- [ ] Filter tenant (`id_group`) konsisten di semua query
- [ ] Data kosong/missing ditangani, tidak menghasilkan angka menyesatkan
- [ ] File docx berhasil dibuka & terbaca rapi di Word/LibreOffice
- [ ] Tidak ada kredensial hardcoded di kode