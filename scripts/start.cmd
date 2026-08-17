@echo off
title novel-rewriting console
rem ---------------------------------------------------------------------------
rem Double-click this file to start the console. Arguments pass through:
rem     tools\start.cmd -Dev
rem     tools\start.cmd -Base outputs\e2e_20260817_jiaoren
rem
rem ASCII ONLY IN THIS FILE. cmd.exe parses .cmd bytes in the console codepage
rem (936/GBK here), so UTF-8 Chinese in a rem line decodes short and swallows
rem the first character of the NEXT line -- "powershell" became "owershell" and
rem the launcher silently never ran. Chinese comments live in start.ps1, which
rem is UTF-8 with BOM and parses correctly.
rem
rem Going through cmd avoids the PowerShell execution policy: double-clicking a
rem .ps1 is blocked under the default policy.
rem ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set RC=%ERRORLEVEL%
rem Keep the window open on failure, otherwise a failed start just flashes by.
if not "%RC%"=="0" pause
exit /b %RC%
