@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "DATA=%MLUVKA_DATA%"
if "%DATA%"=="" set "DATA=%LOCALAPPDATA%\Mluvka"
start "" "%DATA%\venv\Scripts\pythonw.exe" -m mluvka
