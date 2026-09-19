@echo off
setlocal
cd /d "%~dp0"
echo Building TempTrek EXE and MSI using Windows Python 3.12...
python -m pip install pyinstaller Pillow reportlab requests pymodbus "cryptography>=46,<51" certifi
if errorlevel 1 exit /b 1
python -m pip install --upgrade certifi
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm "TempTrek Environmental Monitoring System.spec"
if errorlevel 1 exit /b 1
python build_msi.py
if errorlevel 1 exit /b 1
echo Build completed. The standalone EXE and MSI installer are in dist.
endlocal
