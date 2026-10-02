# -*- coding: utf-8 -*-
"""Dorong catatan tahap folder dari PC ini ke ERP.

KENAPA ADA
`data/tahap_folder.csv` selama ini berpindah antar-PC lewat commit git. Itu
berarti dua hal yang sama-sama tidak enak: dua orang yang bekerja bersamaan bisa
saling menimpa catatan, dan Konsol Mass Upload di ERP hanya setahu salinan
terakhir yang kebetulan diimpor seseorang. Produk yang sudah naik ke Shopee
kemarin masih tampil "belum diproses" di sana.

Mendorongnya langsung ke ERP membuat satu buku besar bersama: PC tetap yang
menilai hasil Shopee (`hasil_shopee.py`), ERP yang menyimpan catatannya untuk
semua orang.

AMAN DITIMPA?
Tidak. Di sisi ERP ada trigger `rnd_listing_tahap_jaga_waktu`: tulisan yang
`waktu`-nya lebih lama daripada baris yang sudah ada akan diabaikan. Jadi PC
yang berkasnya tertinggal tidak bisa membatalkan penandaan yang baru dibuat di
ERP, dan sebaliknya. Mendorong seluruh berkas berkali-kali aman.

Jalankan:

    python tools/erp_tahap.py              # dorong seluruh catatan
    python tools/erp_tahap.py --kering     # lihat yang akan dikirim, tanpa mengirim

Butuh `data/lokal.json` berisi {"erp": {"url": ..., "jwt_secret": ...}} —
lihat tools/erp.py.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp  # noqa: E402
import shopee_mass_upload as inti  # noqa: E402

TABEL = 'rnd_listing_tahap'
# Satu permintaan berisi ribuan baris akan ditolak proxy; 500 sudah terbukti
# nyaman di jalur yang sama dengan pengimpor ERP.
POTONG = 500
# Berkas lokal menulis waktu setempat tanpa zona. PC tim ada di Indonesia, dan
# ERP membaca berkas yang sama dengan anggapan yang sama (lihat tahap.ts).
ZONA = '+07:00'
RX_ZONA = re.compile(r'([zZ]|[+-]\d{2}:?\d{2})$')


def waktu_iso(nilai):
    """'2026-09-15 13:29' -> '2026-09-15T13:29:00+07:00'. Kosong -> None."""
    teks = str(nilai or '').strip()
    if not teks:
        return None
    teks = teks.replace(' ', 'T')
    if RX_ZONA.search(teks):
        return teks
    if len(teks) == 16:  # tanpa detik
        teks += ':00'
    return teks + ZONA


def kumpulkan():
    """Baris tahap dari berkas lokal, dalam bentuk yang diterima PostgREST."""
    keluar = []
    for b in inti.baca_tahap().values():
        jenis = str(b.get('jenis') or '').strip().upper()
        tahap = str(b.get('tahap') or '').strip().lower()
        if not jenis or not tahap:
            continue
        try:
            dari = int(str(b.get('dari')).strip())
            sampai = int(str(b.get('sampai')).strip())
        except (TypeError, ValueError):
            continue
        keluar.append({
            'jenis': jenis,
            'dari': dari,
            'sampai': sampai,
            # Kolom toko kosong berarti "berlaku untuk semua toko" — ERP
            # menyimpannya apa adanya, jadi jangan diisi apa pun di sini.
            'toko': str(b.get('toko') or '').strip(),
            'tahap': tahap,
            'catatan': (str(b.get('catatan') or '').strip() or None),
            'waktu': waktu_iso(b.get('waktu')),
            'oleh': (str(b.get('oleh') or '').strip() or None),
        })
    return keluar


def dorong(baris, kering=False):
    """Kirim ke ERP. Kembalikan (terkirim, pesan)."""
    sambungan = erp.baca_erp()
    if not sambungan:
        return 0, 'data/lokal.json belum memuat {"erp": {"url": ..., "jwt_secret": ...}}'
    if not baris:
        return 0, 'tidak ada catatan tahap di berkas lokal'
    if kering:
        per_tahap = {}
        for b in baris:
            per_tahap[b['tahap']] = per_tahap.get(b['tahap'], 0) + 1
        rincian = ', '.join('{} {}'.format(n, k) for k, n in sorted(per_tahap.items()))
        return 0, '{} baris siap dikirim ({}) — tidak ada yang dikirim, ini uji kering'.format(
            len(baris), rincian)

    terkirim = 0
    for i in range(0, len(baris), POTONG):
        bagian = baris[i:i + POTONG]
        status, jawab = erp.panggil(
            sambungan, 'POST', TABEL, bagian,
            prefer='resolution=merge-duplicates,return=minimal')
        if status >= 300:
            return terkirim, 'gagal di baris ke-{}: HTTP {} {}'.format(i, status, jawab)
        terkirim += len(bagian)
    return terkirim, '{} baris tahap terdorong ke ERP'.format(terkirim)


def dorong_aman():
    """Versi untuk dipanggil dari alur lain: tidak pernah melempar galat.

    Dipakai hasil_shopee.py sesudah menilai berkas hasil Shopee. Kalau ERP
    sedang tidak terjangkau, impornya tetap selesai dan catatannya tetap ada di
    berkas lokal — dorongan berikutnya akan menyusulkan.
    """
    try:
        return dorong(kumpulkan())
    except Exception as e:  # noqa: BLE001 — sengaja menelan apa pun
        return 0, 'tidak terdorong ke ERP: {}'.format(e)


def main():
    p = argparse.ArgumentParser(description='Dorong tahap folder ke ERP')
    p.add_argument('--kering', action='store_true',
                   help='tampilkan yang akan dikirim, jangan mengirim')
    a = p.parse_args()
    baris = kumpulkan()
    terkirim, pesan = dorong(baris, kering=a.kering)
    print('[erp-tahap] ' + pesan)
    return 0 if (terkirim > 0 or a.kering) else 1


if __name__ == '__main__':
    sys.exit(main())
