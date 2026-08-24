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


def muat_cache():
    try:
        with open(BERKAS_CACHE, encoding='utf-8') as f:
            SINGGAHAN.update(json.load(f))
    except Exception:
        pass


def simpan_cache():
    try:
        os.makedirs(os.path.dirname(BERKAS_CACHE), exist_ok=True)
        with open(BERKAS_CACHE, 'w', encoding='utf-8') as f:
            json.dump(SINGGAHAN, f)
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
    """Daftar folder produk per jenis, dibaca dari Google Drive. Cepat — hanya nama folder."""
    hasil = []
    for jenis, j in cfg['jenis'].items():
        akar = inti.dir_jenis(cfg, jenis)
        anak = []
        if os.path.isdir(akar):
            for nama in sorted(os.listdir(akar)):
                rentang = nomor_folder(nama)
                if rentang and os.path.isdir(os.path.join(akar, nama)):
                    anak.append({'nama': nama, 'path': os.path.join(akar, nama),
                                 'dari': rentang[0], 'sampai': rentang[1]})
        hasil.append({'jenis': jenis, 'prefix': j['prefix_sku'], 'akar': akar,
                      'khusus': bool((j.get('path_drive') or '').strip()),
                      'ada': os.path.isdir(akar), 'folder': anak})
    return hasil


def status_folder(cfg, jenis, path, dari, sampai, segar=False):
    """Hitung status satu folder produk. Hasilnya disimpan di cache."""
    if not segar and path in SINGGAHAN:
        return SINGGAHAN[path]

    pre = cfg['jenis'][jenis]['prefix_sku']
    n_foto = 0
    toko_ada = set()
    for dirpath, _, berkas in os.walk(path):
        gambar = [f for f in berkas if f.lower().endswith(inti.EKSTENSI)]
        if not gambar:
            continue
        n_foto += len(gambar)
        for bagian in os.path.normpath(dirpath).split(os.sep)[::-1]:
            m = re.match(r'^(?:toko|foto)[\s_-]*(\d+)$', bagian.strip(), re.I)
            if m:
                toko_ada.add('toko' + m.group(1))
                break

    # berapa SKU pada rentang ini yang sudah terdaftar di sku.csv
    n_sku = 0
    try:
        for j, seri_map in inti.baca_sku().items():
            if j != jenis:
                continue
            for desain in seri_map.values():
                for d in desain:
                    nomor = inti.nomor_sku(d['sku'])
                    if nomor and dari <= nomor <= sampai:
                        n_sku += 1
    except SystemExit:
        pass

    # berapa foto rentang ini yang sudah masuk database / sudah di GitHub
    n_db = n_unggah = 0
    if os.path.exists(inti.DB_PATH):
        db = gudang.buka(inti.DB_PATH)
        for r in db.execute(
                'SELECT kunci, diunggah FROM foto WHERE jenis = ?', (jenis,)):
            nomor = inti.nomor_sku(r['kunci'])
            if nomor and dari <= nomor <= sampai:
                n_db += 1
                n_unggah += r['diunggah'] or 0
        db.close()

    # urutan ini penting: foto boleh sudah terupload, tapi tanpa SKU di sku.csv
    # listing-nya tetap tidak bisa dibuat, jadi jangan disebut siap
    if n_foto == 0:
        keadaan, label = 'kosong', 'belum ada foto'
    elif n_sku == 0:
        keadaan, label = 'tanpasku', 'SKU belum diimpor'
    elif n_unggah and n_unggah >= n_db and n_db >= n_sku:
        keadaan, label = 'siap', 'siap dibuat listing'
    elif n_db:
        keadaan, label = 'sebagian', 'sebagian diupload'
    else:
        keadaan, label = 'baru', 'foto ada, belum diproses'

    hasil = {'path': path, 'foto': n_foto, 'toko': sorted(toko_ada), 'sku': n_sku,
             'db': n_db, 'unggah': n_unggah, 'keadaan': keadaan, 'label': label}
    SINGGAHAN[path] = hasil
    simpan_cache()
    return hasil


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
    }


def simpan_pengaturan(cfg, badan):
    """Simpan perubahan ke config.json setelah diperiksa."""
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
            cfg['jenis'][nama][kunci] = nilai
        if 'spec' in isi:
            cfg['jenis'][nama]['spec'] = isi['spec']
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
            cfg['profil'][profil]['deskripsi'][jenis] = teks
        for jenis, pasangan in (isi.get('judul') or {}).items():
            if jenis in cfg['profil'][profil]['judul'] and pasangan:
                cfg['profil'][profil]['judul'][jenis] = [list(p[:2]) for p in pasangan]

    if galat:
        return {'ok': False, 'galat': galat}
    with open(inti.CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
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
                path, push = badan['path'], bool(badan.get('push', True))

                def lapor(tahap, n, total):
                    SIBUK.update(tahap=tahap, n=n, total=total)

                ok = di_latar('unggah', lambda: (
                    modul_unggah.proses(inti, cfg, path, push=push, lapor=lapor),
                    SINGGAHAN.clear()))
                return self._kirim({'mulai': ok})
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
