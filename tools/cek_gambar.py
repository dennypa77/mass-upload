# -*- coding: utf-8 -*-
"""Cari foto yang isinya jenis produk lain daripada yang disebut namanya.

Latar belakangnya: pernah ada 1.481 foto varian bernama JB-xxxxxxx tapi isinya
mockup PIN AKRILIK. Nama berkasnya benar, jadi tools tidak bisa menyadarinya —
foto itu disalin apa adanya, dan yang salah baru ketahuan setelah listing-nya
tayang di Shopee.

Cara mengenalinya: tiap jenis produk memakai mockup dengan pita atas berwarna
khas. Warna acuannya tidak ditulis di sini melainkan dihitung dari datanya
sendiri — median tiap jenis — supaya pemeriksaan ini tidak ikut salah kalau
desainnya diganti suatu hari.

Yang diperiksa hanya foto varian, yang bentuknya seragam. Foto sampul
(…-utama1/2/3) sengaja dilewati: isinya macam-macam, ada foto produk di atas
sepatu, jadi warnanya memang wajar berbeda dan tidak bisa dinilai begini.
"""
import csv, io, os, statistics, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor

# PNG tidak berselang-seling: baris paling atas tersimpan paling depan, jadi
# potongan awal berkas sudah cukup untuk membaca pitanya. Ini yang membuat
# memeriksa puluhan ribu foto memakan menit, bukan jam.
POTONG = 48 * 1024
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
SERENTAK = 24
BATAS_COCOK = 12        # jarak warna maksimal supaya disebut cocok ke jenis lain


def _warna_pita(data):
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    im = Image.open(io.BytesIO(data))
    try:
        im.load()
    except Exception:
        pass                      # potongan memang tidak utuh, baris atas cukup
    im = im.convert('RGB')
    lebar, tinggi = im.size
    pita = im.crop((0, 0, lebar, max(1, tinggi // 12)))
    return pita.resize((1, 1)).getpixel((0, 0))


def _jarak(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def daftar_foto(inti, cfg, toko=None):
    """[(jenis, nama, url)] foto varian dari satu toko saja.

    Ketiga toko memakai gambar yang sama, jadi memeriksa satu toko sudah
    mewakili — sepertiga pekerjaan untuk kesimpulan yang sama.
    """
    if not os.path.exists(inti.MANIFEST_R2):
        return []
    toko = toko or (cfg['toko'][0]['folder_foto'] if cfg.get('toko') else 'toko1')
    dari_prefix = {j['prefix_sku'].upper(): nama for nama, j in cfg['jenis'].items()}
    hasil = []
    with io.open(inti.MANIFEST_R2, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            jalur, tautan = r.get('path') or '', r.get('url') or ''
            if not tautan or '/{}/'.format(toko) not in jalur:
                continue
            nama = os.path.splitext(jalur.rsplit('/', 1)[-1])[0]
            if not inti.nomor_sku(nama):
                continue            # foto sampul dilewati, lihat catatan di atas
            jenis = dari_prefix.get(nama.split('-', 1)[0].upper())
            if jenis:
                hasil.append((jenis, nama, tautan))
    return hasil


def periksa(inti, cfg, cetak=print, lapor=None, toko=None):
    """Periksa seluruh foto varian, laporkan yang jenisnya tidak cocok."""
    tugas = daftar_foto(inti, cfg, toko)
    if not tugas:
        cetak('[gambar] daftar foto R2 kosong — tekan "Segarkan daftar foto" dulu')
        return {'diperiksa': 0, 'salah': []}

    cetak('[gambar] memeriksa {} foto varian …'.format(len(tugas)))
    terbaca, gagal = [], []
    n = [0]
    kunci = threading.Lock()

    def satu(t):
        jenis, nama, url = t
        try:
            minta = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Range': 'bytes=0-{}'.format(POTONG - 1)})
            data = urllib.request.urlopen(minta, timeout=30).read()
            warna = _warna_pita(data)
            with kunci:
                terbaca.append((jenis, nama, warna))
        except Exception as e:
            with kunci:
                gagal.append((nama, str(e)[:70]))
        with kunci:
            n[0] += 1
            maju = n[0]
        if lapor:
            lapor('gambar', maju, len(tugas))
        if maju % 2000 == 0:
            cetak('      {}/{} foto'.format(maju, len(tugas)))

    with ThreadPoolExecutor(max_workers=SERENTAK) as kolam:
        list(kolam.map(satu, tugas))

    acuan = {}
    for jenis in cfg['jenis']:
        w = [x[2] for x in terbaca if x[0] == jenis]
        if len(w) >= 20:
            acuan[jenis] = tuple(statistics.median(x[i] for x in w) for i in range(3))
    if len(acuan) < 2:
        cetak('[gambar] foto yang terbaca terlalu sedikit untuk dibandingkan')
        return {'diperiksa': len(terbaca), 'salah': []}

    salah = []
    for jenis, nama, warna in terbaca:
        dekat = min(acuan, key=lambda j: _jarak(warna, acuan[j]))
        if dekat != jenis and _jarak(warna, acuan[dekat]) < BATAS_COCOK:
            salah.append({'jenis': jenis, 'isinya': dekat, 'sku': nama,
                          'nomor': inti.nomor_sku(nama)})

    cetak('[gambar] {} foto terbaca, {} gagal, {} isinya jenis lain'.format(
        len(terbaca), len(gagal), len(salah)))
    if gagal:
        cetak('   ! contoh yang gagal dibaca: {}'.format(gagal[:3]))
    _laporkan(cetak, salah)
    cetak('   Catatan: foto sampul (…-utama1/2/3) tidak ikut diperiksa — bentuknya')
    cetak('   macam-macam, jadi harus dilihat sendiri.')
    return {'diperiksa': len(terbaca), 'salah': salah, 'acuan': acuan}


def folder_produk(nomor, per=50):
    """1187 -> 'PRODUK 01151 - 01200', mengikuti penamaan folder di Drive."""
    awal = (nomor - 1) // per * per + 1
    return 'PRODUK {:05d} - {:05d}'.format(awal, awal + per - 1)


def _laporkan(cetak, salah):
    if not salah:
        cetak('[gambar] semua foto varian cocok dengan jenisnya')
        return
    per_folder = {}
    for s in salah:
        kunci = (s['jenis'], s['isinya'], folder_produk(s['nomor']))
        per_folder.setdefault(kunci, []).append(s['sku'])
    cetak('')
    cetak('[gambar] folder yang perlu diperbaiki di Google Drive:')
    urut = sorted(per_folder.items(), key=lambda x: (x[0][0], x[0][2]))
    for (jenis, isinya, folder), daftar in urut:
        cetak('   {:<12} {:<22} {:>3} foto berisi desain {}'.format(
            jenis, folder, len(daftar), isinya))
    cetak('   Perbaiki gambarnya di Drive, lalu proses ulang foldernya dengan')
    cetak('   centang "salin ulang" supaya berkas lama benar-benar tertimpa.')
