# TempTrek Environmental Monitoring System v2 - Project Work Log

## Windows packaging (September 8, 2026)

- Built the standalone x64 EXE and native `TempTrek-2.5.1-x64.msi` in `dist`.
- Added `build_msi.py` and updated `build.bat` to produce both packages with Windows Python 3.12. MSI includes installation/completion UI, an embedded cabinet, shortcuts, uninstall registration, and upgrade metadata.
- Verified required modules in the EXE and confirmed it reaches first-time setup using isolated application data.
- MSI administrative extraction returned 0; extracted EXE SHA-256 matches the standalone build. Full system install/uninstall/upgrade remains untested. No signing certificate was applied.
- Added `BUILDING.md`, distribution instructions, and checksums. No customer data or credentials are included.

## Security and recovery fixes (September 8, 2026)

- Approval startup follow-up: added exact legacy configuration decoding using the key established by the validated company header. Preserves each recovered file as `.legacy-original` and never resets approval history. All 25 tests pass. A separate hidden-window startup check using temporary copies of all 10 actual configuration files passed, retaining all three approval-history entries.

- Startup follow-up: recovered the legacy company header encoded with a different machine key. Added a narrowly scoped company-settings migration that preserves the original as `company_details.json.legacy-original`; genuinely damaged files and account settings still fail closed. All 24 regression tests pass, including three new migration checks.

- Replaced default-account recovery with fail-closed loading and first-run Super Admin password setup. Added salted PBKDF2 hashes, legacy hash upgrades, and mandatory default-password replacement.
- Moved settings to Windows DPAPI encryption and atomic writes. Corrupt configuration is preserved and reported instead of overwritten with defaults.
- Added `storage_security.py` and `backup_engine.py`: encrypted portable backups, administrator-owned storage, explicit ACL errors, shared storage/operation locks, pinned restore input, retention-safe safety backups, transactional folder replacement, rollback, and startup recovery.
- Added a transferred-backup file selector, missing-file history status, backup-password setup, and restart enforcement after restoration stops monitoring.
- Required verified SMTP TLS on the configured host, with no unverified/plaintext credential fallback.
- Made CSV/audit write failures visible and preserved audit identity across new portable backups.
- Retired plaintext export and removed exported application data from installer inputs. Added the encryption dependency to the build and installed it in the current Python environment.
- Validation: all 21 regression tests passed on Python 3.12, including Windows DPAPI, read-only ACL descriptor generation, isolated hidden-window startup, backup/restore integration, and failure recovery. Actual project data was not restored or migrated during testing.
- See [security_changes.md](security_changes.md) for setup, compatibility limits, and remaining live-device/SMTP/packaged-EXE checks. Existing legacy backup archives and plaintext exports have not been altered.

## Implementation Update (September 7, 2026)

- Added exact Viewed-record tracking with username, workstation, date/time, first/last view, and complete view history.
- Required approvers to load and review the exact device/time selection before password-authenticated approval.
- Added explicit PDF limits of eight registers and 50,000 records.
- Added approval status to CSV exports and made general/scheduled PDFs DRAFT whenever any included record is unapproved.
- Added validated, de-duplicated multiple email recipients with a maximum of 50 addresses.
- Added approval-aware scheduled email reports and detailed success, partial, and failure auditing.
- Added SMS delivery success/failure events to the main audit trail.
- Expanded audit records with workstation/device, previous value, updated value, result, details, and chained integrity hashes.
- Added legacy audit-log migration, tamper detection, read-only protection, and operating-system user access restrictions where supported.
- Added automatic and manual ZIP backups containing configuration, readings, approvals, viewed history, users, notification settings, audit logs, reports, and backup history.
- Added configurable backup interval, local destination, maximum backup count, and maximum storage allocation.
- Added backup status/error reporting, manifest metadata, integrity checking, retention, and history display.
- Added password-authorized restore with archive validation, safe extraction checks, mandatory pre-restore backup, polling shutdown, detailed summary, and audit records.
- Reorganized navigation into Data, Reports / PDF, Notifications, and Administration sections.
- Updated `feature_verification_report.md` with the implemented functionality and required release tests.
- Python AST syntax validation and a headless module-import smoke test passed for the updated `2.py` source.

## Project Summary
* **Last Code Modification Date:** June 12, 2026
* **Latest Runtime Activity Date:** June 12, 2026 (Active app execution)

---

## Active Development Updates (June 12, 2026)
* **Custom Polling & CSV Logging Intervals per Slave ID:** Added support for configurable CSV saving/logging intervals while maintaining real-time (1-second) UI updates.
  * **UI Additions:** Introduced a "TIME INTERVAL TO STORE (SEC)" input field to the device node configuration form (when adding/editing a node) to specify how often data is saved to the CSV file.
  * **Threading & Logging Split:** Configured Modbus polling loops to read registers every **1.0 second** (maintaining live dashboard updates and real-time alerts), but throttled the CSV log writes so they are only recorded at the configured interval.
* **Import/Export Configuration & Alarm Rules:** Added the ability to backup and restore configurations to and from local JSON files.
  * **Device Configurations:** Added `EXPORT CONFIG` and `IMPORT CONFIG` buttons under Node Config to back up and restore/merge the list of Monitored Devices.
  * **Alarm/Alert Rules:** Added `EXPORT ALERTS` and `IMPORT ALERTS` buttons under Alert Rules to back up and restore/merge the list of custom threshold alarms.
* **Cursor Color Visibility Fix:** Fixed a usability issue where the typing insertion cursor inside the Alarm/Email configuration entry fields was white, making it invisible against the modern light grey theme. The cursor color has been updated to **black** to ensure high visibility.
* **BOOLEAN Data Type Support:** Implemented a new BOOLEAN data type with custom state labels:
  * **UI Configurations:** In the register mapping configuration panel, selecting **BOOLEAN** dynamically displays two fields to define custom names/labels for state `0` (e.g., `OFF`, `Normal`) and state `1` (e.g., `ON`, `Alarm`).
  * **Modbus Reading & Simulator:** Integrated decoding of boolean values in the live modbus polling thread, displaying the custom labels in the live logs and dashboard while saving the numeric states `0` or `1` into CSVs. The dummy simulator was extended to randomly generate simulated boolean states.
  * **Table & PDF Export Mapping:** Updated the historical trend table and PDF report generator to translate CSV-logged `0` and `1` values back to their user-defined names.
* **Device Form Compact Layout Fix:** Re-organized the Node Config entry fields (IP, Port, Slave ID, and Store Interval) into side-by-side horizontal rows. This optimization saves vertical space and ensures the "SAVE NODE" button remains fully visible even when the "BOOLEAN" value names frame is expanded.
* **Modbus Scaling & Decimal Point Display Fix:** Fixed Modbus integer-to-decimal decoding and formatting behavior.
  * **Modbus Decoding:** The Modbus worker thread now automatically divides integer readings (e.g., `INT16`, `UINT16`, `INT32`, `UINT32`) by `10 ** decimals` to correctly shift the decimal point (e.g., raw value `459` with `1` decimal point becomes `45.9` instead of `459.0`).
* **Modbus Coils & Discrete Inputs Support (FC01 / FC02):** Added full support for reading output coils and discrete inputs.
  * **UI Selector:** Added a **MODE** dropdown to the Register Mapping interface (options: `FC01` for Coils, `FC02` for Discrete Inputs, `FC03` for Holding Registers, `FC04` for Input Registers), defaulting BOOLEAN types automatically to `FC01`.
  * **Worker Thread Integration:** Updated the Modbus worker thread to query the correct Modbus function codes (e.g., `read_coils`, `read_discrete_inputs`) and correctly decode bit response formats (`result.bits`). Math division and scaling operations are now bypassed for BOOLEAN registers to prevent calculations on label strings.
  * **List Mapping Display:** Added a **MODE** column to the Monitored Registers list (mini-treeview) to easily review and manage configured function codes per register.
  * **Excel & PDF Export Formatting:** Updated the Excel/CSV Pivot Export and PDF Report Export engines to translate logged `0`/`1` boolean values back to their user-configured state labels (e.g. `"ON"` / `"OFF"`), and updated the PDF threshold alert checkers to interpret the state labels correctly.
  * **User-Friendly Mode Naming:** Changed Register Mapping Mode dropdown options from technical codes (`FC01`, `FC02`, `FC03`, `FC04`) to user-friendly names (`Output Coil`, `Input Coil`, `Holding Register`, `Input Register`) while maintaining backward-compatible storage.
  * **BOOLEAN Unit Handling:** Automatically set, force, and hide units (set to `"na"`) for BOOLEAN register configurations so that no unit suffix is appended to status labels in logs or dashboard cards.

---

## Last Active Development Files (May 23, 2026)
The last code changes, builds, and installation scripts were updated on this date:

| File Name | Modification Date & Time | Description |
| :--- | :--- | :--- |
| `TempTrek Environmental Monitoring System_Setup.exe` | 2026-05-23 14:42:51 | Compiled setup installer. |
| `installer.iss` | 2026-05-23 14:42:18 | Inno Setup compiler script. |
| `TempTrek Environmental Monitoring System.spec` | 2026-05-23 14:39:19 | PyInstaller specification file for build. |
| `unlock_data.py` | 2026-05-23 14:31:19 | Python script for data/encryption utility. |
| `2.py` | 2026-05-23 14:09:12 | Main Python GUI / application file. |

---

## Runtime & Configuration Files (June 12, 2026)
These configuration files are updated automatically during application execution. They reflect the current system activity and local run times:

| File Name | Directory | Last Write Time |
| :--- | :--- | :--- |
| `trend_settings.json` | `\config` | 2026-06-12 18:48:09 |
| `users.json` | `\config` | 2026-06-12 18:45:21 |
| `email_settings.json` | `\config` | 2026-06-12 18:45:21 |
| `devices.json` | `\config` | 2026-06-12 18:45:21 |

---

*Log generated on 2026-06-12 18:54:29.*
