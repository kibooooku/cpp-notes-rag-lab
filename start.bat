@echo off
setlocal
title CppNotes - Local Notebook QA
pushd "%~dp0"
if errorlevel 1 exit /b 1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "CPP_NOTES_EXIT=%ERRORLEVEL%"
popd
if "%CPP_NOTES_EXIT%"=="0" goto done
echo.
echo Startup failed. See the message above, then press any key to close.
pause
:done
exit /b %CPP_NOTES_EXIT%
