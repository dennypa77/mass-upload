# -*- coding: utf-8 -*-
"""Pekerjaan 'ekspor' dari Konsol Mass Upload ERP: buat berkas Excel Shopee di PC ini.

Kenapa di PC, bukan di server ERP: template Shopee berisi ±13 ribu aturan validasi
data dan tanda tangan di baris 2. Pustaka Excel di ERP (exceljs) membuang hampir
semuanya saat menulis ulang; pembuat yang sudah terbukti adalah perintah "build"
di tools ini (openpyxl). Jadi ERP hanya MENGANTRE daftar folder, PC menjalankan
build, lalu mengunggah berkasnya ke R2 dan menitipkan alamatnya di baris antrean
(`parameter.hasil`) — itulah yang ditampilkan ERP sebagai tombol unduh.

Baris antrean:
    pekerjaan = 'ekspor'
    parameter = {"folder": [{"jenis": "JIBBITZ", "dari": 31601, "sampai": 31650}, ...]}
Sesudah selesai, parameter mendapat:
    "hasil": [{"nama": "...xlsx", "path": "ekspor/<id>-<acak>/...xlsx", "ukuran": 91608}]
"""
import base64
import contextlib
import hashlib
import io
import os
import secrets
import sys
import tempfile

import erp
import foto_r2
import r2 as modul_r2

TIPE_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class _Tee(io.TextIOBase):
    """Teruskan ke jendela Desain Host sambil menyimpan salinan untuk pesan antrean."""

    def __init__(self, asli):
        self.asli = asli
        self.salinan = io.StringIO()

    def write(self, s):
        self.asli.write(s)
        self.salinan.write(s)
        return len(s)

    def flush(self):
        self.asli.flush()


def baca_folder(param):
    """Validasi parameter.folder -> [{'jenis','dari','sampai'}]."""
    hasil = []
    for f in (param or {}).get('folder') or []:
        try:
            jenis = str(f['jenis']).strip().upper()
            dari, sampai = int(f['dari']), int(f['sampai'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('folder tidak sah: {!r}'.format(f))
        if not jenis or dari < 1 or sampai < dari:
            raise ValueError('folder tidak sah: {!r}'.format(f))
        hasil.append({'jenis': jenis, 'dari': dari, 'sampai': sampai})
    if not hasil:
        raise ValueError('tidak ada folder yang dipilih')
    return hasil


def sku_katalog(sambungan, folder):
    """SKU di katalog Konsol ERP untuk rentang folder — supaya produk baru dari Garapan
    yang belum ada di data/sku.csv ikut terbaca build."""
    skus = []
    for f in folder:
        kode, data = erp.panggil(
            sambungan, 'GET',
            'rnd_listing_sku?jenis=eq.{}&nomor=gte.{}&nomor=lte.{}&select=sku&limit=5000'.format(
                erp_kutip(f['jenis']), f['dari'], f['sampai']))
        if kode >= 300 or not isinstance(data, list):
            raise RuntimeError('katalog ERP tidak terbaca ({})'.format(kode))
        skus += [str(r['sku']).upper() for r in data if r.get('sku')]
    return sorted(set(skus))


def erp_kutip(teks):
    from urllib.parse import quote
    return quote('"{}"'.format(teks), safe='')


def ringkas_log(teks):
    """Baris log build yang penting untuk operator (berkas jadi + peringatan)."""
    penting = [b.strip() for b in teks.splitlines()
               if b.strip().startswith('[build]') or b.strip().startswith('!')]
    return ' | '.join(penting)[-1500:]


def tarik_template(sambungan, inti, cfg):
    """Samakan template di PC dengan yang dipasang di ERP (rnd_listing_template).

    Shopee menolak berkas dari template lama ("Please download the newest template").
    Operator memasang template baru di Konsol ERP, tapi build berjalan di PC dari
    template/*.xlsx — tanpa langkah ini template ERP tidak pernah sampai ke build.
    Berkas disalin byte-per-byte (sha256 diperiksa) lewat pasang_template, jadi
    tanda tangannya utuh dan versi lama tetap dicadangkan ke .bak.
    Kembalikan daftar kunci yang diperbarui.
    """
    kode, data = erp.panggil(sambungan, 'GET',
                             'rnd_listing_template?select=kunci,nama_berkas,sha256,berkas_base64')
    if kode >= 300 or not isinstance(data, list):
        raise RuntimeError('template ERP tidak terbaca ({})'.format(kode))
    diperbarui = []
    for t in data:
        rel = (cfg.get('template') or {}).get(t.get('kunci'))
        if not rel or not t.get('berkas_base64'):
            continue
        tujuan = os.path.join(inti.AKAR, rel)
        if os.path.exists(tujuan):
            with open(tujuan, 'rb') as f:
                if hashlib.sha256(f.read()).hexdigest() == t.get('sha256'):
                    continue
        isi = base64.b64decode(t['berkas_base64'])
        if hashlib.sha256(isi).hexdigest() != t.get('sha256'):
            raise RuntimeError('template {} dari ERP rusak (sha256 tidak cocok)'.format(t['kunci']))
        sementara = os.path.join(tempfile.mkdtemp(prefix='template-erp-'),
                                 os.path.basename(t.get('nama_berkas') or 'template.xlsx'))
        with open(sementara, 'wb') as f:
            f.write(isi)
        try:
            hasil = inti.pasang_template(cfg, sementara)
        except SystemExit as e:  # pasang_template memakai sys.exit untuk galat
            raise RuntimeError('gagal memasang template {} dari ERP: {}'.format(t['kunci'], e))
        if hasil.get('kunci') != t['kunci']:
            raise RuntimeError('template ERP "{}" terpasang sebagai {}'.format(t['kunci'], hasil.get('kunci')))
        diperbarui.append(t['kunci'])
    return diperbarui


def kerjakan(sambungan, inti, cfg, baris, lapor, demo=False):
    tanda = '#{} ekspor'.format(baris['id'])
    param = baris.get('parameter') or {}
    if isinstance(param, str):
        import json
        param = json.loads(param or '{}')
    try:
        folder = baca_folder(param)
        if demo:
            print('[ekspor] {} (demo) {}'.format(tanda, folder))
            return
        template_baru = tarik_template(sambungan, inti, cfg)
        if template_baru:
            print('[ekspor] template diperbarui dari ERP: {}'.format(', '.join(template_baru)))
        ditambah = foto_r2.sinkron_sku(sambungan, inti, cfg, sku_katalog(sambungan, folder))
        if ditambah:
            print('[ekspor] {} SKU baru dari katalog ERP digabung ke data/sku.csv'.format(ditambah))
        data = inti.saring_lingkup(inti.baca_sku(), folder)
        if not data:
            raise RuntimeError('tidak ada SKU pada folder yang dipilih')

        sub = os.path.join('erp', 'ekspor-{}'.format(baris['id']))
        tee = _Tee(sys.stdout)
        with contextlib.redirect_stdout(tee):
            inti.perintah_build(cfg, data, sub=sub)
        tujuan = os.path.join(inti.dir_keluaran(), sub)
        berkas = sorted(b for b in os.listdir(tujuan) if b.lower().endswith('.xlsx')) \
            if os.path.isdir(tujuan) else []
        if not berkas:
            raise RuntimeError('build tidak menghasilkan berkas. ' + ringkas_log(tee.salinan.getvalue()))

        # Nama acak di jalur: bucket ini publik lewat domain, jadi alamat berkas tidak boleh
        # bisa ditebak dari nomor antrean saja.
        klien = modul_r2.dari_config(cfg)
        awalan = 'ekspor/{}-{}'.format(baris['id'], secrets.token_hex(8))
        hasil = []
        for b in berkas:
            jalur = os.path.join(tujuan, b)
            kunci = '{}/{}'.format(awalan, b)
            ukuran = klien.unggah(kunci, jalur, tipe=TIPE_XLSX)
            hasil.append({'nama': b, 'path': kunci, 'ukuran': ukuran})
            print('[ekspor] terunggah {}'.format(kunci))
        pesan = '{} berkas Excel jadi.{} {}'.format(
            len(hasil),
            ' Template diperbarui dari ERP: {}.'.format(', '.join(template_baru)) if template_baru else '',
            ringkas_log(tee.salinan.getvalue()))
        lapor(sambungan, baris['id'], 'berhasil', pesan, parameter=dict(param, hasil=hasil))
        print('[ekspor] {} selesai: {} berkas'.format(tanda, len(hasil)))
    except Exception as e:  # noqa: BLE001
        print('[ekspor] {} GAGAL: {}'.format(tanda, e))
        if not demo:
            lapor(sambungan, baris['id'], 'gagal', 'Ekspor gagal: {}'.format(e))

