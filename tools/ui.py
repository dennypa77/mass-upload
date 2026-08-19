# -*- coding: utf-8 -*-
"""Tampilan grafis untuk tools Shopee Mass Upload.

Jalankan: python tools/ui.py   (atau klik dua kali UI.bat)
Semua pekerjaan dikerjakan oleh shopee_mass_upload.py dan unggah.py; berkas ini
hanya membungkusnya supaya bisa dijalankan lewat tombol.
"""
import os, json, queue, subprocess, sys, threading, traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shopee_mass_upload as inti
import gudang
import unggah as modul_unggah

LANGKAH = [
    ('Impor SKU', 'impor', 'Baca ekspor Google Sheet (.xlsx) menjadi data/sku.csv'),
    ('Cek', 'cek', 'Periksa kelengkapan data & foto sebelum upload'),
    ('Buat Excel', 'build', 'Hasilkan berkas siap upload di output/'),
    ('Ekspor URL', 'url', 'Tulis isi database ke data/url_foto.csv (per toko juga)'),
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
        self.geometry('1000x760')
        self.minsize(860, 620)
        self.antrean = queue.Queue()
        self.sibuk = False
        self.tombol = []
        self.temuan = []          # hasil deteksi folder terakhir
        self._susun()
        self._muat_config()
        self.after(80, self._serap_log)

    # ------------------------------------------------------------------ tata letak
    def _susun(self):
        pad = dict(padx=12, pady=5)

        atas = ttk.Frame(self)
        atas.pack(fill='x', **pad)
        ttk.Label(atas, text='Shopee Mass Upload', font=('Segoe UI', 15, 'bold')).pack(anchor='w')
        ttk.Label(atas, text=inti.AKAR, foreground='#666').pack(anchor='w')

        ringkas = ttk.LabelFrame(self, text=' Status ')
        ringkas.pack(fill='x', **pad)
        self.lbl_status = ttk.Label(ringkas, text='memuat…', justify='left')
        self.lbl_status.pack(anchor='w', padx=10, pady=8)

        url = ttk.LabelFrame(self, text=' Alamat dasar foto (akar repo GitHub) ')
        url.pack(fill='x', **pad)
        bu = ttk.Frame(url)
        bu.pack(fill='x', padx=10, pady=8)
        self.var_url = tk.StringVar()
        ttk.Entry(bu, textvariable=self.var_url).pack(side='left', fill='x', expand=True)
        ttk.Button(bu, text='Simpan', command=self.simpan_url).pack(side='left', padx=(8, 0))

        # ---------------- folder foto: pilih -> deteksi -> proses
        blok = ttk.LabelFrame(self, text=' Folder foto produk ')
        blok.pack(fill='x', **pad)

        b1 = ttk.Frame(blok)
        b1.pack(fill='x', padx=10, pady=(8, 4))
        ttk.Label(b1, text='Folder:').pack(side='left')
        self.var_folder = tk.StringVar()
        e = ttk.Entry(b1, textvariable=self.var_folder)
        e.pack(side='left', fill='x', expand=True, padx=6)
        e.bind('<Return>', lambda _e: self.deteksi())
        self.btn_pilih = ttk.Button(b1, text='Pilih Folder…', command=self.pilih_folder)
        self.btn_pilih.pack(side='left')
        self.btn_deteksi = ttk.Button(b1, text='Deteksi', command=self.deteksi)
        self.btn_deteksi.pack(side='left', padx=(6, 0))
        self.tombol += [self.btn_pilih, self.btn_deteksi]

        ttk.Label(blok, text='Bisa juga tempel path langsung lalu tekan Enter. '
                             'Folder seri, folder FOTO PRODUK, atau folder satu toko — semuanya bisa.',
                  foreground='#666').pack(anchor='w', padx=10)

        self.lbl_deteksi = ttk.Label(blok, text='Belum ada folder dipilih.',
                                     justify='left', foreground='#444')
        self.lbl_deteksi.pack(anchor='w', padx=10, pady=(8, 4))

        b2 = ttk.Frame(blok)
        b2.pack(fill='x', padx=10, pady=(0, 10))
        self.var_push = tk.BooleanVar(value=True)
        ttk.Checkbutton(b2, text='Upload ke GitHub setelah disalin',
                        variable=self.var_push).pack(side='left', padx=(0, 12))
        self.btn_proses = ttk.Button(b2, text='Proses Folder Ini', command=self.proses_folder)
        self.btn_proses.pack(side='left')
        self.btn_proses.state(['disabled'])
        self.tombol.append(self.btn_proses)

        # ---------------- langkah lain
        aksi = ttk.LabelFrame(self, text=' Langkah lain ')
        aksi.pack(fill='x', **pad)
        grid = ttk.Frame(aksi)
        grid.pack(fill='x', padx=10, pady=8)
        for i, (judul, perintah, ket) in enumerate(LANGKAH):
            b = ttk.Button(grid, text=judul, width=14, command=lambda p=perintah: self.jalankan(p))
            b.grid(row=i, column=0, sticky='w', pady=2)
            ttk.Label(grid, text=ket, foreground='#555').grid(row=i, column=1, sticky='w', padx=12)
            self.tombol.append(b)

        b3 = ttk.Frame(aksi)
        b3.pack(fill='x', padx=10, pady=(0, 10))
        ttk.Button(b3, text='Buka folder output',
                   command=lambda: self.buka(inti.DIR_OUT)).pack(side='left')
        ttk.Button(b3, text='Buka folder foto',
                   command=lambda: self.buka(inti.DIR_FOTO)).pack(side='left', padx=6)
        self.putar = ttk.Progressbar(b3, mode='indeterminate', length=220)
        self.putar.pack(side='right')
        self.lbl_maju = ttk.Label(b3, text='', foreground='#555')
        self.lbl_maju.pack(side='right', padx=8)

        log = ttk.LabelFrame(self, text=' Log ')
        log.pack(fill='both', expand=True, **pad)
        bingkai = ttk.Frame(log)
        bingkai.pack(fill='both', expand=True, padx=10, pady=8)
        self.teks = tk.Text(bingkai, wrap='none', height=10, bg='#1e1e1e', fg='#dcdcdc',
                            insertbackground='#dcdcdc', font=('Consolas', 9))
        gulir = ttk.Scrollbar(bingkai, command=self.teks.yview)
        self.teks.configure(yscrollcommand=gulir.set)
        gulir.pack(side='right', fill='y')
        self.teks.pack(side='left', fill='both', expand=True)
        self.teks.tag_config('galat', foreground='#f48771')
        self.teks.tag_config('penting', foreground='#dcdcaa')

    # ------------------------------------------------------------------ folder
    def pilih_folder(self):
        """Buka dialog, lalu tampilkan path yang dipilih dan langsung deteksi isinya."""
        awal = self.var_folder.get().strip()
        if not os.path.isdir(awal):
            try:
                awal = inti.baca_config()['foto'].get('root') or inti.AKAR
            except Exception:
                awal = inti.AKAR
        pilihan = filedialog.askdirectory(parent=self, mustexist=True,
                                          title='Pilih folder yang berisi foto produk',
                                          initialdir=awal)
        if not pilihan:
            self.tulis('[ui] pemilihan folder dibatalkan\n')
            return
        self.var_folder.set(os.path.normpath(pilihan))
        self.deteksi()

    def deteksi(self):
        """Hitung berapa foto di folder itu dan kenali tujuannya. Tidak mengubah apa pun."""
        folder = self.var_folder.get().strip().strip('"')
        if not folder:
            messagebox.showinfo('Folder kosong', 'Pilih folder dulu, atau tempel path-nya.')
            return
        if not os.path.isdir(folder):
            self.lbl_deteksi.config(text='Folder tidak ditemukan:\n{}'.format(folder),
                                    foreground='#b00')
            self.btn_proses.state(['disabled'])
            return
        self.var_folder.set(os.path.normpath(folder))
        self.lbl_deteksi.config(text='Mendeteksi isi folder…', foreground='#444')
        self.btn_proses.state(['disabled'])
        self._mulai()
        threading.Thread(target=self._kerja_deteksi, args=(folder,), daemon=True).start()

    def _kerja_deteksi(self, folder):
        asli = sys.stdout
        sys.stdout = Aliran(self.antrean)
        try:
            cfg = inti.baca_config()
            temuan, tanpa_seri, tak_dikenal = modul_unggah.deteksi(inti, cfg, folder)
            self.antrean.put(('deteksi', (folder, temuan, tanpa_seri, tak_dikenal)))
        except SystemExit as e:
            self.antrean.put(('galat', '\n[berhenti] {}\n'.format(e)))
        except Exception:
            self.antrean.put(('galat', '\n[error]\n' + traceback.format_exc()))
        finally:
            sys.stdout = asli
            self.antrean.put(('usai', None))

    def _tampilkan_deteksi(self, folder, temuan, tanpa_seri, tak_dikenal):
        self.temuan = temuan
        if not temuan:
            self.lbl_deteksi.config(
                text='Tidak ada foto yang dikenali di folder ini.\n'
                     '{} berkas dilewati. Pastikan nama file memakai kode SKU '
                     '(JB-/PA-/PB-) atau foto1/2/3.png.'.format(len(tak_dikenal)),
                foreground='#b00')
            self.btn_proses.state(['disabled'])
            return
        rekap = {}
        for t in temuan:
            k = (t['nama_toko'], t['jenis'], t['seri'] or '(seri belum diketahui)')
            rekap[k] = rekap.get(k, 0) + 1
        baris = ['{} foto terdeteksi:'.format(len(temuan))]
        for (tk_, jn, sr), n in sorted(rekap.items()):
            baris.append('     {:<15} {:<12} {:<20} {} foto'.format(tk_, jn, sr, n))
        if tanpa_seri:
            baris.append('     ! {} SKU belum terdaftar di sku.csv'.format(len(tanpa_seri)))
        if tak_dikenal:
            baris.append('     ! {} berkas dilewati (tidak dikenali)'.format(len(tak_dikenal)))
        self.lbl_deteksi.config(text='\n'.join(baris), foreground='#0a0')
        self.btn_proses.state(['!disabled'])
        self.tulis('[deteksi] {} foto di {}\n'.format(len(temuan), folder), 'penting')

    def proses_folder(self):
        folder = self.var_folder.get().strip()
        if not self.temuan or not os.path.isdir(folder):
            messagebox.showinfo('Belum siap', 'Tekan "Deteksi" dulu.')
            return
        push = self.var_push.get()
        pesan = '{} foto akan disalin ke foto-upload/ dan URL-nya disimpan ke database.'.format(
            len(self.temuan))
        if push:
            pesan += '\n\nFoto juga akan di-commit dan di-push ke GitHub.'
        if not messagebox.askyesno('Proses folder', pesan + '\n\n{}\n\nLanjutkan?'.format(folder)):
            return
        self.jalankan('unggah', folder)

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
            teks_sku = '{} SKU · {} jenis · {} seri'.format(
                n_sku, len(data), sum(len(s) for s in data.values()))
        except SystemExit:
            teks_sku = 'data/sku.csv belum ada — mulai dari "Impor SKU"'
        n_db = n_unggah = 0
        rinci = ''
        if os.path.exists(inti.DB_PATH):
            db = gudang.buka(inti.DB_PATH)
            n_db, n_unggah = gudang.jumlah(db)
            rinci = ' · '.join('{} {}/{}'.format(r['nama_toko'], r['jenis'], r['seri'])
                               for r in gudang.ringkasan(db)[:4])
            db.close()
        n_out = len([f for f in os.listdir(inti.DIR_OUT)
                     if f.endswith('.xlsx') and not f.startswith('~$')]) \
            if os.path.isdir(inti.DIR_OUT) else 0
        self.lbl_status.config(text='\n'.join([
            'Data     : ' + teks_sku,
            'Toko     : ' + ', '.join(t['nama'] for t in cfg['toko']),
            'Database : {} foto ber-URL · {} sudah di GitHub'.format(n_db, n_unggah),
            '           ' + (rinci or '(database masih kosong)'),
            'Output   : {} berkas Excel'.format(n_out),
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
    TAHAP = {'salin': 'Menyalin foto', 'unggah': 'Mengunggah ke GitHub'}

    def _mulai(self):
        self.sibuk = True
        for b in self.tombol:
            b.state(['disabled'])
        self.putar.configure(mode='indeterminate')
        self.putar.start(12)
        self.lbl_maju.config(text='')

    def _selesai(self):
        self.sibuk = False
        self.putar.stop()
        self.putar.configure(mode='indeterminate', value=0)
        self.lbl_maju.config(text='')
        for b in self.tombol:
            b.state(['!disabled'])
        if not self.temuan:
            self.btn_proses.state(['disabled'])
        self.perbarui_status()

    def _maju(self, tahap, n, total):
        """Ubah progress bar jadi terukur, dipanggil dari antrean."""
        if self.putar['mode'] != 'determinate':
            self.putar.stop()
            self.putar.configure(mode='determinate', maximum=100)
        self.putar['value'] = (n / total * 100) if total else 0
        self.lbl_maju.config(text='{}: {}/{}'.format(self.TAHAP.get(tahap, tahap), n, total))

    def jalankan(self, perintah, sumber=None):
        if self.sibuk:
            return
        if perintah == 'impor':
            sumber = filedialog.askopenfilename(
                parent=self, title='Pilih ekspor sheet SKU',
                filetypes=[('Excel / CSV', '*.xlsx *.xlsm *.csv'), ('Semua berkas', '*.*')])
            if not sumber:
                return
        self._mulai()
        self.tulis('\n{}\n>>> {}\n'.format('─' * 78, perintah.upper()), 'penting')
        threading.Thread(target=self._kerja, args=(perintah, sumber), daemon=True).start()

    def _kerja(self, perintah, sumber):
        asli = sys.stdout
        sys.stdout = Aliran(self.antrean)
        try:
            cfg = inti.baca_config()
            if perintah == 'impor':
                inti.perintah_impor(cfg, sumber)
            elif perintah == 'unggah':
                modul_unggah.proses(inti, cfg, sumber, push=self.var_push.get(),
                                    lapor=lambda *a: self.antrean.put(('maju', a)))
            elif perintah == 'url':
                inti.perintah_url(cfg, inti.baca_sku())
            else:
                data = inti.baca_sku()
                if perintah == 'cek':
                    inti.perintah_cek(cfg, data)
                if perintah == 'build':
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
                        self._selesai()
                    elif jenis == 'maju':
                        self._maju(*isi)
                    elif jenis == 'deteksi':
                        self._tampilkan_deteksi(*isi)
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
