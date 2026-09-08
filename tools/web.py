# -*- coding: utf-8 -*-
"""Tampilan web untuk tools Shopee Mass Upload.

Jalankan: python tools/web.py   (atau klik dua kali WEB.bat)
Server kecil dari pustaka bawaan Python, tanpa perlu memasang apa pun.
Halaman terbuka sendiri di browser pada http://127.0.0.1:8765

Isinya:
  - penjelajah folder Google Drive, tampil seperti Windows Explorer
  - tiap folder diberi status: berapa foto, berapa yang sudah diupload,
    apakah SKU-nya sudah terdaftar, apakah siap dibuatkan listing
  - tombol proses per folder, dan langkah lain (impor, cek, build, ekspor URL)
  - log berjalan
"""
import json, os, re, subprocess, sys, threading, time, traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shopee_mass_upload as inti
import gudang
import unggah as modul_unggah

PORT = 8765
LOG = []                      # seluruh baris log sejak server hidup
KUNCI = threading.Lock()
SIBUK = {'nama': None, 'tahap': None, 'n': 0, 'total': 0}
SINGGAHAN = {}                # cache hasil pemindaian folder
BERKAS_CACHE = os.path.join(inti.AKAR, 'data', 'cache_folder.json')
# Dinaikkan tiap kali isi hasil status_folder berubah bentuk, supaya cache lama
# dari versi sebelumnya dibuang, bukan ditampilkan sebagai angka yang salah.
VERSI_CACHE = 3


def muat_cache():
    try:
        with open(BERKAS_CACHE, encoding='utf-8') as f:
            isi = json.load(f)
        if isi.get('versi') == VERSI_CACHE:
            SINGGAHAN.update(isi['folder'])
    except Exception:
        pass


def simpan_cache():
    try:
        os.makedirs(os.path.dirname(BERKAS_CACHE), exist_ok=True)
        with open(BERKAS_CACHE, 'w', encoding='utf-8') as f:
            json.dump({'versi': VERSI_CACHE, 'folder': dict(SINGGAHAN)}, f)
    except Exception:
        pass


def catat(teks):
    with KUNCI:
        for baris in str(teks).splitlines():
            LOG.append(baris)
        del LOG[:-4000]


class Aliran:
    def write(self, t):
        if t and t.strip():
            catat(t)

    def flush(self):
        pass


def di_latar(nama, fungsi):
    """Jalankan pekerjaan di thread lain sambil mengalihkan print() ke log."""
    if SIBUK['nama']:
        return False

    def bungkus():
        SIBUK.update(nama=nama, tahap=None, n=0, total=0)
        asli = sys.stdout
        sys.stdout = Aliran()
        try:
            catat('\n' + '─' * 70)
            catat('>>> ' + nama.upper())
            fungsi()
            catat('[selesai]')
        except SystemExit as e:
            catat('[berhenti] {}'.format(e))
        except Exception:
            catat('[error] ' + traceback.format_exc())
        finally:
            sys.stdout = asli
            SIBUK.update(nama=None, tahap=None, n=0, total=0)

    threading.Thread(target=bungkus, daemon=True).start()
    return True


# --------------------------------------------------------------------------- data
def nomor_folder(nama):
    """'PRODUK 00051 - 00100' -> (51, 100). None kalau bukan folder produk."""
    m = re.match(r'^PRODUK\s+0*(\d+)\s*-\s*0*(\d+)$', nama.strip(), re.I)
    return (int(m.group(1)), int(m.group(2))) if m else None


def pohon(cfg):
    """Daftar folder produk per jenis, dibaca dari Google Drive. Cepat — hanya nama folder.

    Tiap folder diberi jumlah SKU yang di Google Sheet sudah ditandai punya foto
    produk. Angka itu berasal dari sku.csv saja, tidak menyentuh Drive, jadi bisa
    dipakai mengurutkan daftar begitu halaman dibuka.
    """
    from bisect import bisect_left, bisect_right
    siap = inti.indeks_foto_siap()
    hasil = []
    for jenis, j in cfg['jenis'].items():
        akar = inti.dir_jenis(cfg, jenis)
        anak = []
        if os.path.isdir(akar):
            for nama in sorted(os.listdir(akar)):
                rentang = nomor_folder(nama)
                if rentang and os.path.isdir(os.path.join(akar, nama)):
                    nomor = siap.get(jenis.upper(), [])
                    n_siap = (bisect_right(nomor, rentang[1])
                              - bisect_left(nomor, rentang[0])) if nomor else 0
                    anak.append({'nama': nama, 'path': os.path.join(akar, nama),
                                 'dari': rentang[0], 'sampai': rentang[1],
                                 'siap': n_siap})
        hasil.append({'jenis': jenis, 'prefix': j['prefix_sku'], 'akar': akar,
                      'khusus': bool((j.get('path_drive') or '').strip()),
                      'ada': os.path.isdir(akar), 'folder': anak})
    return hasil


def _indeks_sku(_ingatan={}):
    """{jenis: [nomor SKU]} terurut, dihitung sekali saja.

    Sebelumnya tiap folder membaca ulang seluruh sku.csv dan seluruh database.
    Dengan puluhan ribu SKU dan ratusan folder itu jadi sangat lambat, jadi
    keduanya diindeks sekali lalu dicari dengan bisect.
    """
    if not os.path.exists(inti.SKU_CSV):
        return {}
    cap = os.path.getmtime(inti.SKU_CSV)
    if _ingatan.get('cap') == cap:
        return _ingatan['isi']
    hasil = {}
    try:
        for jenis, seri_map in inti.baca_sku().items():
            nomor = []
            for desain in seri_map.values():
                for d in desain:
                    n = inti.nomor_sku(d['sku'])
                    if n:
                        nomor.append(n)
            nomor.sort()
            hasil[jenis] = nomor
    except SystemExit:
        return {}
    _ingatan['cap'], _ingatan['isi'] = cap, hasil
    return hasil


def _indeks_db(_ingatan={}):
    """{jenis: ([nomor], [nomor yang sudah terunggah])} dari database foto."""
    if not os.path.exists(inti.DB_PATH):
        return {}
    cap = os.path.getmtime(inti.DB_PATH)
    if _ingatan.get('cap') == cap:
        return _ingatan['isi']
    hasil = {}
    db = gudang.buka(inti.DB_PATH)
    try:
        for r in db.execute('SELECT jenis, kunci, diunggah FROM foto'):
            n = inti.nomor_sku(r['kunci'] or '')
            if not n:
                continue
            semua, terunggah = hasil.setdefault((r['jenis'] or '').upper(), ([], []))
            semua.append(n)
            if r['diunggah']:
                terunggah.append(n)
    finally:
        db.close()
    for semua, terunggah in hasil.values():
        semua.sort()
        terunggah.sort()
    _ingatan['cap'], _ingatan['isi'] = cap, hasil
    return hasil


def _dalam(nomor, dari, sampai):
    from bisect import bisect_left, bisect_right
    return bisect_right(nomor, sampai) - bisect_left(nomor, dari) if nomor else 0


def status_folder(cfg, jenis, path, dari, sampai, segar=False):
    """Hitung status satu folder produk. Hasilnya disimpan di cache."""
    if not segar and path in SINGGAHAN:
        return SINGGAHAN[path]

    n_foto = n_foto_toko = 0
    toko_ada = set()
    for dirpath, _, berkas in os.walk(path):
        gambar = [f for f in berkas if f.lower().endswith(inti.EKSTENSI)]
        if not gambar:
            continue
        n_foto += len(gambar)
        toko = modul_unggah.kenali_toko(cfg, dirpath)
        if toko:
            toko_ada.add(toko)
            n_foto_toko += len(gambar)

    n_sku = _dalam(_indeks_sku().get(jenis.upper(), []), dari, sampai)
    semua, terunggah = _indeks_db().get(jenis.upper(), ([], []))
    n_db = _dalam(semua, dari, sampai)
    n_unggah = _dalam(terunggah, dari, sampai)

    # urutan ini penting: foto boleh sudah terupload, tapi tanpa SKU di sku.csv
    # listing-nya tetap tidak bisa dibuat, jadi jangan disebut siap
    if n_foto == 0:
        keadaan, label = 'kosong', 'belum ada foto'
    elif n_foto_toko == 0:
        keadaan, label = 'tanpatoko', 'folder toko tidak dikenali'
    elif n_sku == 0:
        keadaan, label = 'tanpasku', 'SKU belum diimpor'
    elif n_unggah and n_unggah >= n_db and n_db >= n_sku:
        keadaan, label = 'siap', 'sudah di R2 · siap listing'
    elif n_db:
        keadaan, label = 'sebagian', 'sebagian sudah di R2'
    else:
        keadaan, label = 'baru', 'foto ada, belum diproses'

    hasil = {'path': path, 'foto': n_foto, 'foto_toko': n_foto_toko,
             'toko': sorted(toko_ada), 'sku': n_sku,
             'db': n_db, 'unggah': n_unggah, 'keadaan': keadaan, 'label': label,
             'jenis': jenis, 'nama': os.path.basename(path)}
    SINGGAHAN[path] = hasil
    return hasil


def pindai_semua(cfg, lapor=None):
    """Hitung status seluruh folder produk, tidak menunggu barisnya terlihat."""
    from concurrent.futures import ThreadPoolExecutor
    daftar = [(j['jenis'], f) for j in pohon(cfg) for f in j['folder']]
    total = len(daftar)
    print('[scan] memindai {} folder produk …'.format(total))
    hitung = {'n': 0}
    kunci = threading.Lock()

    def satu(pasang):
        jenis, f = pasang
        try:
            status_folder(cfg, jenis, f['path'], f['dari'], f['sampai'], segar=True)
        except Exception as e:
            print('   ! {}: {}'.format(f['nama'], e))
        with kunci:
            hitung['n'] += 1
            n = hitung['n']
        if lapor:
            lapor('scan', n, total)
        if n % 100 == 0 or n == total:
            print('      {}/{} folder'.format(n, total))

    with ThreadPoolExecutor(max_workers=8) as kolam:
        list(kolam.map(satu, daftar))

    simpan_cache()
    rekap = {}
    for j, f in daftar:
        s = SINGGAHAN.get(f['path'])
        if s:
            rekap[s['label']] = rekap.get(s['label'], 0) + 1
    print('[scan] selesai:')
    for k, n in sorted(rekap.items(), key=lambda x: -x[1]):
        print('      {:<28} {}'.format(k, n))
    return rekap


ANGKA = [('harga_paket', 'Harga paket', 1, 10 ** 9),
         ('min_order', 'Min. order', 1, 999999),
         ('berat_gram', 'Berat (gram)', 0, 100000000),
         ('stok', 'Stok', 0, 10000000)]


def baca_pengaturan(cfg):
    """Nilai-nilai yang boleh diubah lewat tab Pengaturan Produk."""
    jenis = {}
    for nama, j in cfg['jenis'].items():
        jenis[nama] = {k: j.get(k) for k, _, _, _ in ANGKA}
        jenis[nama]['spec'] = j.get('spec', '')
        jenis[nama]['kategori'] = j.get('kategori', '')
        jenis[nama]['harga_pcs'] = round(j['harga_paket'] / j['min_order']) if j.get('min_order') else None
    return {
        'jenis': jenis,
        'toko': [{'nama': t['nama'], 'profil': t['profil']} for t in cfg['toko']],
        'profil': {p: {'judul': v['judul'], 'deskripsi': v['deskripsi']}
                   for p, v in cfg['profil'].items()},
        'batas': {'deskripsi': [inti.MIN_DESK, inti.MAKS_DESK],
                  'judul': [5, inti.MAKS_JUDUL]},
        'berkas': dict(zip(('listing', 'baris'), inti.batas_berkas(cfg))),
    }


def simpan_pengaturan(cfg, badan):
    """Simpan perubahan ke config.json setelah diperiksa.

    cfg yang berjalan adalah gabungan config.json + data/lokal.json, dan
    lokal.json memuat kunci Cloudflare R2 serta letak folder tiap komputer.
    config.json ikut terkirim ke GitHub yang public, jadi yang ditulis ke sana
    hanya isi berkas itu sendiri yang dibaca ulang dari disk — perubahan
    ditempelkan ke situ, bukan cfg gabungan yang sedang dipakai.
    """
    with open(inti.CONFIG, encoding='utf-8') as f:
        bersama = json.load(f)

    def setel(*jalur_nilai):
        """Tulis nilai ke cfg yang berjalan sekaligus ke config.json bersama."""
        *jalur, nilai = jalur_nilai
        for wadah in (cfg, bersama):
            simpul = wadah
            for k in jalur[:-1]:
                simpul = simpul.setdefault(k, {})
            simpul[jalur[-1]] = nilai

    galat = []
    for nama, isi in (badan.get('jenis') or {}).items():
        if nama not in cfg['jenis']:
            continue
        for kunci, label, kecil, besar in ANGKA:
            if kunci not in isi:
                continue
            try:
                nilai = int(str(isi[kunci]).replace('.', '').replace(',', '').strip())
            except ValueError:
                galat.append('{} · {}: bukan angka'.format(nama, label))
                continue
            if not kecil <= nilai <= besar:
                galat.append('{} · {}: harus {} sampai {}'.format(nama, label, kecil, besar))
                continue
            setel('jenis', nama, kunci, nilai)
        if 'spec' in isi:
            setel('jenis', nama, 'spec', isi['spec'])
        j = cfg['jenis'][nama]
        if j['min_order'] and round(j['harga_paket'] / j['min_order']) < 99:
            galat.append('{}: harga per pcs jadi di bawah Rp99'.format(nama))

    for profil, isi in (badan.get('profil') or {}).items():
        if profil not in cfg['profil']:
            continue
        for jenis, teks in (isi.get('deskripsi') or {}).items():
            if jenis not in cfg['profil'][profil]['deskripsi']:
                continue
            panjang = len(teks.replace('{SPEC}', cfg['jenis'][jenis].get('spec', ''))
                              .replace('{TOKO}', 'x' * 14))
            if not inti.MIN_DESK <= panjang <= inti.MAKS_DESK:
                galat.append('deskripsi {} · {}: {} karakter, batas {}-{}'.format(
                    profil, jenis, panjang, inti.MIN_DESK, inti.MAKS_DESK))
                continue
            setel('profil', profil, 'deskripsi', jenis, teks)
        for jenis, pasangan in (isi.get('judul') or {}).items():
            if jenis in cfg['profil'][profil]['judul'] and pasangan:
                setel('profil', profil, 'judul', jenis, [list(p[:2]) for p in pasangan])

    # Batas ukuran berkas Excel. Angkanya perkiraan yang aman, bukan angka resmi
    # dari Shopee, jadi memang perlu bisa disetel kalau kenyataannya beda.
    berkas = badan.get('berkas') or {}
    for kunci, label, kecil, besar in (('listing', 'listing per berkas', 1, 5000),
                                       ('baris', 'baris per berkas', 1, 200000)):
        if kunci not in berkas:
            continue
        try:
            nilai = int(str(berkas[kunci]).replace('.', '').replace(',', '').strip())
        except ValueError:
            galat.append('{}: bukan angka'.format(label))
            continue
        if not kecil <= nilai <= besar:
            galat.append('{}: harus {} sampai {}'.format(label, kecil, besar))
            continue
        setel('batas_' + kunci, nilai)

    if galat:
        return {'ok': False, 'galat': galat}
    with open(inti.CONFIG, 'w', encoding='utf-8') as f:
        json.dump(bersama, f, ensure_ascii=False, indent=2)
    catat('[ui] pengaturan produk disimpan')
    return {'ok': True}


def daftar_tambahan(cfg):
    """Foto tambahan (panduan ukuran) yang sudah terpasang per toko."""
    if not os.path.exists(inti.DB_PATH):
        return []
    db = gudang.buka(inti.DB_PATH)
    try:
        return [dict(r) for r in db.execute(
            "SELECT toko, nama_toko, jenis, kunci, url, diunggah, ukuran "
            "FROM foto WHERE tipe = 'tambahan' ORDER BY toko, jenis")]
    finally:
        db.close()


def ringkas_status(cfg):
    """Hitung berapa listing yang sudah diupload ke Shopee dan berapa yang belum.

    Penandanya diambil dari Google Sheet: kolom FOTO PRODUK menyatakan fotonya
    sudah dibuat, kolom UPLOAD menyatakan listing itu sudah masuk Shopee. Satu
    seri di sheet sama dengan satu listing di Shopee.
    """
    try:
        data = inti.baca_sku()
    except SystemExit:
        return {'kosong': True}

    per_jenis, seri_belum = [], []
    for jenis, seri_map in data.items():
        hitung = {'total': 0, 'sudah': 0, 'siap': 0, 'sebagian': 0, 'kosong': 0}
        sku = {'total': 0, 'siap': 0, 'sudah': 0}
        for seri, desain in seri_map.items():
            n = len(desain)
            n_siap = sum(1 for d in desain if d.get('foto_siap'))
            n_upload = sum(1 for d in desain if d.get('sudah_upload'))
            hitung['total'] += 1
            sku['total'] += n
            sku['siap'] += n_siap
            sku['sudah'] += n_upload
            if n_upload:
                hitung['sudah'] += 1
            elif n_siap >= n:
                hitung['siap'] += 1
                seri_belum.append({'jenis': jenis, 'seri': seri, 'sku': n})
            elif n_siap:
                hitung['sebagian'] += 1
            else:
                hitung['kosong'] += 1
        per_jenis.append({'jenis': jenis, 'seri': hitung, 'sku': sku})

    jumlah_foto = 0
    if os.path.exists(inti.MANIFEST_R2):
        with open(inti.MANIFEST_R2, encoding='utf-8-sig', newline='') as f:
            jumlah_foto = max(0, sum(1 for _ in f) - 1)

    # Keadaan folder sebenarnya di Drive, dari hasil scan terakhir. Angka dari
    # sheet menyatakan niat tim; angka ini menyatakan apa yang benar-benar ada.
    semua_folder = [(j['jenis'], f) for j in pohon(cfg) for f in j['folder']]
    per_status, per_jenis_folder = {}, {}
    discan = 0
    for jenis, f in semua_folder:
        s = SINGGAHAN.get(f['path'])
        if not s:
            continue
        discan += 1
        per_status[s['keadaan']] = per_status.get(s['keadaan'], 0) + 1
        jj = per_jenis_folder.setdefault(jenis, {})
        jj[s['keadaan']] = jj.get(s['keadaan'], 0) + 1

    belum_diproses = [
        {'jenis': jenis, 'nama': f['nama'], 'foto': SINGGAHAN[f['path']]['foto']}
        for jenis, f in semua_folder
        if SINGGAHAN.get(f['path'], {}).get('keadaan') == 'baru']

    return {'per_jenis': per_jenis, 'foto_terunggah': jumlah_foto,
            'siap_dikerjakan': seri_belum[:40],
            'jumlah_siap_dikerjakan': len(seri_belum),
            'folder': {'total': len(semua_folder), 'discan': discan,
                       'status': per_status, 'per_jenis': per_jenis_folder},
            'belum_diproses': belum_diproses[:40],
            'jumlah_belum_diproses': len(belum_diproses)}


def laporan_cek(cfg, lingkup=None):
    """Semua yang dibutuhkan untuk menghasilkan Excel, per berkas keluaran."""
    data = inti.saring_lingkup(inti.baca_sku(), lingkup or [])
    if not data:
        return []
    paket = inti.kumpulkan(cfg, data)
    wajib = {}
    for jenis, j in cfg['jenis'].items():
        wb = inti.openpyxl.load_workbook(os.path.join(inti.AKAR, cfg['template'][j['template']]))
        wajib[jenis] = inti.atribut_wajib(wb, j['kategori'])
        wb.close()
    keluar = []
    for berkas, (_, listings) in paket.items():
        rinci = []
        for L in listings:
            j = cfg['jenis'][L['jenis']]
            n_foto_varian = sum(1 for x in L['per_varian'] if x)
            rinci.append({
                'judul': L['judul'],
                'jenis': L['jenis'],
                'varian': len(L['desain']),
                'foto': bool(L['utama'][0]),
                'foto_utama_n': sum(1 for x in L['utama'] if x),
                'foto_varian': bool(L['per_varian'][0]),
                'foto_varian_n': n_foto_varian,
                'sampul': L['utama'][0],
                'panjang_judul': len(L['judul']),
                'panjang_deskripsi': len(L['deskripsi']),
                'deskripsi': L['deskripsi'],
                'harga': round(j['harga_paket'] / j['min_order']),
                'harga_paket': j['harga_paket'],
                'min_order': j['min_order'],
                'berat': j['berat_gram'],
                'stok': j['stok'],
                'kategori': j['kategori'],
                'sku_induk': L['sku_induk'],
                'kode_integrasi': L['kode_induk'],
                'contoh_sku': [d['sku'] for d in L['desain'][:3]],
                'sku_terakhir': L['desain'][-1]['sku'],
                'tambahan': L['tambahan'],
            })
        keluar.append({'berkas': berkas, 'listing': rinci,
                       'peringatan': inti.periksa(cfg, listings, wajib)})
    return keluar


def dialog_folder(awal=''):
    """Buka dialog pilih folder Windows (lewat tools/pilih_folder.py) dan kembalikan path."""
    skrip = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pilih_folder.py')
    hasil = subprocess.run([sys.executable, skrip, awal or ''],
                           capture_output=True, text=True, timeout=300)
    return (hasil.stdout or '').strip()


def dialog_berkas(judul='', gambar=False):
    """Dialog pilih berkas (.xlsx/.csv) untuk impor SKU atau pasang template."""
    skrip = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pilih_folder.py')
    argumen = [sys.executable, skrip, '', '--berkas']
    if gambar:
        argumen.append('--gambar')
    argumen.append(judul)
    hasil = subprocess.run(argumen, capture_output=True, text=True, timeout=300)
    return (hasil.stdout or '').strip()


# --------------------------------------------------------------------------- server
class Penangan(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _kirim(self, isi, tipe='application/json; charset=utf-8', kode=200):
        if not isinstance(isi, bytes):
            isi = json.dumps(isi, ensure_ascii=False).encode('utf-8') \
                if tipe.startswith('application/json') else isi.encode('utf-8')
        self.send_response(kode)
        self.send_header('Content-Type', tipe)
        self.send_header('Content-Length', str(len(isi)))
        self.end_headers()
        self.wfile.write(isi)

    def _badan(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    def do_GET(self):
        jalur = self.path.split('?')[0]
        tanya = dict(p.split('=', 1) for p in self.path.split('?')[1].split('&')) \
            if '?' in self.path else {}
        try:
            if jalur == '/':
                return self._kirim(halaman(), 'text/html; charset=utf-8')
            if jalur == '/api/status':
                return self._kirim(self._status())
            if jalur == '/api/pohon':
                return self._kirim({'jenis': pohon(inti.baca_config())})
            if jalur == '/api/singgahan':
                return self._kirim({'folder': dict(SINGGAHAN)})
            if jalur == '/api/log':
                sejak = int(tanya.get('sejak', 0))
                with KUNCI:
                    return self._kirim({'baris': LOG[sejak:], 'total': len(LOG),
                                        'sibuk': SIBUK['nama'], 'tahap': SIBUK['tahap'],
                                        'n': SIBUK['n'], 'total_maju': SIBUK['total']})
        except Exception:
            return self._kirim({'galat': traceback.format_exc()}, kode=500)
        self._kirim('404', 'text/plain; charset=utf-8', 404)

    def do_POST(self):
        try:
            badan = self._badan()
            cfg = inti.baca_config()
            if self.path == '/api/folder':
                s = status_folder(cfg, badan['jenis'], badan['path'],
                                  badan['dari'], badan['sampai'], badan.get('segar'))
                return self._kirim(s)
            if self.path == '/api/deteksi':
                temuan, tanpa_seri, tak, survei = modul_unggah.deteksi(
                    inti, cfg, badan['path'])
                rekap = [{'toko': k[0], 'jenis': k[1], 'seri': k[2], 'n': v}
                         for k, v in sorted(modul_unggah._rekap(temuan).items())]
                return self._kirim({'jumlah': len(temuan), 'rekap': rekap,
                                    'tanpa_seri': sorted(set(tanpa_seri))[:10],
                                    'dilewati': len(tak),
                                    'survei': [{'folder': os.path.relpath(s['folder'],
                                                                         badan['path']),
                                                'gambar': s['gambar'], 'toko': s['toko'],
                                                'contoh': s['contoh']} for s in survei[:8]],
                                    'toko_sah': [t['nama'] for t in cfg['toko']],
                                    'contoh': temuan[0]['path_repo'] if temuan else None})
            if self.path == '/api/unggah':
                # 'paths' dipakai kalau beberapa folder dicentang sekaligus;
                # 'path' tetap diterima supaya tombol folder tunggal tidak berubah
                daftar = badan.get('paths') or [badan['path']]
                push = bool(badan.get('push', True))

                def lapor(tahap, n, total):
                    SIBUK.update(tahap=tahap, n=n, total=total)

                def kerja():
                    modul_unggah.proses_banyak(inti, cfg, daftar, push=push, lapor=lapor)
                    letak = {f['path']: (j['jenis'], f) for j in pohon(cfg) for f in j['folder']}
                    for p in daftar:
                        SINGGAHAN.pop(p, None)
                        if p in letak:
                            jenis, f = letak[p]
                            s = status_folder(cfg, jenis, p, f['dari'], f['sampai'], segar=True)
                            print('[status] {:<28} {}'.format(f['nama'], s['label']))
                    simpan_cache()

                nama = 'unggah' if len(daftar) == 1 else 'unggah {} folder'.format(len(daftar))
                return self._kirim({'mulai': di_latar(nama, kerja)})
            if self.path == '/api/perintah':
                nama = badan['perintah']

                lingkup = badan.get('folders') or []

                def kerja():
                    if nama == 'build':
                        data = inti.saring_lingkup(inti.baca_sku(), lingkup)
                        if not data:
                            print('[build] tidak ada SKU pada folder yang dipilih')
                            return
                        inti.perintah_build(cfg, data, sub='pilihan' if lingkup else None)
                        return
                    if nama == 'cek':
                        inti.perintah_cek(cfg, inti.baca_sku())
                    elif nama == 'url':
                        inti.perintah_url(cfg, inti.baca_sku())
                    elif nama == 'impor':
                        inti.perintah_impor(cfg, badan['sumber'])
                        SINGGAHAN.clear()
                return self._kirim({'mulai': di_latar(nama, kerja)})
            if self.path == '/api/cek':
                return self._kirim({'berkas': laporan_cek(cfg, badan.get('folders'))})
            if self.path == '/api/lingkup':
                # folder terpilih -> seri apa saja yang tercakup
                data = inti.saring_lingkup(inti.baca_sku(), badan.get('folders') or [])
                seri = [{'jenis': j, 'seri': s, 'sku': len(d)}
                        for j, m in data.items() for s, d in m.items()]
                return self._kirim({'seri': seri,
                                    'sku': sum(x['sku'] for x in seri)})
            if self.path == '/api/pilih':
                jalur = dialog_folder(badan.get('awal') or '')
                return self._kirim({'path': jalur})
            if self.path == '/api/tambahan':
                berkas = dialog_berkas('Pilih foto tambahan (boleh lebih dari satu)',
                                       gambar=True)
                if not berkas:
                    return self._kirim({'batal': True})
                daftar = [b for b in berkas.split('\n') if b.strip()]
                toko = badan.get('toko')
                jenis = badan.get('jenis') or None
                return self._kirim({'mulai': di_latar(
                    'foto tambahan', lambda: modul_unggah.pasang_foto_tambahan(
                        inti, cfg, toko, daftar, jenis, push=True))})
            if self.path == '/api/hapus_tambahan':
                toko, kunci = badan.get('toko'), badan.get('kunci')
                return self._kirim({'mulai': di_latar(
                    'hapus foto tambahan', lambda: modul_unggah.hapus_foto_tambahan(
                        inti, cfg, toko, kunci, push=True))})
            if self.path == '/api/template':
                berkas = dialog_berkas('Pilih template Shopee yang baru diunduh')
                if not berkas:
                    return self._kirim({'batal': True})
                return self._kirim({'mulai': di_latar(
                    'pasang template', lambda: inti.pasang_template(cfg, berkas))})
            if self.path == '/api/segarkan_r2':
                return self._kirim({'mulai': di_latar('segarkan daftar R2', lambda: (
                    modul_unggah.segarkan_manifest_r2(inti, cfg),
                    print('[daftar] commit data/foto_r2.csv supaya komputer yang '
                          'tidak punya kunci R2 ikut memakainya')))})
            if self.path == '/api/r2':
                lokal = inti.baca_lokal()
                r = (lokal.get('penyimpanan') or {}).get('r2') or {}
                if badan.get('simpan'):
                    lokal.setdefault('penyimpanan', {}).setdefault('r2', {}).update(
                        akses=(badan.get('akses') or '').strip() or None,
                        rahasia=(badan.get('rahasia') or '').strip() or None)
                    inti.tulis_lokal(lokal)
                    catat('[ui] kunci R2 disimpan di data/lokal.json')
                    r = lokal['penyimpanan']['r2']
                cfg2 = inti.baca_config()
                p = (cfg2.get('penyimpanan') or {})
                uji = None
                if badan.get('uji'):
                    sys.path.insert(0, os.path.join(inti.AKAR, 'tools'))
                    import r2 as modul_r2
                    try:
                        klien = modul_r2.dari_config(cfg2)
                        uji = {'ok': True, 'jumlah': len(klien.daftar('foto-upload/'))}
                    except Exception as e:
                        uji = {'ok': False, 'pesan': str(e)[:200]}
                return self._kirim({
                    'mode': (p.get('mode') or 'github'),
                    'bucket': (p.get('r2') or {}).get('bucket'),
                    'domain': (p.get('r2') or {}).get('domain'),
                    'ada_kunci': bool(r.get('akses') and r.get('rahasia')),
                    'akses': (r.get('akses') or '')[:6] + '…' if r.get('akses') else '',
                    'uji': uji})
            if self.path == '/api/sinkron_sku':
                return self._kirim({'mulai': di_latar(
                    'sinkron SKU', lambda: (inti.sinkron_sku(cfg, catat),
                                            SINGGAHAN.clear(), simpan_cache()))})
            if self.path == '/api/tempel_sku':
                catatan = inti.baca_tempelan(badan.get('teks') or '')
                if badan.get('intip'):
                    contoh = catatan[:5]
                    return self._kirim({'jumlah': len(catatan), 'contoh': contoh})
                hasil = inti.tulis_sku(cfg, catatan, gabung=badan.get('gabung', True))
                if hasil.get('ok'):
                    catat('[sku] {} baru, {} diperbarui, {} dilewati -> total {} SKU'.format(
                        hasil['baru'], hasil['diperbarui'], hasil['dilewati'], hasil['total']))
                    SINGGAHAN.clear()
                    simpan_cache()
                return self._kirim(hasil)
            if self.path == '/api/pindai':
                def lapor(tahap, n, total):
                    SIBUK.update(tahap=tahap, n=n, total=total)
                return self._kirim({'mulai': di_latar(
                    'scan folder', lambda: pindai_semua(cfg, lapor))})
            if self.path == '/api/dashboard':
                return self._kirim(ringkas_status(cfg))
            if self.path == '/api/ringkas_sku':
                try:
                    data = inti.baca_sku()
                except SystemExit:
                    return self._kirim({'seri': []})
                return self._kirim({'seri': [
                    {'jenis': j, 'seri': s, 'sku': len(d),
                     'awal': d[0]['sku'], 'akhir': d[-1]['sku']}
                    for j, m in data.items() for s, d in m.items()]})
            if self.path == '/api/pengaturan':
                return self._kirim(baca_pengaturan(cfg))
            if self.path == '/api/simpan_pengaturan':
                return self._kirim(simpan_pengaturan(cfg, badan))
            if self.path == '/api/perbarui':
                import perbarui as modul_perbarui
                if badan.get('pasang'):
                    return self._kirim({'mulai': di_latar(
                        'perbarui', lambda: modul_perbarui.pasang(inti, catat))})
                return self._kirim(modul_perbarui.periksa(inti))
            if self.path == '/api/buka':
                peta = {'output': inti.DIR_OUT, 'foto': inti.DIR_FOTO,
                        'data': os.path.join(inti.AKAR, 'data')}
                folder = peta.get(badan.get('apa'), inti.DIR_OUT)
                if os.path.isdir(folder):
                    subprocess.Popen(['explorer', os.path.normpath(folder)])
                return self._kirim({'ok': os.path.isdir(folder)})
            if self.path == '/api/pilih_berkas':
                return self._kirim({'path': dialog_berkas()})
            if self.path == '/api/sumber':
                # Folder sumber berbeda tiap komputer, jadi disimpan di
                # data/lokal.json yang tidak ikut git — supaya tidak tertimpa
                # saat memperbarui dari GitHub.
                lokal = inti.baca_lokal()
                for jenis, jalur in (badan.get('jenis') or {}).items():
                    if jenis in cfg['jenis']:
                        lokal.setdefault('jenis', {}).setdefault(jenis, {})['path_drive'] = \
                            (jalur or '').strip() or None
                if badan.get('root') is not None:
                    lokal.setdefault('foto', {})['root'] = (badan['root'] or '').strip() or None
                inti.tulis_lokal(lokal)
                cfg = inti.baca_config()
                SINGGAHAN.clear()
                simpan_cache()
                catat('[ui] folder sumber disimpan')
                for jenis in cfg['jenis']:
                    d = inti.dir_jenis(cfg, jenis)
                    catat('   {:<12} {}  {}'.format(
                        jenis, d, '' if os.path.isdir(d) else '(tidak ditemukan)'))
                return self._kirim({'ok': True})
            if self.path == '/api/config':
                cfg['foto']['base_url'] = (badan.get('base_url') or '').strip().rstrip('/') or None
                with open(inti.CONFIG, 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                catat('[ui] base_url disimpan: {}'.format(cfg['foto']['base_url'] or '(kosong)'))
                return self._kirim({'ok': True})
        except Exception:
            return self._kirim({'galat': traceback.format_exc()}, kode=500)
        self._kirim('404', 'text/plain; charset=utf-8', 404)

    def _status(self):
        cfg = inti.baca_config()
        try:
            data = inti.baca_sku()
            sku = {'jumlah': sum(len(d) for s in data.values() for d in s.values()),
                   'jenis': len(data), 'seri': sum(len(s) for s in data.values())}
        except SystemExit:
            sku = None
        n_db = n_unggah = 0
        rinci = []
        if os.path.exists(inti.DB_PATH):
            db = gudang.buka(inti.DB_PATH)
            n_db, n_unggah = gudang.jumlah(db)
            rinci = gudang.ringkasan(db)
            db.close()
        n_out = len([f for f in os.listdir(inti.DIR_OUT)
                     if f.endswith('.xlsx') and not f.startswith('~$')]) \
            if os.path.isdir(inti.DIR_OUT) else 0
        sumber = [{'jenis': j, 'path': inti.dir_jenis(cfg, j),
                   'khusus': bool((cfg['jenis'][j].get('path_drive') or '').strip()),
                   'ada': os.path.isdir(inti.dir_jenis(cfg, j))} for j in cfg['jenis']]
        return {'akar': inti.AKAR, 'root_drive': cfg['foto'].get('root'), 'sumber': sumber,
                'template': inti.info_template(cfg),
                'tambahan': daftar_tambahan(cfg),
                'base_url': cfg['foto'].get('base_url') or '',
                'toko': cfg['toko'], 'sku': sku, 'db': n_db, 'unggah': n_unggah,
                'ringkasan': rinci, 'output': n_out}


BERKAS_HALAMAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'halaman.html')


def halaman():
    """Baca halaman dari berkas tiap kali diminta, supaya perubahan tampilan
    cukup dengan menyegarkan browser — server tidak perlu dihidupkan ulang."""
    with open(BERKAS_HALAMAN, encoding='utf-8') as f:
        return f.read()


BERKAS_KODE = [os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
               for f in ('web.py', 'shopee_mass_upload.py', 'unggah.py', 'gudang.py')]


def _cap_kode():
    return tuple(os.path.getmtime(f) if os.path.exists(f) else 0 for f in BERKAS_KODE)


def awasi_kode():
    """Jalankan ulang server sendiri kalau kode Python-nya berubah.

    halaman.html dibaca ulang tiap permintaan, tapi kode Python hanya dimuat
    sekali. Tanpa ini, halaman baru bisa memanggil endpoint yang belum ada di
    server yang sedang jalan — gejalanya tombol atau dropdown diam saja.
    Penjalanan ulang ditunda selama masih ada pekerjaan berlangsung.
    """
    awal = _cap_kode()
    while True:
        time.sleep(1.5)
        if _cap_kode() != awal and not SIBUK['nama']:
            print('[server] kode berubah, menjalankan ulang…')
            catat('[server] kode berubah, server dijalankan ulang')
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as e:      # kalau gagal, cukup beri tahu
                print('[server] gagal menjalankan ulang: {}'.format(e))
                return


def main():
    alamat = 'http://127.0.0.1:{}'.format(PORT)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Penangan)
    print('Tools Shopee Mass Upload berjalan di {}'.format(alamat))
    print('Tutup jendela ini untuk menghentikan server.')
    muat_cache()
    catat('[siap] buka {} di browser'.format(alamat))
    threading.Timer(0.8, lambda: webbrowser.open(alamat)).start()
    threading.Thread(target=awasi_kode, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\ndihentikan')


if __name__ == '__main__':
    main()
