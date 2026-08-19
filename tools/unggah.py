# -*- coding: utf-8 -*-
"""Proses satu folder produk: deteksi foto -> salin & rename -> push ke GitHub -> URL -> database.

Dipakai lewat:
    python tools/shopee_mass_upload.py unggah "G:\\My Drive\\JIBBITZ\\PRODUK 00001 - 00050"
atau lewat tombol "Pilih folder produk" di UI.

Folder yang dipilih boleh tingkat mana saja — folder seri, folder FOTO PRODUK,
atau langsung folder satu toko. Isinya ditelusuri sampai sub-folder terdalam.
"""
import os, re, subprocess, sys

import gudang


def _jalankan_git(akar, *argumen):
    hasil = subprocess.run(['git'] + list(argumen), cwd=akar,
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
    return hasil.returncode, (hasil.stdout or '') + (hasil.stderr or '')


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

    temuan, tanpa_seri, tak_dikenal = [], [], []
    for dirpath, _, berkas in os.walk(folder):
        gambar = [f for f in sorted(berkas) if f.lower().endswith(inti.EKSTENSI)]
        if not gambar:
            continue

        # toko diambil dari komponen path yang mengandung kata toko/foto + angka
        toko = None
        for bagian in os.path.normpath(dirpath).split(os.sep)[::-1]:
            m = re.match(r'^(?:toko|foto)[\s_-]*(\d+)$', bagian.strip(), re.I)
            if m:
                toko = 'toko' + m.group(1)
                break
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
    return temuan, tanpa_seri, tak_dikenal


def proses(inti, cfg, folder, push=True):
    temuan, tanpa_seri, tak_dikenal = deteksi(inti, cfg, folder)
    if not temuan:
        print('[unggah] tidak ada foto yang dikenali di folder itu')
        for t in tak_dikenal[:5]:
            print('   ! ' + t)
        return

    print('[unggah] {} foto terdeteksi di: {}'.format(len(temuan), folder))
    rekap = {}
    for t in temuan:
        k = (t['nama_toko'], t['jenis'], t['seri'] or '(seri tidak diketahui)')
        rekap[k] = rekap.get(k, 0) + 1
    for (tk, jn, sr), n in sorted(rekap.items()):
        print('   {:<15} {:<12} {:<16} {} foto'.format(tk, jn, sr, n))

    # 1. salin + rename ke foto-upload/
    dikecilkan = 0
    for t in temuan:
        tujuan = os.path.join(inti.DIR_FOTO, t['toko'], t['slug'])
        os.makedirs(tujuan, exist_ok=True)
        akhir = os.path.join(tujuan, t['nama_tujuan'])
        if inti.salin_muat(t['sumber'], akhir):
            dikecilkan += 1
        t['file_lokal'] = akhir
        t['ukuran'] = os.path.getsize(akhir)
    print('[unggah] disalin ke foto-upload/ ({} foto dikecilkan agar di bawah 2 MB)'.format(dikecilkan))

    # 2. simpan dulu ke database, tandai belum terunggah
    base = (cfg['foto'].get('base_url') or '').rstrip('/')
    db = gudang.buka(inti.DB_PATH)
    for t in temuan:
        t['url'] = base + '/' + t['path_repo'] if base else None
        t['diunggah'] = 0
    gudang.simpan(db, temuan)
    print('[unggah] {} baris disimpan ke {}'.format(len(temuan), inti.DB_PATH))

    if not base:
        print('   ! foto.base_url belum diisi, URL belum bisa dibentuk')
    if not push:
        print('[unggah] push dilewati (--tanpa-push)')
        db.close()
        return

    # 3. push ke GitHub supaya jsDelivr bisa menyajikannya
    folder_repo = sorted({os.path.dirname(t['path_repo']) for t in temuan})
    kode, keluaran = _jalankan_git(inti.AKAR, 'add', '-f', *folder_repo)
    if kode:
        print('[unggah] git add gagal:\n' + keluaran.strip())
        db.close()
        return
    kode, keluaran = _jalankan_git(
        inti.AKAR, '-c', 'user.email=tools@local', '-c', 'user.name=mass-upload',
        'commit', '-m', 'Foto: {}'.format(', '.join(sorted(rekap and {k[2] for k in rekap}))))
    if kode and 'nothing to commit' not in keluaran:
        print('[unggah] git commit gagal:\n' + keluaran.strip())
        db.close()
        return
    print('[unggah] mengunggah ke GitHub, mohon tunggu…')
    kode, keluaran = _jalankan_git(inti.AKAR, 'push', 'origin', 'HEAD')
    if kode:
        print('[unggah] git push gagal:\n' + keluaran.strip())
        db.close()
        return

    gudang.tandai_terunggah(db, [t['path_repo'] for t in temuan])
    n, u = gudang.jumlah(db)
    db.close()
    print('[unggah] selesai. Database: {} foto, {} sudah di GitHub'.format(n, u))
    if base:
        print('[unggah] contoh URL untuk dicek di browser:')
        print('       ' + temuan[0]['url'])
        print('   Catatan: jsDelivr butuh beberapa menit sebelum berkas baru bisa diakses.')

    for k in tanpa_seri[:5]:
        print('   ! SKU belum ada di sku.csv: {}'.format(k))
    for t in tak_dikenal[:5]:
        print('   ! dilewati: {}'.format(t))
