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
import shutil
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

# Awalan Sub penanda VERSI di tiap modul: namanya berakhir dengan nomor
# versi (mis. FotoProdukVersi_2_2). Daftar makro CorelDRAW hanya memberi
# NAMA - nilai kembalian tidak sampai ke pemanggil - jadi versinya memang
# harus dititipkan di nama.
AWALAN_VERSI = {
    'corel': 'modFotoProduk.FotoProdukVersi_',
    'stiker': 'AutoStikerOutline1.StikerOutlineVersi_',
}

# Versi minimum yang dibutuhkan FITUR UJI COBA. Sengaja hanya untuk uji:
# proses massal tetap jalan dengan modul lama, karena yang hilang di sana
# cuma batas 1 berkas dan PNG pendamping - keduanya tidak dipakai saat
# massal. Menolak semua pekerjaan hanya karena modul belum diimpor ulang
# akan menghentikan produksi tanpa perlu.
MIN_VERSI_UJI = {'corel': (2, 2), 'stiker': (3, 2)}

# Ukuran/jarak DESIMAL (mis. outline Shoe Charm 0.6 mm) butuh outline >= 3.3:
# versi lama membaca "0.6" lewat CDbl setelah titik diganti koma, dan di Windows
# en-US (PC host) hasilnya 6 mm - batch jalan "berhasil" dengan outline salah.
# Angka bulat aman di semua versi, jadi hanya perintah berdesimal yang ditolak.
MIN_VERSI_DESIMAL = {'stiker': (3, 3)}
RX_DESIMAL = re.compile(r'(?:^|;)(?:ukuran|jarak)=\d+\.\d*[1-9]')

# Setelan yang butuh modul versi tertentu: (pola, versi minimum, nama fitur).
# Modul lama TIDAK mengeluh soal setelan yang tidak dikenalnya - ia memakai
# bawaannya dan mengerjakan hal lain, lalu melapor berhasil. `bundle` di modul
# < 2.8 menghasilkan 9 foto foto1..foto9 berlatar -utama1.cdr yang menimpa
# foto sampul produk, bukan 8 foto bundle.
MIN_VERSI_SETELAN = {
    'corel': (
        (re.compile(r'(?:^|;)bundle=[1-9]'), (2, 8), 'setelan bundle'),
        (re.compile(r'(?:^|;)sampulke=[1-9]'), (2, 6), 'setelan sampulke'),
    ),
}

# Kunci setelan yang boleh dioper ke macro. Yang tidak dikenal DIBUANG dan
# dilaporkan, bukan diteruskan: nama kunci yang salah membuat macro memakai
# bawaannya diam-diam, dan hasilnya baru ketahuan sesudah 150 berkas.
SETELAN_SAH = (
    'ukuran', 'geserx', 'gesery', 'teks', 'turun', 'jarakteks',
    'kolom', 'isi', 'jarakkolom', 'jarakbaris', 'geser', 'turunsampul',
    'jumlah', 'pecah', 'sampulke', 'bundle',
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
        # Folder bahan AI bersama (Google Drive desktop). Kosongkan untuk mematikan unduhan.
        'bahan_ai': str(c.get('bahan_ai', BAHAN_AI_BAWAAN) or '').strip(),
        # True di PC yang BUKAN host: ia berhenti sebelum mengambil
        # apa pun dari antrean. Lihat catatan di README soal kenapa
        # penandanya di sini dan bukan nama berkas.
        'bukan_host': bool(c.get('bukan_host')),
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
def susun_perintah(baris, folder_desain, pengaturan, berkas_hasil, template=''):
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
        'template=' + (template or pengaturan['template']),
        'hasil=' + berkas_hasil,
    ]

    if param.get('uji'):
        bagian.append('maks=1')

    dibuang = []
    for kunci, nilai in param.items():
        k = str(kunci).lower()
        # Kunci urusan ERP/PC, bukan setelan macro. Disebut di sini supaya
        # tidak dilaporkan sebagai "setelan tak dikenal" - peringatan yang
        # benar bentuknya tapi salah sasaran, dan membuat peringatan yang
        # SUNGGUHAN (salah ketik nama setelan) tenggelam di antaranya.
        if k in ('mode', 'keluar', 'unduh', 'set', 'uji') or k.startswith('pratinjau'):
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


def keluar_per_sumber(masuk, akar):
    """<akar>\\<nama folder sumber> - hasil terkumpul per folder, bukan menumpuk.

    Tanpa ini dua batch yang dikirim ke folder tujuan yang sama akan
    bercampur, dan CDR dari batch kemarin tidak bisa dibedakan dari yang
    hari ini kecuali lewat tanggal berkas.

    Kalau operator SUDAH mengetik nama itu di folder tujuan, tidak
    ditambahkan lagi: "D:\\CDR\\JB-001\\JB-001" bukan yang dia maksud.
    """
    nama = os.path.basename(os.path.normpath(masuk))
    if not nama:
        return akar
    if os.path.basename(os.path.normpath(akar)).lower() == nama.lower():
        return akar
    return os.path.join(akar, nama)


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

    keluar = keluar_per_sumber(masuk, keluar)

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
    if param.get('uji'):
        bagian.append('maks=1')
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


def versi_modul(makro, pekerjaan):
    """(mayor, minor) modul yang dimuat CorelDRAW, atau None kalau tidak ada.

    None berarti modulnya lebih tua daripada versi yang mulai menitipkan
    nomor di nama Sub - bukan berarti tidak ada modulnya.
    """
    awalan = AWALAN_VERSI.get(pekerjaan)
    if not awalan:
        return None
    for m in makro:
        if not m.startswith(awalan):
            continue
        angka = m[len(awalan):].split('_')
        try:
            return tuple(int(x) for x in angka[:2])
        except ValueError:
            continue
    return None


def periksa_versi_uji(makro, pekerjaan):
    """Pesan keluhan kalau modul terlalu tua untuk fitur uji, atau None."""
    butuh = MIN_VERSI_UJI.get(pekerjaan)
    if not butuh:
        return None
    ada = versi_modul(makro, pekerjaan)
    if ada is not None and ada >= butuh:
        return None
    modul = PENANDA_MACRO[pekerjaan].split('.')[0]
    return ('modul {} di CorelDRAW masih versi {} - fitur uji coba butuh {}. '
            'Impor ulang dari ERP: RnD -> Unduhan Alat.'
            .format(modul,
                    '.'.join(str(x) for x in ada) if ada else 'lama',
                    '.'.join(str(x) for x in butuh)))


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


def jalankan_macro(perintah, batas_detik, progid='', pekerjaan='corel', pantau=None,
                   uji=False):
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
            if uji:
                # Diperiksa SEBELUM macro dijalankan: modul lama akan
                # mengerjakan perintahnya dengan senang hati lalu diam-diam
                # melewatkan PNG pratinjau, dan yang terlihat operator cuma
                # "tidak ada gambar" tanpa sebab.
                keluhan = periksa_versi_uji(daftar_makro(app), pekerjaan)
                if keluhan:
                    hasil['galat'] = keluhan
                    return
            for pola, butuh_set, fitur in MIN_VERSI_SETELAN.get(pekerjaan, ()):
                if not pola.search(perintah or ''):
                    continue
                ada = versi_modul(daftar_makro(app), pekerjaan)
                if ada is None or ada < butuh_set:
                    hasil['galat'] = (
                        'modul {} di CorelDRAW masih versi {} - {} butuh {}. '
                        'Modul lama tidak menolak setelan itu, ia mengabaikannya '
                        'dan menulis foto yang salah. Impor ulang dari ERP: '
                        'RnD -> Unduhan Alat.'.format(
                            PENANDA_MACRO[pekerjaan].split('.')[0],
                            '.'.join(str(x) for x in ada) if ada else 'lama',
                            fitur,
                            '.'.join(str(x) for x in butuh_set)))
                    return

            butuh = MIN_VERSI_DESIMAL.get(pekerjaan)
            if butuh and RX_DESIMAL.search(perintah or ''):
                ada = versi_modul(daftar_makro(app), pekerjaan)
                if ada is None or ada < butuh:
                    hasil['galat'] = (
                        'modul {} di CorelDRAW masih versi {} - ukuran/jarak desimal butuh {} '
                        '(versi lama membaca 0.6 mm sebagai 6 mm). Impor ulang dari ERP: '
                        'RnD -> Unduhan Alat.'.format(
                            PENANDA_MACRO[pekerjaan].split('.')[0],
                            '.'.join(str(x) for x in ada) if ada else 'lama',
                            '.'.join(str(x) for x in butuh)))
                    return
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


def berkas_terbaru(akar, sejak=0.0, ekstensi=None):
    """Berkas termuda di bawah `akar` yang dibuat sesudah `sejak`.

    `ekstensi` menyaring jenisnya — dipakai pratinjau, yang hanya berguna
    kalau yang diambil gambar.
    """
    muda = None
    for induk, _, berkas in os.walk(akar):
        for b in berkas:
            if ekstensi and not b.lower().endswith(tuple(ekstensi)):
                continue
            jalur = os.path.join(induk, b)
            try:
                waktu = os.path.getmtime(jalur)
            except OSError:
                continue
            if waktu < sejak:
                continue
            if muda is None or waktu > muda[1]:
                muda = (jalur, waktu)
    return muda[0] if muda else None


def unggah_pratinjau(url_unggah, jalur_berkas):
    """Kirim hasil uji ke URL yang sudah ditandatangani ERP.

    Bukan ke R2 milik PC ini, walau kredensialnya ada: domain bucket itu
    tidak melayani pembacaan publik - objek lama pun dijawab 403 - jadi
    gambarnya tidak akan bisa ditampilkan di halaman ERP. Yang dipakai
    URL PUT bertanda tangan dari ERP, pola yang sama dengan background
    tapi arah sebaliknya. Tidak ada kredensial baru di sisi mana pun.
    """
    import urllib.request

    with open(jalur_berkas, 'rb') as f:
        isi = f.read()
    minta = urllib.request.Request(url_unggah, data=isi, method='PUT')
    minta.add_header('Content-Type', 'image/png')
    minta.add_header('Content-Length', str(len(isi)))
    with urllib.request.urlopen(minta, timeout=120) as jawab:
        if jawab.status >= 300:
            raise RuntimeError('penyimpanan menolak: HTTP {}'.format(jawab.status))
    return len(isi)


def unduh_template(daftar, folder):
    """Unduh [{nama, url}] ke `folder`. Kembalikan (jumlah baru, jumlah lewat).

    Sepupu dari unduh_bahan() di bawah, yang mengurus gambar bahan Garapan.
    Dipisah karena aturannya memang beda: background .cdr dipakai ulang
    berkali-kali sehingga perlu cache, sementara gambar bahan yang dibuat
    ulang justru HARUS menimpa yang lama.

    Berkas yang ukurannya SUDAH sama dilewati. Background .cdr belasan MB
    dan satu hari bisa berisi puluhan pekerjaan dengan set yang sama -
    mengunduh ulang tiap kali berarti menunggu jaringan, bukan menunggu
    CorelDRAW.

    Perbandingannya ukuran, bukan tanggal: R2 memberi Content-Length di
    tiap jawaban, sementara tanggal berkas lokal berubah hanya karena
    disalin. Kalau isi berkas di R2 diganti dengan yang ukurannya persis
    sama, cache ini akan meleset - karena itu set background yang diubah
    sebaiknya diberi nama baru, bukan ditimpa.
    """
    import urllib.request

    os.makedirs(folder, exist_ok=True)
    baru = 0
    lewat = 0
    for b in daftar or []:
        nama = os.path.basename(str(b.get('nama') or '').strip())
        url = str(b.get('url') or '').strip()
        if not nama or not url:
            raise ValueError('daftar unduh memuat baris tanpa nama/url')

        tujuan = os.path.join(folder, nama)
        with urllib.request.urlopen(url, timeout=120) as jawab:
            panjang = jawab.headers.get('Content-Length')
            if panjang and os.path.exists(tujuan):
                try:
                    if os.path.getsize(tujuan) == int(panjang):
                        lewat += 1
                        continue
                except (OSError, ValueError):
                    pass
            # Ditulis ke berkas sementara dulu: unduhan yang putus di tengah
            # meninggalkan .cdr setengah jadi yang dibaca CorelDRAW sebagai
            # berkas rusak, dan pesannya tidak menyebut sebabnya.
            sementara = tujuan + '.part'
            with open(sementara, 'wb') as f:
                while True:
                    potong = jawab.read(1024 * 256)
                    if not potong:
                        break
                    f.write(potong)
        os.replace(sementara, tujuan)
        baru += 1
    return baru, lewat


def isi_folder(akar):
    """Set path semua berkas di bawah `akar`. Kosong kalau foldernya belum ada."""
    hasil = set()
    for induk, _, berkas in os.walk(akar):
        for b in berkas:
            hasil.add(os.path.join(induk, b))
    return hasil


def hitung_template(folder, jenis, nomor=0):
    """Berapa template toko di folder itu untuk modul ini.

        'varian' -> tokoN.cdr          (bukan -utama, bukan -bundle)
        'sampul' -> tokoN-utamaM.cdr
        'bundle' -> tokoN-bundle.cdr

    Dipakai sebagai pengali penyebut bar kemajuan. Foto varian menulis
    SATU berkas per desain PER TOKO - 50 desain dengan 3 template berarti
    150 berkas, bukan 50. Tanpa pengali ini barnya sampai "maju 99/50",
    angka yang membuat orang berhenti memercayainya.

    Pembagian tiga ini HARUS sama dengan FpTemplate di modFotoProduk.bas.
    Saat berkas -bundle.cdr muncul (2026-10-16) aturan lama "bukan -utama"
    ikut menghitungnya sebagai template varian: tiga toko terbaca enam, dan
    126 foto dilaporkan "maju 126/252" - mentok di separuh selamanya.
    """
    n = 0
    try:
        for b in os.listdir(folder):
            nama = b.lower()
            if not nama.endswith('.cdr') or not nama.startswith('toko'):
                continue
            bundle = nama[:-4].endswith('-bundle')
            utama = '-utama' in nama
            # `nomor` = setelan SampulKe: modul Foto Utama 2 hanya memakai
            # -utama2.cdr, jadi -utama1.cdr tidak boleh ikut jadi penyebut.
            sesuai = utama and (not nomor or nama[:-4].endswith('-utama' + str(nomor)))
            cocok = {'varian': not utama and not bundle,
                     'sampul': sesuai,
                     'bundle': bundle}.get(jenis, False)
            if cocok:
                n += 1
    except OSError:
        return 0
    return n


def angka_setelan(perintah, kunci, bawaan=0):
    """Nilai satu setelan di dalam perintah macro, mis. jumlah=5 -> 5."""
    for bagian in (perintah or '').split(';'):
        k, _, v = bagian.partition('=')
        if k.strip().lower() == kunci:
            try:
                return float(str(v).strip().replace(',', '.'))
            except ValueError:
                return bawaan
    return bawaan


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


def siapkan_folder_uji():
    """Folder keluaran khusus uji coba, dikosongkan tiap kali.

    Hasil uji TIDAK boleh menumpang folder tujuan sungguhan: satu berkas
    percobaan yang tertinggal di sana akan ikut terunggah sebagai foto
    listing. Dikosongkan dulu supaya "berkas terbaru" yang dicari
    sesudahnya pasti milik uji ini, bukan sisa uji kemarin.
    """
    folder = os.path.join(inti.AKAR, 'output', 'pratinjau-kerja')
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    return folder


def lapor(sambungan, id_baris, status, pesan=None, jumlah=None, parameter=None):
    badan = {'status': status, 'selesai_at': waktu_iso()}
    if pesan is not None:
        badan['pesan'] = pesan[:2000]
    if jumlah is not None:
        badan['jumlah_foto'] = jumlah
    if parameter is not None:
        badan['parameter'] = parameter
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


def laporkan_hasil(sambungan, baris, tanda, teks, galat, lama, label,
                   cfg=None, folder_uji=None, param=None):
    """Satu tempat untuk mengubah hasil macro jadi laporan antrean.

    Untuk pekerjaan uji, berkas yang baru jadi ikut diunggah ke R2 dan
    alamatnya dititipkan di `parameter.pratinjau` — itulah yang
    ditampilkan ERP sebagai thumbnail.
    """
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

    param_baru = None
    if folder_uji and status == 'berhasil':
        # Yang dicari gambar, bukan sekadar berkas termuda: modul Outline
        # menghasilkan .cdr DAN .png pendamping, dan yang bisa ditampilkan
        # browser cuma PNG-nya.
        contoh = berkas_terbaru(folder_uji, ekstensi=('.png', '.jpg', '.jpeg'))
        url_unggah = (param or {}).get('pratinjau_unggah')
        if contoh is None:
            # Tidak adanya gambar BUKAN kegagalan dengan sendirinya. Aturan ini
            # lahir untuk foto produk, yang keluarannya memang PNG: nol gambar
            # di sana berarti macro tidak mengerjakan apa pun sambil melapor
            # berhasil (kejadian 2026-10-13). Outline menghasilkan .cdr, dan
            # PNG pendamping hanya ada di sebagian jalur - menandainya gagal
            # membuat pekerjaan yang jelas-jelas jadi terlihat merah di ERP.
            #
            # Yang menentukan sekarang jumlah berkas jadi, bukan ada-tidaknya
            # pratinjau.
            if jadi <= 0:
                status = 'gagal'
                pesan = 'uji selesai tapi tidak ada berkas yang jadi. ' + pesan
            else:
                pesan = 'uji selesai. Tidak ada pratinjau (hasilnya bukan gambar). ' + pesan
        elif not url_unggah:
            pesan += ' (ERP tidak mengirim izin unggah pratinjau)'
        else:
            try:
                unggah_pratinjau(url_unggah, contoh)
                param_baru = dict(param or {})
                # URL unggah dibuang dari baris: ia izin MENULIS, dan tidak
                # ada alasan menyimpannya sesudah dipakai.
                param_baru.pop('pratinjau_unggah', None)
                param_baru['pratinjau_nama'] = os.path.basename(contoh)
                pesan = 'uji: {} siap dilihat. {}'.format(os.path.basename(contoh), pesan)
            except Exception as e:  # noqa: BLE001
                pesan += ' (pratinjau gagal diunggah: {})'.format(e)

    print('[corel] {} {}: {}'.format(tanda, status.upper(), pesan[:160]))
    lapor(sambungan, baris['id'], status, pesan, jadi, parameter=param_baru)


# Nama bahan dari ERP dipakai sebagai nama berkas: hanya nama polos, tanpa
# folder - nilai seperti "..\x.png" akan menulis di luar folder bahan.
POLA_NAMA_BAHAN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}\.(png|jpe?g)$', re.I)


def unduh_bahan(baris, demo=False):
    """Unduh gambar bahan ke folder sumber SEBELUM outline.

    Dipakai pekerjaan dari Garapan ERP: gambar dibuat Codex di VPS dan dititip
    di R2, karena VPS tidak bisa menaruh berkas langsung ke PC ini. Baris
    antrean membawa `parameter.unduh` = [{nama, url}] dengan link sementara.
    Berkas lama bernama sama DITIMPA - gambar yang dibuat ulang harus
    menggantikan yang lama. Mengembalikan jumlah berkas yang diunduh.
    """
    param = baris.get('parameter') or {}
    if isinstance(param, str):
        try:
            param = json.loads(param)
        except ValueError:
            param = {}
    daftar = param.get('unduh') or []
    if not daftar:
        return 0
    masuk = str(baris.get('folder_path') or '').strip()
    if not masuk:
        raise ValueError('baris antrean tidak memuat folder bahan')
    for b in daftar:
        nama = str((b or {}).get('nama') or '')
        url = str((b or {}).get('url') or '')
        if not POLA_NAMA_BAHAN.match(nama) or '..' in nama:
            raise ValueError('nama bahan tidak sah: ' + nama[:80])
        if not url.startswith('https://'):
            raise ValueError('link bahan bukan https: ' + nama)
    if demo:
        print('[corel]   UNDUH {} berkas ke {}'.format(len(daftar), masuk))
        return len(daftar)
    try:
        os.makedirs(masuk, exist_ok=True)
    except OSError as e:
        raise ValueError('folder bahan tidak bisa dibuat: {} ({})'.format(masuk, e))
    import urllib.request
    import urllib.error
    for i, b in enumerate(daftar, 1):
        tujuan = os.path.join(masuk, b['nama'])
        sementara = tujuan + '.unduh'
        try:
            with urllib.request.urlopen(b['url'], timeout=60) as r, open(sementara, 'wb') as f:
                while True:
                    potong = r.read(1 << 16)
                    if not potong:
                        break
                    f.write(potong)
            os.replace(sementara, tujuan)
        except urllib.error.HTTPError as e:
            # 403 dari R2 hampir selalu berarti link sudah kedaluwarsa (7 hari).
            sebab = 'link kedaluwarsa - kirim ulang dari Garapan' if e.code == 403 else str(e)
            raise ValueError('gagal mengunduh {}: {}'.format(b['nama'], sebab))
        except (OSError, urllib.error.URLError) as e:
            raise ValueError('gagal mengunduh {}: {}'.format(b['nama'], e))
        finally:
            if os.path.exists(sementara):
                try:
                    os.remove(sementara)
                except OSError:
                    pass
    print('[corel]   {} bahan diunduh ke {}'.format(len(daftar), masuk))
    return len(daftar)


# ------------------------------------------------------------ bahan AI
#
# Gambar Codex dari Garapan ERP diunduh OTOMATIS ke folder bahan bersama di
# Google Drive:  <bahan_ai>\<JENIS>\<No. Produk>\<SKU>.png
# Desainer di PC mana pun memeriksa folder itu (Drive desktop), MENIMPA gambar
# yang salah / menambah PNG untuk yang gagal, lalu "Kirim ke Desain Host"
# hanya mengirim path folder ini ke Auto Outline.
#
# PC ini tidak memegang kunci R2: pekerja codex-gambar di VPS menulis link
# presigned 7 hari di kolom url_unduh (dan memperbaruinya sebelum habis).

BAHAN_AI_BAWAAN = r'G:\My Drive\BAHAN AI'
CATATAN_UNDUHAN = '_unduhan-ai.json'
BERKAS_KURANG = 'KURANG.txt'
EKSTENSI_GAMBAR = ('.png', '.jpg', '.jpeg')
_peringatan_bahan = {'kolom': False, 'akar': False}
# Folder yang gagal dibuat - dilaporkan sekali saja, bukan tiap 30 detik.
folder_macet = set()


def _sha_berkas(jalur):
    import hashlib
    h = hashlib.sha256()
    with open(jalur, 'rb') as f:
        for potong in iter(lambda: f.read(1 << 16), b''):
            h.update(potong)
    return h.hexdigest()


def _baca_catatan(folder):
    try:
        with open(os.path.join(folder, CATATAN_UNDUHAN), encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _tulis_catatan(folder, catatan):
    tujuan = os.path.join(folder, CATATAN_UNDUHAN)
    with open(tujuan + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(catatan, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tujuan + '.tmp', tujuan)


def folder_bahan(akar, object_key):
    """garapan/<JENIS>/<No. Produk>/<SKU>.png -> (folder, nama berkas, sku).

    Kunci dibuat ERP, tapi tetap diperiksa: segmen '..' atau berisi ':' / '\\'
    akan menulis di luar folder bahan.
    """
    bagian = str(object_key or '').split('/')
    if len(bagian) != 4 or bagian[0] != 'garapan':
        raise ValueError('kunci gambar tidak dikenal: ' + str(object_key)[:120])
    for b in bagian[1:]:
        if not b or b in ('.', '..') or any(c in b for c in ':\\'):
            raise ValueError('kunci gambar tidak aman: ' + str(object_key)[:120])
    nama = bagian[3]
    if not POLA_NAMA_BAHAN.match(nama):
        raise ValueError('nama berkas tidak sah: ' + nama[:80])
    return os.path.join(akar, bagian[1], bagian[2]), nama, os.path.splitext(nama)[0]


def keputusan_timpa(jalur, entri):
    """Boleh menulis gambar AI baru ke `jalur`?

    - belum ada berkas                    -> ya
    - berkas = hasil unduhan AI sebelumnya -> ya (gambar dibuat ulang di ERP)
    - berkas lain (diganti operator)      -> TIDAK, jangan pernah ditimpa
    """
    if not os.path.exists(jalur):
        return True
    if not entri or not entri.get('sha'):
        return False
    try:
        return _sha_berkas(jalur) == entri['sha']
    except OSError:
        return False


def tulis_kurang(sambungan, folder, lini, blok):
    """KURANG.txt = SKU produk ini yang belum punya gambar di folder."""
    kode, data = erp.panggil(
        sambungan, 'GET',
        'rnd_garapan?lini_produk=eq.{}&blok_no=eq.{}&select=sku&order=no_urut.asc'.format(
            lini, blok))
    if kode >= 300 or not isinstance(data, list):
        return
    ada = {os.path.splitext(n)[0].upper() for n in os.listdir(folder)
           if n.lower().endswith(EKSTENSI_GAMBAR)}
    kurang = [str(r.get('sku') or '').strip() for r in data
              if str(r.get('sku') or '').strip().upper() not in ada]
    jalur = os.path.join(folder, BERKAS_KURANG)
    if not kurang:
        if os.path.exists(jalur):
            os.remove(jalur)
        return
    with open(jalur, 'w', encoding='utf-8') as f:
        f.write('SKU yang belum punya gambar ({}). Tambahkan PNG bernama SKU di folder ini.\n\n'
                .format(len(kurang)))
        f.write('\n'.join(kurang) + '\n')


def akar_bahan_erp(sambungan, cadangan):
    """Folder bahan dari ERP: Admin Panel RnD -> Folder Desain Host.

    Baris yang SAMA dipakai Garapan saat "Kirim ke Desain Host", jadi folder unduhan dan
    folder yang dioutline tidak bisa berbeda. Belum diatur / gagal dibaca -> `cadangan`
    (lokal.json corel.bahan_ai, atau bawaan G:\\My Drive\\BAHAN AI).
    """
    kode, data = erp.panggil(
        sambungan, 'GET',
        'rnd_listing_pengaturan?bagian=eq.desain_host&kunci=eq.folder&select=nilai')
    if kode < 300 and isinstance(data, list) and data:
        nilai = (data[0] or {}).get('nilai') or {}
        akar = str(nilai.get('akar_bahan') or '').strip()
        if akar:
            return akar
    return cadangan


def sinkron_bahan_ai(sambungan, akar, demo=False):
    """Unduh gambar AI yang baru selesai ke folder bahan. Kembalikan jumlah berkas ditulis."""
    if not akar:
        return 0
    induk = os.path.dirname(akar.rstrip('\\/')) or akar
    if not os.path.isdir(induk):
        if not _peringatan_bahan['akar']:
            print('[bahan] folder {} tidak ada (Google Drive desktop belum jalan?) - '
                  'unduhan bahan AI dilewati.'.format(induk))
            _peringatan_bahan['akar'] = True
        return 0
    kode, data = erp.panggil(
        sambungan, 'GET',
        'rnd_garapan_gambar?status=in.(selesai,gagal)&diunduh_at=is.null'
        '&order=dibuat_at.asc&limit=100'
        '&select=id,garapan_id,sku,status,object_key,url_unduh,rnd_garapan(lini_produk,blok_no)')
    if kode >= 300:
        # Kolom belum ada (migrasi 032 belum dipasang di DB ini): diam setelah sekali lapor.
        if not _peringatan_bahan['kolom']:
            print('[bahan] antrean gambar AI belum siap di DB ini ({}).'.format(kode))
            _peringatan_bahan['kolom'] = True
        return 0
    if not data:
        return 0

    # Satu pekerjaan per SKU yang menentukan berkas: yang SELESAI terbaru (urutan dibuat_at
    # naik), atau yang gagal kalau tidak ada yang selesai. Sisanya ikut ditandai.
    terbaru = {}
    for j in data:
        lama = terbaru.get(j['garapan_id'])
        if j.get('status') == 'selesai' or not lama or lama.get('status') != 'selesai':
            terbaru[j['garapan_id']] = j
    ditulis = 0
    selesai_ids = []
    produk = {}
    for j in data:
        if terbaru[j['garapan_id']] is not j:
            selesai_ids.append(j['id'])
            continue
        g = j.get('rnd_garapan') or {}
        try:
            folder, nama, sku = folder_bahan(akar, j.get('object_key'))
        except ValueError as e:
            print('[bahan] #{} dilewati: {}'.format(j['id'], e))
            selesai_ids.append(j['id'])
            continue
        produk[folder] = (g.get('lini_produk'), g.get('blok_no'))
        if j.get('status') != 'selesai' or not j.get('url_unduh'):
            # Gagal: tidak ada yang diunduh, tapi folder & KURANG.txt tetap diperbarui.
            if j.get('status') == 'selesai':
                continue  # link belum ada - pekerja VPS akan membuatnya
            selesai_ids.append(j['id'])
            continue
        jalur = os.path.join(folder, nama)
        if demo:
            print('[bahan]   UNDUH {} -> {}'.format(sku, jalur))
            continue
        # Satu folder bermasalah TIDAK boleh menghentikan unduhan produk lain. Contoh nyata
        # 2026-10-11: folder dihapus operator → Google Drive desktop masih "memegang" namanya
        # (WinError 183) sampai penghapusan selesai / dipulihkan dari Sampah.
        try:
            os.makedirs(folder, exist_ok=True)
            folder_macet.discard(folder)
        except OSError as e:
            if folder not in folder_macet:
                folder_macet.add(folder)
                print('[bahan] folder {} tidak bisa dibuat ({}). Kalau baru dihapus, pulihkan dari '
                      'Sampah Google Drive atau tunggu Drive selesai sinkron - dicoba lagi '
                      'nanti.'.format(folder, e.strerror or e))
            continue
        catatan = _baca_catatan(folder)
        if not keputusan_timpa(jalur, catatan.get(sku)):
            print('[bahan] {} dilewati: sudah diganti operator (tidak ditimpa)'.format(sku))
            selesai_ids.append(j['id'])
            continue
        try:
            unduh_bahan({'folder_path': folder,
                         'parameter': {'unduh': [{'nama': nama, 'url': j['url_unduh']}]}})
        except ValueError as e:
            # 403 = link kedaluwarsa: biarkan, pekerja VPS memperbaruinya lalu dicoba lagi.
            print('[bahan] {} gagal: {}'.format(sku, e))
            continue
        try:
            catatan[sku] = {'job': j['id'], 'sha': _sha_berkas(jalur), 'waktu': waktu_iso()}
            _tulis_catatan(folder, catatan)
        except OSError as e:
            print('[bahan] catatan unduhan {} gagal ditulis: {}'.format(folder, e))
        ditulis += 1
        selesai_ids.append(j['id'])

    if demo:
        return 0
    for folder, (lini, blok) in produk.items():
        if lini and blok:
            try:
                os.makedirs(folder, exist_ok=True)
                tulis_kurang(sambungan, folder, lini, blok)
            except OSError as e:
                print('[bahan] KURANG.txt {} gagal: {}'.format(folder, e))
    for i in range(0, len(selesai_ids), 50):
        potongan = ','.join(selesai_ids[i:i + 50])
        erp.panggil(sambungan, 'PATCH', 'rnd_garapan_gambar?id=in.({})'.format(potongan),
                    {'diunduh_at': waktu_iso()}, prefer='return=minimal')
    if ditulis:
        print('[bahan] {} gambar AI diunduh ke {}'.format(ditulis, akar))
    return ditulis


def kerjakan_stiker(sambungan, cfg, pengaturan, baris, demo=False):
    """Pekerjaan outline stiker: gambar -> .cdr dengan garis potong."""
    tanda = '#{} {}'.format(baris['id'], baris['jenis'])
    berkas_hasil = siapkan_berkas_hasil('stiker-hasil.txt')

    param = baris.get('parameter') or {}
    if isinstance(param, str):
        try:
            param = json.loads(param)
        except ValueError:
            param = {}
    folder_uji = None
    if param.get('uji') and not demo:
        folder_uji = siapkan_folder_uji()
        param = dict(param, keluar=folder_uji)
        baris = dict(baris, parameter=param)

    try:
        # Bahan dari Garapan diunduh dulu - susun_perintah_stiker menolak
        # folder sumber yang belum ada.
        unduh_bahan(baris, demo=demo)
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
        uji=bool(folder_uji),
        pantau=(lambda u: pantau_kemajuan(sambungan, baris['id'], keluar, sebelum, total, u))
        if total else None)
    lama = time.time() - mulai
    teks = baca_hasil(berkas_hasil) or str(nilai or '').strip()
    laporkan_hasil(sambungan, baris, tanda, teks, galat, lama, 'berkas CDR',
                   cfg=cfg, folder_uji=folder_uji, param=param)


def cek_folder(folder, sku=None):
    """Hitung gambar di `folder` (tidak rekursif). Kembalikan (total, pesan).

    `sku` (dari kiriman Garapan) -> sebut juga SKU yang belum punya gambar dan berkas
    yang namanya bukan SKU produk itu (salah nama = tidak akan cocok saat upload).
    """
    if not os.path.isdir(folder):
        raise ValueError('folder tidak ada di PC ini: ' + folder)
    per_ext = {}
    nama = set()
    for n in os.listdir(folder):
        e = os.path.splitext(n)[1].lower()
        if e in EKSTENSI_GAMBAR and os.path.isfile(os.path.join(folder, n)):
            per_ext[e] = per_ext.get(e, 0) + 1
            nama.add(os.path.splitext(n)[0].upper())
    total = sum(per_ext.values())
    rinci = ', '.join('{} {}'.format(v, k.lstrip('.').upper()) for k, v in sorted(per_ext.items()))
    pesan = '{} gambar{}'.format(total, ' ({})'.format(rinci) if rinci else '')
    if sku:
        daftar = [str(x).strip() for x in sku if str(x).strip()]
        kurang = [x for x in daftar if x.upper() not in nama]
        asing = sorted(nama - {x.upper() for x in daftar})
        pesan += ' dari {} SKU'.format(len(daftar))
        if kurang:
            pesan += '. Kurang {}: {}'.format(
                len(kurang), ', '.join(kurang[:30]) + (' …' if len(kurang) > 30 else ''))
        else:
            pesan += '. Lengkap'
        if asing:
            pesan += '. Nama bukan SKU produk ini ({}): {}'.format(
                len(asing), ', '.join(asing[:10]) + (' …' if len(asing) > 10 else ''))
    return total, pesan


def kerjakan_cek(sambungan, baris, demo=False):
    """Pekerjaan 'cek': hitung gambar di folder sumber - cepat, tanpa CorelDRAW."""
    tanda = '#{} cek'.format(baris['id'])
    param = baris.get('parameter') or {}
    if isinstance(param, str):
        try:
            param = json.loads(param)
        except ValueError:
            param = {}
    folder = str(baris.get('folder_path') or '').strip()
    try:
        total, pesan = cek_folder(folder, param.get('sku'))
    except (ValueError, OSError) as e:
        print('[corel] {} GAGAL: {}'.format(tanda, e))
        if not demo:
            lapor(sambungan, baris['id'], 'gagal', str(e))
        return
    print('[corel] {} {}: {}'.format(tanda, folder, pesan))
    if not demo:
        # Daftar LENGKAP SKU yang kurang ikut dilaporkan (pesan hanya menyebut 30 pertama):
        # Alur Otomatis ERP memakainya untuk membuat gambar AI hanya bagi SKU yang kurang.
        kirim = None
        if param.get('sku') and os.path.isdir(folder):
            ada = {os.path.splitext(n)[0].upper() for n in os.listdir(folder)
                   if n.lower().endswith(EKSTENSI_GAMBAR)}
            kirim = dict(param, kurang=[str(x).strip() for x in param['sku']
                                        if str(x).strip() and str(x).strip().upper() not in ada])
        lapor(sambungan, baris['id'], 'berhasil', pesan, total, parameter=kirim)


POLA_FOLDER_PC = re.compile(r'^([A-Za-z]:\\|\\\\[^\\]+\\)')


def kerjakan_folder(sambungan, baris, demo=False):
    """Pekerjaan 'folder': siapkan folder BAHAN AI satu produk Garapan (keputusan operator
    2026-10-14). ERP mengantrekannya saat set dibuat, jadi folder kosong sudah ada sebelum
    gambar AI jadi - tempat menaruh PNG manual / ekspor desain. Path disusun ERP
    (folderProduk, Admin -> Folder Desain Host), sama dengan unduhan AI & Desain Host.
    KURANG.txt berisi SKU yang belum punya gambar. Aman diulang; isi folder tidak disentuh."""
    tanda = '#{} folder'.format(baris['id'])
    param = baris.get('parameter') or {}
    if isinstance(param, str):
        try:
            param = json.loads(param)
        except ValueError:
            param = {}
    folder = str(baris.get('folder_path') or '').strip()
    try:
        if not POLA_FOLDER_PC.match(folder) or '..' in folder:
            raise ValueError('path folder tidak sah: ' + folder[:120])
        induk = os.path.dirname(os.path.dirname(folder.rstrip('\\/')))
        if not os.path.isdir(induk):
            raise ValueError('folder induk {} tidak ada (Google Drive desktop belum jalan?)'.format(induk))
        if demo:
            print('[corel] {} BUAT {}'.format(tanda, folder))
            return
        baru = not os.path.isdir(folder)
        os.makedirs(folder, exist_ok=True)
        if param.get('lini') and param.get('blok'):
            tulis_kurang(sambungan, folder, param['lini'], param['blok'])
        gambar = [n for n in os.listdir(folder) if n.lower().endswith(EKSTENSI_GAMBAR)]
        pesan = '{} {} ({} gambar di dalamnya).'.format(
            'Folder dibuat:' if baru else 'Folder sudah ada:', folder, len(gambar))
    except (ValueError, OSError) as e:
        print('[corel] {} GAGAL: {}'.format(tanda, e))
        if not demo:
            lapor(sambungan, baris['id'], 'gagal', str(e))
        return
    print('[corel] {} {}'.format(tanda, pesan))
    lapor(sambungan, baris['id'], 'berhasil', pesan, len(gambar))


def kerjakan(sambungan, cfg, pengaturan, baris, demo=False):
    if baris.get('pekerjaan') == 'cek':
        return kerjakan_cek(sambungan, baris, demo=demo)
    if baris.get('pekerjaan') == 'folder':
        return kerjakan_folder(sambungan, baris, demo=demo)
    if baris.get('pekerjaan') == 'ekspor':
        import ekspor_antrean
        return ekspor_antrean.kerjakan(sambungan, inti, cfg, baris, lapor, demo=demo)
    if (baris.get('pekerjaan') or 'corel') == 'stiker':
        return kerjakan_stiker(sambungan, cfg, pengaturan, baris, demo=demo)

    tanda = '#{} {} {}-{}'.format(baris['id'], baris['jenis'],
                                  baris['dari'], baris['sampai'])

    param = baris.get('parameter') or {}
    if isinstance(param, str):
        try:
            param = json.loads(param)
        except ValueError:
            param = {}

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

    if not pengaturan['keluaran'] or not (pengaturan['template'] or param.get('unduh')):
        pesan = ('data/lokal.json belum memuat {"corel": {"keluaran": ..., '
                 '"template": ...}}')
        print('[corel] {} GAGAL: {}'.format(tanda, pesan))
        if not demo:
            lapor(sambungan, baris['id'], 'gagal', pesan)
        return

    folder_uji = None
    if param.get('uji') and not demo:
        folder_uji = siapkan_folder_uji()
        param = dict(param, keluar=folder_uji)
        baris = dict(baris, parameter=param)

    berkas_hasil = siapkan_berkas_hasil('corel-hasil.txt')

    # Background yang dipilih di ERP diunduh dulu ke cache lokal, lalu
    # folder itu yang dipakai sebagai template - menggantikan folder
    # template bawaan PC untuk pekerjaan ini saja.
    template = ''
    if param.get('unduh'):
        nama_set = str(param.get('set') or 'template')
        template = os.path.join(inti.AKAR, 'template-cache', nama_set)
        try:
            baru, lewat = unduh_template(param['unduh'], template)
            print('[corel]   background "{}": {} diunduh, {} dipakai ulang'.format(
                nama_set, baru, lewat))
        except Exception as e:  # noqa: BLE001
            pesan = 'gagal mengunduh background "{}": {}'.format(nama_set, e)
            print('[corel] {} GAGAL: {}'.format(tanda, pesan))
            if not demo:
                lapor(sambungan, baris['id'], 'gagal', pesan)
            return

    try:
        perintah, mode, dibuang = susun_perintah(
            baris, folder, pengaturan, berkas_hasil, template)
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
    folder_tpl = perintah.split('template=', 1)[1].split(';', 1)[0]
    bahan = hitung_bahan(folder, ('.cdr', '.png'), False)
    jumlah = int(angka_setelan(perintah, 'jumlah', 0))
    if mode == 'varian':
        # 1 desain x 1 template toko = 1 foto.
        total = bahan * hitung_template(folder_tpl, 'varian')
    elif angka_setelan(perintah, 'bundle', 0) >= 1:
        # Bundle: satu foto per potongan penuh, per template -bundle. Potongan
        # yang tidak penuh dibuang (42 desain + jumlah 5 = 8 foto, bukan 9).
        total = hitung_template(folder_tpl, 'bundle') * (bahan // jumlah if jumlah else 1)
    elif angka_setelan(perintah, 'pecah', 0) >= 1 and jumlah:
        # Memecah: satu template menghasilkan beberapa foto, dibulatkan ke atas
        # (42 desain + jumlah 21 = 2 foto).
        total = (hitung_template(folder_tpl, 'sampul',
                                 int(angka_setelan(perintah, 'sampulke', 0)))
                 * -(-bahan // jumlah))
    else:
        # Foto sampul biasa: satu berkas per template -utama.
        total = hitung_template(folder_tpl, 'sampul')
    sebelum = isi_folder(keluaran)

    mulai = time.time()
    nilai, galat = jalankan_macro(
        perintah, pengaturan['batas_menit'] * 60, pengaturan['progid'],
        uji=bool(folder_uji),
        pantau=(lambda u: pantau_kemajuan(sambungan, baris['id'], keluaran, sebelum, total, u))
        if total else None)
    lama = time.time() - mulai

    # Berkas hasil yang ditulis macro adalah sumber utamanya, bukan pelengkap:
    # GMSManager.RunMacro di CorelDRAW 2026 (suite 27) SELALU mengembalikan
    # Nothing, berapa pun yang dikembalikan fungsi VBA-nya. Sudah diukur di
    # PC host. Kembalian COM tetap dipakai kalau ada, untuk versi yang
    # kelakuannya berbeda.
    teks = baca_hasil(berkas_hasil) or str(nilai or '').strip()
    laporkan_hasil(sambungan, baris, tanda, teks, galat, lama, 'foto',
                   cfg=cfg, folder_uji=folder_uji, param=param)

    # Foto yang jadi langsung diunggah ke R2 (Konsol membaca bucket itu, bukan Drive) -
    # tools/foto_r2.py. Macro menulis ke <keluaran>\<nama folder desain>\tokoN\.
    if not folder_uji and not galat and urai_hasil(teks).get('ok') == '1':
        import foto_r2
        foto_r2.setelah_foto(sambungan, inti, cfg, baris,
                             os.path.join(keluaran, os.path.basename(os.path.normpath(folder))))


def sekali_putaran(sambungan, cfg, pengaturan, batas=None, demo=False):
    # Bahan AI dulu: unduhan kecil & cepat, dan desainer menunggu gambarnya muncul di Drive.
    try:
        # lokal.json corel.bahan_ai = "" mematikan unduhan di PC ini; selain itu folder dari ERP.
        cadangan = pengaturan.get('bahan_ai')
        akar = akar_bahan_erp(sambungan, cadangan) if cadangan else ''
        sinkron_bahan_ai(sambungan, akar, demo=demo)
    except Exception as e:  # noqa: BLE001 - jangan sampai menghentikan antrean Corel
        print('[bahan] sinkron gagal: {}'.format(e))
    # Nomor folder produk tertinggi di Drive -> ERP (tiap 10 menit), supaya nomor produk baru
    # di Garapan tidak menabrak produk yang sudah dikerjakan tapi belum masuk katalog.
    if not demo:
        try:
            import nomor_drive
            nomor_drive.lapor(sambungan, inti, cfg)
        except Exception as e:  # noqa: BLE001
            print('[nomor] gagal memindai Drive: {}'.format(e))
    dikerjakan = 0
    while batas is None or dikerjakan < batas:
        kode, data = erp.panggil(
            sambungan, 'GET',
            '{}?pekerjaan=in.({})&status=eq.menunggu&order=dibuat_at.asc'
            '&limit=5&select=id,pekerjaan,jenis,dari,sampai,folder_path,parameter'
            .format(TABEL, ','.join(sorted(list(MACRO) + ['cek', 'ekspor', 'folder']))),
        )
        if kode >= 300:
            print('[corel] gagal membaca antrean: {} {}'.format(kode, data))
            return dikerjakan
        if not data:
            return dikerjakan

        maju = False
        # Cek gambar & buat folder didahulukan: cuma sedetik, operator menunggu jawabannya.
        data = sorted(data, key=lambda b: 0 if b.get('pekerjaan') in ('cek', 'folder') else 1)
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


def jelaskan_galat(e, url):
    """Pesan galat jaringan yang menyebut kemungkinan sebabnya.

    "WinError 10061 target machine actively refused it" benar secara
    teknis dan tidak berguna bagi operator yang baru menyalakan PC. Yang
    sebenarnya terjadi hampir selalu satu hal: DB dev dicapai lewat
    terowongan SSH, dan terowongan itu tidak ikut menyala saat login.
    """
    teks = str(e)
    if '10061' in teks or 'refused' in teks.lower():
        if '127.0.0.1' in url or 'localhost' in url:
            return ('{} tidak menjawab. Terowongan SSH ke DB dev belum terbuka? '
                    'Pengambil akan mencoba lagi sendiri begitu terbuka.'.format(url))
        return '{} tidak menjawab. Jaringan atau server sedang tidak bisa dihubungi.'.format(url)
    return teks


def kunci_satu_pengambil():
    """Pegang kunci eksklusif, atau None kalau sudah ada yang memegangnya.

    Satu PC = satu CorelDRAW. Dua pengambil yang jalan bersamaan (mis.
    tugas Windows yang sudah menyala lalu .bat-nya diklik lagi) bisa
    menjalankan dua macro sekaligus di dokumen yang sama - sudah terjadi,
    dua pekerjaan foto berjalan bersisian dan saling mengganggu dokumen
    aktif. Antrean tidak bisa menjaga ini: klaimnya memang atomik, tapi
    dua pekerjaan BERBEDA boleh diambil bersamaan.
    """
    import msvcrt

    berkas = os.path.join(inti.AKAR, 'output', 'corel-pengambil.lock')
    os.makedirs(os.path.dirname(berkas), exist_ok=True)
    try:
        f = open(berkas, 'a+')
        # seek(0) WAJIB sebelum mengunci: msvcrt.locking mengunci byte pada
        # POSISI berkas saat itu, dan mode 'a+' menaruh posisi di akhir.
        # Tanpa ini tiap proses mengunci byte yang berbeda - kuncinya ada,
        # tapi tidak menahan siapa pun. Sudah terjadi: dua pengambil hidup
        # bersamaan padahal keduanya mengira berhasil memegang kunci.
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    # Isi berkas ditulis SESUDAH byte 0 terkunci, dan posisinya dikembalikan
    # supaya kuncinya tetap di byte yang sama selama proses hidup.
    f.seek(1)
    f.truncate()
    f.write(' {} {}\n'.format(NAMA_PC, time.strftime('%Y-%m-%d %H:%M:%S')))
    f.flush()
    return f


def lapor_makro(progid=''):
    """Cetak isi GlobalMacros + versi modul yang terbaca. 0 = ada isinya."""
    try:
        import win32com.client as win32com
    except ImportError:
        print('[makro] pywin32 belum terpasang di PC ini.')
        return 2

    kandidat = [progid] if progid else progid_terpasang() + [PROGID_UMUM]
    print('[makro] progid yang dicoba: {}'.format(', '.join(kandidat)))
    ketemu = False
    for nama in kandidat:
        try:
            app = win32com.Dispatch(nama)
        except Exception as e:  # noqa: BLE001
            print('  {}: tidak bisa dijalankan ({})'.format(nama, e))
            continue
        makro = daftar_makro(app)
        print('  {}: {} makro terdaftar di proyek {}'
              .format(nama, len(makro), PROYEK_GMS))
        if not makro:
            print('    (kosong - GlobalMacros belum disimpan, modulnya masuk ke')
            print('     proyek lain, atau ini instance CorelDRAW yang berbeda)')
        for pekerjaan in sorted(PENANDA_MACRO):
            penanda = PENANDA_MACRO[pekerjaan]
            modul = penanda.split('.')[0]
            punya = [m for m in makro if m.startswith(modul + '.')]
            versi = versi_modul(makro, pekerjaan)
            if penanda in makro:
                print('    {:<20} SIAP  versi {}'
                      .format(modul, '.'.join(str(x) for x in versi) if versi else 'lama'))
                ketemu = True
            elif punya:
                print('    {:<20} ADA tapi tanpa {} - impor ulang .bas-nya'
                      .format(modul, penanda.split('.')[-1]))
            else:
                print('    {:<20} TIDAK ADA di daftar'.format(modul))
        for m in sorted(makro):
            print('      - {}'.format(m))
    return 0 if ketemu else 1


def main():
    p = argparse.ArgumentParser(description='Pengerjaan antrean foto produk CorelDRAW')
    p.add_argument('--sekali', action='store_true', help='satu pekerjaan saja')
    p.add_argument('--pantau', action='store_true', help='tetap hidup, periksa berkala')
    p.add_argument('--jeda', type=int, default=30, help='jeda --pantau, detik')
    p.add_argument('--demo', action='store_true',
                   help='tampilkan perintahnya saja, jangan panggil CorelDRAW')
    p.add_argument('--makro', action='store_true',
                   help='sebut isi GlobalMacros di CorelDRAW, lalu berhenti '
                        '(untuk memeriksa modul mana yang benar-benar dimuat)')
    p.add_argument('--url', default='',
                   help='alamat PostgREST lain, mis. http://127.0.0.1:3102 '
                        'untuk DB dev (rahasianya tetap dari lokal.json)')
    a = p.parse_args()

    if a.makro:
        return lapor_makro(baca_corel().get('progid'))

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

    # Dipegang sampai proses berakhir; Windows melepasnya sendiri kalau
    # prosesnya mati mendadak, jadi tidak ada kunci yatim.
    kunci = kunci_satu_pengambil()
    if kunci is None:
        print('[corel] pengambil lain sudah jalan di PC ini - berhenti.')
        print('[corel] (tutup jendela Desain Host yang lama kalau mau memakai yang ini)')
        return 3

    cfg = inti.baca_config()
    pengaturan = baca_corel()

    if pengaturan.get('bukan_host'):
        print('[corel] PC ini ditandai BUKAN host di data/lokal.json '
              '(corel.bukan_host). Berhenti - pekerjaan dibiarkan untuk PC host.')
        print('[corel] Kalau PC ini memang mau dipakai: hapus barisnya, '
              'lalu jalankan lagi.')
        return 4

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
                print('[corel] putaran gagal: {}'.format(jelaskan_galat(e, sambungan['url'])))
            time.sleep(a.jeda)

    n = sekali_putaran(sambungan, cfg, pengaturan,
                       batas=1 if a.sekali else None, demo=a.demo)
    print('[corel] {} pekerjaan dikerjakan.'.format(n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
