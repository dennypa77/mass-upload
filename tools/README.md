# Tools Shopee Mass Upload

Membuat file Excel siap upload ke Shopee dari daftar SKU + foto produk di Google Drive.
Kalau ada SKU, desain, seri, atau toko baru — cukup update `data/sku.csv` dan `tools/config.json`,
lalu jalankan ulang. Tidak perlu menyentuh kode.

## Sekali saja: pasang kebutuhan

```
pip install openpyxl pillow
```

## Cara pakai

Cara termudah: klik dua kali **`UI.bat`** — semua langkah ada tombolnya, lengkap dengan
status dan log. Alternatif lewat menu teks: `jalankan.bat`.

Lewat baris perintah, jalankan dari folder utama project (bukan dari dalam `tools/`):

| Perintah | Fungsi |
|---|---|
| `python tools/shopee_mass_upload.py impor "SKU.xlsx"` | Ubah ekspor sheet SKU jadi `data/sku.csv` |
| `python tools/shopee_mass_upload.py foto` | Salin + rename foto dari Google Drive ke `foto-upload/` |
| `python tools/shopee_mass_upload.py url` | Tulis pemetaan file lokal → URL ke `data/url_foto.csv` |
| `python tools/shopee_mass_upload.py cek` | Laporan kelengkapan data & foto, tanpa membuat file |
| `python tools/shopee_mass_upload.py build` | Membuat file Excel di `output/` |
| `python tools/shopee_mass_upload.py semua` | foto + url + cek + build |

## Daftar URL: `data/url_foto.csv`

Perintah `url` menyimpan pemetaan lengkap dari setiap file foto lokal ke alamat publiknya:

| Kolom | Isi |
|---|---|
| `nama_toko` / `folder_toko` | `Graphica Key` / `toko1` |
| `jenis` | `JIBBITZ`, `PIN AKRILIK`, `PIN BUTTON` |
| `kunci` | `JB-0000001` (varian) atau `JB-CORTIS-utama1` (foto utama) |
| `tipe` | `varian` atau `utama` |
| `file_lokal` | path lengkap file di komputer |
| `url` | alamat publik yang dipakai Shopee |
| `ukuran_byte` | ukuran file, untuk memastikan tidak melebihi 2 MB |

Berguna untuk mengecek satu per satu kalau ada foto yang tidak muncul di Shopee: cari
SKU-nya di file ini, buka URL-nya di browser. Kalau gambarnya tampil, berarti masalahnya
di sisi Shopee, bukan di hosting.

Perintah `url` butuh `foto.base_url` sudah diisi, dan akan menolak dengan pesan jelas
kalau masih kosong.

## Alur kerja

```
ekspor Google Sheet ──► impor ──► data/sku.csv ─┐
                                                 │
G:\My Drive\ (foto lokal) ──► foto ──► foto-upload/ ──► push ke GitHub
                                                 │              │
                                                 │      isi base_url di UI
                                                 │              │
                                                 │              ▼
                                                 │      url ──► data/url_foto.csv
                                                 │              (file lokal → URL)
                                                 ▼              │
                        tools/config.json ──► build ◄───────────┘
                                                 │
                                                 ▼
                                          output/*.xlsx
```

1. **`foto`** — memindai Google Drive, menyalin foto ke `foto-upload/<toko>/<jenis>/`,
   me-rename foto utama (`foto1.png` → `PA-CORTIS-utama1.png`), mengecilkan foto >2 MB,
   membuang file sampah, lalu menulis `_manifest.json`.
2. **Upload `foto-upload/` ke GitHub**, lalu isi `foto.base_url` di `config.json`:
   ```
   "base_url": "https://cdn.jsdelivr.net/gh/USERNAME/REPO@main"
   ```
   Kalau dibiarkan `null`, kolom foto akan dikosongkan (produk tetap bisa diupload,
   foto ditambahkan manual lewat Seller Centre).
3. **`build`** — menghasilkan satu file Excel per toko per template kategori.

## Mengisi `data/sku.csv` dari Google Sheet

Di Google Sheet SKU: **File → Download → Microsoft Excel (.xlsx)**, lalu:

```
python tools/shopee_mass_upload.py impor "PRODUK BARU.xlsx"
```

Semua tab dibaca sekaligus. Tools mencari baris judul yang memuat kolom **Nama Produk**,
**Varian**, dan **SKU** — jadi baris riset/catatan di atas tabel diabaikan sendiri.

Jenis produk **diambil dari prefix SKU** (`JB-` → JIBBITZ, `PA-` → PIN AKRILIK,
`PB-` → PIN BUTTON), bukan dari kolom "Bentuk". Ini disengaja: kolom Bentuk di sheet
punya salah ketik (`PIN BUNTTON`) yang kalau dipakai akan bikin data gagal terbaca.
SKU kembar otomatis dibuang, dan `sku.csv` lama dicadangkan ke `sku.csv.bak`.

## Input: `data/sku.csv`

| Kolom | Wajib | Contoh |
|---|---|---|
| `jenis` | ya | `PIN AKRILIK` (harus cocok dengan key di `config.json` → `jenis`) |
| `seri` | ya | `PIN AKRILIK CUSTOM KPOP - CORTIS SERIES` — jadi nama tengah judul |
| `varian` | ya | `Desain 01` (maks. 20 karakter) |
| `sku` | ya | `PA-0000001` — harus sama dengan nama file fotonya |
| `kode_seri` | tidak | `CORTIS`. Kalau kosong, diambil otomatis dari `seri` |

Satu baris = satu varian. Semua baris dengan `seri` yang sama digabung jadi satu listing.

## Menambah sesuatu

**Desain/SKU baru** → tambah baris di `sku.csv`, taruh fotonya di folder Drive dengan nama
file = SKU, jalankan `semua`.

**Seri baru** → sama seperti di atas. Folder foto di Drive dicari otomatis berdasarkan nomor
SKU (`PA-0000251` → folder `PRODUK 00251 - 00300`), jadi tidak perlu didaftarkan.

**Toko baru** → tambah entri di `config.json` → `toko`. Isi `folder_foto` sesuai nama folder
hasil scan (`toko1`/`toko2`/…, diambil dari angka pada nama folder `TOKO 1` di Drive) dan
`profil` sesuai gaya judul/deskripsi yang dipakai.

**Jenis produk baru** → tambah entri di `config.json` → `jenis`, dan pastikan template
kategorinya terdaftar di `config.json` → `template`.

**Kategori/harga/berat/spesifikasi berubah** → cukup ubah di `config.json`.

## Yang otomatis diurus

- Judul dirakit dari `prefix - seri - suffix`, dan diputar antar seri supaya tidak kembar
- `{TOKO}` dan `{SPEC}` di deskripsi diganti nama toko & spesifikasi produk
- Harga per pcs dihitung dari `harga_paket ÷ min_order`
- Foto varian bersifat semua-atau-tidak-sama-sekali: kalau ada satu varian tanpa foto,
  seluruh kolom foto varian di listing itu dikosongkan. Varian tanpa foto sendiri
  (mis. `CUSTOM`) otomatis memakai foto utama
- Kolom dicari lewat **kode field** di baris 1 template (`ps_price`, `ps_stock`, …), bukan
  posisi kolom — jadi tetap jalan kalau Shopee mengubah susunan template
- Atribut diisi lewat **ID atribut** (`100037` = Asal Produk), yang sama di semua template
- `cek` memperingatkan: judul/deskripsi di luar batas, SKU atau nama varian kembar,
  listing tanpa foto, dan atribut wajib kategori yang belum diisi

## Batasan Shopee yang dipakai

| Hal | Batas |
|---|---|
| Judul produk | 5–255 karakter |
| Deskripsi | 20–3.000 karakter |
| Nama variasi | maks. 14 karakter |
| Nama varian | maks. 20 karakter |
| Harga | Rp99 – Rp1.000.000.000, rasio termahal:termurah maks. 7× dalam 1 listing |
| Stok | 0 – 10.000.000 |
| Ukuran foto | maks. 2 MB, format JPG/JPEG/PNG |
| Jumlah varian | 20 untuk 1 level variasi, 50 untuk 2 level (menurut sheet `Panduan`) |

Catatan: batas jumlah varian di atas mengikuti sheet `Panduan` bawaan template. Berdasarkan
pengalaman, 1 level variasi masih diterima sampai ~60 varian. Tools ini memakai 1 level
variasi. Kalau file ditolak Shopee karena kelebihan varian, seri perlu dipecah jadi beberapa
listing.

## Kalau error

**`Unable to read workbook ... invalid XML`** — sudah ditangani. Template Shopee memakai nilai
`activePane` yang tidak baku sehingga ditolak openpyxl; tools ini melonggarkannya di awal file.

**`File input tidak ada: data/sku.csv`** — buat file CSV-nya dulu, atau jalankan dari folder
utama project (bukan dari dalam `tools/`).

**`folder foto belum ada: ...`** — folder `PRODUK xxxxx - xxxxx` untuk seri itu belum ada di
Drive, atau nomor SKU-nya di luar rentang folder mana pun. Listing tetap dibuat, hanya tanpa foto.
