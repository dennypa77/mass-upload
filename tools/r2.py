# -*- coding: utf-8 -*-
"""Klien kecil untuk Cloudflare R2 (S3-compatible), tanpa pustaka tambahan.

Hanya tiga hal yang dibutuhkan tools ini: menaruh berkas, mendaftar isi bucket,
dan menghapus berkas. Semuanya lewat HTTP biasa dengan tanda tangan AWS
Signature V4 yang dibuat memakai hmac/hashlib bawaan Python — jadi karyawan
tidak perlu memasang boto3.

Kunci akses TIDAK boleh masuk ke tools/config.json karena berkas itu ikut ke
GitHub. Simpan di data/lokal.json yang tidak dilacak git.
"""
import hashlib
import hmac
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

WILAYAH = 'auto'
LAYANAN = 's3'
KOSONG = hashlib.sha256(b'').hexdigest()


class Galat(Exception):
    pass


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _hmac(kunci, pesan):
    return hmac.new(kunci, pesan.encode('utf-8'), hashlib.sha256).digest()


def _kunci_tanda_tangan(rahasia, tanggal):
    k = _hmac(('AWS4' + rahasia).encode('utf-8'), tanggal)
    k = _hmac(k, WILAYAH)
    k = _hmac(k, LAYANAN)
    return _hmac(k, 'aws4_request')


class R2:
    def __init__(self, endpoint, bucket, akses, rahasia, domain=None):
        self.endpoint = (endpoint or '').rstrip('/')
        # "S3 upload url" dari Cloudflare kadang sudah memuat nama bucket di
        # belakangnya; buang supaya tidak tertulis dua kali
        if bucket and self.endpoint.endswith('/' + bucket):
            self.endpoint = self.endpoint[:-(len(bucket) + 1)]
        self.bucket = bucket
        self.akses = akses
        self.rahasia = rahasia
        self.domain = (domain or '').rstrip('/')
        self.host = urllib.parse.urlparse(self.endpoint).netloc

    # ------------------------------------------------------------------ dasar
    def _minta(self, cara, jalur, isi=None, tanya=None, tipe=None, batas=120):
        isi = isi or b''
        tanya = tanya or {}
        sekarang = datetime.now(timezone.utc)
        cap = sekarang.strftime('%Y%m%dT%H%M%SZ')
        tanggal = sekarang.strftime('%Y%m%d')
        sidik = _sha256(isi)

        jalur_aman = urllib.parse.quote(jalur, safe='/~')
        kueri = '&'.join('{}={}'.format(urllib.parse.quote(k, safe='-_.~'),
                                        urllib.parse.quote(str(v), safe='-_.~'))
                         for k, v in sorted(tanya.items()))

        kepala = {'host': self.host, 'x-amz-content-sha256': sidik, 'x-amz-date': cap}
        if tipe:
            kepala['content-type'] = tipe
        urut = sorted(kepala)
        kepala_kanonik = ''.join('{}:{}\n'.format(k, kepala[k]) for k in urut)
        daftar_kepala = ';'.join(urut)

        kanonik = '\n'.join([cara, jalur_aman, kueri, kepala_kanonik, daftar_kepala, sidik])
        lingkup = '{}/{}/{}/aws4_request'.format(tanggal, WILAYAH, LAYANAN)
        untuk_ditandatangani = '\n'.join(
            ['AWS4-HMAC-SHA256', cap, lingkup, _sha256(kanonik.encode('utf-8'))])
        tanda = hmac.new(_kunci_tanda_tangan(self.rahasia, tanggal),
                         untuk_ditandatangani.encode('utf-8'), hashlib.sha256).hexdigest()

        kepala['Authorization'] = (
            'AWS4-HMAC-SHA256 Credential={}/{}, SignedHeaders={}, Signature={}'
            .format(self.akses, lingkup, daftar_kepala, tanda))

        alamat = self.endpoint + jalur_aman + (('?' + kueri) if kueri else '')
        permintaan = urllib.request.Request(alamat, data=isi if isi else None,
                                            method=cara, headers=kepala)
        try:
            with urllib.request.urlopen(permintaan, timeout=batas) as balas:
                return balas.status, balas.read()
        except urllib.error.HTTPError as e:
            pesan = e.read().decode('utf-8', 'replace')[:400]
            raise Galat('{} {} -> {} {}'.format(cara, jalur, e.code, pesan))

    # ------------------------------------------------------------------ pakai
    def unggah(self, kunci, berkas, tipe='image/png'):
        with open(berkas, 'rb') as f:
            isi = f.read()
        self._minta('PUT', '/{}/{}'.format(self.bucket, kunci), isi, tipe=tipe,
                    batas=300)
        return len(isi)

    def hapus(self, kunci):
        self._minta('DELETE', '/{}/{}'.format(self.bucket, kunci))

    def daftar(self, awalan=''):
        """Semua kunci objek di bucket, sudah termasuk halaman berikutnya."""
        hasil, lanjut = [], None
        while True:
            tanya = {'list-type': '2', 'max-keys': '1000'}
            if awalan:
                tanya['prefix'] = awalan
            if lanjut:
                tanya['continuation-token'] = lanjut
            _, isi = self._minta('GET', '/' + self.bucket, tanya=tanya)
            akar = ET.fromstring(isi)
            ruang = akar.tag.split('}')[0] + '}' if '}' in akar.tag else ''
            for anak in akar.findall('{}Contents'.format(ruang)):
                kunci = anak.findtext('{}Key'.format(ruang))
                if kunci:
                    hasil.append(kunci)
            if akar.findtext('{}IsTruncated'.format(ruang)) == 'true':
                lanjut = akar.findtext('{}NextContinuationToken'.format(ruang))
            else:
                break
        return hasil

    def alamat(self, kunci):
        if self.domain:
            return '{}/{}'.format(self.domain, kunci)
        return '{}/{}/{}'.format(self.endpoint, self.bucket, kunci)


def dari_config(cfg):
    """Bentuk klien R2 dari pengaturan. Kembalikan None kalau belum lengkap."""
    p = (cfg.get('penyimpanan') or {})
    if (p.get('mode') or 'github') != 'r2':
        return None
    r = p.get('r2') or {}
    kurang = [k for k in ('endpoint', 'bucket', 'akses', 'rahasia') if not r.get(k)]
    if kurang:
        raise Galat('Pengaturan R2 belum lengkap: {}. Isi di data/lokal.json.'
                    .format(', '.join(kurang)))
    return R2(r['endpoint'], r['bucket'], r['akses'], r['rahasia'], r.get('domain'))
