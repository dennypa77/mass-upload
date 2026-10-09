@echo off
rem ===================================================================
rem  Desain Host - penghubung ERP <-> CorelDRAW di PC ini
rem
rem  Klik dua kali, lalu BIARKAN jendela ini terbuka. Selama ia hidup,
rem  perintah dari ERP (RnD -> Desain Host, dan Konsol Mass Upload)
rem  diambil sendiri lalu dikerjakan di CorelDRAW.
rem
rem  Tutup jendela = ERP tidak lagi punya tangan di PC ini. Pekerjaan
rem  tetap masuk antrean, hanya diam di "menunggu" sampai dinyalakan lagi.
rem
rem  SYARAT: CorelDRAW terbuka, dan modul modFotoProduk +
rem  AutoStikerOutline1 versi terbaru sudah diimpor ke GlobalMacros
rem  (unduh di ERP: RnD -> Unduhan Alat).
rem ===================================================================

title Desain Host - pengambil perintah ERP

rem Folder alat ini, diambil dari letak berkas .bat - jadi pindah folder
rem pun tetap jalan, dan tidak ada path yang perlu diubah tangan.
cd /d "%~dp0.."

rem ------------------------------------------------------------------
rem  DB yang dipakai.
rem
rem  Selama modul ini masih diuji, antreannya ada di DB dev yang dicapai
rem  lewat terowongan SSH di 127.0.0.1:3102. Begitu naik ke produksi,
rem  KOSONGKAN baris ini (jadi: set ERP_URL=) - alamat produksi sudah ada
rem  di data/lokal.json dan dipakai otomatis.
rem ------------------------------------------------------------------
set ERP_URL=http://127.0.0.1:3102

set ARG=--pantau --jeda 15
if not "%ERP_URL%"=="" set ARG=%ARG% --url %ERP_URL%

echo.
echo   Desain Host siap. Biarkan jendela ini terbuka.
echo   Berhenti: tekan Ctrl+C dua kali, atau tutup jendela.
echo.

:ulang
python tools\corel.py %ARG%

rem Sampai di sini berarti pengambilnya berhenti: CorelDRAW ditutup di
rem tengah jalan, jaringan putus, atau Python galat. Dinyalakan lagi
rem sendiri - operator tidak akan sadar jendelanya mati diam-diam, dan
rem pekerjaan yang menumpuk di antrean baru ketahuan saat ditanyakan.
echo.
echo   [%date% %time%] pengambil berhenti - menyalakan ulang 15 detik lagi.
echo   (kalau ini terus berulang, baca pesan galat di atas)
echo.
timeout /t 15 /nobreak >nul
goto ulang
