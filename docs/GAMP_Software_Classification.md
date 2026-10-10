# TempTrek Environmental Monitoring System v2.5.5
## GAMP 5 Software Classification & Computer System Validation (CSV) Guide

---

### Executive Summary

| Attribute | Classification Details |
| :--- | :--- |
| **Software Name** | TempTrek Environmental Monitoring System |
| **Current Version** | `2.5.5` (Windows x64) |
| **GAMP 5 Primary Category** | **Category 4: Configured Software** (Standard Deployment) |
| **GAMP 5 Secondary Category** | **Category 5: Custom / Bespoke Software** (When source code is modified/customized) |
| **Regulatory Scope** | FDA 21 CFR Part 11, EU Annex 11, GxP / GDP / GMP Environmental Monitoring |
| **System Purpose** | Real-time Modbus TCP sensor data acquisition, threshold alarm notifications, audit trail logging, and report generation |

---

## 1. What is GAMP 5?

**GAMP** (*Good Automated Manufacturing Practice*) is a set of guidelines published by the **ISPE** (*International Society for Pharmaceutical Engineering*). It provides a risk-based approach to compliant GxP Computer System Validation (CSV) accepted by global regulatory authorities including the **US FDA**, **EMA (Europe)**, and **MHRA (UK)**.

---

## 2. TempTrek GAMP 5 Software Category Rationale

Under GAMP 5 (Second Edition), software systems are categorized from 1 to 5 to establish the appropriate lifecycle model and validation testing scope:

### Primary Classification: GAMP Category 4 (Configured Software)
In standard production deployments, TempTrek is delivered as a compiled binary package (`TempTrek-2.5.5-x64.msi` or `TempTrek Environmental Monitoring System.exe`). 

Site administrators configure system parameters through the application interface without modifying the underlying Python source code:
* **Device Node Configuration**: Modbus TCP IP addresses, ports, slave IDs, and storage intervals (`devices.json`).
* **Register Mapping**: Mapping coils and registers (`FC01`, `FC02`, `FC03`, `FC04`) to sensor channels and custom labels.
* **Alarm Rules & Notifications**: Setting high/low threshold limits and recipient email lists (`email_settings.json`).
* **User Access Control**: Setting user roles, permissions, and password policies (`users.json`).

*Validation Requirement for Category 4*: Focuses on verifying the **Configuration Specification (CS)**, User Requirement Specification (URS), and conducting IQ, OQ, and PQ testing.

---

### Secondary Classification: GAMP Category 5 (Custom / Bespoke Software)
When the underlying Python source code ([`2.py`](file:///d:/TAP/tempterk/tempterk/2.py), [`storage_security.py`](file:///d:/TAP/tempterk/tempterk/storage_security.py), [`backup_engine.py`](file:///d:/TAP/tempterk/tempterk/backup_engine.py)) is modified, extended, or customized for specific client requirements.

*Validation Requirement for Category 5*: Requires full Software Development Life Cycle (SDLC) validation, including Functional Specifications (FS), Detailed Design Specifications (DDS), Source Code Reviews, Unit Testing, and full IQ/OQ/PQ execution.

---

## 3. FDA 21 CFR Part 11 & EU Annex 11 Compliance Features

TempTrek implements technical controls required for electronic records and electronic signatures:

1. **Audit Trail & Data Integrity**:
   * Complete audit log capturing User ID, Workstation Name, Timestamp, Action Type, Previous Value, New Value, and Chained SHA-256 Hashes.
   * View-tracking records every user review of historical trend data.

2. **Access Control & Password Policy**:
   * Salted PBKDF2-SHA256 password hashing.
   * Enforced minimum 6-character password policy with non-repeat requirements.
   * Configurable password expiration (default 90 days).

3. **Data Security & Storage Protection**:
   * Windows DPAPI encryption for local configuration files (`config/`).
   * Exclusive Windows Administrators / SYSTEM ACL access controls on backup destination (`%ProgramData%\TempTrekBackups`).
   * Password-encrypted `.ttbackup` archives using AES-GCM encryption.
   * Atomic file writes to prevent data corruption during disk interruptions.

4. **Electronic Signatures & Report Controls**:
   * Password-authenticated report approval workflow.
   * Generated PDF reports automatically bear draft watermarks if containing unapproved sensor records.

---

## 4. Computer System Validation (CSV) Lifecycle Deliverables

For GxP site installation, the following CSV package should be assembled:

```
┌────────────────────────────────────────────────────────┐
│             User Requirement Specification (URS)       │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│          Functional Specification (FS) / Config Spec   │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                 Risk Assessment (RA)                   │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│      Installation Qualification (IQ) - Setup           │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  Operational Qualification (OQ) - Test Suite Execution │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Performance Qualification (PQ) - 24-72h Sensor Run     │
└────────────────────────────────────────────────────────┘
```

1. **User Requirement Specification (URS)**: Defines operational, monitoring, logging, and security requirements.
2. **Functional Specification (FS)**: Details system architecture, Modbus protocols, DPAPI security, and PDF generation.
3. **Configuration Specification (CS)**: Documents specific IP addresses, channel mappings, and alarm thresholds.
4. **Traceability Matrix (TM)**: Maps each URS requirement to FS specifications and OQ/PQ test scripts.
5. **IQ Execution**: Verifies host OS compatibility, administrative UAC rights, and directory creation.
6. **OQ Execution**: Executes automated unit test suite (`python -m unittest discover -s tempterk -p "test_*.py" -v`).
7. **PQ Execution**: Validates continuous sensor polling, alarm dispatches, and PDF report creation over a 24 to 72-hour trial.
