@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\voice-chat.ps1"
if errorlevel 1 pause
