# -*- coding: utf-8 -*-
"""Laporkan nomor folder produk tertinggi di Google Drive per jenis ke ERP.

Kenapa: penomoran produk baru di Garapan ERP hanya bisa melihat Garapan, Master Desain,
dan katalog Konsol. Tim sering sudah mengerjakan produk di Drive (CDR + FOTO PRODUK)
sebelum katalognya diperbarui — 2026-10-11 Drive sudah sampai PRODUK 31451 - 31500
sementara katalog baru 31000, dan 12 produk Garapan mendapat nomor yang sudah dipakai.
ERP tidak bisa membaca Drive, jadi PC host yang memindai dan melapor; Garapan memakai
angka ini sebagai batas bawah nomor berikutnya.

Disimpan di rnd_listing_pengaturan (bagian 'desain_host', kunci 'nomor_drive'):
  {"JIBBITZ": 31500, "PIN AKRILIK": 31500, "PIN BUTTON": 31500, "diperbarui": ..., "pc": ...}
"""
import os
import re
import time

import erp

# Aturan nama folder sama dengan antrean.cari_folder: hanya "PRODUK a - b" persis.
POLA = re.compile(r'^PRODUK\s+0*(\d+)\s*-\s*0*(\d+)$', re.I)
JEDA_DETIK = 600
_terakhir = [0.0]


def pindai(inti, cfg):
    """{jenis: nomor tertinggi} dari semua akar folder jenis di PC ini."""
    hasil = {}
    for jenis in (cfg.get('jenis') or {}):
        tertinggi = 0
        for akar in inti.dirs_jenis(cfg, jenis):
            if not os.path.isdir(akar):
                continue
            for nama in os.listdir(akar):
                m = POLA.match(nama.strip())
                if m:
                    tertinggi = max(tertinggi, int(m.group(2)))
        if tertinggi:
            hasil[jenis] = tertinggi
    return hasil


def lapor(sambungan, inti, cfg, paksa=False):
    """Pindai + kirim ke ERP, paling sering tiap JEDA_DETIK. Kembalikan hasil atau None."""
    if not paksa and time.time() - _terakhir[0] < JEDA_DETIK:
        return None
    _terakhir[0] = time.time()
    hasil = pindai(inti, cfg)
    if not hasil:
        return None
    nilai = dict(hasil, diperbarui=time.strftime('%Y-%m-%dT%H:%M:%S'),
                 pc=os.environ.get('COMPUTERNAME', ''))
    kode, data = erp.panggil(
        sambungan, 'POST', 'rnd_listing_pengaturan?on_conflict=bagian,kunci',
        [{'bagian': 'desain_host', 'kunci': 'nomor_drive', 'nilai': nilai,
          'catatan': 'Nomor folder PRODUK tertinggi di Drive per jenis (tools/nomor_drive.py).'}],
        prefer='resolution=merge-duplicates,return=minimal')
    if kode >= 300:
        print('[nomor] gagal melapor nomor Drive ke ERP: {} {}'.format(kode, str(data)[:200]))
        return None
    return hasil
