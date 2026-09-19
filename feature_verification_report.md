# TempTrek Software Implementation and Verification Report

> September 8, 2026 update: the backup, restore, account recovery, encryption and storage behavior described below has been superseded by [security_changes.md](security_changes.md). The new storage regression suite passes all 21 tests; live service and packaged application checks remain required.

## Overall Result

The requested approval, reporting, notification, auditing, automatic backup, and restore enhancements have now been implemented in the TempTrek source code.

The updated source passes Python AST syntax validation and a headless module-import smoke test. Functional testing with live devices, SMTP, Fast2SMS, and production backup data is still required before release.

## Current Implementation Status

| Requirement | Status | Implementation |
|---|---|---|
| PDF and CSV export | Implemented | Export actions are available from View Data, Approved Data, and the general export screen. CSV exports include an Approval Status column. |
| PDF limits | Implemented | A PDF supports a maximum of 8 registers and 50,000 records. An explanatory error is displayed when a selection exceeds the limit. |
| DRAFT reports | Implemented | A report remains DRAFT when any included record is unapproved. Approved-only reports are marked FINAL / APPROVED. |
| View Data tracking | Implemented | Every displayed record is saved as viewed with username, date/time, workstation, device identity, first/last view, and view history. |
| Data approval | Implemented | An approver must load and review the exact data selection and re-enter their password before approval. |
| Approved Data section | Implemented | Approved readings are separated from draft readings and appear under Approved Data. |
| Approval history | Implemented | Approval range, approver, approval time, workstation, notes, and record count are retained. |
| Multiple email recipients | Implemented | Comma- and semicolon-separated addresses are accepted, validated, de-duplicated, and limited to 50 recipients. |
| Scheduled report emails | Implemented | Intervals of 5 minutes, 30 minutes, 1 hour, 6 hours, 12 hours, and 24 hours are supported while TempTrek is running. Emailed PDFs now follow the approval workflow. |
| Email auditing | Implemented | Configuration changes, scheduled delivery results, individual failures, and alarm-email results are audited. |
| SMS alerts | Implemented | Recipient numbers, conditions, intervals, enable/disable state, message templates, service response, and success/failure activity are supported and audited. |
| Audit log | Implemented | Audit entries contain timestamp, user, workstation/device, action, category, previous value, updated value, result, details, and a chained integrity hash. |
| Audit protection | Implemented | Audit files are marked read-only, restricted to the current operating-system user where supported, and verified through a chained integrity hash when displayed. |
| Automatic backup | Implemented | Automatic ZIP backup runs at the configured interval in a background thread while TempTrek is operating. |
| Backup content | Implemented | Configuration, readings, approvals, viewed history, users, device information, email/SMS settings, audit data, reports, and backup history are included. |
| Backup status/history | Implemented | Last attempt, last success, current result, error details, file, date/time, size, location, record counts, and included contents are retained and displayed. |
| Backup storage controls | Implemented | Authorized users can configure the local location, interval, maximum backup count, and maximum storage allocation. |
| Backup access protection | Implemented | Backup directories/files receive current-OS-user access restrictions where supported. Backup files are marked read-only. |
| Restore | Implemented | Authorized users can select and validate a local backup, authenticate, confirm its details, and restore data/configuration. |
| Pre-restore protection | Implemented | A successful current-state backup is mandatory before restoration can modify application data. |
| Restore result | Implemented | The result displays restored record/configuration/audit counts, restored file, date/time, pre-restore backup, status, and errors. |
| Restore auditing | Implemented | Successful and failed restore operations are written to the audit trail and backup history. |
| Menu structure | Implemented | Navigation is grouped under Data, Reports / PDF, Notifications, and Administration. |

## Supported Limits

| Item | Supported Value |
|---|---|
| Registers in one PDF | 8 |
| Records in one PDF | 50,000 |
| Email recipients | 50 unique valid addresses |
| Automatic backup intervals | 5 minutes, 30 minutes, 1 hour, 6 hours, 12 hours, or 24 hours |
| Default automatic backup interval | 6 hours |
| Default maximum backup count | 30 backup files |
| Default local backup storage allocation | 2,048 MB |
| Default backup location | `backups` under the TempTrek application-data directory |

## Approval Workflow

1. An authorized user opens **Data > View Data**.
2. The user selects a device and exact date/time range and clicks **Load Data**.
3. Displayed records are marked Viewed and retain the user, workstation, and viewing time.
4. An authorized approver clicks **Approve Data**.
5. TempTrek verifies that the exact selection was loaded and reviewed.
6. The approver re-enters their password.
7. Approval information is saved and written to the audit log.
8. Approved records disappear from View Data and appear under **Approved Data**.
9. View Data PDFs remain DRAFT. Approved Data PDFs are final.
10. General and scheduled PDFs are final only when every included record is approved.

## Automatic Backup Workflow

1. TempTrek checks the configured backup interval while the application is running.
2. When due, a background backup operation starts.
3. A BACKUP_STARTED audit record is created.
4. TempTrek counts recorded data, configuration items, and audit records.
5. A manifest and the `config`, `live`, and `reports` folders are written into a ZIP archive.
6. The completed archive is made read-only and receives local access restrictions where supported.
7. Backup settings and history are updated with status, time, size, location, counts, and errors.
8. Retention rules enforce the configured maximum backup count and storage allocation.
9. Success or failure is written to the audit trail.

## Restore Workflow

1. An authorized user opens **Administration > Backup & Restore**.
2. The user selects a successful local backup.
3. TempTrek validates the ZIP structure, manifest, and archive integrity.
4. The user re-enters their password.
5. TempTrek displays the selected backup details and requests confirmation.
6. A mandatory PRE_RESTORE backup is created.
7. Device polling threads are stopped and active clients are closed.
8. Archive paths are checked to prevent unsafe path extraction.
9. Configuration, live data, and reports are restored.
10. Application settings and histories are reloaded.
11. A detailed restore summary and restart instruction are displayed.
12. Success or failure is stored in both the audit trail and backup history.

## Audit Record Structure

Each new audit record contains:

- Timestamp
- Username
- Workstation or monitored-device information
- Action
- Category
- Previous value
- Updated value
- Status/result
- Details
- Chained integrity hash

Legacy five-column audit files are automatically upgraded when the next entry is written. The Audit Log screen verifies the hash chain and displays **TAMPER WARNING** when an entry does not pass verification.

Sensitive SMTP passwords and SMS API keys are excluded from previous/updated-value audit snapshots.

## Required Release Testing

The following tests should be completed on the target Windows device before deployment:

- Load View Data and confirm Viewed history is created.
- Attempt approval without loading data and confirm it is rejected.
- Approve a reviewed range and confirm it moves to Approved Data.
- Export draft, approved, and mixed PDF ranges and inspect their status markings.
- Verify the eight-register and 50,000-record PDF limits.
- Save 1, 50, and more than 50 email recipients.
- Send scheduled DRAFT and FINAL / APPROVED email reports.
- Trigger successful and failed email/SMS notifications and inspect the audit log.
- Run a manual backup and inspect its manifest and included files.
- Confirm automatic backup runs at each supported interval.
- Verify backup retention by count and maximum storage allocation.
- Attempt backup and restore using an unauthorized application user.
- Restore a test backup and verify the mandatory PRE_RESTORE archive.
- Confirm readings, approvals, users, viewed history, email/SMS settings, audit records, and reports after restoration.
- Modify a test audit row and confirm TAMPER WARNING appears.
- Rebuild the Windows executable and installer from the updated source.

## Source References

- Limits and storage constants: `2.py`
- Viewed-record storage: `viewed_records.json` at runtime
- Backup settings: `backup_settings.json` at runtime
- Backup history: `backup_history.json` at runtime
- Local backups: `backups` directory at runtime
- Main implementation: `2.py`
