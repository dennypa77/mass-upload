# -*- coding: utf-8 -*-
"""Unggah otomatis foto hasil CorelDRAW ke R2, langsung sesudah pekerjaan foto selesai.

Konsol Mass Upload membaca foto dari bucket R2 (foto-upload/<toko>/<slug>/...), bukan
dari Google Drive. Dulu foto hasil macro berhenti di folder keluaran PC dan harus
diunggah tangan lewat "Proses Folder Ini". Modul ini menjalankan langkah yang sama
(unggah.proses: kenali toko & SKU, namai ulang sampul jadi <PREFIX>-<KODE>-utamaN,
kecilkan > 2 MB, unggah) segera sesudah corel.py menyelesaikan pekerjaan foto.

Satu syarat: unggah.py mengenali seri dari data/sku.csv. Produk baru dari Garapan
ERP belum ada di berkas itu, jadi sebelum mengunggah baris SKU-nya ditarik dari
katalog Konsol di ERP (rnd_listing_sku + rnd_listing_seri) dan DIGABUNG ke
data/sku.csv (inti.tulis_sku, gabung=True — baris lain tidak disentuh).
"""
import os
import re

import erp

POLA_SKU = re.compile(r'^([A-Z]{2}-\d{7})\.(png|jpe?g)$', re.I)


def sku_di_folder(folder):
    """SKU varian (berkas <SKU>.png) di folder hasil dan subfolder tokonya."""
    hasil = set()
    for _, _, berkas in os.walk(folder):
        for b in berkas:
            m = POLA_SKU.match(b)
            if m:
                hasil.add(m.group(1).upper())
    return sorted(hasil)


def sinkron_sku(sambungan, inti, cfg, skus):
    """Tambahkan ke data/sku.csv baris SKU yang belum dikenal, dari katalog ERP.

    Kembalikan jumlah baris yang ditulis. Yang sudah ada dengan seri sama dilewati,
    jadi aman dipanggil berulang.
    """
    if not skus:
        return 0
    try:
        lokal = {}
        for _jenis, seri_map in inti.baca_sku().items():
            for seri, desain in seri_map.items():
                for d in desain:
                    lokal[d['sku'].upper()] = seri.strip().upper()
    except SystemExit:
        lokal = {}
    catatan = []
    for i in range(0, len(skus), 50):
        potongan = ','.join(skus[i:i + 50])
        kode, data = erp.panggil(
            sambungan, 'GET',
            'rnd_listing_sku?sku=in.({})&select=sku,varian,rnd_listing_seri(nama)'.format(potongan))
        if kode >= 300 or not isinstance(data, list):
            raise RuntimeError('katalog ERP tidak terbaca ({})'.format(kode))
        for r in data:
            seri = ((r.get('rnd_listing_seri') or {}).get('nama') or '').strip()
            sku = str(r.get('sku') or '').upper()
            if not seri or not sku or lokal.get(sku) == seri.upper():
                continue
            catatan.append({'sku': sku, 'seri': seri, 'varian': r.get('varian') or sku})
    if not catatan:
        return 0
    hasil = inti.tulis_sku(cfg, catatan, gabung=True)
    if not hasil.get('ok', True):
        raise RuntimeError(hasil.get('pesan') or 'gagal menulis data/sku.csv')
    return len(catatan)


def _tambah_pesan(sambungan, id_baris, tambahan):
    """Sambungkan hasil unggah ke pesan pekerjaan yang sudah dilaporkan corel.py."""
    kode, data = erp.panggil(sambungan, 'GET',
                             'rnd_listing_antrean?id=eq.{}&select=pesan'.format(id_baris))
    lama = (data[0].get('pesan') or '') if kode < 300 and isinstance(data, list) and data else ''
    erp.panggil(sambungan, 'PATCH', 'rnd_listing_antrean?id=eq.{}'.format(id_baris),
                {'pesan': (lama + ' · ' + tambahan)[:2000]}, prefer='return=minimal')


def setelah_foto(sambungan, inti, cfg, baris, folder_hasil):
    """Unggah folder hasil satu pekerjaan foto ke R2 dan catat hasilnya di antrean.

    Tidak pernah melempar galat: foto sudah jadi di PC, jadi kegagalan unggah
    dilaporkan sebagai catatan (bisa diulang lewat "Proses Folder Ini"), bukan
    menggagalkan pekerjaan fotonya.
    """
    id_baris = baris.get('id')
    try:
        if not os.path.isdir(folder_hasil):
            raise RuntimeError('folder hasil tidak ada: ' + folder_hasil)
        skus = sku_di_folder(folder_hasil)
        if not skus:
            # Hanya sampul (foto1/2/3.png): unggah.py mengenali sampul dari SKU foto VARIAN di
            # folder yang sama (seri → nama <PREFIX>-<KODE>-utamaN). Sampul ditahan di PC dan
            # ikut terunggah saat Foto Varian produk ini selesai (folder hasil yang sama).
            catatan = ('R2: sampul disimpan di PC, menunggu Foto Varian — ikut terunggah otomatis '
                       'setelah Foto Varian produk ini selesai')
            print('[r2] ' + catatan)
            if id_baris:
                _tambah_pesan(sambungan, id_baris, catatan)
            return catatan
        # Jenis & slug lini baru (mis. CA-/SC-/MA-) hanya ada di Pengaturan Produk ERP —
        # tanpa ini unggah.py tidak mengenali prefix-nya. Gagal baca = lanjut dengan cfg lokal.
        try:
            import pengaturan_erp
            pengaturan_erp.gabung(sambungan, cfg)
        except Exception as e:  # noqa: BLE001
            print('[r2] pengaturan ERP tidak terbaca, memakai config lokal: {}'.format(e))
        ditambah = sinkron_sku(sambungan, inti, cfg, skus)
        if ditambah:
            print('[r2] {} SKU baru dari katalog ERP digabung ke data/sku.csv'.format(ditambah))
        import unggah
        print('[r2] mengunggah {} ke R2 …'.format(folder_hasil))
        hasil = unggah.proses(inti, cfg, folder_hasil, push=True)
        if hasil.get('ok'):
            catatan = 'R2: {} foto dikenali, {} terunggah'.format(
                hasil.get('foto', 0), hasil.get('terkirim', 0))
            try:
                n = catat_indeks(sambungan, inti, cfg, folder_hasil)
                catatan += ', {} tercatat di Konsol'.format(n)
            except Exception as e:  # noqa: BLE001
                catatan += ' (indeks Konsol gagal diperbarui: {} — pakai "Segarkan dari R2")'.format(e)
        else:
            catatan = 'unggah R2 GAGAL: {}'.format(hasil.get('alasan') or 'lihat jendela Desain Host')
    except Exception as e:  # noqa: BLE001
        catatan = 'unggah R2 GAGAL: {}'.format(e)
    print('[r2] ' + catatan)
    if id_baris:
        _tambah_pesan(sambungan, id_baris, catatan)
    return catatan


def catat_indeks(sambungan, inti, cfg, folder_hasil):
    """Catat foto yang baru terunggah ke indeks foto Konsol di ERP (rnd_listing_foto), lalu
    segarkan tampilan folder (rnd_listing_segarkan_kesiapan).

    Kenapa di sini: "Segarkan dari R2" di ERP butuh kunci bucket upload-ganci di server, yang
    sengaja tidak dipasang (kunci baca-tulis bucket listing Shopee). PC host sudah memegangnya
    dan baru saja mengunggah, jadi PC yang melapor — bentuk barisnya sama dengan uraiPath()
    di erp-frontend/src/lib/server/rnd/listing/manifest.ts.
    """
    import unggah
    temuan, _, _, _ = unggah.deteksi(inti, cfg, folder_hasil)
    domain = str(((cfg.get('penyimpanan') or {}).get('r2') or {}).get('domain') or '').rstrip('/')
    baris = []
    for t in temuan:
        path = t['path_repo']
        kunci = os.path.splitext(os.path.basename(path))[0].upper()
        lokal = os.path.join(inti.DIR_FOTO, t['toko'], t['slug'], t['nama_tujuan'])
        ukuran = os.path.getsize(lokal) if os.path.exists(lokal) else None
        utama = re.match(r'^[A-Z]{2}-(.+)-UTAMA(\d+)$', kunci)
        baris.append({
            'toko': t['toko'], 'kunci': kunci, 'tipe': t['tipe'],
            'sku': kunci if t['tipe'] == 'varian' else None,
            'seri_kode': utama.group(1) if utama else None,
            'urutan': int(utama.group(2)) if utama else None,
            'jenis': t['jenis'], 'url': '{}/{}'.format(domain, path),
            'ukuran': ukuran, 'path': path, 'aktif': True,
        })
    for i in range(0, len(baris), 200):
        kode, data = erp.panggil(sambungan, 'POST', 'rnd_listing_foto?on_conflict=toko,kunci',
                                 baris[i:i + 200], prefer='resolution=merge-duplicates,return=minimal')
        if kode >= 300:
            raise RuntimeError('rnd_listing_foto {}: {}'.format(kode, str(data)[:200]))
    kode, data = erp.panggil(sambungan, 'POST', 'rpc/rnd_listing_segarkan_kesiapan', {})
    if kode >= 300:
        raise RuntimeError('penyegaran Konsol {}: {}'.format(kode, str(data)[:200]))
    return len(baris)
