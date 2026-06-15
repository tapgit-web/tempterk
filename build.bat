@echo off
setlocal enabledelayedexpansion

echo ========================================================
echo          DN PRO - Build ^& Installation System        
echo ========================================================

rem Check dependencies
echo [1/3] Checking requirements...

python -m pip install pyinstaller Pillow reportlab requests pymodbus >nul

rem Run PyInstaller
echo [2/3] Generating Executable (this may take a minute)...
python -m PyInstaller --noconfirm --onefile --windowed --icon "icon.ico" --add-data "icon.ico;." --add-data "icon.png;." --hidden-import=reportlab --name "TempTrek Environmental Monitoring System" 2.py

if %errorlevel% neq 0 (
    echo [ERROR] Build failed during PyInstaller phase.
    pause
    exit /b %errorlevel%
)

echo [3/3] Build Successful! Portable EXE is in dist\TempTrek Environmental Monitoring System.exe

echo.
echo --------------------------------------------------------
echo NEXT STEP: Create the Installation Wizard (Setup.exe)
echo --------------------------------------------------------
echo To create the "Installation EXE", you need Inno Setup (free).
echo 1. Open 'installer.iss' in Inno Setup.
echo 2. Press 'Compile' (F9).
echo.
echo If Inno Setup is in your PATH, the installer will be 'DN_PRO_Setup.exe'.

set ISCC_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist !ISCC_PATH! (
    echo Found Inno Setup Compiler. Building Installer...
    !ISCC_PATH! installer.iss
    echo Installer created: DN_PRO_Setup.exe
)

echo.
echo [DONE]
explorer dist
pause
