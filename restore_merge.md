# Merge restore

Restore merges the selected backup with current data after stopping monitoring and creating the required safety backup. The merged folders are prepared in staging and installed using the existing rollback transaction. After success, the application stays open and previously running devices resume automatically in their original live or simulation mode. Devices that were stopped remain stopped. Recording pauses during the merge; continuous sampling during that interval is not provided. Failed restores that stopped monitoring still require a restart for recovery.

- Current-only files remain; backup-only data files are added.
- Compatible reading CSV files are merged in timestamp order. Exact duplicate rows are skipped. For the same timestamp, IP, port, slave and register, the current reading wins a conflicting value; the backup file is preserved separately.
- Current accounts, passwords, device settings and notification settings win file conflicts. Conflicting backup copies remain available for review.
- Approval, viewed-record and backup-history JSON collections are combined, with current scalar values winning conflicts and identical list entries deduplicated.
- Backup audit chains are kept separately rather than appended to the current audit chain.
- Conflicting files and imported audit files are saved under `reports/restored_backups`, using content hashes for stable names. They are not automatically shown as active audit events.

The backup's record count is no longer presented as the number of newly restored records. Existing device configuration remains authoritative; backup-only devices may need to be configured to view their readings.

Validation: 32 tests passed across merge restore, storage and password policy suites. Tests use isolated data; no production restore or MSI rebuild was performed.
