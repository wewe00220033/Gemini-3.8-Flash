@echo off
chcp 65001 >nul
title تفريغ الصوت الذكي - Gemini AI

echo ============================================================
echo   🎙️ أداة تفريغ الصوت التلقائي السريع (Google Gemini Flash)
echo ============================================================
echo.

python "%~dp0transcribe_tool.py" %*

echo.
pause
