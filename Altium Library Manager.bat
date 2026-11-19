@echo off
rem Double-click to start Altium Library Manager (no console window).
rem Uses Python 3.10+ from python.org: per-user install, the "py" launcher, or PATH.
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" (
  start "" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" -m alm
  exit /b
)
where pyw >nul 2>nul && ( start "" pyw -3 -m alm & exit /b )
where pythonw >nul 2>nul && ( start "" pythonw -m alm & exit /b )
echo Python 3 was not found. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH"), then double-click this file again.
pause
