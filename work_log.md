# TempTrek Environmental Monitoring System v2 - Project Work Log

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
