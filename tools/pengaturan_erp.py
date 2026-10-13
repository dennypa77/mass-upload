# -*- coding: utf-8 -*-
"""Pengaturan listing dari Konsol ERP (rnd_listing_pengaturan) digabung ke cfg PC.

Sejak 2026-10-12 operator menyunting harga, kategori Shopee, spec, judul & deskripsi
di tab Pengaturan Produk ERP — termasuk lini baru yang tidak pernah ada di
tools/config.json (Coaster Akrilik, Shoe Charm, Magnet Akrilik). Tanpa langkah ini
build & unggah di PC tetap memakai config.json lama dan suntingan di ERP tidak pernah
sampai ke berkas Excel.

ERP menang untuk nilai yang ADA di ERP; yang khusus PC (path_drive dari data/lokal.json,
pola foto lama) tetap dari cfg. Hanya mengubah cfg di memori — config.json tidak ditulis.
"""
import erp
from erp import inti  # shopee_mass_upload; lewat erp supaya tidak ada impor melingkar

WAJIB_JENIS = ('template', 'kategori', 'harga_paket', 'min_order', 'berat_gram', 'stok', 'spec')


def gabung(sambungan, cfg):
    """Tarik bagian jenis & profil dari ERP ke cfg (di tempat). Kembalikan jenis yang baru."""
    kode, data = erp.panggil(sambungan, 'GET',
                             'rnd_listing_pengaturan?bagian=in.(jenis,profil,toko)&select=bagian,kunci,nilai')
    if kode >= 300 or not isinstance(data, list):
        raise RuntimeError('pengaturan ERP tidak terbaca ({})'.format(kode))
    baru = []
    toko_erp = []
    for r in data:
        nilai = r.get('nilai') or {}
        if r['bagian'] == 'jenis':
            semua = cfg.setdefault('jenis', {})
            if r['kunci'] not in semua:
                baru.append(r['kunci'])
            lama = dict(semua.get(r['kunci']) or {})
            lama.update({k: v for k, v in nilai.items() if v is not None})
            semua[r['kunci']] = lama
        elif r['bagian'] == 'toko':
            # Daftar toko kini disunting di Konsol ERP, bukan config.json: sejak
            # 2026-10-13 toko bisa ditambah dari web, dan PC yang masih memakai
            # daftar lama akan membuat berkas untuk toko yang sudah tidak ada
            # (atau melewatkan toko baru sama sekali).
            toko_erp.append(dict(nilai, folder_foto=nilai.get('folder_foto') or r['kunci']))
        elif r['bagian'] == 'profil':
            p = cfg.setdefault('profil', {}).setdefault(r['kunci'], {})
            for sub in ('judul', 'deskripsi'):
                if isinstance(nilai.get(sub), dict):
                    p.setdefault(sub, {}).update(nilai[sub])
    # Urut menurut nomor folder supaya urutan berkas ekspor tidak berubah-ubah
    # tiap kali PostgREST mengembalikan baris dengan urutan berbeda.
    if toko_erp:
        def _nomor(t):
            sisa = ''.join(c for c in str(t.get('folder_foto') or '') if c.isdigit())
            return int(sisa) if sisa else 0
        cfg['toko'] = sorted(toko_erp, key=_nomor)

    # Template baru yang dipilih di ERP (mis. cabang kategori lain) belum punya path di
    # config.json — tetapkan path bawaan supaya tarik_template bisa memasangnya.
    tpl = cfg.setdefault('template', {})
    for j in cfg.get('jenis', {}).values():
        k = j.get('template')
        if k and k not in tpl:
            tpl[k] = 'template/{}.xlsx'.format(k.title())
    return baru


def kekurangan(cfg, daftar_jenis):
    """Daftar isian yang masih kosong untuk jenis-jenis ini — kosong berarti siap ekspor."""
    kurang = []
    for jenis in sorted(set(daftar_jenis)):
        j = (cfg.get('jenis') or {}).get(jenis)
        if not j:
            kurang.append('{}: jenis tidak dikenal'.format(jenis))
            continue
        hilang = [k for k in WAJIB_JENIS if j.get(k) in (None, '', 0)]
        dijual = set(inti.toko_untuk_jenis(cfg, jenis))
        for toko in cfg.get('toko') or []:
            # Toko yang tidak menjual produk ini tidak butuh judul/deskripsi untuknya.
            if toko.get('folder_foto') not in dijual:
                continue
            pr = (cfg.get('profil') or {}).get(toko.get('profil')) or {}
            if not ((pr.get('judul') or {}).get(jenis)):
                hilang.append('judul {}'.format(toko.get('nama')))
            if not str((pr.get('deskripsi') or {}).get(jenis) or '').strip():
                hilang.append('deskripsi {}'.format(toko.get('nama')))
        if hilang:
            kurang.append('{}: {}'.format(jenis, ', '.join(hilang)))
    return kurang
