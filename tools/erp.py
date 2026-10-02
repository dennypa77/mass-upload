# -*- coding: utf-8 -*-
"""Sambungan ke PostgREST ERP: alamat, JWT, dan satu pemanggil permintaan.

Dipisah dari `antrean.py` karena sekarang ada dua pemakai — mengambil antrean
unggah, dan mendorong catatan tahap folder — dan nanti mungkin lebih. Isinya
pustaka baku saja, tanpa dependensi tambahan.

RAHASIA. Butuh dua nilai di `data/lokal.json` (berkas itu TIDAK ikut git):

    "erp": { "url": "https://db.erp-hog.com", "jwt_secret": "…" }

`jwt_secret` sama dengan `VPS_DB_JWT_SECRET` di server. JANGAN menaruhnya di
`tools/config.json` — berkas itu DILACAK git dan terbit ke repo publik.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shopee_mass_upload as inti  # noqa: E402


def baca_erp():
    """Alamat + rahasia dari data/lokal.json. None kalau belum diisi."""
    lokal = inti.baca_lokal()
    e = (lokal or {}).get('erp') or {}
    url = str(e.get('url') or '').rstrip('/')
    rahasia = str(e.get('jwt_secret') or '')
    if not url or not rahasia:
        return None
    return {'url': url, 'rahasia': rahasia}


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=')


def mint_jwt(rahasia, detik=900):
    """JWT HS256 role service_role — pola yang sama dengan vpsDbJwt.ts di ERP."""
    sekarang = int(time.time())
    kepala = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    isi = _b64(json.dumps({
        'role': 'service_role', 'iat': sekarang, 'exp': sekarang + detik,
    }).encode())
    tanda = _b64(hmac.new(rahasia.encode(), kepala + b'.' + isi, hashlib.sha256).digest())
    return (kepala + b'.' + isi + b'.' + tanda).decode()


def panggil(erp, metode, path, badan=None, prefer=None, timeout=60):
    """Satu permintaan PostgREST. Kembalikan (status, data terurai)."""
    url = '{}/rest/v1/{}'.format(erp['url'], path.lstrip('/'))
    data = json.dumps(badan).encode() if badan is not None else None
    minta = urllib.request.Request(url, data=data, method=metode)
    minta.add_header('Authorization', 'Bearer ' + mint_jwt(erp['rahasia']))
    minta.add_header('Content-Type', 'application/json')
    minta.add_header('Accept', 'application/json')
    if prefer:
        minta.add_header('Prefer', prefer)
    try:
        with urllib.request.urlopen(minta, timeout=timeout) as jawab:
            mentah = jawab.read().decode('utf-8') or '[]'
            return jawab.status, (json.loads(mentah) if mentah.strip() else [])
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'ignore')
