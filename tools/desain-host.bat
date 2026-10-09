@echo off
rem Versi  : 1.3   (2026-10-09 - tidak meninggalkan jendela menggantung)
rem ===================================================================
rem  Desain Host - penghubung ERP <-> CorelDRAW di PC ini
rem
rem  Klik dua kali, lalu BIARKAN jendela ini terbuka. Selama ia hidup,
rem  perintah dari ERP (RnD -> Desain Host, dan Konsol Mass Upload)
rem  diambil sendiri lalu dikerjakan di CorelDRAW.
rem
rem  Tutup jendela = ERP tidak lagi punya tangan di PC ini. Pekerjaan
rem  tetap masuk antrean, hanya diam di "menunggu" sampai dinyalakan
rem  lagi. Supaya tidak perlu diingat: pasang desain-host-tugas.bat.
rem
rem  SYARAT: CorelDRAW terbuka, dan modul modFotoProduk +
rem  AutoStikerOutline1 versi terbaru sudah diimpor ke GlobalMacros
rem  (unduh di ERP: RnD -> Unduhan Alat).
rem ===================================================================

title Desain Host - pengambil perintah ERP

rem ------------------------------------------------------------------
rem  Letak folder alat (yang berisi tools\corel.py).
rem
rem  Dikosongkan = dicari sendiri. Isi manual kalau alatnya dipasang di
rem  tempat lain, mis.  set "ALAT=D:\Mass upload shopee"
rem ------------------------------------------------------------------
set "ALAT="

rem Berkas ini bisa berada DI DALAM folder tools\, atau di sebelahnya,
rem atau di Downloads karena baru diunduh dari ERP. Ketiganya dicoba,
rem lalu letak baku di PC kantor. Versi 1.0 menganggap dirinya selalu di
rem dalam tools\ - salinan yang diunduh lalu dijalankan dari Downloads
rem mencari "C:\Users\<nama>\tools\corel.py" dan gagal tiap 15 detik.
if not defined ALAT if exist "%~dp0..\tools\corel.py" set "ALAT=%~dp0.."
if not defined ALAT if exist "%~dp0tools\corel.py" set "ALAT=%~dp0."
if not defined ALAT if exist "E:\Project\Mass upload shopee\tools\corel.py" set "ALAT=E:\Project\Mass upload shopee"

if not defined ALAT (
	echo.
	echo   TIDAK BISA JALAN: tools\corel.py tidak ketemu.
	echo.
	echo   Berkas ini dijalankan dari:
	echo     %~dp0
	echo.
	echo   Perbaikannya, pilih salah satu:
	echo     1. Pindahkan berkas ini ke folder "tools" milik alat Mass
	echo        upload ^(yang berisi corel.py^), lalu jalankan dari sana.
	echo     2. Buka berkas ini dengan Notepad dan isi barisnya:
	echo          set "ALAT=D:\letak\Mass upload shopee"
	echo.
	echo   Tidak dicoba lagi otomatis - path yang salah tidak akan
	echo   berubah benar dengan menunggu.
	echo.
	pause
	exit /b 1
)

cd /d "%ALAT%"

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
echo   Alat: %ALAT%
echo   Berhenti: tekan Ctrl+C dua kali, atau tutup jendela.
echo.

:ulang
python tools\corel.py %ARG%

rem Kode 3 = sudah ada pengambil lain di PC ini, mis. tugas Windows yang
rem menyala sendiri. Berhenti, jangan ikut mengantre: satu PC punya satu
rem CorelDRAW, dan dua pengambil bisa menjalankan dua macro sekaligus di
rem dokumen yang sama.
if errorlevel 3 if not errorlevel 4 (
	echo.
	echo   Sudah ada Desain Host lain yang jalan di PC ini - jendela ini
	echo   tidak diperlukan dan akan tertutup sendiri.
	echo.
	rem Sengaja TIDAK pause: tugas Windows memanggil berkas ini tiap 5 menit,
	rem dan tiap panggilan yang kebetulan menemukan pengambil lain akan
	rem meninggalkan satu jendela menggantung menunggu tombol. Sepuluh detik
	rem cukup untuk dibaca orang yang kebetulan mengkliknya sendiri.
	%SystemRoot%\System32\timeout.exe /t 10 >nul
	exit /b 0
)

rem Sampai di sini berarti pengambilnya berhenti: CorelDRAW ditutup di
rem tengah jalan, jaringan putus, atau Python galat. Dinyalakan lagi
rem sendiri - operator tidak akan sadar jendelanya mati diam-diam, dan
rem pekerjaan yang menumpuk di antrean baru ketahuan saat ditanyakan.
echo.
echo   [%date% %time%] pengambil berhenti - menyalakan ulang 15 detik lagi.
echo   (kalau ini terus berulang, baca pesan galat di atas)
echo.
rem Dipanggil dengan path penuh: kalau Git Bash ada di PATH, "timeout"
rem bisa tertuju ke timeout.exe miliknya yang argumennya berbeda, dan
rem jeda ini berubah jadi galat yang membuat loop berputar tanpa jeda.
%SystemRoot%\System32\timeout.exe /t 15 /nobreak >nul
goto ulang
