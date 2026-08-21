# -*- coding: utf-8 -*-
"""Ambil pembaruan tools dari GitHub.

Dipakai lewat tombol di tab Sumber Data, lewat UPDATE.bat, atau:
    python tools/shopee_mass_upload.py perbarui          -> cek saja
    python tools/shopee_mass_upload.py perbarui --pasang -> cek lalu pasang

Yang aman dan tidak ikut tertimpa saat memperbarui:
    data/lokal.json       letak folder foto di komputer ini
    data/cache_folder.json
    foto-upload/          foto yang belum di-commit

Berkas terlacak yang sedang diubah di komputer ini (mis. data/foto.db yang
berubah setelah memproses foto) dicadangkan dulu ke data/cadangan-<waktu>/
sebelum ditimpa versi dari GitHub, jadi tidak ada yang hilang diam-diam.
"""
import os
import shutil
import subprocess
from datetime import datetime


def _git(akar, argumen, cetak=None):
    proses = subprocess.Popen(
        ['git'] + list(argumen), cwd=akar,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1)
    baris = []
    for b in proses.stdout:
        b = b.rstrip()
        if b:
            baris.append(b)
            if cetak:
                cetak(b)
    proses.wait()
    return proses.returncode, '\n'.join(baris)


def periksa(inti):
    """Lihat apakah ada pembaruan di GitHub, tanpa mengubah apa pun."""
    if not os.path.isdir(os.path.join(inti.AKAR, '.git')):
        return {'siap': False, 'pesan': 'Folder ini bukan salinan git. '
                                        'Ambil ulang dengan "git clone".'}
    kode, keluaran = _git(inti.AKAR, ['fetch', 'origin', '--quiet'])
    if kode:
        return {'siap': False, 'pesan': 'Gagal menghubungi GitHub:\n' + keluaran}

    _, cabang = _git(inti.AKAR, ['rev-parse', '--abbrev-ref', 'HEAD'])
    cabang = cabang.strip() or 'main'
    _, jauh = _git(inti.AKAR, ['rev-parse', '--short', 'origin/' + cabang])
    _, sini = _git(inti.AKAR, ['rev-parse', '--short', 'HEAD'])
    _, daftar = _git(inti.AKAR, [
        'log', '--oneline', '--no-decorate', 'HEAD..origin/' + cabang])
    _, diubah = _git(inti.AKAR, ['status', '--porcelain', '--untracked-files=no'])

    baru = [b for b in daftar.split('\n') if b.strip()]
    return {
        'siap': True, 'cabang': cabang, 'sini': sini.strip(), 'jauh': jauh.strip(),
        'jumlah': len(baru), 'commit': baru[:20],
        'diubah': [b[3:] for b in diubah.split('\n') if b.strip()],
    }


def pasang(inti, cetak=print):
    """Pasang pembaruan. Berkas terlacak yang berubah dicadangkan dulu."""
    info = periksa(inti)
    if not info['siap']:
        cetak('[perbarui] ' + info['pesan'])
        return info
    if not info['jumlah']:
        cetak('[perbarui] sudah versi terbaru ({})'.format(info['sini']))
        return info

    cetak('[perbarui] {} pembaruan tersedia: {} -> {}'.format(
        info['jumlah'], info['sini'], info['jauh']))
    for b in info['commit']:
        cetak('   ' + b)

    if info['diubah']:
        cap = datetime.now().strftime('%Y%m%d-%H%M%S')
        folder = os.path.join(inti.AKAR, 'data', 'cadangan-' + cap)
        cetak('[perbarui] {} berkas di komputer ini sedang berubah, '
              'dicadangkan dulu:'.format(len(info['diubah'])))
        for rel in info['diubah']:
            asal = os.path.join(inti.AKAR, rel.replace('/', os.sep))
            if not os.path.exists(asal):
                continue
            tujuan = os.path.join(folder, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(tujuan), exist_ok=True)
            shutil.copy2(asal, tujuan)
            cetak('   ' + rel)
        cetak('[perbarui] cadangan di: {}'.format(folder))

    kode, keluaran = _git(inti.AKAR, ['reset', '--hard', 'origin/' + info['cabang']],
                          cetak=lambda b: cetak('   ' + b))
    if kode:
        cetak('[perbarui] gagal memasang:\n' + keluaran)
        return dict(info, berhasil=False)

    _, sekarang = _git(inti.AKAR, ['rev-parse', '--short', 'HEAD'])
    cetak('[perbarui] selesai, sekarang di {}'.format(sekarang.strip()))
    cetak('[perbarui] pengaturan komputer ini (data/lokal.json) tidak tersentuh')
    cetak('[perbarui] server akan menjalankan ulang sendiri sebentar lagi')
    return dict(info, berhasil=True)
