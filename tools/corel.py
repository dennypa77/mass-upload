# -*- coding: utf-8 -*-
"""Ambil perintah "buat foto produk" dari ERP, jalankan macro CorelDRAW di PC
ini, lalu lapor balik.

Pasangan dari tools/antrean.py, dengan pembagian kerja yang sama: ERP yang
MENGANTRE, PC yang MENGERJAKAN. Sambungannya selalu keluar dari PC ini, jadi
tidak ada port yang perlu dibuka di jaringan kantor.

Bedanya hanya isi pekerjaannya. antrean.py menyalin foto yang sudah jadi;
berkas ini menjalankan macro yang MEMBUAT fotonya, lewat COM:

    CorelDRAW.Application -> GMSManager.RunMacro
        "GlobalMacros", "modFotoProduk.FotoProdukOtomatis", "<perintah>"

Jalankan:

    python tools/corel.py              # kerjakan antrean sampai habis
    python tools/corel.py --sekali     # satu pekerjaan saja
    python tools/corel.py --pantau     # tetap hidup, periksa tiap 30 detik
    python tools/corel.py --demo       # tampilkan perintahnya, JANGAN panggil
                                       # CorelDRAW. Pakai ini dulu.

BUTUH DI data/lokal.json (berkas itu TIDAK ikut git):

    "erp":   { "url": "https://db.erp-hog.com", "jwt_secret": "..." },
    "corel": { "keluaran": "E:\\\\Foto Produk",
               "template": "E:\\\\Template Foto",
               "batas_menit": 20 }

Path keluaran dan template sengaja TIDAK disimpan di ERP. Begitu path satu
komputer masuk ke sana, satu PC lain yang susunan foldernya berbeda sudah
cukup membuat seluruh antrean salah alamat. ERP cukup menyebut jenis + nomor
blok; PC ini yang tahu di mana berkasnya.

SYARAT YANG TIDAK KELIHATAN
  * CorelDRAW harus jalan di sesi desktop yang LOGIN. Otomasi COM aplikasi
    GUI tidak bekerja sebagai Windows service.
  * Satu pekerjaan sekaligus. Satu instance CorelDRAW, satu dokumen aktif.
  * Font yang dipakai template harus terpasang di PC ini. Kalau tidak, teks
    SKU berubah bentuk tanpa ada yang gagal - kesalahan yang baru ketahuan
    di Shopee.
  * Macro bisa menggantung kalau CorelDRAW memunculkan dialognya SENDIRI
    (font hilang, pemulihan berkas, pembaruan). Mode senyap di modul VBA
    tidak bisa mencegah itu - karena itu ada batas waktu di sini.
"""

import argparse
import json
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp  # noqa: E402
import shopee_mass_upload as inti  # noqa: E402

TABEL = 'rnd_listing_antrean'
PEKERJAAN = 'corel'
NAMA_PC = os.environ.get('COMPUTERNAME') or socket.gethostname()

PROYEK_GMS = 'GlobalMacros'
MACRO = 'modFotoProduk.FotoProdukOtomatis'

# Kunci setelan yang boleh dioper ke macro. Yang tidak dikenal DIBUANG dan
# dilaporkan, bukan diteruskan: nama kunci yang salah membuat macro memakai
# bawaannya diam-diam, dan hasilnya baru ketahuan sesudah 150 berkas.
SETELAN_SAH = (
    'ukuran', 'geserx', 'gesery', 'teks', 'turun', 'jarakteks',
    'kolom', 'isi', 'jarakkolom', 'jarakbaris', 'geser', 'turunsampul',
)

BATAS_MENIT_BAWAAN = 20


# --------------------------------------------------------------- pengaturan
def baca_corel():
    """Folder keluaran & template PC ini, dari data/lokal.json."""
    lokal = inti.baca_lokal() or {}
    c = lokal.get('corel') or {}
    return {
        'keluaran': str(c.get('keluaran') or '').strip(),
        'template': str(c.get('template') or '').strip(),
        'batas_menit': float(c.get('batas_menit') or BATAS_MENIT_BAWAAN),
    }


def cari_folder_desain(cfg, jenis, dari, sampai):
    """Folder PRODUK <dari> - <sampai> di PC ini, atau None.

    Akar yang disebut lebih dulu di config menang - aturan yang sama dengan
    antrean.py, supaya folder revisi mengalahkan folder lama.
    """
    for akar in inti.dirs_jenis(cfg, jenis):
        if not os.path.isdir(akar):
            continue
        for nama in sorted(os.listdir(akar)):
            m = re.match(r'^PRODUK\s+0*(\d+)\s*-\s*0*(\d+)$', nama, re.I)
            if not m:
                continue
            if int(m.group(1)) == int(dari) and int(m.group(2)) == int(sampai):
                jalur = os.path.join(akar, nama)
                if os.path.isdir(jalur):
                    return jalur
    return None


# ------------------------------------------------------------------ perintah
def susun_perintah(baris, folder_desain, pengaturan, berkas_hasil):
    """String `kunci=nilai;...` yang dimengerti FotoProdukOtomatis."""
    param = baris.get('parameter') or {}
    if isinstance(param, str):
        try:
            param = json.loads(param)
        except ValueError:
            param = {}

    mode = str(param.get('mode') or 'varian').lower()
    if mode not in ('varian', 'utama'):
        mode = 'varian'

    bagian = [
        'mode=' + mode,
        'desain=' + folder_desain,
        'keluaran=' + pengaturan['keluaran'],
        'template=' + pengaturan['template'],
        'hasil=' + berkas_hasil,
    ]

    dibuang = []
    for kunci, nilai in param.items():
        k = str(kunci).lower()
        if k == 'mode':
            continue
        if k not in SETELAN_SAH:
            dibuang.append(k)
            continue
        bagian.append('{}={}'.format(k, nilai))

    # Titik koma di dalam path akan memecah perintahnya jadi dua. Belum pernah
    # terjadi, tapi kalau terjadi hasilnya kacau tanpa pesan apa pun.
    for b in bagian:
        if b.count('=') >= 1 and ';' in b:
            raise ValueError('Ada titik koma di dalam nilai: {}'.format(b))

    return ';'.join(bagian), mode, dibuang


# --------------------------------------------------------------------- corel
def jalankan_macro(perintah, batas_detik):
    """Panggil macro lewat COM. Kembalikan (hasil, galat).

    Panggilan COM tidak bisa dibatalkan dari luar, jadi dijalankan di utas
    sendiri dan ditunggu dengan batas waktu. Kalau lewat batas, utasnya
    DIBIARKAN - CorelDRAW masih memegangnya - dan pekerjaan ini dilaporkan
    gagal supaya tidak menggantung selamanya di antrean sebagai "berjalan".
    """
    hasil = {'nilai': None, 'galat': None}

    def kerja():
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            hasil['galat'] = ('pywin32 belum terpasang. Pasang dengan: '
                              'pip install pywin32')
            return
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            app = win32com.client.Dispatch('CorelDRAW.Application')
            try:
                app.Visible = True
            except Exception:
                pass
            hasil['nilai'] = app.GMSManager.RunMacro(PROYEK_GMS, MACRO, perintah)
        except Exception as e:  # noqa: BLE001
            hasil['galat'] = '{}: {}'.format(type(e).__name__, e)

    utas = threading.Thread(target=kerja, daemon=True)
    utas.start()
    utas.join(batas_detik)

    if utas.is_alive():
        return None, ('lewat batas {:.0f} menit - CorelDRAW mungkin menampilkan '
                      'dialognya sendiri. Periksa layar PC host.'
                      .format(batas_detik / 60.0))
    return hasil['nilai'], hasil['galat']


def baca_hasil(berkas):
    """Isi berkas hasil yang ditulis macro, kalau ada."""
    try:
        with open(berkas, encoding='utf-8') as f:
            return f.read().strip()
    except Exception:  # noqa: BLE001
        return ''


def urai_hasil(teks):
    """`ok=1;jadi=150;gagal=0;...` -> dict."""
    out = {}
    for bagian in str(teks or '').split(';'):
        pos = bagian.find('=')
        if pos > 0:
            out[bagian[:pos].strip().lower()] = bagian[pos + 1:].strip()
    return out


# ------------------------------------------------------------------ antrean
def klaim(sambungan, baris):
    """Ambil satu pekerjaan secara atomik. Pola sama dengan antrean.py:
    syarat status=eq.menunggu ikut di URL, jadi kalau PC lain sudah
    mengambilnya, PATCH ini mengenai NOL baris."""
    status, data = erp.panggil(
        sambungan, 'PATCH',
        '{}?id=eq.{}&status=eq.menunggu'.format(TABEL, baris['id']),
        {'status': 'berjalan', 'diambil_oleh': NAMA_PC, 'diambil_at': waktu_iso()},
        prefer='return=representation',
    )
    if status >= 300:
        print('[corel] gagal klaim #{}: {} {}'.format(baris['id'], status, data))
        return None
    return data[0] if isinstance(data, list) and data else None


def waktu_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + 'Z'


def lapor(sambungan, id_baris, status, pesan=None, jumlah=None):
    badan = {'status': status, 'selesai_at': waktu_iso()}
    if pesan is not None:
        badan['pesan'] = pesan[:2000]
    if jumlah is not None:
        badan['jumlah_foto'] = jumlah
    kode, data = erp.panggil(sambungan, 'PATCH',
                             '{}?id=eq.{}'.format(TABEL, id_baris), badan)
    if kode >= 300:
        print('[corel] gagal melapor #{}: {} {}'.format(id_baris, kode, data))


def kerjakan(sambungan, cfg, pengaturan, baris, demo=False):
    tanda = '#{} {} {}-{}'.format(baris['id'], baris['jenis'],
                                  baris['dari'], baris['sampai'])

    folder = cari_folder_desain(cfg, baris['jenis'], baris['dari'], baris['sampai'])
    if not folder:
        pesan = 'folder desain tidak ketemu di PC ini (PRODUK {} - {})'.format(
            baris['dari'], baris['sampai'])
        print('[corel] {} GAGAL: {}'.format(tanda, pesan))
        if not demo:
            lapor(sambungan, baris['id'], 'gagal', pesan)
        return

    if not pengaturan['keluaran'] or not pengaturan['template']:
        pesan = ('data/lokal.json belum memuat {"corel": {"keluaran": ..., '
                 '"template": ...}}')
        print('[corel] {} GAGAL: {}'.format(tanda, pesan))
        if not demo:
            lapor(sambungan, baris['id'], 'gagal', pesan)
        return

    berkas_hasil = os.path.join(inti.AKAR, 'output', 'corel-hasil.txt')
    os.makedirs(os.path.dirname(berkas_hasil), exist_ok=True)
    try:
        if os.path.exists(berkas_hasil):
            os.remove(berkas_hasil)
    except OSError:
        pass

    try:
        perintah, mode, dibuang = susun_perintah(baris, folder, pengaturan, berkas_hasil)
    except ValueError as e:
        print('[corel] {} GAGAL: {}'.format(tanda, e))
        if not demo:
            lapor(sambungan, baris['id'], 'gagal', str(e))
        return

    print('[corel] {} mode {} -> {}'.format(tanda, mode, folder))
    if dibuang:
        print('[corel]   setelan tak dikenal dibuang: {}'.format(', '.join(dibuang)))

    if demo:
        print('[corel]   PERINTAH: {}'.format(perintah))
        print('[corel]   (demo - CorelDRAW tidak dipanggil, antrean tidak diubah)')
        return

    mulai = time.time()
    nilai, galat = jalankan_macro(perintah, pengaturan['batas_menit'] * 60)
    lama = time.time() - mulai

    # Macro menulis hasilnya ke berkas juga - dipakai kalau kembalian COM
    # kosong, yang bisa terjadi tergantung versi CorelDRAW.
    teks = str(nilai or '').strip() or baca_hasil(berkas_hasil)
    hasil = urai_hasil(teks)

    if galat:
        pesan = '{} ({:.0f} detik)'.format(galat, lama)
        print('[corel] {} GAGAL: {}'.format(tanda, pesan))
        lapor(sambungan, baris['id'], 'gagal', pesan)
        return

    jadi = int(hasil.get('jadi') or 0)
    gagal = int(hasil.get('gagal') or 0)
    status = 'berhasil' if hasil.get('ok') == '1' else 'gagal'
    pesan = '{} berkas jadi, {} gagal, {:.0f} detik. {}'.format(
        jadi, gagal, lama, hasil.get('catatan', '')[:1200])

    print('[corel] {} {}: {}'.format(tanda, status.upper(), pesan[:160]))
    lapor(sambungan, baris['id'], status, pesan, jadi)


def sekali_putaran(sambungan, cfg, pengaturan, batas=None, demo=False):
    dikerjakan = 0
    while batas is None or dikerjakan < batas:
        kode, data = erp.panggil(
            sambungan, 'GET',
            '{}?pekerjaan=eq.{}&status=eq.menunggu&order=dibuat_at.asc&limit=5'
            '&select=id,jenis,dari,sampai,parameter'.format(TABEL, PEKERJAAN),
        )
        if kode >= 300:
            print('[corel] gagal membaca antrean: {} {}'.format(kode, data))
            return dikerjakan
        if not data:
            return dikerjakan

        maju = False
        for baris in data:
            if demo:
                kerjakan(sambungan, cfg, pengaturan, baris, demo=True)
                dikerjakan += 1
                maju = True
            else:
                diambil = klaim(sambungan, baris)
                if not diambil:
                    continue
                kerjakan(sambungan, cfg, pengaturan, diambil)
                dikerjakan += 1
                maju = True
            if batas is not None and dikerjakan >= batas:
                break
        # Mode demo tidak mengklaim apa pun, jadi baris yang sama akan terbaca
        # lagi di putaran berikutnya - dan berputar selamanya. Satu lintasan
        # sudah cukup untuk melihat perintahnya.
        if demo:
            return dikerjakan
        if not maju:
            return dikerjakan
    return dikerjakan


def main():
    p = argparse.ArgumentParser(description='Pengerjaan antrean foto produk CorelDRAW')
    p.add_argument('--sekali', action='store_true', help='satu pekerjaan saja')
    p.add_argument('--pantau', action='store_true', help='tetap hidup, periksa berkala')
    p.add_argument('--jeda', type=int, default=30, help='jeda --pantau, detik')
    p.add_argument('--demo', action='store_true',
                   help='tampilkan perintahnya saja, jangan panggil CorelDRAW')
    a = p.parse_args()

    sambungan = erp.baca_erp()
    if not sambungan:
        print('[corel] data/lokal.json belum memuat {"erp": {"url": ..., "jwt_secret": ...}}')
        return 1

    cfg = inti.baca_config()
    pengaturan = baca_corel()
    print('[corel] PC {} | keluaran {} | template {} | batas {:.0f} menit'.format(
        NAMA_PC, pengaturan['keluaran'] or '(belum diisi)',
        pengaturan['template'] or '(belum diisi)', pengaturan['batas_menit']))

    if a.pantau:
        print('[corel] memantau antrean tiap {} detik. Ctrl+C untuk berhenti.'.format(a.jeda))
        while True:
            try:
                sekali_putaran(sambungan, cfg, pengaturan, demo=a.demo)
            except KeyboardInterrupt:
                print('[corel] berhenti.')
                return 0
            except Exception as e:  # noqa: BLE001
                print('[corel] putaran gagal: {}'.format(e))
            time.sleep(a.jeda)

    n = sekali_putaran(sambungan, cfg, pengaturan,
                       batas=1 if a.sekali else None, demo=a.demo)
    print('[corel] {} pekerjaan dikerjakan.'.format(n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
