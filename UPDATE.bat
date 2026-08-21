@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Perbarui Tools Shopee Mass Upload
echo ============================================
echo   Memeriksa pembaruan dari GitHub
echo ============================================
echo.
python "tools\shopee_mass_upload.py" perbarui
echo.
set /p lanjut="Pasang pembaruan sekarang? (y/n): "
if /i not "%lanjut%"=="y" goto akhir
echo.
python "tools\shopee_mass_upload.py" perbarui --pasang
echo.
echo Selesai. Jalankan WEB.bat untuk memakai versi baru.
:akhir
echo.
pause
