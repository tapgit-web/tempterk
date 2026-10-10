# Windows application packages

Built version: 2.5.5, Windows x64.

- `dist/TempTrek Environmental Monitoring System.exe`: standalone application, including Python and required libraries.
- `dist/TempTrek-2.5.5-x64.msi`: native per-machine Windows Installer package. Installs to 64-bit Program Files, creates Start menu and desktop shortcuts, and supports uninstall through Windows Installed apps.

Run `build.bat` using Windows Python 3.12 to rebuild both artifacts. `build_msi.py` reads the version from `installer.iss`; increase that version for a new release. The MSI builder uses Python 3.12's Windows-only `msilib`, which was removed in Python 3.13. No WiX or Inno Setup installation is required for this path. `installer.iss` remains available separately if an Inno Setup EXE installer is also desired.

The packages do not contain customer settings, passwords, licenses, backups, or recorded data. On a clean Windows profile the app prompts for a Super Admin password. Existing runtime data stays in `%APPDATA%\TempTrek`; backup management requires an elevated administrator session. Builds are not code-signed.

The executable requests Windows administrator rights at launch via its PyInstaller manifest (`uac_admin=True`). Accept the Windows UAC prompt to run the app with the rights required for backup management. This applies to both installer formats and the standalone executable after rebuilding. For an existing build, close TempTrek and use **Run as administrator**. Application backup permissions are still required. If elevation uses a different Windows account, that account has its own `%APPDATA%` and application data.

## Latest rebuild validation (2.5.4, September 22, 2026)

- Includes monitoring indicators, configurable password expiry, merge restore with automatic monitoring resumption, DD-MM-YY display dates, FDA 21 CFR navigation grouping, and matching graph decimal precision.
- All 36 regression tests passed; clean EXE and MSI builds completed.
- Package resource checks passed. Embedded application and all six local feature/security modules match current source code.
- Checksums refreshed. Installation, upgrade and live hardware operation were not tested.

## Previous rebuild validation (2.5.3, September 21, 2026)

- Clean PyInstaller rebuild from the current source and successful MSI creation.
- All 26 storage regression tests passed.
- Package checks passed for shortcuts, embedded logo, administrator manifest and CA bundle.
- Embedded application code was compared with compiled current `2.py` and matched.
- `dist/SHA256SUMS.txt` was refreshed for the new EXE and MSI.
- Installation, upgrade and live device operation were not tested in this rebuild.

## Previous release validation (2.5.1)

- PyInstaller build succeeded and archive inspection confirmed the application, Tkinter, Modbus and encryption modules are embedded.
- `python verify_exe.py` reached first-time administrator setup using temporary application data, then stopped only the test instance.
- MSI database creation and file-table validation passed.
- Windows Installer administrative extraction (`msiexec /a ... /qn TARGETDIR=...`) returned exit code 0. SHA-256 comparison confirmed the extracted EXE is identical to the standalone build.
- No system-wide install/uninstall or upgrade was performed on this workstation.

MSI UI progress events follow Microsoft's [ProgressBar control documentation](https://learn.microsoft.com/en-us/windows/win32/msi/progressbar-control). Installer progress details are in `build/msi-extraction-check.log`.


## Version 2.5.2 validation

Rebuilt EXE and MSI successfully. python verify_package.py passed: embedded icon resources, requireAdministrator manifest, bundled certifi/cacert.pem, and desktop/Start menu MSI icon references. The MSI now uses an EXE icon stream with a matching .exe identifier, as required for advertised shortcuts. No installation, upgrade, or desktop visual check was performed for this release.

