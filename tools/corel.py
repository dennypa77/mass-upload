# -*- coding: utf-8 -*-
"""Ambil perintah dari ERP, jalankan macro CorelDRAW di PC ini, lalu lapor
balik.

Pasangan dari tools/antrean.py, dengan pembagian kerja yang sama: ERP yang
MENGANTRE, PC yang MENGERJAKAN. Sambungannya selalu keluar dari PC ini, jadi
tidak ada port yang perlu dibuka di jaringan kantor.

Bedanya hanya isi pekerjaannya. antrean.py menyalin foto yang sudah jadi;
berkas ini menjalankan macro yang MEMBUATNYA, lewat COM:

    CorelDRAW.Application -> GMSManager.RunMacro
        "GlobalMacros", "<modul>.<gerbang>", "<perintah>"

Dua jenis pekerjaan diambil dari antrean yang sama:

    pekerjaan=corel   -> modFotoProduk.FotoProdukOtomatis
                         (foto varian & foto sampul; folder desain dicari
                          sendiri dari nomor blok)
    pekerjaan=stiker  -> AutoStikerOutline1.StikerOutlineOtomatis
                         (gambar -> .cdr bergaris potong; folder DISEBUT di
                          baris antreannya, diisi operator di ERP)

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
NAMA_PC = os.environ.get('COMPUTERNAME') or socket.gethostname()

PROYEK_GMS = 'GlobalMacros'

# Tiap jenis pekerjaan punya macro sendiri DAN penanda sendiri. Penanda
# selalu Sub tanpa parameter, karena hanya yang seperti itu terdaftar di
# GMSManager Macros - lihat sambung_corel.
MACRO = {
    'corel': 'modFotoProduk.FotoProdukOtomatis',
    'stiker': 'AutoStikerOutline1.StikerOutlineOtomatis',
}
PENANDA_MACRO = {
    'corel': 'modFotoProduk.FotoProdukJembatanSiap',
    'stiker': 'AutoStikerOutline1.StikerOutlineJembatanSiap',
}

# Kunci setelan yang boleh dioper ke macro. Yang tidak dikenal DIBUANG dan
# dilaporkan, bukan diteruskan: nama kunci yang salah membuat macro memakai
# bawaannya diam-diam, dan hasilnya baru ketahuan sesudah 150 berkas.
SETELAN_SAH = (
    'ukuran', 'geserx', 'gesery', 'teks', 'turun', 'jarakteks',
    'kolom', 'isi', 'jarakkolom', 'jarakbaris', 'geser', 'turunsampul',
)

BATAS_MENIT_BAWAAN = 20

PROGID_UMUM = 'CorelDRAW.Application'

SETELAN_STIKER = ('jalur', 'mode', 'ukuran', 'jarak')


# --------------------------------------------------------------- pengaturan
def baca_corel():
    """Folder keluaran & template PC ini, dari data/lokal.json."""
    lokal = inti.baca_lokal() or {}
    c = lokal.get('corel') or {}
    return {
        'keluaran': str(c.get('keluaran') or '').strip(),
        'template': str(c.get('template') or '').strip(),
        'batas_menit': float(c.get('batas_menit') or BATAS_MENIT_BAWAAN),
        'progid': str(c.get('progid') or '').strip(),
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

    keluaran = str(param.get('keluar') or '').strip() or pengaturan['keluaran']

    bagian = [
        'mode=' + mode,
        'desain=' + folder_desain,
        'keluaran=' + keluaran,
        'template=' + pengaturan['template'],
        'hasil=' + berkas_hasil,
    ]

    dibuang = []
    for kunci, nilai in param.items():
        k = str(kunci).lower()
        if k in ('mode', 'keluar'):
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


def susun_perintah_stiker(baris, berkas_hasil):
    """String perintah untuk StikerOutlineOtomatis, dari baris antrean.

    Folder DISEBUT di baris antreannya (berbeda dengan pekerjaan foto
    produk, yang foldernya dicari PC sendiri dari nomor blok): gambar
    stiker tidak tersusun per blok dan operator memilih foldernya tiap
    kali. Konsekuensinya diperiksa di sini - folder yang tidak ada di PC
    ini dilaporkan GAGAL dengan menyebut path-nya, bukan dicari-cari di
    tempat lain.
    """
    param = baris.get('parameter') or {}
    if isinstance(param, str):
        try:
            param = json.loads(param)
        except ValueError:
            param = {}

    masuk = str(baris.get('folder_path') or '').strip()
    keluar = str(param.get('keluar') or '').strip()
    if not masuk or not keluar:
        raise ValueError('baris antrean tidak memuat folder masuk/keluar')
    if not os.path.isdir(masuk):
        raise ValueError('folder sumber tidak ada di PC ini: ' + masuk)

    # Folder tujuan dibuat kalau belum ada - macro menulis ke dalamnya dan
    # akan gagal satu per satu kalau induknya tidak ada.
    try:
        os.makedirs(keluar, exist_ok=True)
    except OSError as e:
        raise ValueError('folder tujuan tidak bisa dibuat: {} ({})'.format(keluar, e))

    jalur = str(param.get('jalur') or 'png').lower()
    if jalur not in ('massal', 'png', 'svg'):
        raise ValueError('jalur tidak dikenal: ' + jalur)

    bagian = [
        'jalur=' + jalur,
        'masuk=' + masuk,
        'keluar=' + keluar,
        'hasil=' + berkas_hasil,
    ]
    for kunci in SETELAN_STIKER:
        if kunci == 'jalur':
            continue
        if param.get(kunci) is not None:
            bagian.append('{}={}'.format(kunci, param[kunci]))

    for b in bagian:
        if ';' in b:
            raise ValueError('ada titik koma di dalam nilai: ' + b)

    return ';'.join(bagian), jalur


# --------------------------------------------------------------------- corel
def progid_terpasang():
    """ProgID CorelDRAW yang terdaftar di PC ini, versi terbaru dulu.

    `CorelDRAW.Application` TANPA nomor versi hanya menunjuk satu suite -
    yang terakhir mendaftarkan diri, belum tentu yang dipakai operator. Di PC
    develop ini ia menunjuk suite 25 sementara modFotoProduk dipasang di
    suite 27: memakainya buta berarti Corel versi lain ikut dinyalakan dalam
    keadaan kosong, lalu macronya dilaporkan "tidak ditemukan" - padahal ada,
    cuma di instance yang lain.
    """
    try:
        import winreg
    except ImportError:
        return []
    ada = []
    for versi in range(40, 19, -1):
        nama = '{}.{}'.format(PROGID_UMUM, versi)
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, nama))
        except OSError:
            continue
        ada.append(nama)
    return ada


def daftar_makro(app):
    """Nama makro yang terdaftar di proyek GMS, mis. "modFotoProduk.Stop"."""
    nama = []
    try:
        proyek = app.GMSManager.Projects.Item(PROYEK_GMS)
        makro = proyek.Macros
        for i in range(1, makro.Count + 1):
            nama.append(str(makro.Item(i).Name))
    except Exception:  # noqa: BLE001
        pass
    return nama


def sambung_corel(win32com, penanda, progid=''):
    """(app, progid) untuk suite yang BENAR-BENAR memuat jembatan ERP.

    Kenapa daftar makro diperiksa dan bukan sekadar mencoba memanggil:
    GMSManager.RunMacro MENGABAIKAN nama yang tidak dikenal tanpa sepatah
    pun kesalahan. Sudah diuji di PC host - "modNgawur.Apa" kembali None
    dalam 0,0 detik, sama persis dengan panggilan yang berhasil. Jadi
    panggilan buta tidak bisa membedakan "berhasil" dari "modulnya versi
    lama", dan pekerjaan yang sebetulnya tidak terjadi akan dilaporkan
    selesai.

    Daftar Macros hanya memuat Sub tanpa parameter, jadi FotoProdukOtomatis
    (Function, berparameter) tidak pernah ada di situ. Itulah sebabnya
    modFotoProduk.bas punya Sub penanda yang isinya kosong.
    """
    kandidat = [progid] if progid else progid_terpasang() + [PROGID_UMUM]
    sebab = []
    for nama in kandidat:
        try:
            app = win32com.Dispatch(nama)
        except Exception as e:  # noqa: BLE001
            sebab.append('{}: tidak bisa dijalankan ({})'.format(nama, e))
            continue
        try:
            app.Visible = True
        except Exception:  # noqa: BLE001
            pass
        modul = penanda.split('.')[0]
        makro = daftar_makro(app)
        if penanda in makro:
            return app, nama
        if any(m.startswith(modul + '.') for m in makro):
            sebab.append('{}: {} ada tapi versi lama - belum memuat {}'
                         .format(nama, modul, penanda.split('.')[-1]))
        else:
            sebab.append('{}: {} tidak dimuat di sini'.format(nama, modul))
    raise RuntimeError(
        'Tidak ada CorelDRAW yang siap. Di PC host: buka VBA editor '
        '(Alt+F11) -> klik proyek GlobalMacros -> File > Import File -> '
        'pilih {}.bas versi terbaru (ganti modul lamanya), lalu simpan. '
        'Bisa juga menyebut suitenya di data/lokal.json -> corel.progid. '
        'Yang dicoba: {}'.format(penanda.split('.')[0], ' | '.join(sebab)))


def ringkas_galat(e):
    """Pesan COM yang terbaca manusia, bukan tuple kode kesalahan."""
    try:
        rinci = e.args[2]
        if rinci and rinci[2]:
            return str(rinci[2])
    except Exception:  # noqa: BLE001
        pass
    return str(e)


def jalankan_macro(perintah, batas_detik, progid='', pekerjaan='corel', pantau=None):
    """Panggil macro lewat COM. Kembalikan (hasil, galat).

    Panggilan COM tidak bisa dibatalkan dari luar, jadi dijalankan di utas
    sendiri dan ditunggu dengan batas waktu. Kalau lewat batas, utasnya
    DIBIARKAN - CorelDRAW masih memegangnya - dan pekerjaan ini dilaporkan
    gagal supaya tidak menggantung selamanya di antrean sebagai "berjalan".
    """
    hasil = {'nilai': None, 'galat': None, 'progid': ''}

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
            app, dipakai = sambung_corel(
                win32com.client, PENANDA_MACRO[pekerjaan], progid)
            hasil['progid'] = dipakai
            # Perintah dikirim sebagai argumen biasa, bukan tuple: RunMacro
            # meneruskan apa yang diberikan apa adanya, dan tuple sampai di
            # VBA sebagai deret - tidak cocok dengan `ByVal perintah As
            # String`, dan macro-nya diam tanpa kesalahan.
            hasil['nilai'] = app.GMSManager.RunMacro(
                PROYEK_GMS, MACRO[pekerjaan], perintah)
        except Exception as e:  # noqa: BLE001
            hasil['galat'] = '{}: {}'.format(type(e).__name__, e)

    utas = threading.Thread(target=kerja, daemon=True)
    utas.start()
    if pantau is not None:
        pantau(utas)
    utas.join(batas_detik)

    if utas.is_alive():
        return None, ('lewat batas {:.0f} menit - CorelDRAW mungkin menampilkan '
                      'dialognya sendiri. Periksa layar PC host.'
                      .format(batas_detik / 60.0))
    return hasil['nilai'], hasil['galat']


def isi_folder(akar):
    """Set path semua berkas di bawah `akar`. Kosong kalau foldernya belum ada."""
    hasil = set()
    for induk, _, berkas in os.walk(akar):
        for b in berkas:
            hasil.add(os.path.join(induk, b))
    return hasil


def hitung_bahan(folder, ekstensi, rekursif):
    """Berapa berkas yang akan dikerjakan - penyebut untuk bar kemajuan."""
    n = 0
    if rekursif:
        for _, _, berkas in os.walk(folder):
            n += sum(1 for b in berkas if b.lower().endswith(ekstensi))
    else:
        try:
            n = sum(1 for b in os.listdir(folder)
                    if os.path.isfile(os.path.join(folder, b))
                    and b.lower().endswith(ekstensi))
        except OSError:
            n = 0
    return n


def pantau_kemajuan(sambungan, id_baris, keluaran, sebelum, total, utas, jeda=4):
    """Lapor "maju N/TOTAL" ke antrean selama macro masih bekerja.

    Angkanya dihitung dari berkas yang SUDAH muncul di folder keluaran,
    bukan dari waktu yang berlalu. Bar yang bergerak karena timer akan tetap
    bergerak saat CorelDRAW sebenarnya menggantung - justru di saat itulah
    operator paling butuh tahu bahwa tidak ada yang terjadi.

    Dijalankan di utas pemanggil sementara macro berjalan di utas lain.
    """
    terakhir = -1
    while utas.is_alive():
        utas.join(jeda)
        if not utas.is_alive():
            break
        n = len(isi_folder(keluaran) - sebelum)
        if n == terakhir:
            continue
        terakhir = n
        try:
            erp.panggil(sambungan, 'PATCH',
                        '{}?id=eq.{}'.format(TABEL, id_baris),
                        {'pesan': 'maju {}/{}'.format(n, total)})
        except Exception:  # noqa: BLE001
            # Laporan kemajuan tidak boleh menjatuhkan pekerjaannya sendiri:
            # jaringan putus sebentar lebih baik daripada batch 50 berkas
            # yang berhenti di tengah.
            pass


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


def siapkan_berkas_hasil(nama):
    """Berkas tempat macro menulis hasilnya; dikosongkan dulu."""
    berkas = os.path.join(inti.AKAR, 'output', nama)
    os.makedirs(os.path.dirname(berkas), exist_ok=True)
    try:
        if os.path.exists(berkas):
            os.remove(berkas)
    except OSError:
        pass
    return berkas


def laporkan_hasil(sambungan, baris, tanda, teks, galat, lama, label):
    """Satu tempat untuk mengubah hasil macro jadi laporan antrean."""
    if galat:
        pesan = '{} ({:.0f} detik)'.format(galat, lama)
        print('[corel] {} GAGAL: {}'.format(tanda, pesan))
        lapor(sambungan, baris['id'], 'gagal', pesan)
        return

    hasil = urai_hasil(teks)
    jadi = int(hasil.get('jadi') or 0)
    gagal = int(hasil.get('gagal') or 0)
    status = 'berhasil' if hasil.get('ok') == '1' else 'gagal'
    pesan = '{} {} jadi, {} gagal, {:.0f} detik. {}'.format(
        jadi, label, gagal, lama, hasil.get('catatan', '')[:1200])
    print('[corel] {} {}: {}'.format(tanda, status.upper(), pesan[:160]))
    lapor(sambungan, baris['id'], status, pesan, jadi)


def kerjakan_stiker(sambungan, pengaturan, baris, demo=False):
    """Pekerjaan outline stiker: gambar -> .cdr dengan garis potong."""
    tanda = '#{} {}'.format(baris['id'], baris['jenis'])
    berkas_hasil = siapkan_berkas_hasil('stiker-hasil.txt')

    try:
        perintah, jalur = susun_perintah_stiker(baris, berkas_hasil)
    except ValueError as e:
        print('[corel] {} GAGAL: {}'.format(tanda, e))
        if not demo:
            lapor(sambungan, baris['id'], 'gagal', str(e))
        return

    print('[corel] {} outline jalur {}'.format(tanda, jalur))
    if demo:
        print('[corel]   PERINTAH: {}'.format(perintah))
        print('[corel]   (demo - CorelDRAW tidak dipanggil, antrean tidak diubah)')
        return

    keluar = perintah.split('keluar=', 1)[1].split(';', 1)[0]
    total = hitung_bahan(baris['folder_path'],
                         ('.svg',) if jalur == 'svg' else ('.png', '.jpg', '.jpeg'),
                         jalur == 'massal')
    sebelum = isi_folder(keluar)
    print('[corel]   {} berkas bahan'.format(total))

    mulai = time.time()
    nilai, galat = jalankan_macro(
        perintah, pengaturan['batas_menit'] * 60, pengaturan['progid'],
        pekerjaan='stiker',
        pantau=(lambda u: pantau_kemajuan(sambungan, baris['id'], keluar, sebelum, total, u))
        if total else None)
    lama = time.time() - mulai
    teks = baca_hasil(berkas_hasil) or str(nilai or '').strip()
    laporkan_hasil(sambungan, baris, tanda, teks, galat, lama, 'berkas CDR')


def kerjakan(sambungan, cfg, pengaturan, baris, demo=False):
    if (baris.get('pekerjaan') or 'corel') == 'stiker':
        return kerjakan_stiker(sambungan, pengaturan, baris, demo=demo)

    tanda = '#{} {} {}-{}'.format(baris['id'], baris['jenis'],
                                  baris['dari'], baris['sampai'])

    # Folder desain yang DISEBUT di baris antrean menang: itu perintah dari
    # halaman Desain Host, untuk folder yang tidak bernomor blok. Kalau
    # kosong, barulah dicari dari nomor bloknya seperti biasa.
    folder = str(baris.get('folder_path') or '').strip()
    if folder:
        if not os.path.isdir(folder):
            pesan = 'folder desain tidak ada di PC ini: ' + folder
            print('[corel] {} GAGAL: {}'.format(tanda, pesan))
            if not demo:
                lapor(sambungan, baris['id'], 'gagal', pesan)
            return
    else:
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

    berkas_hasil = siapkan_berkas_hasil('corel-hasil.txt')

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

    keluaran = perintah.split('keluaran=', 1)[1].split(';', 1)[0]
    # Penyebutnya jumlah desain untuk foto varian (1 desain = 1 foto); untuk
    # foto sampul jumlahnya ditentukan template, jadi barnya tanpa angka.
    total = hitung_bahan(folder, ('.cdr', '.png'), False) if mode == 'varian' else 0
    sebelum = isi_folder(keluaran)

    mulai = time.time()
    nilai, galat = jalankan_macro(
        perintah, pengaturan['batas_menit'] * 60, pengaturan['progid'],
        pantau=(lambda u: pantau_kemajuan(sambungan, baris['id'], keluaran, sebelum, total, u))
        if total else None)
    lama = time.time() - mulai

    # Berkas hasil yang ditulis macro adalah sumber utamanya, bukan pelengkap:
    # GMSManager.RunMacro di CorelDRAW 2026 (suite 27) SELALU mengembalikan
    # Nothing, berapa pun yang dikembalikan fungsi VBA-nya. Sudah diukur di
    # PC host. Kembalian COM tetap dipakai kalau ada, untuk versi yang
    # kelakuannya berbeda.
    teks = baca_hasil(berkas_hasil) or str(nilai or '').strip()
    laporkan_hasil(sambungan, baris, tanda, teks, galat, lama, 'foto')


def sekali_putaran(sambungan, cfg, pengaturan, batas=None, demo=False):
    dikerjakan = 0
    while batas is None or dikerjakan < batas:
        kode, data = erp.panggil(
            sambungan, 'GET',
            '{}?pekerjaan=in.({})&status=eq.menunggu&order=dibuat_at.asc'
            '&limit=5&select=id,pekerjaan,jenis,dari,sampai,folder_path,parameter'
            .format(TABEL, ','.join(sorted(MACRO))),
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
    p.add_argument('--url', default='',
                   help='alamat PostgREST lain, mis. http://127.0.0.1:3102 '
                        'untuk DB dev (rahasianya tetap dari lokal.json)')
    a = p.parse_args()

    sambungan = erp.baca_erp()
    if not sambungan:
        print('[corel] data/lokal.json belum memuat {"erp": {"url": ..., "jwt_secret": ...}}')
        return 1

    # Alamat boleh ditimpa, rahasianya TIDAK: yang perlu diarahkan saat
    # mencoba di lokal cuma DB-nya (dev lewat terowongan SSH), dan rahasia
    # di baris perintah akan tertinggal di riwayat shell.
    if a.url:
        sambungan = dict(sambungan, url=a.url.rstrip('/'))
        print('[corel] DB ditimpa: {}'.format(sambungan['url']))

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
