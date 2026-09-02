@echo off
title novel-rewriting restart console
rem Explicit double-click restart entry. start.cmd itself keeps the safe
rem start-or-reuse behavior; -Restart verifies the Novel API is idle before
rem stopping its listener and loading the current backend code.
call "%~dp0start.cmd" -Restart %*
exit /b %ERRORLEVEL%
