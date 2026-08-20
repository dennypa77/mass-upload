# -*- coding: utf-8 -*-
"""Buka dialog "pilih folder" milik Windows lalu cetak path-nya ke stdout.

Dipanggil sebagai proses terpisah oleh tools/web.py. Browser tidak boleh
memberi tahu path asli sebuah folder, jadi dialognya dijalankan di sisi server
— yang memang komputer yang sama. Dijadikan proses sendiri supaya Tk tidak
bentrok dengan thread server.

Pemakaian: python tools/pilih_folder.py [folder_awal] [--berkas]
"""
import sys
import tkinter as tk
from tkinter import filedialog


def main():
    awal = sys.argv[1] if len(sys.argv) > 1 else ''
    akar = tk.Tk()
    akar.withdraw()
    akar.attributes('-topmost', True)
    if '--berkas' in sys.argv:
        judul = next((a for a in sys.argv[2:] if not a.startswith('--')), '')
        jalur = filedialog.askopenfilename(
            title=judul or 'Pilih berkas',
            filetypes=[('Excel / CSV', '*.xlsx *.xlsm *.csv'), ('Semua berkas', '*.*')],
            initialdir=awal or None)
    else:
        jalur = filedialog.askdirectory(
            title='Pilih folder sumber foto', mustexist=True,
            initialdir=awal or None)
    akar.destroy()
    sys.stdout.write(jalur or '')


if __name__ == '__main__':
    main()
