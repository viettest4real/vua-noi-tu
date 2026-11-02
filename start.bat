@echo off
title U Nu - Vua Loi Tu Discord Bot
cd /d "%~dp0"
chcp 65001 > nul

set "PYTHON=C:\Users\PC\AppData\Local\Programs\Python\Python313\python.exe"

echo ===================================================
echo     DANG KHOI DONG BOT NOI TU (U NU - VUA LOI TU)
echo ===================================================
echo.

if exist words.txt goto :run_bot

echo [1/2] Dang tai va khoi tao tu dien words.txt...
"%PYTHON%" build_dict.py
if not exist words.txt (
    echo.
    echo [LOI] Khong the tao file words.txt!
    echo Vui long kiem tra lai ket noi Internet.
    goto :end
)

:run_bot
echo [1/2] Tu dien words.txt: DA SAN SANG.
echo [2/2] Dang ket noi toi Discord...
echo (Neu thanh cong, bot se hien dong 'Bot noi tu da online...')
echo.

"%PYTHON%" bot.py

echo.
echo ===================================================
echo Bot da dung hoat dong.
echo ===================================================

:end
echo.
echo Cua so se giu nguyen de ban xem thong bao loi tren.
pause
