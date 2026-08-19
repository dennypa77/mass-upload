@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Tools Shopee Mass Upload
echo ============================================
echo   0. impor  - ekspor sheet SKU  -^> data/sku.csv
echo   1. foto   - salin ^& rename foto dari Drive
echo   2. cek    - laporan kelengkapan data ^& foto
echo   3. build  - buat file Excel di output/
echo   4. semua  - foto + cek + build
echo ============================================
set /p pilih="Pilih (0-4): "

if "%pilih%"=="0" goto impor
if "%pilih%"=="1" set perintah=foto& goto jalan
if "%pilih%"=="2" set perintah=cek& goto jalan
if "%pilih%"=="3" set perintah=build& goto jalan
if "%pilih%"=="4" set perintah=semua& goto jalan
echo Pilihan tidak dikenal.
pause
exit /b 1

:impor
echo.
echo Seret file ekspor SKU (.xlsx) ke jendela ini, lalu tekan Enter.
set /p berkas="File: "
echo.
python "tools\shopee_mass_upload.py" impor %berkas%
goto akhir

:jalan
echo.
python "tools\shopee_mass_upload.py" %perintah%

:akhir
echo.
pause
