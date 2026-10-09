@echo off
rem Versi  : 1.0   (2026-10-09 - pertama)
rem ===================================================================
rem  Pasang / cabut "Desain Host" sebagai TUGAS WINDOWS
rem
rem  Lebih kuat daripada shortcut Startup, dan menggantikannya:
rem
rem    * menyala saat operator masuk Windows;
rem    * DIPERIKSA ULANG tiap 5 menit - kalau jendelanya tertutup atau
rem      prosesnya mati, ia hidup lagi sendiri paling lama 5 menit
rem      kemudian;
rem    * tidak pernah jalan dua kali sekaligus.
rem
rem  Klik dua kali untuk MEMASANG, jalankan lagi untuk MENCABUT.
rem
rem  CATATAN JUJUR: "selama PC menyala" di sini berarti selama ada yang
rem  LOGIN. CorelDRAW butuh desktop; tugas yang jalan sebagai SYSTEM
rem  sebelum ada yang login tidak bisa menyetirnya. Kalau PC host
rem  dibiarkan menyala tanpa login, nyalakan auto-login Windows-nya.
rem ===================================================================

setlocal
title Desain Host - pasang sebagai tugas Windows

set "SUMBER=%~dp0desain-host.bat"
set "TUGAS=Desain Host"

if not exist "%SUMBER%" (
	echo.
	echo   TIDAK JADI: desain-host.bat tidak ada di folder ini.
	echo   Folder yang diperiksa: %~dp0
	echo.
	pause
	exit /b 1
)

schtasks /Query /TN "%TUGAS%" >nul 2>&1
if %errorlevel%==0 (
	schtasks /Delete /TN "%TUGAS%" /F >nul
	echo.
	echo   DICABUT. Desain Host tidak lagi menyala sendiri.
	echo   Masih bisa dijalankan manual lewat desain-host.bat.
	echo.
	pause
	exit /b 0
)

rem Shortcut Startup dicabut kalau ada: dua jalur otomatis berarti dua
rem jendela yang sama-sama mengambil pekerjaan, dan dua CorelDRAW yang
rem berebut dokumen aktif.
set "PINTAS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Desain Host.lnk"
if exist "%PINTAS%" del "%PINTAS%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
	"$bat = '%SUMBER%';" ^
	"$act = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c \"' + $bat + '\"') -WorkingDirectory (Split-Path $bat);" ^
	"$trg = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\$env:USERNAME\";" ^
	"$trg.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition;" ^
	"$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero);" ^
	"Register-ScheduledTask -TaskName '%TUGAS%' -Action $act -Trigger $trg -Settings $set -Description 'Penghubung ERP - CorelDRAW. Menyala saat login, diperiksa ulang tiap 5 menit.' -Force | Out-Null"

schtasks /Query /TN "%TUGAS%" >nul 2>&1
if %errorlevel%==0 (
	echo.
	echo   TERPASANG. Desain Host menyala saat login dan diperiksa ulang
	echo   tiap 5 menit - jendela yang tertutup tidak lagi membuat
	echo   perintah ERP menumpuk tanpa ada yang mengerjakan.
	echo.
	echo   Nyalakan sekarang tanpa menunggu? Tekan tombol apa saja.
	pause >nul
	schtasks /Run /TN "%TUGAS%" >nul 2>&1
	echo   Dijalankan. Cari jendela "Desain Host" di taskbar.
	echo.
) else (
	echo.
	echo   GAGAL mendaftarkan tugas. Coba klik kanan berkas ini lalu
	echo   "Run as administrator".
	echo.
)
pause
