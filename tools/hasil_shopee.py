# -*- coding: utf-8 -*-
"""Impor berkas hasil mass upload dari Shopee, lalu perbarui tahap folder per toko.

Kalau ada produk yang gagal, Shopee memberi berkas "Result_shopee_…xlsx". Isinya
salinan berkas upload yang HANYA memuat produk yang gagal, dengan alasannya di
kolom "Alasan Gagal" (et_title_reason) pada baris pertama tiap produk. Produk
yang berhasil tidak ada di dalamnya, jadi dihitung dari berkas ekspor aslinya.

Berkas ekspor asal itu dikenali dari tanda tangan template di baris 2 — Shopee
ikut menyalinnya ke berkas hasil — ditambah nama toko di nama berkasnya, dan
harus memuat semua produk yang gagal.

Hasil tiap produk per toko disimpan di data/hasil_shopee.csv. Tahap sebuah folder
dinilai dari SELURUH hasil produknya yang pernah diimpor, bukan hanya dari berkas
yang barusan: satu folder bisa terbagi ke "bagian 1" dan "bagian 2", dan produk
yang gagal kemarin bisa berhasil setelah diupload ulang hari ini.
"""
import bisect
import collections
import csv
import glob
import os
import re
import time

import shopee_mass_upload  # noqa: F401 — memasang tambalan openpyxl untuk berkas Shopee
import openpyxl

KOLOM = {'kode': 'et_title_variation_integration_no', 'induk': 'ps_sku_parent_short',
         'nama': 'ps_product_name', 'sku': 'ps_sku_short', 'alasan': 'et_title_reason',
         'sampul': 'ps_item_cover_image'}
KOLOM_FOTO = ('ps_item_cover_image', 'et_title_image_per_variation', 'ps_item_image_')

BERKAS_REKAMAN = os.path.join(shopee_mass_upload.AKAR, 'data', 'hasil_shopee.csv')
KOLOM_REKAMAN = ['toko', 'kode', 'status', 'penyebab', 'alasan', 'folder', 'waktu', 'berkas']

SARAN = {
    'foto belum lengkap': 'lengkapi fotonya dulu, lalu ekspor ulang',
    'gangguan server Shopee': 'foto sudah benar — cukup upload ulang, sebaiknya berkas lebih kecil',
    'lainnya': 'lihat alasan lengkapnya di laporan',
}


def kategori_alasan(teks):
    """Kelompokkan alasan gagal dari Shopee menurut apa yang harus dilakukan."""
    t = (teks or '').lower()
    if 'gambar produk wajib' in t or 'image is required' in t:
        return 'foto belum lengkap'
    if any(x in t for x in ('external system', 'server failed', 'timeout', 'try again')):
        return 'gangguan server Shopee'
    return 'lainnya'


def penyebab(p):
    """Penyebab gagal sebuah produk.

    Produk tanpa foto sampul tidak mungkin diterima, apa pun alasan yang ditulis
    Shopee — sebagian malah hanya diberi "external system return error", yang
    kalau dipercaya begitu saja membuat orang mengupload ulang tanpa hasil.
    """
    if not p.get('sampul', True):
        return 'foto belum lengkap'
    return kategori_alasan(p['alasan'])


# --------------------------------------------------------------------------- berkas Excel
def _baca_template(path):
    """(tanda, {kode kolom: indeks}, baris data) dari sheet Template."""
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise SystemExit('Berkas tidak bisa dibuka: {} ({})'.format(os.path.basename(path), e))
    try:
        if 'Template' not in wb.sheetnames:
            raise SystemExit('Berkas itu bukan berkas mass upload Shopee (sheet Template '
                             'tidak ada): {}'.format(os.path.basename(path)))
        rows = list(wb['Template'].iter_rows(values_only=True))
    finally:
        wb.close()
    if len(rows) < 7:
        raise SystemExit('Berkas itu tidak berisi produk: {}'.format(os.path.basename(path)))
    indeks = {}
    for i, v in enumerate(rows[0]):
        kode = str(v or '').split('|')[0].strip()
        if kode and kode not in indeks:
            indeks[kode] = i
    tanda = str((rows[1][1] if len(rows[1]) > 1 else '') or '').strip()
    return tanda, indeks, rows[6:]


def _tanda_saja(path):
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        try:
            baris = next(wb['Template'].iter_rows(min_row=2, max_row=2, max_col=2,
                                                  values_only=True), ())
        finally:
            wb.close()
        return str((baris[1] if len(baris) > 1 else '') or '').strip()
    except Exception:
        return ''


def _toko_dari(cfg, url_contoh, kode_contoh):
    """Toko sebuah berkas: dari alamat foto (/foto-upload/toko2/…), atau kode produknya."""
    sah = {t['folder_foto'] for t in cfg['toko']}
    for u in url_contoh:
        m = re.search(r'/foto-upload/([^/]+)/', u or '')
        if m and m.group(1) in sah:
            return m.group(1)
    for k in kode_contoh:
        awal = str(k or '').split('-')[0].upper()
        for t in cfg['toko']:
            if (t.get('kode') or '').upper() == awal:
                return t['folder_foto']
    return None


def baca_berkas(path, cfg):
    """{'tanda', 'toko', 'produk': {kode integrasi: {...}}} sebuah berkas upload atau hasil."""
    tanda, ix, data = _baca_template(path)
    if KOLOM['kode'] not in ix:
        raise SystemExit('Kolom "Kode Integrasi Variasi" tidak ada di {}'.format(
            os.path.basename(path)))

    def ambil(r, nama):
        i = ix.get(KOLOM[nama])
        return r[i] if i is not None and i < len(r) else None

    kolom_foto = [i for kode, i in ix.items() if kode.startswith(KOLOM_FOTO)]
    ada_kolom_sampul = KOLOM['sampul'] in ix
    produk, url, kode_contoh = collections.OrderedDict(), [], []
    for r in data:
        kode = str(ambil(r, 'kode') or '').strip()
        if not kode:
            continue
        p = produk.setdefault(kode, {'kode': kode, 'nama': '', 'induk': '', 'sku': [],
                                     'alasan': '', 'sampul': not ada_kolom_sampul})
        for nama in ('nama', 'induk'):
            nilai = ambil(r, nama)
            if nilai and not p[nama]:
                p[nama] = str(nilai).strip()
        if ambil(r, 'sampul'):
            p['sampul'] = True
        sku = ambil(r, 'sku')
        if sku:
            p['sku'].append(str(sku).strip())
        alasan = str(ambil(r, 'alasan') or '').strip()
        if alasan and alasan not in p['alasan']:
            p['alasan'] = (p['alasan'] + ' ' + alasan).strip()
        if len(url) < 20:
            url.extend(str(r[i]) for i in kolom_foto if i < len(r) and r[i])
        if len(kode_contoh) < 5:
            kode_contoh.append(kode)
    return {'tanda': tanda, 'toko': _toko_dari(cfg, url, kode_contoh), 'produk': produk}


def waktu_hasil(path):
    """Kapan Shopee memproses upload itu: dari nama berkasnya (…_20260915150257.xlsx)."""
    m = re.search(r'_(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)\.xlsx$', os.path.basename(path), re.I)
    if m:
        return '{}-{}-{} {}:{}:{}'.format(*m.groups())
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(path)))


def cari_ekspor(inti, cfg, hasil):
    """(path, isi) berkas ekspor asal sebuah berkas hasil, atau (None, None).

    Dicari di output/: nama berkasnya diawali nama toko, tanda tangan template di
    baris 2 sama, dan memuat semua produk yang gagal. Kalau ekspor yang sama
    dibuat beberapa kali, yang terbaru yang dipakai.
    """
    nama_toko = next((t['nama'] for t in cfg['toko'] if t['folder_foto'] == hasil['toko']), '')
    gagal = set(hasil['produk'])
    calon = []
    for p in glob.glob(os.path.join(inti.DIR_OUT, '**', '*.xlsx'), recursive=True):
        dasar = os.path.basename(p)
        if dasar.startswith('~$') or (nama_toko and not dasar.startswith(nama_toko + ' - ')):
            continue
        calon.append(p)
    calon.sort(key=os.path.getmtime, reverse=True)
    sama = [p for p in calon if hasil['tanda'] and _tanda_saja(p) == hasil['tanda']]
    lain = [p for p in calon if p not in sama][:10]
    for kelompok in (sama, lain):
        for p in kelompok:
            try:
                isi = baca_berkas(p, cfg)
            except SystemExit:
                continue
            if gagal <= set(isi['produk']):
                return p, isi
    return None, None


# --------------------------------------------------------------------------- folder
def peta_folder(inti, cfg):
    """{JENIS: [(dari, sampai), ...]} rentang folder produk di Drive, terurut."""
    import cek_gambar
    hasil = {}
    for jenis, path in cek_gambar.folder_drive(cfg, inti):
        m = re.match(r'^PRODUK\s+0*(\d+)\s*-\s*0*(\d+)$', os.path.basename(path).strip(), re.I)
        if m:
            hasil.setdefault(jenis.upper(), []).append((int(m.group(1)), int(m.group(2))))
    return {j: sorted(v) for j, v in hasil.items()}


def folder_untuk(peta, jenis, nomor):
    daftar = peta.get(str(jenis).upper()) or []
    i = bisect.bisect_right(daftar, (nomor, float('inf'))) - 1
    if i >= 0 and daftar[i][0] <= nomor <= daftar[i][1]:
        return daftar[i]
    return None


def folder_produk(inti, cfg, peta, p):
    """['JENIS|dari|sampai', ...] folder tempat SKU-SKU sebuah produk berada."""
    prefix = {j['prefix_sku'].upper(): nama for nama, j in cfg['jenis'].items()}
    kena = set()
    for sku in p['sku']:
        n = inti.nomor_sku(sku)
        jenis = prefix.get(sku.split('-', 1)[0].upper())
        f = folder_untuk(peta, jenis, n) if (n and jenis) else None
        if f:
            kena.add('{}|{}|{}'.format(jenis.upper(), f[0], f[1]))
    return sorted(kena)


# --------------------------------------------------------------------------- rekaman
def baca_rekaman(path=None):
    """{toko|kode: baris} hasil terakhir tiap produk per toko."""
    path = path or BERKAS_REKAMAN
    hasil = {}
    if not os.path.exists(path):
        return hasil
    with open(path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            if r.get('toko') and r.get('kode'):
                hasil[r['toko'] + '|' + r['kode']] = {c: r.get(c) or '' for c in KOLOM_REKAMAN}
    return hasil


def tulis_rekaman(semua):
    """Simpan rekaman. Ikut git, sama seperti tahap folder, supaya komputer lain tahu."""
    os.makedirs(os.path.dirname(BERKAS_REKAMAN), exist_ok=True)
    sementara = BERKAS_REKAMAN + '.tmp'
    with open(sementara, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=KOLOM_REKAMAN)
        w.writeheader()
        for k in sorted(semua):
            w.writerow(semua[k])
    shopee_mass_upload._ganti_berkas(sementara, BERKAS_REKAMAN)


def rekam(semua, baris):
    """Masukkan satu hasil; hasil upload yang lebih lama tidak menimpa yang lebih baru."""
    k = baris['toko'] + '|' + baris['kode']
    ada = semua.get(k)
    if ada and (ada.get('waktu') or '') > baris['waktu']:
        return False
    semua[k] = baris
    return True


def gabung_rekaman(berkas_lain):
    """Gabungkan rekaman dari berkas lain (dipakai waktu memperbarui), yang terbaru menang."""
    if not os.path.exists(berkas_lain):
        return 0
    sekarang = baca_rekaman()
    baru = sum(1 for b in baca_rekaman(berkas_lain).values() if rekam(sekarang, b))
    if baru:
        tulis_rekaman(sekarang)
    return baru


# --------------------------------------------------------------------------- impor
def impor(inti, cfg, path, cetak=print, folder_laporan=None):
    """Impor satu berkas hasil Shopee; kembalikan ringkasannya untuk halaman."""
    hasil = baca_berkas(path, cfg)
    gagal = collections.OrderedDict((k, p) for k, p in hasil['produk'].items() if p['alasan'])
    if not gagal:
        raise SystemExit('Berkas itu tidak berisi satu pun "Alasan Gagal", jadi bukan berkas '
                         'hasil dari Shopee — sepertinya berkas upload biasa.')
    if not hasil['toko']:
        raise SystemExit('Toko berkas hasil ini tidak bisa dikenali dari alamat foto maupun '
                         'kode produknya.')
    toko = hasil['toko']
    nama_toko = next(t['nama'] for t in cfg['toko'] if t['folder_foto'] == toko)
    waktu = waktu_hasil(path)
    cetak('[hasil] {} — {} produk gagal di berkas hasil (diproses Shopee {})'.format(
        nama_toko, len(gagal), waktu))

    asal, ekspor = cari_ekspor(inti, cfg, {'tanda': hasil['tanda'], 'toko': toko,
                                           'produk': gagal})
    if asal:
        berhasil = collections.OrderedDict(
            (k, v) for k, v in ekspor['produk'].items() if k not in gagal)
        cetak('[hasil] berkas ekspor asalnya: {}'.format(os.path.relpath(asal, inti.DIR_OUT)))
        cetak('[hasil] {} produk di berkas itu: {} berhasil, {} gagal'.format(
            len(ekspor['produk']), len(berhasil), len(gagal)))
    else:
        berhasil = collections.OrderedDict()
        cetak('[hasil] ! berkas ekspor asalnya tidak ketemu di output/ — hanya produk yang '
              'gagal yang bisa ditandai. Yang berhasil tandai sendiri di tab 1.')

    # ---------------------------------------------------------------- rekam per produk
    peta = peta_folder(inti, cfg)
    rekaman = baca_rekaman()
    disentuh, tanpa_folder, usang = set(), [], 0
    for status, kumpulan in (('gagal', gagal), ('berhasil', berhasil)):
        for p in kumpulan.values():
            folder = folder_produk(inti, cfg, peta, p)
            if not folder:
                tanpa_folder.append(p['kode'])
            baris = {'toko': toko, 'kode': p['kode'], 'status': status,
                     'penyebab': penyebab(p) if status == 'gagal' else '',
                     'alasan': p['alasan'], 'folder': ';'.join(folder), 'waktu': waktu,
                     'berkas': os.path.basename(path)}
            if rekam(rekaman, baris):
                disentuh.update(folder)
            else:
                usang += 1
    tulis_rekaman(rekaman)

    # ---------------------------------------------------------------- tahap folder
    # dinilai dari semua hasil produk toko ini di folder itu yang pernah diimpor
    per_folder = collections.defaultdict(list)
    for b in rekaman.values():
        if b['toko'] != toko:
            continue
        for f in filter(None, b['folder'].split(';')):
            if f in disentuh:
                per_folder[f].append(b)
    entri = []
    n_diupload = n_ditolak = 0
    for f, daftar in sorted(per_folder.items()):
        jenis, dari, sampai = f.split('|')
        dasar = {'jenis': jenis, 'dari': int(dari), 'sampai': int(sampai), 'toko': [toko]}
        jatuh = [b for b in daftar if b['status'] == 'gagal']
        if jatuh:
            sebab = collections.Counter(b['penyebab'] for b in jatuh)
            catatan = '{} dari {} produk gagal: {}'.format(
                len(jatuh), len(daftar),
                ', '.join('{} {}'.format(n, k) for k, n in sebab.most_common()))
            entri.append(dict(dasar, tahap='ditolak', catatan=catatan))
            n_ditolak += 1
        else:
            entri.append(dict(dasar, tahap='diupload',
                              catatan='{} produk berhasil'.format(len(daftar))))
            n_diupload += 1
    if entri:
        inti.setel_tahap_banyak(entri, os.environ.get('COMPUTERNAME') or '',
                                [t['folder_foto'] for t in cfg['toko']])

    # ---------------------------------------------------------------- laporan
    folder_laporan = folder_laporan or os.path.join(inti.DIR_OUT, 'hasil_shopee')
    os.makedirs(folder_laporan, exist_ok=True)
    laporan = os.path.join(folder_laporan, '{} {}.csv'.format(
        time.strftime('%Y-%m-%d %H.%M.%S'), nama_toko))
    with open(laporan, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['status', 'penyebab', 'yang perlu dilakukan', 'kode integrasi',
                    'nama produk', 'sku awal', 'sku akhir', 'alasan dari Shopee'])
        for p in gagal.values():
            k = penyebab(p)
            w.writerow(['gagal', k, SARAN[k], p['kode'], p['nama'],
                        (p['sku'] or [''])[0], (p['sku'] or [''])[-1], p['alasan']])
        for p in berhasil.values():
            w.writerow(['berhasil', '', '', p['kode'], p['nama'],
                        (p['sku'] or [''])[0], (p['sku'] or [''])[-1], ''])

    sebab = collections.Counter(penyebab(p) for p in gagal.values())
    cetak('[hasil] penyebab kegagalan:')
    for k, n in sebab.most_common():
        cetak('   {:>4} produk  {:<24} -> {}'.format(n, k, SARAN[k]))
    cetak('[hasil] tahap {} diperbarui: {} folder "sudah di Shopee", {} folder '
          '"ditolak Shopee"'.format(nama_toko, n_diupload, n_ditolak))
    if usang:
        cetak('   {} produk tidak diubah: sudah ada hasil upload yang lebih baru'.format(usang))
    if tanpa_folder:
        cetak('   ! {} produk tidak ketemu foldernya di Drive, contoh: {}'.format(
            len(tanpa_folder), ', '.join(tanpa_folder[:5])))
    cetak('[hasil] laporan per produk: {}'.format(laporan))
    cetak('[hasil] commit data/hasil_shopee.csv dan data/tahap_folder.csv supaya komputer '
          'lain ikut tahu')

    return {'toko': toko, 'nama_toko': nama_toko, 'waktu_shopee': waktu,
            'ekspor': os.path.relpath(asal, inti.DIR_OUT) if asal else None,
            'berhasil': len(berhasil), 'gagal': len(gagal),
            'penyebab': [[k, n] for k, n in sebab.most_common()],
            'folder_diupload': n_diupload, 'folder_ditolak': n_ditolak,
            'usang': usang, 'tanpa_folder': len(tanpa_folder), 'laporan': laporan}
