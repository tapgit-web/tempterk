# Windows application packages

Built version: 2.5.2, Windows x64.

- `dist/TempTrek Environmental Monitoring System.exe`: standalone application, including Python and required libraries.
- `dist/TempTrek-2.5.2-x64.msi`: native per-machine Windows Installer package. Installs to 64-bit Program Files, creates Start menu and desktop shortcuts, and supports uninstall through Windows Installed apps.

Run `build.bat` using Windows Python 3.12 to rebuild both artifacts. `build_msi.py` reads the version from `installer.iss`; increase that version for a new release. The MSI builder uses Python 3.12's Windows-only `msilib`, which was removed in Python 3.13. No WiX or Inno Setup installation is required for this path. `installer.iss` remains available separately if an Inno Setup EXE installer is also desired.

The packages do not contain customer settings, passwords, licenses, backups, or recorded data. On a clean Windows profile the app prompts for a Super Admin password. Existing runtime data stays in `%APPDATA%\TempTrek`; backup management requires an elevated administrator session. Builds are not code-signed.

The executable requests Windows administrator rights at launch via its PyInstaller manifest (`uac_admin=True`). Accept the Windows UAC prompt to run the app with the rights required for backup management. This applies to both installer formats and the standalone executable after rebuilding. For an existing build, close TempTrek and use **Run as administrator**. Application backup permissions are still required. If elevation uses a different Windows account, that account has its own `%APPDATA%` and application data.

## Previous release validation (2.5.1)

- PyInstaller build succeeded and archive inspection confirmed the application, Tkinter, Modbus and encryption modules are embedded.
- `python verify_exe.py` reached first-time administrator setup using temporary application data, then stopped only the test instance.
- MSI database creation and file-table validation passed.
- Windows Installer administrative extraction (`msiexec /a ... /qn TARGETDIR=...`) returned exit code 0. SHA-256 comparison confirmed the extracted EXE is identical to the standalone build.
- No system-wide install/uninstall or upgrade was performed on this workstation.

MSI UI progress events follow Microsoft's [ProgressBar control documentation](https://learn.microsoft.com/en-us/windows/win32/msi/progressbar-control). Installer progress details are in `build/msi-extraction-check.log`.


## Version 2.5.2 validation

Rebuilt EXE and MSI successfully. python verify_package.py passed: embedded icon resources, requireAdministrator manifest, bundled certifi/cacert.pem, and desktop/Start menu MSI icon references. The MSI now uses an EXE icon stream with a matching .exe identifier, as required for advertised shortcuts. No installation, upgrade, or desktop visual check was performed for this release.

