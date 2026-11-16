@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Zvonilka console
".venv\Scripts\python.exe" start.py
pause
