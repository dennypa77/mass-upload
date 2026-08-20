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
import json, os, re, subprocess, sys, threading, traceback, webbrowser
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


def laporan_cek(cfg):
    """Semua yang dibutuhkan untuk menghasilkan Excel, per berkas keluaran."""
    data = inti.baca_sku()
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
            rinci.append({
                'judul': L['judul'],
                'jenis': L['jenis'],
                'varian': len(L['desain']),
                'foto': bool(L['utama'][0]),
                'foto_varian': bool(L['per_varian'][0]),
                'panjang_judul': len(L['judul']),
                'panjang_deskripsi': len(L['deskripsi']),
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
                return self._kirim(HALAMAN, 'text/html; charset=utf-8')
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
                temuan, tanpa_seri, tak = modul_unggah.deteksi(inti, cfg, badan['path'])
                rekap = [{'toko': k[0], 'jenis': k[1], 'seri': k[2], 'n': v}
                         for k, v in sorted(modul_unggah._rekap(temuan).items())]
                return self._kirim({'jumlah': len(temuan), 'rekap': rekap,
                                    'tanpa_seri': sorted(set(tanpa_seri))[:10],
                                    'dilewati': len(tak),
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

                def kerja():
                    if nama == 'cek':
                        inti.perintah_cek(cfg, inti.baca_sku())
                    elif nama == 'build':
                        inti.perintah_build(cfg, inti.baca_sku())
                    elif nama == 'url':
                        inti.perintah_url(cfg, inti.baca_sku())
                    elif nama == 'impor':
                        inti.perintah_impor(cfg, badan['sumber'])
                        SINGGAHAN.clear()
                return self._kirim({'mulai': di_latar(nama, kerja)})
            if self.path == '/api/cek':
                return self._kirim({'berkas': laporan_cek(cfg)})
            if self.path == '/api/pilih':
                jalur = dialog_folder(badan.get('awal') or '')
                return self._kirim({'path': jalur})
            if self.path == '/api/sumber':
                # simpan folder sumber tiap jenis produk
                for jenis, jalur in (badan.get('jenis') or {}).items():
                    if jenis in cfg['jenis']:
                        cfg['jenis'][jenis]['path_drive'] = (jalur or '').strip() or None
                if badan.get('root') is not None:
                    cfg['foto']['root'] = (badan['root'] or '').strip() or None
                with open(inti.CONFIG, 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
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
                'base_url': cfg['foto'].get('base_url') or '',
                'toko': cfg['toko'], 'sku': sku, 'db': n_db, 'unggah': n_unggah,
                'ringkasan': rinci, 'output': n_out}


HALAMAN = r"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<title>Shopee Mass Upload</title>
<style>
:root{--bg:#1b1d21;--kartu:#24272c;--garis:#33373d;--teks:#e6e6e6;--redup:#9aa0a6;
--biru:#4c9aff;--hijau:#4caf6d;--kuning:#e0b64a;--merah:#e06c5f;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--teks);
font:14px/1.5 "Segoe UI",system-ui,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--garis);display:flex;
align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:17px;margin:0}
.jalur{color:var(--redup);font-size:12px}
main{display:grid;grid-template-columns:minmax(420px,1.4fr) 1fr;gap:14px;padding:14px 20px}
@media(max-width:1100px){main{grid-template-columns:1fr}}
.kartu{background:var(--kartu);border:1px solid var(--garis);border-radius:8px;overflow:hidden}
.kartu>h2{font-size:13px;margin:0;padding:10px 14px;border-bottom:1px solid var(--garis);
color:var(--redup);font-weight:600;letter-spacing:.4px;text-transform:uppercase}
.isi{padding:12px 14px}
button{background:#2f333a;color:var(--teks);border:1px solid var(--garis);border-radius:6px;
padding:7px 13px;font:inherit;cursor:pointer}
button:hover:not(:disabled){background:#3a3f47;border-color:#4a505a}
button:disabled{opacity:.45;cursor:not-allowed}
button.utama{background:var(--biru);border-color:var(--biru);color:#08111f;font-weight:600}
input[type=text]{background:#1b1d21;border:1px solid var(--garis);color:var(--teks);
border-radius:6px;padding:7px 10px;font:inherit;width:100%}
label.centang{display:inline-flex;align-items:center;gap:6px;cursor:pointer}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #2b2f35;font-size:13px}
th{color:var(--redup);font-weight:600;font-size:11px;text-transform:uppercase}
tr.folder{cursor:pointer}
tr.folder:hover{background:#2b2f36}
tr.folder.pilih{background:#2d3a4d;outline:1px solid var(--biru)}
.jenis td{background:#20232700;color:var(--biru);font-weight:600;padding-top:12px}
.ikon{margin-right:7px}
.panah{display:inline-block;width:14px;color:var(--redup)}
.tanda{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;
border:1px solid transparent;white-space:nowrap}
.t-kosong{color:var(--redup);border-color:#3c4046}
.t-baru{color:var(--kuning);border-color:#5c4a20;background:#3a2f16}
.t-sebagian{color:var(--biru);border-color:#264468;background:#1d2c40}
.t-siap{color:var(--hijau);border-color:#2b5236;background:#1c3324}
.t-tanpasku{color:var(--merah);border-color:#5c2b26;background:#3a1e1b}
.angka{color:var(--redup);font-variant-numeric:tabular-nums}
#log{background:#141619;color:#cfd3d8;font:12px/1.55 Consolas,monospace;padding:10px 12px;
height:280px;overflow:auto;white-space:pre-wrap;word-break:break-word}
#log .g{color:var(--merah)} #log .p{color:var(--kuning)}
.bar{height:6px;background:#2b2f35;border-radius:3px;overflow:hidden;margin-top:8px}
.bar>div{height:100%;background:var(--biru);width:0;transition:width .2s}
.baris{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.redup{color:var(--redup)}
.kecil{font-size:12px}
ul.ringkas{margin:6px 0 0;padding-left:18px;color:var(--redup);font-size:12px}
</style></head><body>

<header>
  <div><h1>Shopee Mass Upload</h1><div class="jalur" id="akar"></div></div>
  <div style="flex:1"></div>
  <div id="ringkas" class="kecil redup"></div>
</header>

<main>
  <section class="kartu" style="grid-column:1/-1">
    <h2>Sumber data — folder foto tiap jenis produk</h2>
    <div class="isi">
      <div id="sumber"></div>
      <div class="kecil redup" style="margin-top:8px">
        Tiap jenis produk boleh berada di drive atau komputer yang berbeda.
        Tekan “Pilih…” untuk membuka penjelajah folder, atau tempel path langsung.
      </div>
      <div class="baris" style="margin-top:10px">
        <button class="utama" onclick="simpanSumber()">Simpan sumber</button>
        <span id="statusSumber" class="kecil redup"></span>
      </div>
    </div>
  </section>

  <section class="kartu">
    <h2>Folder foto produk</h2>
    <div class="isi">
      <div class="baris">
        <input type="text" id="cari" placeholder="saring nama folder, mis. 00751"
               style="flex:1;max-width:280px" oninput="saring()">
        <label class="centang kecil"><input type="checkbox" id="hanyaFoto" onchange="saring()">
          hanya yang ada fotonya</label>
        <div style="flex:1"></div>
        <span class="kecil redup" id="hitung"></span>
        <button onclick="muatPohon()">Muat ulang</button>
      </div>
      <div class="kecil redup" id="rootdrive" style="margin-top:6px"></div>
    </div>
    <div style="max-height:520px;overflow:auto">
      <table id="pohon"><tbody></tbody></table>
    </div>
  </section>

  <div style="display:flex;flex-direction:column;gap:14px">
    <section class="kartu">
      <h2>Folder terpilih</h2>
      <div class="isi" id="terpilih"><span class="redup">Klik salah satu folder di sebelah kiri.</span></div>
    </section>

    <section class="kartu">
      <h2>Pengaturan &amp; langkah lain</h2>
      <div class="isi">
        <div class="kecil redup" style="margin-bottom:4px">Alamat dasar foto (akar repo GitHub)</div>
        <div class="baris">
          <input type="text" id="baseurl" style="flex:1">
          <button onclick="simpanUrl()">Simpan</button>
        </div>
        <div class="baris" style="margin-top:12px">
          <button onclick="perintah('cek')">Cek kesiapan</button>
          <button onclick="perintah('build')">Buat Excel</button>
          <button onclick="perintah('url')">Ekspor URL</button>
        </div>
      </div>
    </section>

    <section class="kartu">
      <h2>Kesiapan berkas Excel</h2>
      <div class="isi" id="cek"><span class="redup">Tekan “Cek kesiapan”.</span></div>
    </section>
  </div>
</main>

<section class="kartu" style="margin:0 20px 20px">
  <h2>Log <span id="tahap" class="redup" style="text-transform:none;font-weight:400"></span></h2>
  <div class="bar" style="margin:0"><div id="barKerja"></div></div>
  <div id="log"></div>
</section>

<script>
let sejak=0, pilih=null, pengamat=null;
const $=s=>document.querySelector(s);
const api=(u,d)=>fetch(u,d?{method:'POST',body:JSON.stringify(d)}:undefined).then(r=>r.json());

function muatStatus(){
  api('/api/status').then(s=>{
    $('#akar').textContent=s.akar;
    $('#rootdrive').textContent=s.root_drive||'';
    if(document.activeElement!==$('#baseurl')) $('#baseurl').value=s.base_url;
    if(s.sumber)gambarSumber(s.sumber);
    const sku=s.sku?`${s.sku.jumlah} SKU · ${s.sku.seri} seri`:'sku.csv belum ada';
    $('#ringkas').innerHTML=`${sku} &nbsp;•&nbsp; database ${s.db} foto (${s.unggah} di GitHub)`
      +` &nbsp;•&nbsp; ${s.output} berkas Excel`;
  });
}

// Folder produk ada ratusan, jadi statusnya baru diminta ketika barisnya
// benar-benar kelihatan di layar. Hasilnya disimpan di cache server.
function amati(tr){
  if(!pengamat)pengamat=new IntersectionObserver(es=>{
    es.forEach(e=>{ if(e.isIntersecting){ pengamat.unobserve(e.target); isiStatus(e.target); } });
  },{rootMargin:'150px'});
  pengamat.observe(tr);
}

function isiStatus(tr,segar){
  const d=tr.dataset;
  api('/api/folder',{jenis:d.jenis,path:d.path,dari:+d.dari,sampai:+d.sampai,segar:!!segar})
   .then(s=>{
     tr.dataset.status=JSON.stringify(s);
     tr.querySelector('[data-k=foto]').textContent=s.foto?s.foto+' foto':'—';
     tr.querySelector('[data-k=sku]').textContent=s.sku?s.sku+' SKU':'';
     tr.querySelector('[data-k=tanda]').innerHTML=
       `<span class="tanda t-${s.keadaan}">${s.label}</span>`;
     if(tr.classList.contains('pilih'))tulisDetail(s);
     saring();
   }).catch(()=>{});
}

function gambarSumber(daftar){
  $('#sumber').innerHTML=daftar.map((s,i)=>`
    <div class="baris" style="margin-bottom:6px">
      <span style="width:110px" class="kecil"><b>${s.jenis}</b></span>
      <input type="text" class="jalurSumber" data-jenis="${s.jenis}" value="${s.path||''}"
             style="flex:1">
      <button onclick="pilihSumber(${i})">Pilih…</button>
      <span class="tanda ${s.ada?'t-siap':'t-tanpasku'}">${s.ada?'ditemukan':'tidak ada'}</span>
    </div>`).join('');
}

function pilihSumber(i){
  const kotak=document.querySelectorAll('.jalurSumber')[i];
  $('#statusSumber').textContent='menunggu dialog…';
  api('/api/pilih',{awal:kotak.value}).then(r=>{
    $('#statusSumber').textContent='';
    if(r.path)kotak.value=r.path;
  });
}

function simpanSumber(){
  const isi={};
  document.querySelectorAll('.jalurSumber').forEach(k=>isi[k.dataset.jenis]=k.value.trim());
  $('#statusSumber').textContent='menyimpan…';
  api('/api/sumber',{jenis:isi}).then(()=>{
    $('#statusSumber').textContent='tersimpan';
    setTimeout(()=>$('#statusSumber').textContent='',2000);
    muatStatus(); muatPohon();
  });
}

function muatPohon(){
  if(pengamat){pengamat.disconnect();pengamat=null;}
  api('/api/pohon').then(d=>{
    const tb=$('#pohon tbody'); tb.innerHTML=''; let total=0;
    d.jenis.forEach((j,idx)=>{
      const kel='j'+idx;
      const th=document.createElement('tr'); th.className='jenis';
      th.innerHTML=`<td colspan="4">
        <span class="panah">▾</span>
        <span class="ikon">🗂️</span><b>${j.jenis}</b>
        <span class="redup" style="font-weight:400">— ${j.folder.length} folder</span>
        ${j.ada?'':'<span class="tanda t-tanpasku">folder sumber tidak ditemukan</span>'}
        <span class="redup kecil" style="font-weight:400;margin-left:8px">${j.akar}</span>
      </td>`;
      th.onclick=()=>{
        const buka=th.dataset.buka!=='0'; th.dataset.buka=buka?'0':'1';
        th.querySelector('.panah').textContent=buka?'▸':'▾';
        saring();
      };
      tb.appendChild(th);
      j.folder.forEach(f=>{
        total++;
        const r=document.createElement('tr');
        r.className='folder '+kel;
        Object.assign(r.dataset,{jenis:j.jenis,path:f.path,dari:f.dari,sampai:f.sampai,
                                 nama:f.nama,kel:kel});
        r.innerHTML=`<td><span class="ikon" style="margin-left:14px">📂</span>${f.nama}
            <span class="redup kecil">· ${j.jenis}</span></td>
          <td class="angka" data-k="foto">…</td>
          <td class="angka" data-k="sku"></td>
          <td data-k="tanda"></td>`;
        r.onclick=()=>pilihFolder(r);
        tb.appendChild(r); amati(r);
      });
    });
    $('#hitung').textContent=total+' folder';
    saring();
  });
}

function saring(){
  const q=$('#cari').value.trim().toLowerCase();
  const hanya=$('#hanyaFoto').checked;
  let tampak=0;
  document.querySelectorAll('tr.jenis').forEach(th=>{
    const buka=th.dataset.buka!=='0';
    let anak=th.nextElementSibling;
    while(anak&&anak.classList.contains('folder')){
      const s=anak.dataset.status?JSON.parse(anak.dataset.status):null;
      let ok=buka;
      if(ok&&q) ok=anak.dataset.nama.toLowerCase().includes(q);
      if(ok&&hanya) ok=!!(s&&s.foto);
      anak.style.display=ok?'':'none';
      if(ok)tampak++;
      anak=anak.nextElementSibling;
    }
  });
  $('#hitung').textContent=tampak+' folder tampil';
}

function pilihFolder(tr){
  document.querySelectorAll('tr.folder').forEach(x=>x.classList.remove('pilih'));
  tr.classList.add('pilih');
  pilih={jenis:tr.dataset.jenis,path:tr.dataset.path,nama:tr.dataset.nama,tr:tr};
  $('#terpilih').innerHTML=`<div><b>${tr.dataset.nama}</b>
      <span class="redup kecil">${tr.dataset.jenis}</span></div>
    <div class="kecil redup" style="word-break:break-all">${tr.dataset.path}</div>
    <div id="detail" class="kecil" style="margin-top:8px">…</div>
    <div class="baris" style="margin-top:10px">
      <button onclick="deteksi()">Deteksi isi</button>
      <button onclick="isiStatus(pilih.tr,true)">Hitung ulang</button>
      <label class="centang"><input type="checkbox" id="push" checked> upload ke GitHub</label>
      <button class="utama" onclick="proses()">Proses folder ini</button>
    </div>`;
  const s=tr.dataset.status?JSON.parse(tr.dataset.status):null;
  if(s)tulisDetail(s); else isiStatus(tr);
}

function tulisDetail(s){
  const el=$('#detail'); if(!el)return;
  el.innerHTML=`${s.foto} foto · ${s.toko.length} toko (${s.toko.join(', ')||'-'})
    · ${s.sku} SKU terdaftar di sku.csv
    · <b>${s.unggah}/${s.db}</b> sudah di GitHub`;
}

function deteksi(){
  if(!pilih)return;
  $('#detail').textContent='mendeteksi…';
  api('/api/deteksi',{path:pilih.path}).then(d=>{
    if(!d.jumlah){$('#detail').innerHTML=
      `<span style="color:var(--merah)">Tidak ada foto yang dikenali (${d.dilewati} berkas dilewati).</span>`;return;}
    let h=`<b>${d.jumlah} foto dikenali</b><ul class="ringkas">`;
    d.rekap.forEach(r=>h+=`<li>${r.toko} · ${r.jenis} · ${r.seri} — ${r.n} foto</li>`);
    h+='</ul>';
    if(d.tanpa_seri.length)h+=`<div style="color:var(--kuning);margin-top:6px">
      ! SKU belum ada di sku.csv: ${d.tanpa_seri.join(', ')}</div>`;
    if(d.contoh)h+=`<div class="redup" style="margin-top:6px">contoh tujuan: ${d.contoh}</div>`;
    $('#detail').innerHTML=h;
  });
}

function proses(){
  if(!pilih)return;
  const push=$('#push').checked;
  if(!confirm(`Proses folder:\n${pilih.path}\n\n`+
     (push?'Foto akan disalin, lalu di-commit dan di-push ke GitHub.':
           'Foto hanya disalin, tanpa upload.')))return;
  api('/api/unggah',{path:pilih.path,push:push}).then(r=>{
    if(!r.mulai)alert('Masih ada pekerjaan lain yang berjalan.');
  });
}

function perintah(n){api('/api/perintah',{perintah:n}).then(r=>{
  if(!r.mulai)alert('Masih ada pekerjaan lain yang berjalan.');
  if(n==='cek')setTimeout(muatCek,600);});}

function muatCek(){
  $('#cek').innerHTML='<span class="redup">memeriksa…</span>';
  api('/api/cek',{}).then(d=>{
    let h='<table><tr><th>Berkas</th><th>Listing</th><th>Berfoto</th><th>Masalah</th></tr>';
    d.berkas.forEach(b=>{
      const foto=b.listing.filter(l=>l.foto).length;
      const w=b.peringatan.length;
      h+=`<tr><td>${b.berkas.replace('.xlsx','')}</td>
        <td class="angka">${b.listing.length}</td>
        <td class="angka">${foto}/${b.listing.length}</td>
        <td><span class="tanda ${w?'t-baru':'t-siap'}">${w?w+' peringatan':'siap'}</span></td></tr>`;
    });
    $('#cek').innerHTML=h+'</table>';
  }).catch(()=>$('#cek').innerHTML='<span style="color:var(--merah)">gagal memeriksa</span>');
}

function simpanUrl(){
  api('/api/config',{base_url:$('#baseurl').value}).then(muatStatus);
}

setInterval(()=>{
  api('/api/log?sejak='+sejak).then(d=>{
    if(d.baris.length){
      const el=$('#log'); const bawah=el.scrollTop+el.clientHeight>=el.scrollHeight-30;
      d.baris.forEach(b=>{
        const s=document.createElement('span');
        s.className=/^\s*!|^\[error|^\[berhenti/.test(b)?'g':(/^>>>|^\[selesai/.test(b)?'p':'');
        s.textContent=b+'\n'; el.appendChild(s);
      });
      sejak=d.total; if(bawah)el.scrollTop=el.scrollHeight;
    }
    $('#tahap').textContent=d.sibuk
      ? `— ${d.sibuk}${d.tahap?': '+d.tahap+' '+d.n+'/'+d.total_maju:''}` : '';
    $('#barKerja').style.width=(d.sibuk&&d.total_maju?d.n/d.total_maju*100:0)+'%';
    if(!d.sibuk&&window._tadiSibuk){muatStatus();}
    window._tadiSibuk=!!d.sibuk;
  }).catch(()=>{});
},700);

muatStatus(); muatPohon();
</script></body></html>
"""


def main():
    alamat = 'http://127.0.0.1:{}'.format(PORT)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Penangan)
    print('Tools Shopee Mass Upload berjalan di {}'.format(alamat))
    print('Tutup jendela ini untuk menghentikan server.')
    muat_cache()
    catat('[siap] buka {} di browser'.format(alamat))
    threading.Timer(0.8, lambda: webbrowser.open(alamat)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\ndihentikan')


if __name__ == '__main__':
    main()
