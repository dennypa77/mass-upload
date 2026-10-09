@echo off
rem Versi  : 1.0   (2026-10-09 - pertama)
rem ===================================================================
rem  Pasang / cabut "Desain Host" dari Startup Windows
rem
rem  Klik dua kali untuk MEMASANG: dibuatkan shortcut di folder Startup,
rem  sehingga penghubung ERP <-> CorelDRAW menyala sendiri tiap kali
rem  komputer dinyalakan dan operator masuk Windows.
rem
rem  Jalankan lagi untuk MENCABUT (shortcut-nya dihapus).
rem
rem  Taruh berkas ini di folder yang sama dengan desain-host.bat.
rem ===================================================================

setlocal
title Desain Host - pasang ke Startup

set "SUMBER=%~dp0desain-host.bat"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "PINTAS=%STARTUP%\Desain Host.lnk"

rem Dipasang menunjuk berkas yang SALAH tidak akan terlihat sampai PC
rem di-restart dan tidak ada yang mengambil pekerjaan - jadi diperiksa
rem sekarang, bukan nanti.
if not exist "%SUMBER%" (
	echo.
	echo   TIDAK JADI: desain-host.bat tidak ada di folder ini.
	echo   Folder yang diperiksa: %~dp0
	echo   Taruh kedua berkas di folder yang sama, lalu jalankan lagi.
	echo.
	pause
	exit /b 1
)

if exist "%PINTAS%" (
	del "%PINTAS%"
	echo.
	echo   DICABUT. Desain Host tidak lagi menyala sendiri saat PC dinyalakan.
	echo   Masih bisa dijalankan manual lewat desain-host.bat.
	echo.
	pause
	exit /b 0
)

rem Shortcut dibuat lewat PowerShell karena .lnk berformat biner - tidak
rem bisa ditulis langsung dari batch.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
	"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%PINTAS%');" ^
	"$s.TargetPath = '%SUMBER%';" ^
	"$s.WorkingDirectory = '%~dp0';" ^
	"$s.Description = 'Penghubung ERP - CorelDRAW (Desain Host)';" ^
	"$s.Save()"

if exist "%PINTAS%" (
	echo.
	echo   TERPASANG. Desain Host akan menyala sendiri saat PC dinyalakan.
	echo   Shortcut: %PINTAS%
	echo.
	echo   Jalankan berkas ini sekali lagi kalau mau mencabutnya.
	echo.
) else (
	echo.
	echo   GAGAL membuat shortcut. Coba jalankan sebagai Administrator,
	echo   atau buat manual: tekan Win+R, ketik shell:startup, lalu salin
	echo   shortcut desain-host.bat ke folder yang terbuka.
	echo.
)
pause
