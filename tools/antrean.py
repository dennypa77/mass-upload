"""Ambil antrean unggah dari ERP, kerjakan di PC ini, lalu lapor balik.

ERP (Konsol Mass Upload di /rnd/listing) hanya MENGANTRE: operator mencentang
folder di sana, dan baris pekerjaan masuk tabel `rnd_listing_antrean`. Yang
menyalin dari Google Drive dan mengunggah ke R2 tetap komputer ini, karena:

  * foto aslinya ada di `G:\\My Drive` — hanya PC kantor yang bisa membacanya;
  * kunci TULIS bucket R2 sengaja tidak dipegang server ERP: bucket itu yang
    alamatnya dipakai Shopee mengunduh gambar listing yang sedang tayang.

Jalankan:

    python tools/antrean.py            # kerjakan antrean sampai habis
    python tools/antrean.py --sekali   # satu pekerjaan saja lalu berhenti
    python tools/antrean.py --pantau   # tetap hidup, periksa tiap 30 detik

RAHASIA. Butuh dua nilai di `data/lokal.json` (berkas itu TIDAK ikut git):

    "erp": { "url": "https://db.erp-hog.com", "jwt_secret": "…" }

`jwt_secret` sama dengan `VPS_DB_JWT_SECRET` di server. JANGAN menaruhnya di
`tools/config.json` — berkas itu DILACAK git dan terbit ke repo publik.
"""

import argparse
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp  # noqa: E402
import shopee_mass_upload as inti  # noqa: E402
import unggah  # noqa: E402

TABEL = 'rnd_listing_antrean'
NAMA_PC = os.environ.get('COMPUTERNAME') or socket.gethostname()


# ---------------------------------------------------------------- koneksi ERP
# Pindah ke tools/erp.py karena sekarang dipakai juga oleh erp_tahap.py.
baca_erp = erp.baca_erp
mint_jwt = erp.mint_jwt
panggil = erp.panggil


# ------------------------------------------------------------ folder di Drive
def cari_folder(cfg, jenis, dari, sampai):
    """Letak folder di PC ini untuk rentang nomor tertentu.

    Memakai aturan yang sama dengan tampilan web: akar yang disebut LEBIH DULU
    menang, supaya folder revisi menggantikan folder lama dan bukan sebaliknya.
    """
    for akar in inti.dirs_jenis(cfg, jenis):
        if not os.path.isdir(akar):
            continue
        for nama in sorted(os.listdir(akar)):
            m = re.match(r'^PRODUK\s+0*(\d+)\s*-\s*0*(\d+)$', nama.strip(), re.I)
            if not m:
                continue
            if int(m.group(1)) == int(dari) and int(m.group(2)) == int(sampai):
                jalur = os.path.join(akar, nama)
                if os.path.isdir(jalur):
                    return jalur
    return None


# --------------------------------------------------------------------- bekerja
def klaim(erp, baris):
    """Ambil satu pekerjaan secara atomik.

    Syarat `status=eq.menunggu` ikut di URL: kalau PC lain sudah mengambilnya
    sepersekian detik lebih dulu, PATCH ini mengenai NOL baris — dan itulah
    tandanya pekerjaan ini bukan milik kita. Tanpa syarat itu, dua PC bisa
    menyalin folder yang sama lalu saling menimpa hasilnya di R2.
    """
    status, data = panggil(
        erp, 'PATCH',
        '{}?id=eq.{}&status=eq.menunggu'.format(TABEL, baris['id']),
        {
            'status': 'berjalan',
            'diambil_oleh': NAMA_PC,
            'diambil_at': waktu_iso(),
        },
        prefer='return=representation',
    )
    if status >= 300:
        print('[antrean] gagal klaim #{}: {} {}'.format(baris['id'], status, data))
        return None
    return data[0] if isinstance(data, list) and data else None


def waktu_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + 'Z'


def lapor(erp, id_baris, status, pesan=None, jumlah_foto=None):
    badan = {'status': status, 'selesai_at': waktu_iso()}
    if pesan is not None:
        badan['pesan'] = str(pesan)[:500]
    if jumlah_foto is not None:
        badan['jumlah_foto'] = int(jumlah_foto)
    kode, data = panggil(erp, 'PATCH', '{}?id=eq.{}'.format(TABEL, id_baris), badan)
    if kode >= 300:
        print('[antrean] gagal melapor #{}: {} {}'.format(id_baris, kode, data))


def kerjakan(erp, cfg, baris):
    """Satu pekerjaan: salin dari Drive, unggah ke R2, lalu lapor."""
    label = '{} {:05d}-{:05d}'.format(baris['jenis'], baris['dari'], baris['sampai'])
    folder = cari_folder(cfg, baris['jenis'], baris['dari'], baris['sampai'])
    if not folder:
        print('[antrean] #{} {}: folder tidak ada di PC ini'.format(baris['id'], label))
        lapor(erp, baris['id'], 'gagal',
              'Folder untuk rentang ini tidak ditemukan di komputer {}.'.format(NAMA_PC))
        return False

    print('[antrean] #{} {} -> {}'.format(baris['id'], label, folder))
    try:
        hasil = unggah.proses(
            inti, cfg, folder,
            push=bool(baris.get('opsi_push', True)),
            paksa=bool(baris.get('opsi_paksa', False)),
        )
    except Exception as e:  # noqa: BLE001 — apa pun yang gagal harus terlapor
        print('[antrean] #{} GAGAL: {}'.format(baris['id'], e))
        lapor(erp, baris['id'], 'gagal', e)
        return False

    # `proses` mengembalikan ringkasan (bentuknya berbeda antar versi), jadi
    # jumlah foto diambil seadanya dan tidak pernah menggagalkan pelaporan.
    jumlah = None
    if isinstance(hasil, dict):
        for k in ('disalin', 'jumlah', 'n', 'total'):
            if isinstance(hasil.get(k), int):
                jumlah = hasil[k]
                break
    lapor(erp, baris['id'], 'berhasil', 'Selesai di {}.'.format(NAMA_PC), jumlah)
    return True


def sekali_putaran(erp, cfg, batas=None):
    """Kerjakan antrean sampai habis. Kembalikan jumlah pekerjaan yang dijalankan."""
    dikerjakan = 0
    while batas is None or dikerjakan < batas:
        status, data = panggil(
            erp, 'GET',
            # pekerjaan=eq.unggah WAJIB sejak antrean dipakai dua jenis
            # pekerjaan: tanpa ini, pengambil lama akan mengklaim perintah
            # Corel dan mencoba menyalin foto yang belum dibuat.
            '{}?pekerjaan=eq.unggah&status=eq.menunggu&order=dibuat_at.asc&limit=5'
            '&select=id,jenis,dari,sampai,opsi_push,opsi_paksa'.format(TABEL),
        )
        if status >= 300:
            print('[antrean] gagal membaca antrean: {} {}'.format(status, data))
            return dikerjakan
        if not data:
            return dikerjakan

        maju = False
        for baris in data:
            diambil = klaim(erp, baris)
            if not diambil:
                # Sudah diambil PC lain — lanjut ke pekerjaan berikutnya.
                continue
            # Satu folder gagal TIDAK menghentikan sisanya; itu perilaku yang
            # sama dengan `proses_banyak` di aplikasi ini.
            kerjakan(erp, cfg, diambil)
            dikerjakan += 1
            maju = True
            if batas is not None and dikerjakan >= batas:
                break
        if not maju:
            return dikerjakan
    return dikerjakan


def main():
    p = argparse.ArgumentParser(description='Pengambil antrean unggah dari ERP')
    p.add_argument('--sekali', action='store_true', help='kerjakan satu pekerjaan lalu berhenti')
    p.add_argument('--pantau', action='store_true', help='tetap hidup, periksa berkala')
    p.add_argument('--jeda', type=int, default=30, help='detik antar pemeriksaan saat --pantau')
    arg = p.parse_args()

    erp = baca_erp()
    if not erp:
        print('[antrean] data/lokal.json belum memuat {"erp": {"url": ..., "jwt_secret": ...}}')
        print('          Minta nilainya ke pengelola ERP. JANGAN taruh di tools/config.json —')
        print('          berkas itu dilacak git dan terbit ke repo publik.')
        return 2

    cfg = inti.baca_config()
    print('[antrean] {} -> {} (sebagai {})'.format(NAMA_PC, erp['url'], 'service_role'))

    if arg.pantau:
        print('[antrean] memantau tiap {} detik; Ctrl+C untuk berhenti'.format(arg.jeda))
        try:
            while True:
                n = sekali_putaran(erp, cfg)
                if n:
                    print('[antrean] {} pekerjaan selesai'.format(n))
                time.sleep(arg.jeda)
        except KeyboardInterrupt:
            print('\n[antrean] berhenti')
            return 0

    n = sekali_putaran(erp, cfg, batas=1 if arg.sekali else None)
    print('[antrean] {} pekerjaan dijalankan'.format(n))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
