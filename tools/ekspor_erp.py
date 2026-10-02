# -*- coding: utf-8 -*-
"""Ekspor seluruh data lokal tools ini ke satu paket, untuk dipindahkan ke ERP.

Selama ini catatan pekerjaan hanya ada di komputer masing-masing: tahap tiap
folder per toko, hasil upload Shopee per produk, daftar SKU, dan foto mana yang
sudah terunggah beserta alamat publiknya. Perintah ini menyalin semuanya jadi
beberapa CSV datar + manifes, lalu memampatkannya jadi satu berkas .zip yang
tinggal dikirim ke tim ERP.

Yang TIDAK ikut: data/lokal.json. Berkas itu memuat kunci Cloudflare R2 dan
folder sumber tiap komputer. Pengaturan bersama diambil dari tools/config.json
yang memang sudah publik.
"""
import csv
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile

JUDUL = 'Ekspor data tools Shopee Mass Upload'


def _tulis(tujuan, nama, kolom, baris):
    """Tulis satu CSV; kembalikan jumlah barisnya."""
    n = 0
    with open(os.path.join(tujuan, nama), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=kolom, extrasaction='ignore')
        w.writeheader()
        for b in baris:
            w.writerow(b)
            n += 1
    return n


def _salin(asal, tujuan, nama):
    """Salin berkas CSV apa adanya; kembalikan jumlah baris datanya."""
    if not os.path.exists(asal):
        return None
    shutil.copy2(asal, os.path.join(tujuan, nama))
    with open(asal, encoding='utf-8-sig', newline='') as f:
        return max(sum(1 for _ in f) - 1, 0)


# --------------------------------------------------------------------------- tabel
def tabel_toko(cfg):
    return [{'kode': t.get('kode'), 'nama': t['nama'], 'folder_foto': t['folder_foto'],
             'profil': t.get('profil'), 'alias': ';'.join(t.get('alias') or [])}
            for t in cfg['toko']]


def tabel_jenis(cfg):
    hasil = []
    for nama, j in cfg['jenis'].items():
        hasil.append({'jenis': nama, 'prefix_sku': j.get('prefix_sku'), 'slug': j.get('slug'),
                      'template': j.get('template'), 'kategori': j.get('kategori'),
                      'harga_paket': j.get('harga_paket'), 'min_order': j.get('min_order'),
                      'berat_gram': j.get('berat_gram'), 'stok': j.get('stok'),
                      'spec': j.get('spec')})
    return hasil


def tabel_folder(inti, cfg, tahap, daftar_toko, indeks_sku):
    """Satu baris per folder produk di Drive, dengan tahap tiap toko jadi kolom."""
    import cek_gambar
    kolom = ['jenis', 'dari', 'sampai', 'nama_folder', 'sumber', 'jumlah_sku', 'jumlah_seri',
             'tahap_ringkas']
    for t in daftar_toko:
        kolom += ['tahap_' + t, 'waktu_' + t, 'catatan_' + t]
    baris, terlihat = [], set()
    for jenis, path in cek_gambar.folder_drive(cfg, inti):
        m = re.match(r'^PRODUK\s+0*(\d+)\s*-\s*0*(\d+)$', os.path.basename(path).strip(), re.I)
        if not m:
            continue
        dari, sampai = int(m.group(1)), int(m.group(2))
        if (jenis.upper(), dari, sampai) in terlihat:
            continue                      # sumber paling depan yang dipakai, seperti di UI
        terlihat.add((jenis.upper(), dari, sampai))
        per_toko = inti.tahap_per_toko(tahap, jenis, dari, sampai, daftar_toko)
        isi = indeks_sku.get(jenis.upper(), {})
        sku = [s for s in isi if dari <= s <= sampai]
        b = {'jenis': jenis.upper(), 'dari': dari, 'sampai': sampai,
             'nama_folder': os.path.basename(path), 'sumber': path,
             'jumlah_sku': len(sku), 'jumlah_seri': len({isi[s] for s in sku}),
             'tahap_ringkas': inti.ringkas_tahap(per_toko)}
        for t in daftar_toko:
            r = per_toko.get(t) or {}
            b['tahap_' + t] = r.get('tahap') or 'belum'
            b['waktu_' + t] = r.get('waktu') or ''
            b['catatan_' + t] = r.get('catatan') or ''
        baris.append(b)
    baris.sort(key=lambda b: (b['jenis'], b['dari']))
    return kolom, baris


def tabel_listing(inti, cfg, data, tahap, peta_url, hasil_produk):
    """Satu baris per listing per toko — inilah yang jadi produk di Shopee.

    Dikumpulkan dari daftar SKU, bukan dari foto, supaya seri yang fotonya belum
    siap pun ikut terdata.
    """
    kolom = ['jenis', 'seri', 'kode_seri', 'toko_kode', 'toko_nama', 'kode_induk', 'sku_induk',
             'jumlah_varian', 'sku_awal', 'sku_akhir', 'folder_dari', 'folder_sampai',
             'tahap', 'tahap_waktu', 'hasil_shopee', 'penyebab_gagal', 'alasan_shopee',
             'foto_varian_terunggah', 'url_utama1', 'url_utama2', 'url_utama3']
    baris = []
    for jenis, seri_semua in data.items():
        j = cfg['jenis'][jenis]
        for seri, desain in seri_semua.items():
            kode = desain[0]['kode_seri']
            nomor = [n for n in (inti.nomor_sku(d['sku']) for d in desain) if n]
            for t in cfg['toko']:
                tk = t['folder_foto']
                url = peta_url.get(tk, {})
                kode_induk = '{}-{}-{}'.format(t.get('kode'), j['prefix_sku'], kode)
                r = (inti.tahap_per_toko(tahap, jenis, min(nomor), max(nomor), [tk]).get(tk)
                     if nomor else None) or {}
                h = hasil_produk.get(tk + '|' + kode_induk, {})
                b = {'jenis': jenis, 'seri': seri, 'kode_seri': kode, 'toko_kode': t.get('kode'),
                     'toko_nama': t['nama'], 'kode_induk': kode_induk,
                     'sku_induk': desain[0]['sku'], 'jumlah_varian': len(desain),
                     'sku_awal': desain[0]['sku'], 'sku_akhir': desain[-1]['sku'],
                     'folder_dari': min(nomor) if nomor else '',
                     'folder_sampai': max(nomor) if nomor else '',
                     'tahap': r.get('tahap') or 'belum', 'tahap_waktu': r.get('waktu') or '',
                     'hasil_shopee': h.get('status') or '', 'penyebab_gagal': h.get('penyebab') or '',
                     'alasan_shopee': h.get('alasan') or '',
                     'foto_varian_terunggah': sum(1 for d in desain if d['sku'].upper() in url)}
                for n in (1, 2, 3):
                    b['url_utama{}'.format(n)] = url.get(
                        '{}-{}-UTAMA{}'.format(j['prefix_sku'], kode, n).upper()) or ''
                baris.append(b)
    return kolom, baris


def tabel_foto(inti):
    """Seluruh isi data/foto.db: foto mana sudah terunggah dan alamat publiknya."""
    if not os.path.exists(inti.DB_PATH):
        return [], []
    db = sqlite3.connect(inti.DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        baris = [dict(r) for r in db.execute(
            'SELECT toko, kunci, nama_toko, jenis, seri, tipe, file_lokal, path_repo, url, '
            'ukuran, diunggah, waktu FROM foto ORDER BY toko, jenis, kunci')]
    finally:
        db.close()
    return (list(baris[0]) if baris else []), baris


# --------------------------------------------------------------------------- ekspor
def ekspor(inti, cfg, cetak=print, tujuan=None):
    """Tulis paket ekspor; kembalikan ringkasan untuk halaman."""
    cap = time.strftime('%Y-%m-%d %H.%M.%S')
    komputer = os.environ.get('COMPUTERNAME') or ''
    dasar = tujuan or os.path.join(inti.DIR_OUT, 'ekspor_erp')
    folder = os.path.join(dasar, '{} {}'.format(cap, komputer).strip())
    os.makedirs(folder, exist_ok=True)
    cetak('[erp] menyiapkan paket di {}'.format(folder))

    daftar_toko = [t['folder_foto'] for t in cfg['toko']]
    tahap = inti.baca_tahap()
    data = inti.baca_sku()
    jumlah = {}

    jumlah['toko.csv'] = _tulis(folder, 'toko.csv',
                                ['kode', 'nama', 'folder_foto', 'profil', 'alias'],
                                tabel_toko(cfg))
    jumlah['jenis_produk.csv'] = _tulis(
        folder, 'jenis_produk.csv',
        ['jenis', 'prefix_sku', 'slug', 'template', 'kategori', 'harga_paket', 'min_order',
         'berat_gram', 'stok', 'spec'], tabel_jenis(cfg))

    # nomor SKU -> seri, dipakai untuk menghitung isi tiap folder
    indeks = {}
    for jenis, seri_semua in data.items():
        for seri, desain in seri_semua.items():
            for d in desain:
                n = inti.nomor_sku(d['sku'])
                if n:
                    indeks.setdefault(jenis.upper(), {})[n] = seri
    kolom, baris = tabel_folder(inti, cfg, tahap, daftar_toko, indeks)
    jumlah['folder_produk.csv'] = _tulis(folder, 'folder_produk.csv', kolom, baris)
    cetak('[erp] {} folder produk'.format(jumlah['folder_produk.csv']))

    kolom, baris = tabel_foto(inti)
    jumlah['foto.csv'] = _tulis(folder, 'foto.csv', kolom, baris) if kolom else 0
    terunggah = sum(1 for b in baris if b.get('diunggah'))
    cetak('[erp] {} foto ({} sudah di R2)'.format(jumlah['foto.csv'], terunggah))

    # alamat foto per toko untuk tabel listing, dari foto yang benar-benar terunggah
    peta = {}
    for b in baris:
        if b.get('diunggah') and b.get('url'):
            peta.setdefault(b['toko'], {})[(b['kunci'] or '').upper()] = b['url']

    hasil_produk = {}
    try:
        import hasil_shopee
        hasil_produk = hasil_shopee.baca_rekaman()
    except Exception as e:
        cetak('[erp] ! hasil upload Shopee tidak terbaca: {}'.format(e))
    kolom, baris = tabel_listing(inti, cfg, data, tahap, peta, hasil_produk)
    jumlah['listing.csv'] = _tulis(folder, 'listing.csv', kolom, baris)
    cetak('[erp] {} listing (seri x toko)'.format(jumlah['listing.csv']))

    for asal, nama in ((inti.BERKAS_TAHAP, 'tahap_folder.csv'),
                       (os.path.join(inti.AKAR, 'data', 'hasil_shopee.csv'),
                        'hasil_upload_shopee.csv'),
                       (inti.SKU_CSV, 'sku.csv'),
                       (os.path.join(inti.AKAR, 'data', 'foto_r2.csv'), 'foto_r2.csv'),
                       (os.path.join(inti.AKAR, 'data', 'url_foto.csv'), 'url_foto.csv')):
        n = _salin(asal, folder, nama)
        if n is None:
            cetak('[erp] - {} belum ada di komputer ini, dilewati'.format(nama))
        else:
            jumlah[nama] = n
    # pengaturan bersama; data/lokal.json sengaja tidak ikut (ada kunci R2 di sana)
    shutil.copy2(os.path.join(inti.AKAR, 'tools', 'config.json'),
                 os.path.join(folder, 'config_publik.json'))

    manifes = {'judul': JUDUL, 'waktu_ekspor': time.strftime('%Y-%m-%d %H:%M:%S'),
               'komputer': komputer, 'versi_tools': _versi(inti),
               'toko': tabel_toko(cfg), 'jenis': list(cfg['jenis']),
               'base_url_foto': (cfg['foto'].get('base_url') or ''),
               'jumlah_baris': jumlah,
               'tahap_dikenal': inti.TAHAP,
               'catatan': ['tahap folder ditandai manual oleh operator, bukan otomatis',
                           'kunci R2 dan folder sumber tiap komputer tidak ikut diekspor']}
    with open(os.path.join(folder, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifes, f, ensure_ascii=False, indent=2)
    with open(os.path.join(folder, 'BACA_DULU.md'), 'w', encoding='utf-8') as f:
        f.write(_penjelasan(cfg, jumlah, komputer))

    zip_path = folder + '.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for nama in sorted(os.listdir(folder)):
            z.write(os.path.join(folder, nama), nama)
    ukuran = os.path.getsize(zip_path)
    cetak('[erp] selesai: {} ({:.1f} MB)'.format(zip_path, ukuran / 1024 ** 2))
    cetak('[erp] kirim berkas .zip itu ke tim ERP; isinya juga ada di folder sebelahnya')
    return {'folder': folder, 'zip': zip_path, 'ukuran': ukuran, 'jumlah': jumlah,
            'waktu': manifes['waktu_ekspor'], 'komputer': komputer}


def main():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import shopee_mass_upload as inti
    ekspor(inti, inti.baca_config())


def _versi(inti):
    """Commit tools yang dipakai, supaya tim ERP tahu data ini dari versi mana."""
    try:
        import subprocess
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=inti.AKAR,
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ''


def _penjelasan(cfg, jumlah, komputer):
    toko = '\n'.join('| {} | {} | {} |'.format(t.get('kode'), t['nama'], t['folder_foto'])
                     for t in cfg['toko'])
    berkas = '\n'.join('| `{}` | {} |'.format(k, v) for k, v in sorted(jumlah.items()))
    return """# {judul}

Dari komputer **{komputer}**, {waktu}.

Semua berkas CSV memakai UTF-8 dengan BOM, pemisah koma, baris pertama nama kolom.

## Isi paket

| Berkas | Jumlah baris |
|---|---|
{berkas}

- **`toko.csv`** — ketiga toko Shopee. `folder_foto` (toko1/toko2/toko3) adalah kunci yang
  dipakai di berkas lain; `kode` adalah awalan kode produk di Shopee (mis. `GK-JB-…`).
- **`jenis_produk.csv`** — jenis produk beserta awalan SKU, template kategori Shopee, harga
  paket, berat, dan spesifikasinya.
- **`sku.csv`** — daftar SKU mentah dari Google Sheet: `jenis, seri, varian, sku, foto_siap,
  sudah_upload`. Satu baris = satu varian/desain.
- **`listing.csv`** — **tabel terpenting.** Satu baris per seri per toko, yaitu satu produk di
  Shopee. Memuat `kode_induk` (kode produk di Shopee), rentang SKU, tahap pengerjaan, hasil
  upload terakhir dari Shopee, dan alamat foto utamanya.
- **`folder_produk.csv`** — folder kerja di Google Drive (`PRODUK 00001 - 00050`), satu baris
  per folder, dengan tahap tiap toko jadi kolom `tahap_toko1` dan seterusnya.
- **`tahap_folder.csv`** — catatan tahap mentah, satu baris per folder per toko. Kunci:
  `jenis + dari + sampai + toko`. Kolom `toko` kosong berarti tanda itu berlaku untuk semua
  toko (format lama).
- **`hasil_upload_shopee.csv`** — hasil tiap produk per toko dari berkas hasil mass upload
  Shopee: berhasil/gagal, penyebab, alasan asli dari Shopee, dan folder asalnya.
- **`foto.csv`** — seluruh foto yang pernah disiapkan: sumbernya di Drive, letaknya di
  penyimpanan, alamat publiknya, ukuran, dan apakah sudah terunggah (`diunggah` = 1).
- **`foto_r2.csv`** — daftar berkas yang benar-benar ada di penyimpanan Cloudflare R2.
- **`url_foto.csv`** — pemetaan berkas foto ke alamat publiknya (hasil ekspor daftar URL).
- **`config_publik.json`** — pengaturan bersama: judul, deskripsi, harga, atribut, kategori.
- **`manifest.json`** — waktu ekspor, komputer, versi tools, dan jumlah baris tiap berkas.

## Toko

| Kode | Nama | folder_foto |
|---|---|---|
{toko}

## Tahap pengerjaan

Nilai kolom `tahap`:

| Nilai | Arti |
|---|---|
| `belum` | belum ditandai |
| `diekspor` | berkas Excel-nya sudah dibuat |
| `diupload` | sudah tayang di Shopee |
| `ditolak` | ditolak Shopee, perlu diulang |

Penting untuk dipahami tim ERP:

1. **Tahap ditandai manual oleh operator**, kecuali yang berasal dari impor berkas hasil
   Shopee. Berhasil membuat berkas Excel tidak berarti produknya tayang.
2. **Tahap dicatat per rentang nomor** (`jenis + dari + sampai`), bukan per path folder. Satu
   folder yang sama bisa ada di dua lokasi sumber di Drive (mis. folder revisi dan folder
   utama) dan tetap dianggap satu folder yang sama.
3. **Tahap dicatat per toko.** Satu folder bisa sudah tayang di dua toko dan ditolak di toko
   ketiga.
4. **Rentang folder bisa tidak rapi.** Ada folder di Drive yang namanya salah ketik sehingga
   rentangnya aneh; angka `dari`/`sampai` diambil apa adanya dari nama folder.

## Hubungannya dengan jalur ERP yang sudah jalan

Paket ini **potret sesaat**, bukan sumber kebenaran yang terus hidup. Dua hal sudah
mengalir langsung dari PC ke ERP:

- **Tahap folder** — `tools/erp_tahap.py` mendorong isi `tahap_folder.csv` ke tabel
  `rnd_listing_tahap`. Trigger `rnd_listing_tahap_jaga_waktu` di ERP mengabaikan tulisan yang
  `waktu`-nya lebih lama, jadi `tahap_folder.csv` di paket ini hanya berguna untuk membandingkan
  atau mengisi data awal, bukan untuk menimpa.
- **Antrean unggah** — `tools/antrean.py` mengambil pekerjaan dari ERP dan melapor balik.

Yang **belum** punya jalur langsung dan hanya ada di paket ini: `sku.csv`, `listing.csv`,
`foto.csv`, `foto_r2.csv`, dan `hasil_upload_shopee.csv`.

## Yang tidak ikut

`data/lokal.json` tidak diekspor: isinya kunci Cloudflare R2 dan folder sumber khusus
komputer ini. Foto aslinya juga tidak ikut — yang ikut hanya alamat publiknya, yang bisa
diunduh langsung dari alamat itu.
""".format(judul=JUDUL, komputer=komputer or '(tanpa nama)',
           waktu=time.strftime('%d-%m-%Y %H:%M'), berkas=berkas, toko=toko)


if __name__ == '__main__':
    main()
