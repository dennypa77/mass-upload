# -*- coding: utf-8 -*-
"""Proses satu folder produk: deteksi foto -> salin & rename -> push ke GitHub -> URL -> database.

Dipakai lewat:
    python tools/shopee_mass_upload.py unggah "G:\\My Drive\\JIBBITZ\\PRODUK 00001 - 00050"
atau lewat tombol "Proses Folder Ini" di UI.

Push dipecah jadi beberapa bagian kecil. GitHub lewat HTTPS sering menolak
kiriman besar dengan galat HTTP 408, jadi tiap bagian dibatasi ukurannya dan
dikirim satu per satu — kalau satu bagian gagal, bagian yang sudah terkirim
tetap tercatat dan proses bisa dilanjutkan tanpa mengulang dari awal.
"""
import os, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

import gudang
import r2 as modul_r2

BATAS_KIRIM = 40 * 1024 * 1024      # ukuran maks. satu commit sebelum di-push
COBA_ULANG = 3
SALINAN_SERENTAK = 8                # berapa foto disalin bersamaan dari Drive


def _lingkungan_git():
    """Git tidak boleh berhenti menunggu ketikan.

    Kalau kredensial belum diatur, git akan meminta username/password. Di sini
    keluarannya ditangkap, jadi permintaan itu tidak pernah terlihat dan
    prosesnya menggantung selamanya. Dengan GIT_TERMINAL_PROMPT=0 git langsung
    gagal dengan pesan yang bisa dibaca.
    """
    return dict(os.environ, GIT_TERMINAL_PROMPT='0')


def _git(akar, argumen, cetak=None, masukan=None, jeda_kabar=25):
    """Jalankan git sambil menampilkan keluarannya begitu muncul.

    Git menulis progress dengan carriage return, bukan baris baru, jadi
    pemisahnya harus mencakup \r — kalau tidak, unggahan besar terlihat diam
    bermenit-menit padahal sedang berjalan.
    """
    proses = subprocess.Popen(
        ['git'] + list(argumen), cwd=akar, env=_lingkungan_git(),
        stdin=subprocess.PIPE if masukan is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1)
    if masukan is not None:
        proses.stdin.write(masukan)
        proses.stdin.close()

    baris, sisa = [], ''
    mulai = [time.time()]

    def kabar():
        # kalau lama tidak ada keluaran, beri tanda masih hidup
        while proses.poll() is None:
            time.sleep(1)
            diam = time.time() - mulai[0]
            if cetak and diam >= jeda_kabar:
                cetak('… masih berjalan ({:.0f} detik tanpa kabar)'.format(diam))
                mulai[0] = time.time()

    penjaga = threading.Thread(target=kabar, daemon=True)
    if cetak:
        penjaga.start()

    while True:
        potong = proses.stdout.read(1)
        if not potong:
            break
        if potong in '\r\n':
            teks = sisa.strip()
            sisa = ''
            if not teks:
                continue
            mulai[0] = time.time()
            baris.append(teks)
            if cetak:
                cetak(teks)
        else:
            sisa += potong
    if sisa.strip():
        baris.append(sisa.strip())
        if cetak:
            cetak(sisa.strip())
    proses.wait()
    return proses.returncode, '\n'.join(baris)


def periksa_akses(inti):
    """Pastikan git bisa menghubungi GitHub sebelum menyalin apa pun."""
    kode, keluaran = _git(inti.AKAR, ['ls-remote', '--exit-code', 'origin', 'HEAD'])
    if kode == 0:
        return True, ''
    pesan = keluaran.strip()
    if 'could not read Username' in pesan or 'Authentication failed' in pesan \
            or 'terminal prompts disabled' in pesan:
        pesan = ('Git belum punya izin ke GitHub di komputer ini.\n'
                 '     Jalankan sekali: gh auth login\n'
                 '     atau pasang Git Credential Manager lalu push manual sekali '
                 'supaya kredensialnya tersimpan.')
    return False, pesan


def kenali_toko(cfg, dirpath):
    """Tentukan folder itu milik toko mana, dari salah satu komponen path-nya.

    Yang dikenali:
        TOKO 1 / toko_2 / Toko3 / FOTO_1      -> nomor toko langsung
        Graphica Key / kaitin.aja             -> dicocokkan ke nama toko di config
    """
    bagian = os.path.normpath(dirpath).split(os.sep)
    for potong in bagian[::-1]:
        potong = potong.strip()
        m = re.match(r'^(?:toko|foto|shop|store)[\s_.-]*(\d+)$', potong, re.I)
        if m:
            return 'toko' + m.group(1)
    # cocokkan dengan nama toko di config, mis. folder bernama "Graphica Key"
    for potong in bagian[::-1]:
        rapi = re.sub(r'[^a-z0-9]', '', potong.lower())
        for tk in cfg['toko']:
            if rapi and rapi == re.sub(r'[^a-z0-9]', '', tk['nama'].lower()):
                return tk['folder_foto']
    return None


def deteksi(inti, cfg, folder):
    """Telusuri folder, kenali tiap foto: toko, jenis, seri, dan nama tujuannya.

    Jenis diambil dari prefix nama file (JB/PA/PB). Seri ditentukan per folder
    sumber: dilihat dari nomor SKU foto varian di folder yang sama, lalu dicocokkan
    ke data/sku.csv. Foto utama (foto1/2/3.png) ikut seri folder tempatnya berada.
    """
    if not os.path.isdir(folder):
        sys.exit('Folder tidak ada: {}'.format(folder))

    jenis_dari_prefix = {j['prefix_sku'].upper(): nama for nama, j in cfg['jenis'].items()}
    peta_utama = {n.lower(): i + 1 for i, n in enumerate(cfg['foto']['nama_foto_utama'])}
    nama_toko = {t['folder_foto']: t['nama'] for t in cfg['toko']}

    # SKU -> (jenis, seri) dari data/sku.csv, supaya seri tidak ditebak-tebak
    seri_dari_sku = {}
    try:
        for jenis, seri_map in inti.baca_sku().items():
            for seri, desain in seri_map.items():
                for d in desain:
                    seri_dari_sku[d['sku'].upper()] = (jenis, d['kode_seri'])
    except SystemExit:
        pass

    temuan, tanpa_seri, tak_dikenal, survei = [], [], [], []
    for dirpath, _, berkas in os.walk(folder):
        gambar = [f for f in sorted(berkas) if f.lower().endswith(inti.EKSTENSI)]
        if not gambar:
            continue

        toko = kenali_toko(cfg, dirpath)
        survei.append({'folder': dirpath, 'gambar': len(gambar), 'toko': toko,
                       'contoh': gambar[:3]})
        if not toko:
            tak_dikenal.append('{} (folder toko tidak dikenali)'.format(dirpath))
            continue

        # seri folder ini: dari SKU varian yang ada di dalamnya
        jenis_folder = seri_folder = None
        for f in gambar:
            kunci = os.path.splitext(f)[0].upper()
            if kunci in seri_dari_sku:
                jenis_folder, seri_folder = seri_dari_sku[kunci]
                break

        for f in gambar:
            asal = os.path.join(dirpath, f)
            kunci = os.path.splitext(f)[0].upper()
            if kunci in seri_dari_sku:
                jenis, seri, tipe = seri_dari_sku[kunci][0], seri_dari_sku[kunci][1], 'varian'
                nama = kunci + os.path.splitext(f)[1].lower()
            elif f.lower() in peta_utama and jenis_folder:
                jenis, seri, tipe = jenis_folder, seri_folder, 'utama'
                nama = '{}-{}-utama{}.png'.format(
                    cfg['jenis'][jenis]['prefix_sku'], seri, peta_utama[f.lower()])
                kunci = os.path.splitext(nama)[0].upper()
            elif re.match(r'^[A-Z]{2}-', kunci) and kunci.split('-')[0] in jenis_dari_prefix:
                # SKU belum terdaftar di sku.csv — tetap diproses, serinya dikosongkan
                jenis, seri, tipe = jenis_dari_prefix[kunci.split('-')[0]], seri_folder, 'varian'
                nama = kunci + os.path.splitext(f)[1].lower()
                tanpa_seri.append(kunci)
            else:
                tak_dikenal.append(asal)
                continue
            slug = cfg['jenis'][jenis]['slug']
            temuan.append({
                'toko': toko, 'nama_toko': nama_toko.get(toko, toko), 'jenis': jenis,
                'seri': seri, 'tipe': tipe, 'kunci': kunci, 'sumber': asal,
                'nama_tujuan': nama, 'slug': slug,
                'path_repo': 'foto-upload/{}/{}/{}'.format(toko, slug, nama),
            })
    return temuan, tanpa_seri, tak_dikenal, survei


def _rekap(temuan):
    r = {}
    for t in temuan:
        k = (t['nama_toko'], t['jenis'], t['seri'] or '(seri belum diketahui)')
        r[k] = r.get(k, 0) + 1
    return r


def _bagi(temuan, batas=BATAS_KIRIM):
    """Pecah daftar foto jadi beberapa bagian, masing-masing di bawah batas ukuran."""
    bagian, sekarang, ukuran = [], [], 0
    for t in temuan:
        if sekarang and ukuran + t['ukuran'] > batas:
            bagian.append(sekarang)
            sekarang, ukuran = [], 0
        sekarang.append(t)
        ukuran += t['ukuran']
    if sekarang:
        bagian.append(sekarang)
    return bagian


def _mb(byte):
    return byte / 1024 ** 2


def migrasi_r2(inti, cfg, cetak=print):
    """Unggah semua foto di foto-upload/ ke R2, lalu tulis daftar URL bersama."""
    from concurrent.futures import ThreadPoolExecutor
    klien = modul_r2.dari_config(cfg)
    if not klien:
        cetak('[migrasi] mode penyimpanan bukan r2')
        return

    lokal = []
    for dp, _, berkas in os.walk(inti.DIR_FOTO):
        for f in berkas:
            if f.lower().endswith(inti.EKSTENSI):
                penuh = os.path.join(dp, f)
                lokal.append((os.path.relpath(penuh, inti.AKAR).replace(os.sep, '/'), penuh))
    cetak('[migrasi] {} foto ada di komputer ini'.format(len(lokal)))

    sudah = set(klien.daftar('foto-upload/'))
    cetak('[migrasi] {} sudah ada di bucket'.format(len(sudah)))
    sisa = [x for x in lokal if x[0] not in sudah]
    cetak('[migrasi] {} perlu diunggah'.format(len(sisa)))

    hitung = {'n': 0, 'gagal': 0}
    kunci = threading.Lock()

    def satu(pasang):
        jalur, penuh = pasang
        try:
            klien.unggah(jalur, penuh)
        except Exception as e:
            with kunci:
                hitung['gagal'] += 1
                if hitung['gagal'] <= 3:
                    cetak('   ! gagal {}: {}'.format(jalur, e))
            return
        with kunci:
            hitung['n'] += 1
            n = hitung['n']
        if n % 50 == 0 or n == len(sisa):
            cetak('      {}/{} terunggah'.format(n, len(sisa)))

    if sisa:
        with ThreadPoolExecutor(max_workers=int(cfg.get('salinan_serentak') or 8)) as kolam:
            list(kolam.map(satu, sisa))

    semua = klien.daftar('foto-upload/')
    jumlah = inti.tulis_manifest_r2(cfg, {k: klien.alamat(k) for k in semua})
    cetak('[migrasi] selesai — {} gagal, {} foto tercatat di data/foto_r2.csv'.format(
        hitung['gagal'], jumlah))
    cetak('[migrasi] commit berkas itu supaya komputer lain ikut memakainya')


def kirim_r2(inti, cfg, db, temuan, maju):
    """Unggah foto ke Cloudflare R2, beberapa berkas sekaligus."""
    from concurrent.futures import ThreadPoolExecutor
    klien = modul_r2.dari_config(cfg)
    total_mb = _mb(sum(t['ukuran'] for t in temuan))
    print('[3/3] mengunggah {} foto ({:.1f} MB) ke Cloudflare R2 …'.format(
        len(temuan), total_mb))

    hitung = {'n': 0, 'gagal': 0}
    kunci_hitung = threading.Lock()
    berhasil = []

    def satu(t):
        try:
            klien.unggah(t['path_repo'], t['file_lokal'])
            t['url'] = klien.alamat(t['path_repo'])
            with kunci_hitung:
                berhasil.append(t)
        except Exception as e:
            with kunci_hitung:
                hitung['gagal'] += 1
                if hitung['gagal'] <= 3:
                    print('   ! gagal {}: {}'.format(t['nama_tujuan'], e))
        with kunci_hitung:
            hitung['n'] += 1
            n = hitung['n']
        maju('unggah', n, len(temuan))
        if n % 25 == 0 or n == len(temuan):
            print('      {}/{} foto'.format(n, len(temuan)))

    with ThreadPoolExecutor(max_workers=int(cfg.get('salinan_serentak') or 8)) as kolam:
        list(kolam.map(satu, temuan))

    if berhasil:
        gudang.simpan(db, berhasil)
        gudang.tandai_terunggah(db, [t['path_repo'] for t in berhasil])
    print('[3/3] {} terkirim, {} gagal'.format(len(berhasil), hitung['gagal']))


def _sudah_tersalin(sumber, tujuan):
    """Benar kalau salinan di foto-upload sudah ada dan tidak lebih tua dari sumbernya."""
    try:
        if not os.path.exists(tujuan) or os.path.getsize(tujuan) == 0:
            return False
        return os.path.getmtime(tujuan) >= os.path.getmtime(sumber) - 2
    except OSError:
        return False


def proses(inti, cfg, folder, push=True, lapor=None, paksa=False):
    """Proses satu folder produk.

    lapor(tahap, selesai, total) dipanggil untuk memperbarui progress bar.
    paksa=True menyalin ulang walau berkasnya sudah ada di foto-upload.
    """
    def maju(tahap, n, total):
        if lapor:
            lapor(tahap, n, total)

    temuan, tanpa_seri, tak_dikenal, survei = deteksi(inti, cfg, folder)
    if not temuan:
        jelaskan_gagal(cfg, folder, survei, tak_dikenal)
        return

    if push and inti.mode_penyimpanan(cfg) != 'r2':
        boleh, kenapa = periksa_akses(inti)
        if not boleh:
            print('[unggah] tidak bisa mengunggah:')
            print('     ' + kenapa.replace('\n', '\n     '))
            print('[unggah] dibatalkan sebelum menyalin. Perbaiki dulu izinnya, '
                  'atau lepas centang upload untuk menyalin saja.')
            return

    rekap = _rekap(temuan)
    print('[unggah] folder : {}'.format(folder))
    print('[unggah] {} foto akan diproses:'.format(len(temuan)))
    for (tk, jn, sr), n in sorted(rekap.items()):
        print('   {:<15} {:<12} {:<22} {} foto'.format(tk, jn, sr, n))
    if tanpa_seri:
        print('   ! {} SKU belum terdaftar di data/sku.csv, serinya tidak diketahui.'
              .format(len(tanpa_seri)))
        print('     Contoh: {}'.format(', '.join(sorted(set(tanpa_seri))[:6])))
        print('     Jalankan "Impor SKU" dulu supaya foto ini masuk ke listing yang benar.')

    # ---------------------------------------------------------------- 1. salin
    # Foto di Google Drive bersifat streaming: isinya baru diunduh saat berkasnya
    # dibuka. Menyalin beberapa sekaligus jauh lebih cepat karena waktu tunggu
    # jaringan bisa saling menutupi. Berkas yang sudah pernah disalin dilewati,
    # jadi menjalankan ulang tidak mengunduh apa pun lagi.
    serentak = int(cfg.get('salinan_serentak') or SALINAN_SERENTAK)
    print('[1/3] menyalin & rename ke foto-upload/ ({} berkas sekaligus) …'.format(serentak))
    hitung = {'selesai': 0, 'kecil': 0, 'lewat': 0}
    kunci = threading.Lock()

    def kerjakan(t):
        tujuan = os.path.join(inti.DIR_FOTO, t['toko'], t['slug'])
        os.makedirs(tujuan, exist_ok=True)
        akhir = os.path.join(tujuan, t['nama_tujuan'])
        t['file_lokal'] = akhir
        if not paksa and _sudah_tersalin(t['sumber'], akhir):
            t['ukuran'] = os.path.getsize(akhir)
            with kunci:
                hitung['lewat'] += 1
        else:
            kecil = inti.salin_muat(t['sumber'], akhir)
            t['ukuran'] = os.path.getsize(akhir)
            with kunci:
                if kecil:
                    hitung['kecil'] += 1
        with kunci:
            hitung['selesai'] += 1
            n = hitung['selesai']
        maju('salin', n, len(temuan))
        if n % 25 == 0 or n == len(temuan):
            print('      {}/{} foto'.format(n, len(temuan)))

    with ThreadPoolExecutor(max_workers=serentak) as kolam:
        list(kolam.map(kerjakan, temuan))

    total_mb = _mb(sum(t['ukuran'] for t in temuan))
    print('[1/3] selesai — {} foto, {:.1f} MB, {} dikecilkan, {} dilewati '
          '(sudah tersalin)'.format(len(temuan), total_mb, hitung['kecil'], hitung['lewat']))

    # ---------------------------------------------------------------- 2. database
    base = (cfg['foto'].get('base_url') or '').rstrip('/')
    db = gudang.buka(inti.DB_PATH)
    for t in temuan:
        t['url'] = base + '/' + t['path_repo'] if base else None
        t['diunggah'] = 0
    gudang.simpan(db, temuan)
    print('[2/3] {} baris URL disimpan ke database'.format(len(temuan)))
    if not base:
        print('   ! foto.base_url belum diisi, URL belum bisa dibentuk')

    if not push:
        print('[3/3] dilewati — upload tidak dicentang')
        db.close()
        return

    # ---------------------------------------------------------------- 3. kirim
    if inti.mode_penyimpanan(cfg) == 'r2':
        kirim_r2(inti, cfg, db, temuan, maju)
        n, u = gudang.jumlah(db)
        db.close()
        print('[3/3] selesai. Database: {} foto, {} sudah terunggah'.format(n, u))
        if temuan[0].get('url'):
            print('[unggah] contoh URL untuk dicek di browser:')
            print('       ' + temuan[0]['url'])
        for t in tak_dikenal[:5]:
            print('   ! dilewati: {}'.format(t))
        return

    bagian = _bagi(temuan)
    print('[3/3] mengunggah ke GitHub: {:.1f} MB dipecah jadi {} bagian '
          '(maks. {:.0f} MB per bagian)'.format(total_mb, len(bagian), _mb(BATAS_KIRIM)))

    berhasil = 0
    for nomor, kelompok in enumerate(bagian, 1):
        mb = _mb(sum(t['ukuran'] for t in kelompok))
        label = 'bagian {}/{} — {} foto, {:.1f} MB'.format(nomor, len(bagian), len(kelompok), mb)
        print('   > {}'.format(label))
        maju('unggah', nomor - 1, len(bagian))

        daftar = '\n'.join(t['path_repo'] for t in kelompok) + '\n'
        kode, keluaran = _git(inti.AKAR, ['add', '-f', '--pathspec-from-file=-'], masukan=daftar)
        if kode:
            print('     git add gagal:\n     ' + keluaran.replace('\n', '\n     '))
            break

        kode, keluaran = _git(inti.AKAR, [
            '-c', 'user.email=tools@local', '-c', 'user.name=mass-upload',
            'commit', '-m', 'Foto {} ({})'.format(
                ', '.join(sorted({k[2] for k in _rekap(kelompok)})), label)])
        if kode and 'nothing to commit' not in keluaran:
            print('     git commit gagal:\n     ' + keluaran.replace('\n', '\n     '))
            break

        terkirim = False
        for percobaan in range(1, COBA_ULANG + 1):
            if percobaan > 1:
                print('     percobaan ulang {}/{}…'.format(percobaan, COBA_ULANG))
            kode, keluaran = _git(inti.AKAR, [
                # kiriman besar lewat HTTPS mudah kena batas waktu; longgarkan buffer
                '-c', 'http.postBuffer=157286400',
                '-c', 'http.version=HTTP/1.1',
                '-c', 'http.lowSpeedLimit=1000',
                '-c', 'http.lowSpeedTime=300',
                'push', '--progress', 'origin', 'HEAD'],
                cetak=lambda b: print('       ' + b))
            if kode == 0:
                terkirim = True
                break
        if not terkirim:
            print('     bagian ini gagal terkirim setelah {} percobaan.'.format(COBA_ULANG))
            print('     Bagian yang sudah berhasil tetap tersimpan — jalankan lagi '
                  'untuk melanjutkan sisanya.')
            break

        gudang.tandai_terunggah(db, [t['path_repo'] for t in kelompok])
        berhasil += 1
        maju('unggah', nomor, len(bagian))
        print('     terkirim ({}/{} bagian selesai)'.format(berhasil, len(bagian)))

    n, u = gudang.jumlah(db)
    db.close()
    print('[3/3] {} dari {} bagian terkirim. Database: {} foto, {} sudah di GitHub'.format(
        berhasil, len(bagian), n, u))
    if berhasil and base:
        print('[unggah] contoh URL untuk dicek di browser:')
        print('       ' + temuan[0]['url'])
        print('   Catatan: jsDelivr butuh beberapa menit sebelum berkas baru bisa diakses.')
    for t in tak_dikenal[:5]:
        print('   ! dilewati: {}'.format(t))


def _kunci_berikut(db, folder_toko, slug):
    """Nomor urut berikutnya untuk foto tambahan toko ini."""
    awalan = 'TAMBAHAN-' + slug.upper() + '-' if slug else 'TAMBAHAN-U'
    ada = [r['kunci'] for r in db.execute(
        "SELECT kunci FROM foto WHERE toko = ? AND tipe = 'tambahan'", (folder_toko,))]
    nomor = [int(k.rsplit('-', 1)[-1].lstrip('U')) for k in ada
             if k.startswith(awalan) and k.rsplit('-', 1)[-1].lstrip('U').isdigit()]
    return awalan + str(max(nomor) + 1 if nomor else 1)


def pasang_foto_tambahan(inti, cfg, folder_toko, berkas, jenis=None, push=True):
    """Pasang satu atau beberapa foto tambahan (panduan ukuran) untuk sebuah toko.

    Foto ini mengisi Foto Produk 3 dan seterusnya di semua listing toko itu.
    `berkas` boleh satu path atau daftar path. Kalau `jenis` diisi, foto hanya
    berlaku untuk jenis produk tersebut dan mengalahkan yang berlaku umum.
    """
    daftar = [berkas] if isinstance(berkas, str) else list(berkas)
    daftar = [b for b in daftar if b.strip()]
    nama_toko = {t['folder_foto']: t['nama'] for t in cfg['toko']}
    if folder_toko not in nama_toko:
        print('[tambahan] toko "{}" tidak ada di config.json'.format(folder_toko))
        return
    hilang = [b for b in daftar if not os.path.exists(b)]
    if hilang:
        print('[tambahan] berkas tidak ada: {}'.format(', '.join(hilang)))
        daftar = [b for b in daftar if b not in hilang]
    if not daftar:
        return

    slug = cfg['jenis'][jenis]['slug'] if jenis else None
    base = (cfg['foto'].get('base_url') or '').rstrip('/')
    db = gudang.buka(inti.DB_PATH)
    baris = []
    for sumber in daftar:
        kunci = _kunci_berikut(db, folder_toko, slug)
        nama = kunci + '.png'
        tujuan = os.path.join(inti.DIR_FOTO, folder_toko, 'umum')
        os.makedirs(tujuan, exist_ok=True)
        akhir = os.path.join(tujuan, nama)
        kecil = inti.salin_muat(sumber, akhir)
        path_repo = 'foto-upload/{}/umum/{}'.format(folder_toko, nama)
        b = {'toko': folder_toko, 'nama_toko': nama_toko[folder_toko], 'jenis': jenis,
             'seri': None, 'tipe': 'tambahan', 'kunci': kunci, 'sumber': sumber,
             'file_lokal': akhir, 'path_repo': path_repo,
             'ukuran': os.path.getsize(akhir),
             'url': base + '/' + path_repo if base else None, 'diunggah': 0}
        gudang.simpan(db, [b])     # disimpan satu per satu agar nomor berikutnya benar
        baris.append(b)
        print('[tambahan] {} -> {} ({:.2f} MB{})'.format(
            os.path.basename(sumber), path_repo, _mb(b['ukuran']),
            ', dikecilkan' if kecil else ''))
    print('[tambahan] {} foto untuk {}, berlaku: {}'.format(
        len(baris), nama_toko[folder_toko], jenis or 'semua jenis produk'))

    if not push:
        print('[tambahan] push dilewati')
        db.close()
        return
    if inti.mode_penyimpanan(cfg) == 'r2':
        klien = modul_r2.dari_config(cfg)
        for b in baris:
            klien.unggah(b['path_repo'], b['file_lokal'])
            b['url'] = klien.alamat(b['path_repo'])
        gudang.simpan(db, baris)
        gudang.tandai_terunggah(db, [b['path_repo'] for b in baris])
        print('[tambahan] terkirim ke R2: {}'.format(baris[0]['url']))
    else:
        _kirim(inti, db, [b['path_repo'] for b in baris],
               'Foto tambahan {} ({})'.format(nama_toko[folder_toko], jenis or 'semua jenis'))
    db.close()


def hapus_foto_tambahan(inti, cfg, folder_toko, kunci, push=True):
    """Hapus satu foto tambahan: dari disk, dari repo, dan dari database."""
    db = gudang.buka(inti.DB_PATH)
    baris = db.execute(
        "SELECT * FROM foto WHERE toko = ? AND kunci = ? AND tipe = 'tambahan'",
        (folder_toko, kunci)).fetchone()
    if not baris:
        print('[tambahan] tidak ketemu: {} / {}'.format(folder_toko, kunci))
        db.close()
        return
    path_repo = baris['path_repo']
    lokal = os.path.join(inti.AKAR, path_repo.replace('/', os.sep))
    if inti.mode_penyimpanan(cfg) == 'r2':
        try:
            modul_r2.dari_config(cfg).hapus(path_repo)
        except Exception as e:
            print('[tambahan] gagal menghapus dari R2: {}'.format(e))
        push = False
    else:
        kode, keluaran = _git(inti.AKAR, ['rm', '-f', '--ignore-unmatch', path_repo])
        if kode:
            print('[tambahan] git rm gagal:\n' + keluaran)
    if os.path.exists(lokal):
        os.remove(lokal)
    db.execute("DELETE FROM foto WHERE toko = ? AND kunci = ?", (folder_toko, kunci))
    db.commit()
    print('[tambahan] {} dihapus'.format(path_repo))
    if push:
        # penghapusan ini memang disengaja, jadi hook penjaga foto dilewati
        _kirim(inti, db, [], 'Hapus foto tambahan {}'.format(kunci),
               tandai=False, paksa=True)
    db.close()


def _kirim(inti, db, path_repo, pesan, tandai=True, paksa=False):
    """Commit + push perubahan foto tambahan."""
    if path_repo:
        kode, keluaran = _git(inti.AKAR, ['add', '-f'] + list(path_repo))
        if kode:
            print('[tambahan] git add gagal:\n' + keluaran)
            return
    kode, keluaran = _git(inti.AKAR, [
        '-c', 'user.email=tools@local', '-c', 'user.name=mass-upload',
        'commit', '-m', pesan])
    if kode and 'nothing to commit' not in keluaran:
        print('[tambahan] git commit gagal:\n' + keluaran)
        return
    kode, _ = _git(inti.AKAR, ['-c', 'http.postBuffer=157286400',
                               '-c', 'http.version=HTTP/1.1',
                               'push', '--progress', 'origin', 'HEAD'],
                   cetak=lambda b: print('   ' + b))
    if kode:
        print('[tambahan] push gagal, coba lagi nanti')
    elif tandai and path_repo:
        gudang.tandai_terunggah(db, list(path_repo))
        print('[tambahan] terkirim ({} berkas)'.format(len(path_repo)))
    else:
        print('[tambahan] terkirim')


def jelaskan_gagal(cfg, folder, survei, tak_dikenal):
    """Terangkan kenapa tidak ada foto yang dikenali, bukan sekadar bilang gagal."""
    print('[unggah] tidak ada foto yang dikenali di folder itu')
    print('   folder: {}'.format(folder))
    if not survei:
        print('   Tidak ada satu pun berkas gambar di folder ini maupun sub-foldernya.')
        print('   Kalau foldernya ada di Google Drive, mungkin isinya belum tersinkron —')
        print('   buka foldernya sekali di File Explorer, tunggu sebentar, lalu coba lagi.')
        return

    print('   Yang ditemukan:')
    for s in survei[:8]:
        rel = os.path.relpath(s['folder'], folder)
        print('     {:<42} {} gambar  toko: {}'.format(
            (rel if rel != '.' else '(folder ini sendiri)')[:42], s['gambar'],
            s['toko'] or 'TIDAK DIKENALI'))
        print('        contoh berkas: {}'.format(', '.join(s['contoh'])))
    if len(survei) > 8:
        print('     ... dan {} folder lain'.format(len(survei) - 8))

    if all(not s['toko'] for s in survei):
        print('   Masalahnya: nama folder toko tidak dikenali.')
        print('   Tools mencari folder bernama seperti: TOKO 1, Toko_2, FOTO_3,')
        print('   atau persis nama tokonya: {}'.format(
            ', '.join(t['nama'] for t in cfg['toko'])))
        print('   Perbaiki nama foldernya di Drive, atau pilih folder yang lebih dalam.')
    else:
        print('   Folder tokonya dikenali, tapi nama berkasnya tidak.')
        print('   Nama berkas harus berupa kode SKU (mis. JB-0000101.png)')
        print('   atau foto utama: {}'.format(', '.join(cfg['foto']['nama_foto_utama'])))


def lapor_deteksi(inti, cfg, folder):
    """Tampilkan hasil deteksi saja, tanpa menyalin/mengunggah apa pun."""
    temuan, tanpa_seri, tak_dikenal, survei = deteksi(inti, cfg, folder)
    if not temuan:
        jelaskan_gagal(cfg, folder, survei, tak_dikenal)
        return
    print('[deteksi] folder : {}'.format(os.path.abspath(folder)))
    print('[deteksi] {} foto dikenali, {} berkas dilewati'.format(len(temuan), len(tak_dikenal)))
    for (tk, jn, sr), n in sorted(_rekap(temuan).items()):
        print('   {:<15} {:<12} {:<22} {} foto'.format(tk, jn, sr, n))
    if temuan:
        besar = sum(os.path.getsize(t['sumber']) for t in temuan)
        print('[deteksi] total {:.1f} MB, perkiraan {} bagian saat diunggah'.format(
            _mb(besar), max(1, int(besar // BATAS_KIRIM) + 1)))
        print('[deteksi] contoh tujuan: {} -> {}'.format(
            os.path.basename(temuan[0]['sumber']), temuan[0]['path_repo']))
    if tanpa_seri:
        print('   ! {} SKU belum ada di data/sku.csv: {}'.format(
            len(tanpa_seri), ', '.join(sorted(set(tanpa_seri))[:6])))
    for t in tak_dikenal[:5]:
        print('   ! dilewati: {}'.format(t))
