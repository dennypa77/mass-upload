# -*- coding: utf-8 -*-
"""Tampilan grafis untuk tools Shopee Mass Upload.

Jalankan: python tools/ui.py   (atau klik dua kali UI.bat)
Semua pekerjaan dikerjakan oleh shopee_mass_upload.py; berkas ini hanya
membungkusnya supaya bisa dijalankan lewat tombol.
"""
import json, os, queue, subprocess, sys, threading, traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shopee_mass_upload as inti

LANGKAH = [
    ('1. Impor SKU',  'impor', 'Baca ekspor Google Sheet (.xlsx) menjadi data/sku.csv'),
    ('2. Proses Folder Foto', 'unggah',
     'Pilih 1 folder produk -> deteksi PNG -> upload ke GitHub -> URL masuk database'),
    ('3. Cek',        'cek',   'Periksa kelengkapan data & foto sebelum upload'),
    ('4. Buat Excel', 'build', 'Hasilkan file siap upload di output/'),
]
LANJUTAN = [
    ('Ekspor daftar URL', 'url', 'Tulis isi database ke data/url_foto.csv (per toko juga)'),
    ('Sapu semua folder Drive', 'foto', 'Salin seluruh foto Drive sekaligus (tanpa upload)'),
]


class Aliran:
    """Alihkan print() ke antrean supaya bisa ditampilkan di kotak log."""

    def __init__(self, antrean):
        self.antrean = antrean

    def write(self, teks):
        if teks:
            self.antrean.put(teks)

    def flush(self):
        pass


class Aplikasi(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Shopee Mass Upload — Tools')
        self.geometry('980x680')
        self.minsize(820, 560)
        self.antrean = queue.Queue()
        self.sibuk = False
        self.tombol = []
        self._susun()
        self._muat_config()
        self.after(80, self._serap_log)

    # ------------------------------------------------------------------ tata letak
    def _susun(self):
        pad = dict(padx=12, pady=6)

        atas = ttk.Frame(self)
        atas.pack(fill='x', **pad)
        ttk.Label(atas, text='Shopee Mass Upload', font=('Segoe UI', 15, 'bold')).pack(anchor='w')
        self.lbl_folder = ttk.Label(atas, text=inti.AKAR, foreground='#666')
        self.lbl_folder.pack(anchor='w')

        ringkas = ttk.LabelFrame(self, text=' Status ')
        ringkas.pack(fill='x', **pad)
        self.lbl_status = ttk.Label(ringkas, text='memuat…', justify='left')
        self.lbl_status.pack(anchor='w', padx=10, pady=8)

        url = ttk.LabelFrame(self, text=' Alamat dasar foto (GitHub / jsDelivr) ')
        url.pack(fill='x', **pad)
        baris = ttk.Frame(url)
        baris.pack(fill='x', padx=10, pady=8)
        self.var_url = tk.StringVar()
        ttk.Entry(baris, textvariable=self.var_url).pack(side='left', fill='x', expand=True)
        ttk.Button(baris, text='Simpan', command=self.simpan_url).pack(side='left', padx=(8, 0))
        ttk.Label(url, text='Contoh: https://cdn.jsdelivr.net/gh/username/repo@main '
                            '— kosongkan kalau foto belum diupload',
                  foreground='#666').pack(anchor='w', padx=10, pady=(0, 8))

        uji = ttk.LabelFrame(self, text=' Uji coba — kosongkan untuk memproses semua ')
        uji.pack(fill='x', **pad)
        bu = ttk.Frame(uji)
        bu.pack(fill='x', padx=10, pady=8)
        self.saring = {}
        for label, kunci, contoh in (('Toko', 'toko', 'mis. Hangs on You'),
                                     ('Jenis', 'jenis', 'mis. JIBBITZ'),
                                     ('Seri', 'seri', 'mis. CORTIS')):
            ttk.Label(bu, text=label + ':').pack(side='left')
            v = tk.StringVar()
            e = ttk.Entry(bu, textvariable=v, width=18)
            e.pack(side='left', padx=(4, 14))
            self.saring[kunci] = v
        ttk.Label(uji, text='Kalau diisi, hasil ditulis ke output/uji dan data/uji — '
                            'berkas asli tidak tersentuh.',
                  foreground='#666').pack(anchor='w', padx=10, pady=(0, 8))

        aksi = ttk.LabelFrame(self, text=' Langkah ')
        aksi.pack(fill='x', **pad)
        grid = ttk.Frame(aksi)
        grid.pack(fill='x', padx=10, pady=10)
        for i, (judul, perintah, keterangan) in enumerate(LANGKAH):
            b = ttk.Button(grid, text=judul, width=16,
                           command=lambda p=perintah: self.jalankan(p))
            b.grid(row=i, column=0, sticky='w', pady=3)
            ttk.Label(grid, text=keterangan, foreground='#555').grid(
                row=i, column=1, sticky='w', padx=12)
            self.tombol.append(b)

        for i, (judul, perintah, keterangan) in enumerate(LANJUTAN, start=len(LANGKAH)):
            b = ttk.Button(grid, text=judul, width=16,
                           command=lambda p=perintah: self.jalankan(p))
            b.grid(row=i, column=0, sticky='w', pady=3)
            ttk.Label(grid, text=keterangan, foreground='#888').grid(
                row=i, column=1, sticky='w', padx=12)
            self.tombol.append(b)

        bawah = ttk.Frame(aksi)
        bawah.pack(fill='x', padx=10, pady=(0, 10))
        self.var_push = tk.BooleanVar(value=True)
        ttk.Checkbutton(bawah, text='Upload ke GitHub saat proses folder',
                        variable=self.var_push).pack(side='left', padx=(0, 12))
        b = ttk.Button(bawah, text='Jalankan Semua', command=lambda: self.jalankan('semua'))
        b.pack(side='left')
        self.tombol.append(b)
        ttk.Button(bawah, text='Buka folder output',
                   command=lambda: self.buka(inti.DIR_OUT)).pack(side='left', padx=6)
        ttk.Button(bawah, text='Buka folder foto',
                   command=lambda: self.buka(inti.DIR_FOTO)).pack(side='left')
        self.putar = ttk.Progressbar(bawah, mode='indeterminate', length=150)
        self.putar.pack(side='right')

        log = ttk.LabelFrame(self, text=' Log ')
        log.pack(fill='both', expand=True, **pad)
        bingkai = ttk.Frame(log)
        bingkai.pack(fill='both', expand=True, padx=10, pady=8)
        self.teks = tk.Text(bingkai, wrap='none', height=12, bg='#1e1e1e', fg='#dcdcdc',
                            insertbackground='#dcdcdc', font=('Consolas', 9))
        gulir = ttk.Scrollbar(bingkai, command=self.teks.yview)
        self.teks.configure(yscrollcommand=gulir.set)
        gulir.pack(side='right', fill='y')
        self.teks.pack(side='left', fill='both', expand=True)
        self.teks.tag_config('galat', foreground='#f48771')
        self.teks.tag_config('penting', foreground='#dcdcaa')

    # ------------------------------------------------------------------ config
    def _muat_config(self):
        try:
            cfg = inti.baca_config()
        except Exception as e:
            self.lbl_status.config(text='config.json tidak terbaca: {}'.format(e))
            return
        self.var_url.set(cfg['foto'].get('base_url') or '')
        self.perbarui_status()

    def perbarui_status(self):
        try:
            cfg = inti.baca_config()
        except Exception as e:
            self.lbl_status.config(text='config.json tidak terbaca: {}'.format(e))
            return
        try:
            data = inti.baca_sku()
            n_sku = sum(len(d) for s in data.values() for d in s.values())
            n_seri = sum(len(s) for s in data.values())
            teks_sku = '{} SKU · {} jenis · {} seri'.format(n_sku, len(data), n_seri)
        except SystemExit:
            teks_sku = 'data/sku.csv belum ada — mulai dari langkah 1'
        indeks, _ = inti.pindai_foto(cfg)
        n_foto = sum(len(v) for t in indeks.values() for v in t.values())
        rinci_toko = ' · '.join(
            '{}: {}'.format(t['nama'], sum(len(v) for v in indeks.get(t['folder_foto'], {}).values()))
            for t in cfg['toko'])
        n_db = n_unggah = 0
        if os.path.exists(inti.DB_PATH):
            import gudang
            db = gudang.buka(inti.DB_PATH)
            n_db, n_unggah = gudang.jumlah(db)
            db.close()
        n_out = len([f for f in os.listdir(inti.DIR_OUT)
                     if f.endswith('.xlsx') and not f.startswith('~$')]) \
            if os.path.isdir(inti.DIR_OUT) else 0
        self.lbl_status.config(text='\n'.join([
            'Data     : ' + teks_sku,
            'Toko     : ' + ', '.join(t['nama'] for t in cfg['toko']),
            'Foto     : {} file siap · URL {}'.format(
                n_foto, 'aktif' if cfg['foto'].get('base_url') else 'belum diisi'),
            '           ' + (rinci_toko if n_foto else '(belum ada foto)'),
            'Database : {} foto ber-URL · {} sudah di GitHub'.format(n_db, n_unggah),
            'Output   : {} file Excel'.format(n_out),
        ]))

    def simpan_url(self):
        nilai = self.var_url.get().strip().rstrip('/')
        try:
            with open(inti.CONFIG, encoding='utf-8') as f:
                cfg = json.load(f)
            cfg['foto']['base_url'] = nilai or None
            with open(inti.CONFIG, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror('Gagal menyimpan', str(e))
            return
        self.tulis('[ui] base_url disimpan: {}\n'.format(nilai or '(kosong)'), 'penting')
        self.perbarui_status()

    def buka(self, folder):
        if os.path.isdir(folder):
            subprocess.Popen(['explorer', os.path.normpath(folder)])
        else:
            messagebox.showinfo('Belum ada', 'Folder belum dibuat:\n{}'.format(folder))

    # ------------------------------------------------------------------ eksekusi
    def jalankan(self, perintah):
        if self.sibuk:
            return
        sumber = None
        if perintah == 'impor':
            sumber = filedialog.askopenfilename(
                title='Pilih ekspor sheet SKU',
                filetypes=[('Excel / CSV', '*.xlsx *.xlsm *.csv'), ('Semua berkas', '*.*')])
            if not sumber:
                return
        elif perintah == 'unggah':
            try:
                awal = inti.baca_config()['foto'].get('root') or ''
            except Exception:
                awal = ''
            sumber = filedialog.askdirectory(
                title='Pilih folder produk (mis. PRODUK 00001 - 00050)',
                initialdir=awal if os.path.isdir(awal) else None)
            if not sumber:
                return
            if self.var_push.get() and not messagebox.askyesno(
                    'Upload ke GitHub',
                    'Foto di folder ini akan disalin, di-commit, dan di-push ke GitHub.\n\n'
                    '{}\n\nLanjutkan?'.format(sumber)):
                return
        inti.SARING.update({k: (v.get().strip() or None) for k, v in self.saring.items()})
        self.sibuk = True
        for b in self.tombol:
            b.state(['disabled'])
        self.putar.start(12)
        aktif = {k: v for k, v in inti.SARING.items() if v}
        self.tulis('\n{}\n>>> {}{}\n'.format(
            '─' * 78, perintah.upper(),
            '   [uji coba: {}]'.format(aktif) if aktif else ''), 'penting')
        threading.Thread(target=self._kerja, args=(perintah, sumber), daemon=True).start()

    def _kerja(self, perintah, sumber):
        asli = sys.stdout
        sys.stdout = Aliran(self.antrean)
        try:
            cfg = inti.baca_config()
            if perintah == 'impor':
                inti.perintah_impor(cfg, sumber)
            elif perintah == 'unggah':
                import unggah as modul
                modul.proses(inti, cfg, sumber, push=self.var_push.get())
            elif perintah == 'url':
                inti.perintah_url(cfg, inti.baca_sku())
            else:
                data = inti.baca_sku()
                if perintah in ('foto', 'semua'):
                    inti.perintah_foto(cfg, data)
                if perintah == 'semua' and cfg['foto'].get('base_url'):
                    inti.perintah_url(cfg, data)
                if perintah in ('cek', 'semua'):
                    inti.perintah_cek(cfg, data)
                if perintah in ('build', 'semua'):
                    inti.perintah_build(cfg, data)
            print('[selesai]')
        except SystemExit as e:
            self.antrean.put(('galat', '\n[berhenti] {}\n'.format(e)))
        except Exception:
            self.antrean.put(('galat', '\n[error]\n' + traceback.format_exc()))
        finally:
            sys.stdout = asli
            self.antrean.put(('usai', None))

    def _serap_log(self):
        try:
            while True:
                butir = self.antrean.get_nowait()
                if isinstance(butir, tuple):
                    jenis, isi = butir
                    if jenis == 'usai':
                        self.sibuk = False
                        self.putar.stop()
                        for b in self.tombol:
                            b.state(['!disabled'])
                        self.perbarui_status()
                    else:
                        self.tulis(isi, 'galat')
                else:
                    self.tulis(butir, 'galat' if butir.lstrip().startswith('!') else None)
        except queue.Empty:
            pass
        self.after(80, self._serap_log)

    def tulis(self, teks, tag=None):
        self.teks.insert('end', teks, tag or ())
        self.teks.see('end')


if __name__ == '__main__':
    Aplikasi().mainloop()
