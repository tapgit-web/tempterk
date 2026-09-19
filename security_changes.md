# Storage and security fixes — September 8, 2026

## Changed behavior

- Missing/corrupt user databases on existing installations stop startup instead of recreating default privileged accounts. Fresh installations prompt for a new Super Admin password. Existing default passwords must be changed at login.
- New passwords use salted PBKDF2-SHA256; successful login upgrades legacy SHA-256 hashes. New/changed passwords require 12 characters.
- Settings are encrypted with Windows DPAPI for the Windows account running the application. Existing plaintext and hardware-obfuscated settings remain readable on their original machine/account and migrate after successful loading. Failed reads preserve the original files. Writes use unique temporary files, flush, then atomic replacement.
- Backup management requires both the application's backup permission and an elevated Windows administrator session. Automatic backups require elevation too; the status shows when administrator rights or password setup is missing.
- New backups use password-authenticated encryption (`.ttbackup`). Configuration inside the encrypted archive is portable; it is re-encrypted for the destination Windows account during restore. Store the backup password separately. It is saved locally under DPAPI protection and excluded from backup settings inside the archive and audit setting diffs.
- New backup storage is restricted to `%ProgramData%\TempTrekBackups` and its subfolders. The owner and complete access-control list are set to Windows Administrators/SYSTEM, and permission failures fail the operation. Existing custom locations switch to the managed destination; old archives are not deleted or retroactively secured. Use **SELECT BACKUP FILE** to restore an old archive or a transferred backup.
- Backup and restore share an operation lock. CSV/configuration/report writes share a storage lock so snapshots do not mix concurrent app writes. Restore refuses to replace data until monitoring workers have stopped. Restart is required after successful restore or after a restore attempt has stopped monitoring.
- Restore validates and pins the selected archive, stages and validates configuration before modification, and requires a safety backup without retention cleanup. Original folders are renamed aside with a recovery journal. Failures roll back; startup recovers an interrupted uncommitted replacement. Retention only deletes TempTrek-named archives and protects the current archive.
- Disk/audit write failures reach the UI instead of silently disappearing. Audit identity is retained in encrypted configuration so new portable backups preserve historical hash verification across machines.
- SMTP uses the configured host only, verifies TLS certificates and requires STARTTLS for non-465 ports. It never retries with certificate verification disabled or sends credentials over plaintext SMTP.
- `unlock_data.py` no longer writes plaintext credentials or records. The installer no longer bundles exported user data. Transfer data with encrypted backups.

## Installation and use

The build script installs `cryptography>=46,<51` along with the existing dependencies. Python 3.12 was used for verification. Run the app as the same Windows account that owns its current configuration; elevation under a different account has a different Windows profile and DPAPI identity.

Open **Administration > Backup & Restore** in an elevated administrator session. Use **SAVE SETTINGS** or **CREATE BACKUP NOW** to set the backup encryption password. The backup password is separate from the application login password. Regular Windows sessions can monitor devices but cannot manage backups in the protected destination.

Old ZIPs remain readable only where their legacy configuration can be decoded. Create a new encrypted backup on that machine before moving to another computer. Existing unencrypted archives and already-created plaintext export bundles are not altered by this source change.

Backups currently support at most 512 MiB compressed and 2 GiB uncompressed data, with a 100,000-member extraction limit. Exceeding a limit fails explicitly. Retention may temporarily exceed configured limits to preserve a restore source and safety archive. Protection applies to application-managed storage; Windows administrators can still change permissions and delete archives.

## Verification

All 24 tests passed on September 8, 2026 using Windows and Python 3.12.10. The additional company-header migration tests cover a legacy file from another machine, original-file preservation, and rejection of damaged/DPAPI data. The targeted migration preserves `company_details.json.legacy-original` before normal startup saves the recovered company values using DPAPI.

Run `python -m unittest discover -s tempterk -p test_storage_security.py -v` from the workspace root. The suite covers password compatibility, real Windows DPAPI, authenticated encryption, atomic-write failures, rollback and restart recovery, retention, rejected archive paths, storage error reporting, SMTP failures, backup/restore integration, and isolated first-run startup. It uses temporary data; it does not restore or modify the actual project configuration.

Before distributing a rebuilt executable, check deletion using separate standard-user and elevated-admin Windows sessions, live Modbus monitoring, a real SMTP server, and the packaged EXE. The automated SMTP/device checks do not contact production services.
