# -*- coding: utf-8 -*-
"""Basis data foto: menyimpan hasil "file lokal -> URL" secara permanen.

Dipakai supaya URL tidak perlu dibuat ulang tiap kali. Folder yang sudah diproses
tersimpan di sini, dan folder baru tinggal ditambahkan — isinya menumpuk, tidak
saling menimpa.

Tabel `foto`, satu baris per (toko, kunci):
    kunci      JB-0000001  atau  JB-CORTIS-utama1
    toko       toko1                 nama_toko  Graphica Key
    jenis      JIBBITZ               seri       CORTIS
    tipe       varian | utama
    sumber     path asli di Google Drive
    file_lokal path setelah disalin ke foto-upload/
    path_repo  foto-upload/toko1/jibbitz/JB-0000001.png
    url        alamat publik jsDelivr
    ukuran     byte
    diunggah   1 kalau sudah ada di GitHub
    waktu      kapan baris ini ditulis
"""
import os, sqlite3
from datetime import datetime

SKEMA = """
CREATE TABLE IF NOT EXISTS foto (
    toko       TEXT NOT NULL,
    kunci      TEXT NOT NULL,
    nama_toko  TEXT,
    jenis      TEXT,
    seri       TEXT,
    tipe       TEXT,
    sumber     TEXT,
    file_lokal TEXT,
    path_repo  TEXT,
    url        TEXT,
    ukuran     INTEGER,
    diunggah   INTEGER DEFAULT 0,
    waktu      TEXT,
    PRIMARY KEY (toko, kunci)
);
CREATE INDEX IF NOT EXISTS idx_foto_seri ON foto (jenis, seri);
"""

KOLOM = ['toko', 'kunci', 'nama_toko', 'jenis', 'seri', 'tipe', 'sumber',
         'file_lokal', 'path_repo', 'url', 'ukuran', 'diunggah', 'waktu']


def buka(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SKEMA)
    return db


def simpan(db, baris):
    """Tambah/perbarui banyak baris sekaligus. `baris` = daftar dict."""
    waktu = datetime.now().isoformat(timespec='seconds')
    isi = [tuple(b.get(k, waktu if k == 'waktu' else None) for k in KOLOM) for b in baris]
    db.executemany(
        'INSERT INTO foto ({0}) VALUES ({1}) ON CONFLICT(toko, kunci) DO UPDATE SET {2}'.format(
            ', '.join(KOLOM), ', '.join('?' * len(KOLOM)),
            ', '.join('{0}=excluded.{0}'.format(k) for k in KOLOM if k not in ('toko', 'kunci'))),
        isi)
    db.commit()
    return len(isi)


def tandai_terunggah(db, path_repo):
    db.executemany('UPDATE foto SET diunggah=1 WHERE path_repo=?', [(p,) for p in path_repo])
    db.commit()


def ambil(db, toko=None, jenis=None, seri=None, hanya_terunggah=False):
    syarat, nilai = [], []
    for kolom, v in (('toko', toko), ('jenis', jenis), ('seri', seri)):
        if v:
            syarat.append('{} = ?'.format(kolom))
            nilai.append(v)
    if hanya_terunggah:
        syarat.append('diunggah = 1')
    sql = 'SELECT * FROM foto'
    if syarat:
        sql += ' WHERE ' + ' AND '.join(syarat)
    return [dict(r) for r in db.execute(sql + ' ORDER BY toko, jenis, kunci', nilai)]


def peta_url(db):
    """{toko: {KUNCI: url}} — dipakai saat mengisi berkas Excel."""
    hasil = {}
    for r in db.execute('SELECT toko, kunci, url FROM foto WHERE url IS NOT NULL'):
        hasil.setdefault(r['toko'], {})[r['kunci'].upper()] = r['url']
    return hasil


def ringkasan(db):
    return [dict(r) for r in db.execute(
        'SELECT nama_toko, toko, jenis, seri, COUNT(*) n, SUM(diunggah) terunggah '
        'FROM foto GROUP BY toko, jenis, seri ORDER BY toko, jenis, seri')]


def jumlah(db):
    r = db.execute('SELECT COUNT(*) n, COALESCE(SUM(diunggah),0) u FROM foto').fetchone()
    return r['n'], r['u']
