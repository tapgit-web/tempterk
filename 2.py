import time
import math
import base64
import struct
import threading
import json
import os
import csv
import re
import webbrowser
from datetime import datetime, timedelta
from collections import deque
import random
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

try:
    from pymodbus.client.sync import ModbusTcpClient
except ImportError:
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        # Fallback for even older versions or different structures
        from pymodbus.client.sync import ModbusTcpClient as ModbusTcpClient


try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter, landscape
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

import pymodbus
PYMODBUS_V3 = pymodbus.__version__.startswith('3')

import sys
import shutil
import smtplib
import imaplib
import poplib
import ssl
import hashlib
import uuid
import requests
import subprocess
import platform
import stat
import tempfile
import zipfile
import getpass
import io
from backup_engine import BackupMixin
import queue
from pathlib import Path
from storage_security import (
    STORAGE_LOCK, STORAGE_FROZEN, STORAGE_ERRORS, storage_operation,
    DPAPI_PREFIX, BACKUP_MAGIC, protect_text, unprotect_text, atomic_write,
    hash_password, verify_password, encrypt_backup, decrypt_backup,
    require_windows_admin, secure_backup_root, protect_admin_path, connect_smtp,
    replace_folders, recover_restore, commit_restore,
    recover_legacy_company_details,
    recover_legacy_config,
)
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# ================= CONFIG =================
# Use localhost for local testing
API_URL = "https://tap-server-v2.onrender.com/activate"
# API_URL = "http://127.0.0.1:3000/activate" # Uncomment for local testing

# Hidden location to store license locally
LICENSE_FILE = os.path.join(
    os.getenv("APPDATA"),
    "Microsoft",
    "sys.dat"
)
 
def get_hwid():
    """Generates a stable unique ID for this specific PC using Disk Serial."""
    try:
        # Use Volume Serial of C: - very stable on Windows
        out = subprocess.check_output('vol c:', shell=True).decode()
        serial = out.split()[-1].replace('-', '')
        return hashlib.sha256(serial.encode()).hexdigest()[:16].upper()
    except:
        # Fallback to MAC if Vol fails
        mac = uuid.getnode()
        return hashlib.sha256(str(mac).encode()).hexdigest()[:16].upper()

def obfuscate_data(data_str):
    """Protect new settings with Windows user-bound encryption."""
    return protect_text(data_str)

def deobfuscate_data(encoded_str):
    """Decodes data encoded by obfuscate_data. Tries both stable and legacy keys."""
    if encoded_str.startswith(DPAPI_PREFIX):
        return unprotect_text(encoded_str)
    try:
        raw_data = base64.b64decode(encoded_str).decode()
        
        # 1. Try stable key (Current)
        cur_key = get_hwid()
        decoded = "".join(chr(ord(c) ^ ord(cur_key[i % len(cur_key)])) for i, c in enumerate(raw_data))
        if decoded.strip().startswith(("{", "[")):
            return decoded
            
        # 2. Try legacy MAC-based key (Fallback for migration)
        # This uses the mac-only logic before disk serial was added
        mac_key = hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()[:16].upper()
        decoded_mac = "".join(chr(ord(c) ^ ord(mac_key[i % len(mac_key)])) for i, c in enumerate(raw_data))
        if decoded_mac.strip().startswith(("{", "[")):
            return decoded_mac

        return None
    except Exception:
        return None

def save_local_license(key):
    """Saves the successful activation locally (obfuscated)."""
    try:
        os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)
        with open(LICENSE_FILE, "w") as f:
            f.write(obfuscate_data(key))
        return True
    except:
        return False

def load_local_license():
    """Reads the local activation file and handles deobfuscation."""
    if not os.path.exists(LICENSE_FILE):
        return None
    try:
        with open(LICENSE_FILE, "r") as f:
            content = f.read().strip()
            decoded = deobfuscate_data(content)
            if decoded:
                return decoded
            return content # Fallback for old plain-text license files
    except Exception:
        return None

def check_license_online(key):
    """Sends the key and HWID to the server for validation."""
    try:
        response = requests.post(
            API_URL,
            json={
                "key": key,
                "hwid": get_hwid()
            },
            timeout=30
        )
        data = response.json()
        return data.get("valid", False), data.get("msg", "Unknown error")
    except Exception as e:
        return False, f"Connection Error: {str(e)}"

# -------------------------
# GLOBALS
# -------------------------

# Writable data directory
if getattr(sys, 'frozen', False):
    # When running as EXE, use APPDATA to avoid permission issues in Program Files
    DATA_PATH = os.path.join(os.getenv("APPDATA"), "TempTrek")
else:
    # When running as script, keep it local for development
    DATA_PATH = os.path.dirname(os.path.abspath(__file__))

# Recover an interrupted restore before reading configuration. Surface recovery
# failure through the GUI instead of continuing with a partially restored state.
STARTUP_STORAGE_ERROR = None
try:
    recover_restore(DATA_PATH)
except Exception as exc:
    STARTUP_STORAGE_ERROR = exc

# Ensure directories exist
os.makedirs(os.path.join(DATA_PATH, "config"), exist_ok=True)
os.makedirs(os.path.join(DATA_PATH, "live"), exist_ok=True)
os.makedirs(os.path.join(DATA_PATH, "reports"), exist_ok=True)
os.makedirs(os.path.join(DATA_PATH, "backups"), exist_ok=True)

CONFIG_FILE = os.path.join(DATA_PATH, "config", "devices.json")
EMAIL_CONFIG_FILE = os.path.join(DATA_PATH, "config", "email_settings.json")
SMS_CONFIG_FILE = os.path.join(DATA_PATH, "config", "sms_settings.json")
USERS_FILE = os.path.join(DATA_PATH, "config", "users.json")
TREND_CONFIG_FILE = os.path.join(DATA_PATH, "config", "trend_settings.json")
APPROVALS_FILE = os.path.join(DATA_PATH, "config", "approvals.json")
COMPANY_CONFIG_FILE = os.path.join(DATA_PATH, "config", "company_details.json")
VIEWED_FILE = os.path.join(DATA_PATH, "config", "viewed_records.json")
BACKUP_SETTINGS_FILE = os.path.join(DATA_PATH, "config", "backup_settings.json")
BACKUP_HISTORY_FILE = os.path.join(DATA_PATH, "config", "backup_history.json")

# Explicit, validated application limits. These values keep generated reports
# readable and prevent a single operation from exhausting the workstation.
MAX_PDF_REGISTERS = 8
MAX_PDF_RECORDS = 50000
MAX_EMAIL_RECIPIENTS = 50
AUDIT_WRITE_LOCK = threading.Lock()

def migrate_config_files():
    """Migration happens only after every configuration has loaded successfully."""
    return

def load_devices():
    return load_secure_json(CONFIG_FILE, [])

def save_devices(devices):
    save_secure_json(CONFIG_FILE, devices)

def load_email_settings():
    default_settings = {
        "server_type": "Gmail",
        "smtp_server": "smtp.gmail.com",
        "smtp_port": "587",
        "imap_server": "imap.gmail.com",
        "imap_port": "993",
        "pop3_server": "pop.gmail.com",
        "pop3_port": "995",
        "sender_email": "",
        "sender_password": "",
        "recipient_email": "",
        "subject": "TempTrek System Alert",
        "email_enabled": True,
        "alerts": []
    }
    loaded = load_secure_json(EMAIL_CONFIG_FILE, {})
    default_settings.update(loaded)
    return default_settings

def save_email_settings(settings):
    save_secure_json(EMAIL_CONFIG_FILE, settings)

def load_sms_settings():
    default_settings = {
        "api_key": "",
        "route": "q",
        "sender_id": "",
        "template_id": "",
        "entity_id": "",
        "recipient_numbers": "",
        "sms_enabled": True,
        "alerts": []
    }
    loaded = load_secure_json(SMS_CONFIG_FILE, {})
    default_settings.update(loaded)
    return default_settings

def save_sms_settings(settings):
    save_secure_json(SMS_CONFIG_FILE, settings)

def load_approvals():
    return load_secure_json(APPROVALS_FILE, {})

def save_approvals(approvals_data):
    save_secure_json(APPROVALS_FILE, approvals_data)

def load_secure_json(filepath, default_value):
    if not os.path.exists(filepath):
        return default_value.copy() if isinstance(default_value, dict) else list(default_value)
    content = ""
    try:
        with open(filepath, "r", encoding="utf-8") as stream:
            content = stream.read().strip()
        decoded = deobfuscate_data(content)
        value = json.loads(decoded if decoded is not None else content)
        if not isinstance(value, type(default_value)):
            raise ValueError("Unexpected configuration type")
        return value
    except Exception as exc:
        # Some shipped settings use the same old machine key as the company
        # header. Recover exact JSON, never defaults, and retain the source.
        if not content.startswith(DPAPI_PREFIX):
            for reference_path in (COMPANY_CONFIG_FILE + ".legacy-original", COMPANY_CONFIG_FILE):
                try:
                    reference = Path(reference_path).read_text(encoding="utf-8")
                    value = recover_legacy_config(content, reference)
                    if not isinstance(value, type(default_value)):
                        continue
                    original_path = filepath + ".legacy-original"
                    if not os.path.exists(original_path):
                        atomic_write(original_path, Path(filepath).read_bytes())
                    return value
                except (ValueError, OSError, UnicodeError):
                    continue
        raise RuntimeError(f"Cannot read {os.path.basename(filepath)}. Original file preserved; restore a valid backup. {exc}") from exc

def save_secure_json(filepath, value):
    atomic_write(filepath, obfuscate_data(json.dumps(value, indent=4)))

def load_viewed_records():
    return load_secure_json(VIEWED_FILE, {})

def save_viewed_records(records):
    save_secure_json(VIEWED_FILE, records)

def load_backup_settings():
    defaults = {
        "enabled": True,
        "interval": "6 Hours",
        "storage_location": secure_backup_root(),
        "backup_password": "",
        "max_history": 30,
        "max_storage_mb": 2048,
        "last_success": "",
        "last_attempt": "",
        "last_status": "NOT RUN",
        "last_error": ""
    }
    loaded = load_secure_json(BACKUP_SETTINGS_FILE, {})
    defaults.update(loaded if isinstance(loaded, dict) else {})
    root = os.path.abspath(secure_backup_root())
    location = os.path.abspath(str(defaults.get("storage_location") or root))
    try:
        protected_location = os.path.commonpath([root, location]) == root
    except ValueError:
        protected_location = False
    if not protected_location:
        defaults["previous_storage_location"] = location
        defaults["storage_location"] = root
    return defaults

def save_backup_settings(settings):
    save_secure_json(BACKUP_SETTINGS_FILE, settings)

def load_backup_history():
    history = load_secure_json(BACKUP_HISTORY_FILE, [])
    return history if isinstance(history, list) else []

def save_backup_history(history):
    save_secure_json(BACKUP_HISTORY_FILE, history)

def parse_email_recipients(raw_value):
    """Return a unique validated recipient list or raise a user-facing error."""
    recipients = []
    seen = set()
    for value in str(raw_value or "").replace(";", ",").split(","):
        address = value.strip()
        if not address:
            continue
        lowered = address.lower()
        if lowered in seen:
            continue
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
            raise ValueError(f"Invalid recipient email address: {address}")
        seen.add(lowered)
        recipients.append(address)
    if len(recipients) > MAX_EMAIL_RECIPIENTS:
        raise ValueError(f"A maximum of {MAX_EMAIL_RECIPIENTS} recipient email addresses is supported.")
    return recipients

def remove_readonly_file(function, path, _exc_info):
    """Allow controlled restore cleanup of application-created read-only files."""
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    function(path)

def protect_local_path(path, is_directory=False):
    """Restrict backup files to the current operating-system user when possible."""
    try:
        if os.name == "nt":
            username = getpass.getuser()
            permission = f"{username}:(OI)(CI)F" if is_directory else f"{username}:F"
            subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", permission],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15, check=True)
        else:
            os.chmod(path, stat.S_IRUSR | (stat.S_IWUSR | stat.S_IXUSR if is_directory else 0))
    except Exception as exc:
        raise RuntimeError(f"Cannot protect {path}: {exc}") from exc

def load_company_details():
    default_details = {
        "company_name": "TempTrek Environmental Monitoring System",
        "department_name": "Environmental Monitoring Department",
        "report_title": "Temperature and Humidity Report"
    }
    try:
        loaded = load_secure_json(COMPANY_CONFIG_FILE, {})
    except RuntimeError:
        # Older installations shipped this non-secret header with the source
        # machine's XOR key. Preserve its exact bytes before migrating it.
        try:
            original = Path(COMPANY_CONFIG_FILE).read_bytes()
            loaded = recover_legacy_company_details(original.decode("utf-8"))
        except Exception:
            raise RuntimeError("Cannot recover company_details.json. Original file preserved; restore valid company settings.")
        original_path = COMPANY_CONFIG_FILE + ".legacy-original"
        if not os.path.exists(original_path):
            atomic_write(original_path, original)
    default_details.update(loaded)
    return default_details

def save_company_details(company_details):
    save_secure_json(COMPANY_CONFIG_FILE, company_details)

def is_timestamp_approved(approvals, ip, port, slave_id, row_dt):
    """Return whether one exact reading timestamp is inside an approved range."""
    day_key = f"{ip}:{port}:{slave_id}_{row_dt.strftime('%Y-%m-%d')}"
    entry = approvals.get(day_key, {})

    # New format: one day may contain one or more exact From/To ranges.
    for approved_range in entry.get("ranges", []):
        try:
            range_from = datetime.strptime(approved_range["from"], "%Y-%m-%d %H:%M:%S")
            range_to = datetime.strptime(approved_range["to"], "%Y-%m-%d %H:%M:%S")
            if range_from <= row_dt <= range_to:
                return True
        except (KeyError, TypeError, ValueError):
            continue

    # Older versions saved the selected times in the note but marked the whole
    # date approved. Recover that original selection instead of approving every
    # reading on the day.
    if entry.get("status") == "APPROVED" and "ranges" not in entry:
        note = str(entry.get("notes", ""))
        match = re.search(r"\((\d{2}:\d{2}(?::\d{2})?)\s+to\s+(\d{2}:\d{2}(?::\d{2})?)\)", note)
        if match:
            try:
                start_text = match.group(1) + (":00" if len(match.group(1)) == 5 else "")
                end_text = match.group(2) + (":00" if len(match.group(2)) == 5 else "")
                day_text = row_dt.strftime("%Y-%m-%d")
                legacy_from = datetime.strptime(f"{day_text} {start_text}", "%Y-%m-%d %H:%M:%S")
                legacy_to = datetime.strptime(f"{day_text} {end_text}", "%Y-%m-%d %H:%M:%S")
                return legacy_from <= row_dt <= legacy_to
            except ValueError:
                return False
        return True
    return False

def load_users():
    if not os.path.exists(USERS_FILE):
        # An existing installation must never recreate privileged accounts.
        if os.path.exists(os.path.join(DATA_PATH, ".accounts-initialized")) or any(
                os.path.exists(p) for p in (CONFIG_FILE, EMAIL_CONFIG_FILE, SMS_CONFIG_FILE)):
            raise RuntimeError("User database is missing. Restore a valid backup; automatic account reset is disabled.")
        password = simpledialog.askstring("First-time setup", "Create the Super Admin password (at least 12 characters):", show="*")
        if password is None or len(password) < 12:
            raise RuntimeError("Initial administrator setup requires a password of at least 12 characters.")
        confirmation = simpledialog.askstring("Confirm password", "Re-enter the new Super Admin password:", show="*")
        if confirmation != password:
            raise RuntimeError("Passwords did not match. No account was created.")
        permissions = ["live_data", "add_device", "live_log", "view_data", "approve_data", "email_config", "email_smtp_config", "sms_config", "export_data", "data_trends", "user_manager", "audit_log", "backup_restore", "company_details"]
        users = {"superadmin": {"password": hash_password(password), "permissions": permissions,
                               "is_admin": True, "is_superadmin": True, "is_approver": True}}
        save_users(users)
    else:
        users = load_secure_json(USERS_FILE, {})
    if not users or not any(u.get("is_superadmin") for u in users.values() if isinstance(u, dict)):
        raise RuntimeError("Invalid user database: no Super Admin. Restore a valid backup.")
    for name, user in users.items():
        if not isinstance(user, dict) or not isinstance(user.get("password"), str) or not isinstance(user.get("permissions"), list):
            raise RuntimeError(f"Invalid account configuration for {name}.")
    atomic_write(os.path.join(DATA_PATH, ".accounts-initialized"), "initialized")
    return users

def save_users(users):
    save_secure_json(USERS_FILE, users)

def load_trend_settings():
    defaults = {"low_limit": "", "upper_limit": "", "visible_condition": "Both Limits", "decimal_places": "3"}
    defaults.update(load_secure_json(TREND_CONFIG_FILE, {}))
    return defaults

def save_trend_settings(settings):
    save_secure_json(TREND_CONFIG_FILE, settings)

def csv_filename(ip, port, slave_id=1):
    # Daily rotation: filename includes current date to prevent Excel row limits
    date_str = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(DATA_PATH, "live", f"{ip.replace('.', '_')}_{port}_s{slave_id}_{date_str}.csv")

def safe_format_val(val, prec=3):
    """Safely formats a value as a float string if numeric, or returns string as-is if text/boolean."""
    if val is None:
        return "N/A"
    if isinstance(val, (int, float)):
        return f"{val:.{prec}f}"
    s = str(val).strip()
    try:
        f_val = float(s)
        return f"{f_val:.{prec}f}"
    except (ValueError, TypeError):
        return s

@storage_operation
def write_csv(ip, port, slave_id, reg, value):
    # Safety filter: Skip NaN, inf, or empty strings
    val_str = str(value).strip().lower()
    if val_str in ["nan", "none", "null", "", "inf", "-inf"]:
        return
    try:
        f_val = float(value)
        if math.isnan(f_val) or math.isinf(f_val):
            return
    except (ValueError, TypeError):
        return

    filename = csv_filename(ip, port, slave_id)
    for attempt in range(3):
        exists = os.path.exists(filename)
        try:
            with open(filename, "a", newline="") as f:
                writer = csv.writer(f)
                if not exists:
                    writer.writerow(["Timestamp", "IP", "Port", "SlaveID", "Register", "Value"])
                writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ip, port, slave_id, reg, value])
            return
        except Exception:
            time.sleep(0.1)
    raise OSError(f"Could not save records to {filename} after three attempts.")


@storage_operation
def write_alarm_history_csv(dev_name, reg_name, reg_addr, value, condition, threshold):
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = os.path.join(DATA_PATH, "live", f"Alarm_History_{date_str}.csv")
    for attempt in range(3):
        exists = os.path.exists(filename)
        try:
            with open(filename, "a", newline="") as f:
                writer = csv.writer(f)
                if not exists:
                    writer.writerow(["Timestamp", "Device Name", "Field Name", "Addr", "Reading Value", "Condition", "Threshold"])
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                    dev_name, 
                    reg_name, 
                    reg_addr, 
                    value, 
                    condition, 
                    threshold
                ])
            return
        except Exception:
            time.sleep(0.1)

    raise OSError(f"Could not save alarm history to {filename} after three attempts.")

@storage_operation
def write_email_log_csv(dev_name, field_name, recipient, status):
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = os.path.join(DATA_PATH, "live", f"Email_Logs_{date_str}.csv")
    for attempt in range(3):
        exists = os.path.exists(filename)
        try:
            with open(filename, "a", newline="") as f:
                writer = csv.writer(f)
                if not exists:
                    writer.writerow(["Timestamp", "Device Name", "Field Name", "Recipient", "Status"])
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                    dev_name, 
                    field_name, 
                    recipient, 
                    status
                ])
            return
        except Exception:
            time.sleep(0.1)
    raise OSError(f"Could not save records to {filename} after three attempts.")


def send_fast2sms(api_key, route, numbers, message_text, sender_id="", template_id="", entity_id=""):
    if not api_key:
        return False, "API Key is missing"
    if not numbers:
        return False, "Recipient phone number(s) missing"
    
    clean_numbers = ",".join([n.strip() for n in numbers.replace(";", ",").split(",") if n.strip()])
    if not clean_numbers:
        return False, "No valid recipient numbers provided"

    route_val = route.split()[0].strip() if " " in route else route
    if not route_val:
        route_val = "q"

    url = "https://www.fast2sms.com/dev/bulkV2"
    headers = {
        "authorization": api_key,
        "Content-Type": "application/json"
    }

    def execute_api_call(r_val, is_get=False):
        payload = {
            "route": r_val,
            "message": template_id.strip() if (r_val == "dlt" and template_id and template_id.strip()) else message_text,
            "language": "english",
            "flash": 0,
            "numbers": clean_numbers
        }
        if r_val == "dlt":
            if sender_id and sender_id.strip():
                payload["sender_id"] = sender_id.strip()
            if entity_id and entity_id.strip():
                payload["entity_id"] = entity_id.strip()
            if template_id and template_id.strip() and message_text:
                payload["variables_values"] = message_text

        try:
            if is_get:
                params = dict(payload)
                params["authorization"] = api_key
                res = requests.get(url, params=params, timeout=15)
            else:
                res = requests.post(url, json=payload, headers=headers, timeout=15)
                
            if res.status_code == 200:
                res_json = res.json()
                if res_json.get("return") == True or res_json.get("status_code") == 200:
                    msg = res_json.get("message", ["SMS sent successfully"])
                    msg_str = msg[0] if isinstance(msg, list) else str(msg)
                    return True, msg_str
                else:
                    err = res_json.get("message", "Fast2SMS Error")
                    err_str = ", ".join(err) if isinstance(err, list) else str(err)
                    return False, err_str
            else:
                try:
                    res_json = res.json()
                    err = res_json.get("message", res.text)
                    err_str = ", ".join(err) if isinstance(err, list) else str(err)
                    return False, f"API Error ({res.status_code}): {err_str}"
                except Exception:
                    return False, f"HTTP Error {res.status_code}: {res.text}"
        except Exception as e:
            return False, f"Connection Exception: {e}"

    # 1. Try requested route with POST
    ok, msg = execute_api_call(route_val, is_get=False)
    if ok:
        return True, msg

    # 2. Try requested route with GET
    ok_get, msg_get = execute_api_call(route_val, is_get=True)
    if ok_get:
        return True, msg_get

    # 3. If requested route was 'dlt' and failed (e.g. Invalid Sender ID / Message ID),
    #    try automatic fallback to Quick SMS ('q') via POST and GET
    if route_val == "dlt":
        ok_q_post, msg_q_post = execute_api_call("q", is_get=False)
        if ok_q_post:
            return True, "SMS sent successfully via Quick SMS route (Fallback from DLT)."
            
        ok_q_get, msg_q_get = execute_api_call("q", is_get=True)
        if ok_q_get:
            return True, "SMS sent successfully via Quick SMS route (Fallback from DLT)."

    # If all attempts failed, present clean actionable instructions to user
    combined_err = f"{msg} | {msg_get}"
    if "Sender ID" in combined_err or "406" in combined_err:
        return False, "Invalid Sender ID.\n\nAction Required:\n1. Change FAST2SMS ROUTE to 'q (Quick SMS)' in the dropdown.\n2. Click 'SAVE SMS SETTINGS'.\n3. Click 'TEST FAST2SMS ALERT' again."

    if "Message ID" in combined_err or "Template" in combined_err:
        return False, "Invalid DLT Template / Message ID.\n\nAction Required:\n1. Change FAST2SMS ROUTE to 'q (Quick SMS)' in the dropdown.\n2. Click 'SAVE SMS SETTINGS'.\n3. Click 'TEST FAST2SMS ALERT' again."

    return False, msg

@storage_operation
def write_sms_log_csv(dev_name, field_name, recipient, status, message_text=""):
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = os.path.join(DATA_PATH, "live", f"SMS_Logs_{date_str}.csv")
    for attempt in range(3):
        exists = os.path.exists(filename)
        try:
            with open(filename, "a", newline="") as f:
                writer = csv.writer(f)
                if not exists:
                    writer.writerow(["Timestamp", "Device Name", "Field Name", "Recipient", "Status", "Message"])
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                    dev_name, 
                    field_name, 
                    recipient, 
                    status,
                    message_text
                ])
            return
        except Exception:
            time.sleep(0.1)
    raise OSError(f"Could not save records to {filename} after three attempts.")


def _audit_hash(previous_hash, values):
    identity_path = os.path.join(DATA_PATH, "config", "audit_identity.json")
    identity = load_secure_json(identity_path, {})
    if not identity:
        identity = {"workstation_key": get_hwid()}
        save_secure_json(identity_path, identity)
    payload = "|".join([str(previous_hash)] + [str(v) for v in values]) + "|" + identity["workstation_key"]
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()

def _upgrade_audit_file(filename):
    """Upgrade legacy five-column logs to the tamper-evident audit schema."""
    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        return ""
    try:
        os.chmod(filename, stat.S_IREAD | stat.S_IWRITE)
        with open(filename, "r", newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.reader((line.replace('\0', '') for line in f)))
        if not rows:
            return ""
        expected = ["Timestamp", "Username", "Workstation", "Action", "Category",
                    "Previous Value", "Updated Value", "Status", "Details", "Entry Hash"]
        if rows[0] == expected:
            if len(rows) == 1:
                return ""
            if len(rows[-1]) < 10:
                raise ValueError("Audit file ends with an incomplete record.")
            return rows[-1][9]
        converted = [expected]
        previous_hash = ""
        for row in rows[1:]:
            if len(row) < 5:
                continue
            values = [row[0], row[1], "LEGACY", row[2], row[3], "", "", "UNKNOWN", row[4]]
            entry_hash = _audit_hash(previous_hash, values)
            converted.append(values + [entry_hash])
            previous_hash = entry_hash
        output = io.StringIO(newline="")
        csv.writer(output).writerows(converted)
        atomic_write(filename, output.getvalue())
        return previous_hash
    except Exception as exc:
        raise OSError(f"Could not read or upgrade audit file {filename}: {exc}") from exc

@storage_operation
def write_audit_log_csv(username, action, category, details, device_info="",
                        previous_value="", updated_value="", status="SUCCESS"):
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = os.path.join(DATA_PATH, "live", f"Audit_Logs_{date_str}.csv")
    workstation = device_info or f"{platform.node()} / {platform.system()} {platform.release()}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    values = [timestamp, username, workstation, action, category, previous_value,
              updated_value, status, details]
    with AUDIT_WRITE_LOCK:
        for attempt in range(3):
            exists = os.path.exists(filename)
            try:
                previous_hash = _upgrade_audit_file(filename) if exists else ""
                if exists:
                    os.chmod(filename, stat.S_IREAD | stat.S_IWRITE)
                entry_hash = _audit_hash(previous_hash, values)
                with open(filename, "a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if not exists or os.path.getsize(filename) == 0:
                        writer.writerow(["Timestamp", "Username", "Workstation", "Action", "Category",
                                         "Previous Value", "Updated Value", "Status", "Details", "Entry Hash"])
                    writer.writerow(values + [entry_hash])
                try:
                    os.chmod(filename, stat.S_IREAD)
                except Exception:
                    pass
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise OSError(f"Could not persist audit event to {filename} after three attempts.")
        if not exists:
            protect_local_path(filename)
        return tuple(values + [entry_hash])

# -------------------------
# FLOAT DECODING (ABCD)
# -------------------------
# def decode_float(h, l):
#     """
#     ABCD Big-endian float
#     Most Input Register devices use this
#     """
#     raw = struct.pack(">HH", h, l)
#     return struct.unpack(">f", raw)[0]

def decode_int(h, l):
    # Pack as ABCD (big-endian)
    raw = struct.pack(">HH", h, l)
    int_value = struct.unpack(">i", raw)[0]
    hex_value = raw.hex().upper()
    return int_value, hex_value

def decode_float(h, l):
    raw = struct.pack(">HH", h, l)
    return struct.unpack(">f", raw)[0]

def decode_uint(h, l):
    raw = struct.pack(">HH", h, l)
    return struct.unpack(">I", raw)[0]

def decode_int16(h):
    raw = struct.pack(">H", h)
    return struct.unpack(">h", raw)[0]

def decode_uint16(h):
    return h

def decode_bcd(h, l):
    """ Decode 4-byte (2-reg) BCD value """
    def bcd_to_int(val):
        res = 0
        for i, digit in enumerate(reversed(hex(val)[2:])):
            try: res += int(digit) * (10**i)
            except: pass
        return res
    # Combine registers and treat as hex digits
    s = f"{h:04x}{l:04x}"
    try: return int(s)
    except: return 0


# -------------------------
# MODBUS WORKER THREAD
# -------------------------
def read_modbus_device(host, port, registers, app, stop_event, unit_id=1, refresh_event=None):
    last_csv_write = {} # { reg: timestamp }
    # Get or create shared client per (host, port)
    with app.comms_lock:
        if (host, port) not in app.shared_clients:
            app.shared_clients[(host, port)] = ModbusTcpClient(host=host, port=port, timeout=5)
        client = app.shared_clients[(host, port)]
        if (host, port) not in app.ip_locks:
            app.ip_locks[(host, port)] = threading.Lock()
        lock = app.ip_locks[(host, port)]

    def log_ui(msg):
        # Format: [IP:PORT:SLAVE] MESSAGE
        prefix = f"[{host}:{port}:{unit_id}]"
        app.root.after(0, lambda: app.log(f"{prefix} {msg}"))

    while not stop_event.is_set():
        with lock:
            try:
                # Ensure connection is active
                is_connected = False
                try:
                    is_connected = client.is_connected()
                except:
                    # Fallback for older pymodbus or different versions
                    is_connected = getattr(client, 'connected', False)
                
                if not is_connected:
                    if not client.connect():
                        log_ui(f"Connection Failed ❌")
                        # Skip this poll cycle if connection fails
                        time.sleep(2)
                        continue
                
                # One-time log upon successful connection for this thread's visibility
                if getattr(stop_event, 'first_run', True):
                    log_ui(f"Connected ✔")
                    stop_event.first_run = False
                for reg in registers:
                    try:
                        # Find correct data type for this specific device and register
                        dt = "INT32"
                        reg_mode = "FC04" # Fixed back to FC04
                        poll_interval = 1.0
                        for d in list(app.devices):
                            if d['ip'] == host and d['port'] == port and d.get('slave_id', 1) == unit_id:
                                dt = d.get('types', {}).get(str(reg), "INT32")
                                reg_mode = d.get('modes', {}).get(str(reg), "FC04")
                                try:
                                    poll_interval = float(d.get('poll_interval', 1.0))
                                except:
                                    poll_interval = 1.0
                                try:
                                    divisor = float(d.get('divisors', {}).get(str(reg), 1.0))
                                except: divisor = 1.0
                                break

                        # Determine how many registers to read
                        count_to_read = 2 if ("32" in dt or dt == "BCD" or "DOUBLE" in dt) else 1

                        param_name = 'slave' if PYMODBUS_V3 else 'unit'
                        kwargs = {param_name: unit_id}

                        reg_num = int(''.join(filter(str.isdigit, str(reg)))) if any(c.isdigit() for c in str(reg)) else 0
                        if reg_mode == "FC03":
                            result = client.read_holding_registers(address=reg_num, count=count_to_read, **kwargs)
                        elif reg_mode == "FC01":
                            result = client.read_coils(address=reg_num, count=count_to_read, **kwargs)
                        elif reg_mode == "FC02":
                            result = client.read_discrete_inputs(address=reg_num, count=count_to_read, **kwargs)
                        else:
                            result = client.read_input_registers(address=reg_num, count=count_to_read, **kwargs)

                        if result is None:
                            log_ui(f"TIMEOUT @ {reg}")
                            log_ui(f"Reg {reg} \u2192 0")
                            continue
                            
                        if result.isError():
                            # Log specific modbus error code
                            err_msg = str(result)
                            if hasattr(result, 'exception_code'):
                                codes = {1:"Illegal Func", 2:"Illegal Addr", 3:"Illegal Data", 4:"Fail"}
                                err_msg = codes.get(result.exception_code, f"Error {result.exception_code}")
                            log_ui(f"Read Error @ {reg}: {err_msg}")
                            log_ui(f"Reg {reg} \u2192 0")
                            continue

                        try:
                            # Safe Access to registers/bits
                            if reg_mode in ["FC01", "FC02"]:
                                if not hasattr(result, "bits") or len(result.bits) < count_to_read:
                                    log_ui(f"Partial Read @ {reg}")
                                    log_ui(f"Reg {reg} \u2192 0")
                                    continue
                                value = 1 if result.bits[0] else 0
                            else:
                                if not hasattr(result, "registers") or len(result.registers) < count_to_read:
                                    log_ui(f"Partial Read @ {reg}")
                                    log_ui(f"Reg {reg} \u2192 0")
                                    continue

                                # 32-bit Types
                                if count_to_read == 2:
                                    h, l = result.registers[0], result.registers[1]
                                    
                                    if "FLOAT32" in dt:
                                        value = decode_float(h, l)
                                    elif "UINT32" in dt or "UNSIGNED DOUBLE" in dt:
                                        value = decode_uint(h, l)
                                    elif dt == "BCD":
                                        value = decode_bcd(h, l)
                                    else: # Default 32-bit (INT32, DOUBLE INTEGER, etc.)
                                        value, _ = decode_int(h, l)
                                # 16-bit Types 
                                elif dt == "INT16":
                                    value = decode_int16(result.registers[0])
                                elif dt in ["UINT16", "UNSIGNED INT"]:
                                    value = decode_uint16(result.registers[0])
                                else:
                                    value = result.registers[0]

                            if dt != "BOOLEAN":
                                # Apply Math (Multiplier or Divisor)
                                op = d.get('ops', {}).get(str(reg), "/")
                                try:
                                    factor = float(d.get('factors', {}).get(str(reg), 1.0))
                                except:
                                    factor = 1.0
                                
                                if op == "*":
                                    value = value * factor
                                else:
                                    value = value / factor

                            if dt == "BOOLEAN":
                                label_0 = d.get('ops', {}).get(str(reg), "OFF")
                                label_1 = d.get('factors', {}).get(str(reg), "ON")
                                val_to_log = label_0 if int(value) == 0 else label_1
                                csv_val = "0" if int(value) == 0 else "1"
                            else:
                                # Format value based on selected precision
                                try:
                                    # Use per-register decimal places if available, else fallback to global setting
                                    p_val = d.get('decimals', {}).get(str(reg))
                                    if p_val is not None:
                                        prec = int(p_val)
                                        # Scale integer Modbus readings by the decimals setting (shift decimal point)
                                        if "FLOAT32" not in dt:
                                            value = value / (10 ** prec)
                                    else:
                                        prec = int(app.trend_settings.get("decimal_places", 3))
                                    val_to_log = f"{float(value):.{prec}f}"
                                except Exception as e:
                                    val_to_log = str(value)
                                csv_val = val_to_log

                            log_ui(f"Reg {reg} \u2192 {val_to_log}")
                            
                            # Throttle CSV writes based on poll_interval
                            now = time.time()
                            last_t = last_csv_write.get(reg, 0.0)
                            if now - last_t >= poll_interval:
                                is_lora = ("LORA" in str(d.get('node_type', '')).upper()) or ("LORA" in str(d.get('device_name', '')).upper())
                                should_write = True
                                if is_lora:
                                    try:
                                        if float(csv_val) <= 0:
                                            should_write = False
                                    except Exception:
                                        pass
                                if should_write:
                                    write_csv(host, port, unit_id, reg, csv_val)
                                    last_csv_write[reg] = now
                        except Exception as e:
                            log_ui(f"Decode Error @ {reg}: {e}")
                            log_ui(f"Reg {reg} \u2192 0")

                        # Check Email Alerts
                        for alert in app.email_settings.get('alerts', []):
                            # Match IP, Port, and Slave ID
                            if (str(alert.get('device_ip')) == str(host) and 
                                str(alert.get('port')) == str(port) and 
                                str(alert.get('slave_id', 1)) == str(unit_id) and 
                                str(alert.get('reg')) == str(reg)):
                                
                                cond = alert.get('condition')
                                try:
                                    thresh = float(alert.get('threshold'))
                                    triggered = False
                                    if is_lora and value <= 0:
                                        triggered = False
                                    elif cond == ">" and value > thresh: triggered = True
                                    elif cond == "<" and value < thresh: triggered = True
                                    elif cond == "==" and value == thresh: triggered = True
                                    
                                    # Process Alert State Logic (Alert vs Normal)
                                    app.check_alert_logic(alert, value, triggered)
                                except:
                                    continue

                        # Check SMS Alerts
                        for alert in app.sms_settings.get('alerts', []):
                            if (str(alert.get('device_ip')) == str(host) and 
                                str(alert.get('port')) == str(port) and 
                                str(alert.get('slave_id', 1)) == str(unit_id) and 
                                str(alert.get('reg')) == str(reg)):
                                cond = alert.get('condition')
                                try:
                                    thresh = float(alert.get('threshold'))
                                    triggered = False
                                    if is_lora and value <= 0:
                                        triggered = False
                                    elif cond == ">" and value > thresh: triggered = True
                                    elif cond == "<" and value < thresh: triggered = True
                                    elif cond == "==" and value == thresh: triggered = True
                                    app.check_sms_alert_logic(alert, value, triggered)
                                except:
                                    continue

                    except PermissionError:
                        log_ui(f"Error: CSV File Open! Close it.")
                        log_ui(f"Reg {reg} \u2192 0")
                    except Exception as e:
                        log_ui(f"Error: {e}")
                        log_ui(f"Reg {reg} \u2192 0")

            except Exception as e:
                log_ui(f"Global IP Error ({host}): {e}")

        if refresh_event:
            refresh_event.wait(timeout=1.0)
            refresh_event.clear()
        else:
            time.sleep(1.0)

    log_ui(f"Polling Stopped")

def read_dummy_device(host, port, registers, app, stop_event, unit_id=1, refresh_event=None):
    last_csv_write = {} # { reg: timestamp }
    def log_ui(msg):
        prefix = f"[{host}:{port}:{unit_id} (SIM)]"
        app.root.after(0, lambda: app.log(f"{prefix} {msg}"))

    if getattr(stop_event, 'first_run', True):
        log_ui(f"Connected \u2714")
        stop_event.first_run = False

    while not stop_event.is_set():
        try:
            for reg in registers:
                prec = 3
                dt = "FLOAT32"
                target_dev = None
                poll_interval = 1.0
                for d in list(app.devices):
                    if d['ip'] == host and d['port'] == port and d.get('slave_id', 1) == unit_id:
                        p_val = d.get('decimals', {}).get(str(reg))
                        if p_val is not None:
                            prec = int(p_val)
                        else:
                            prec = int(app.trend_settings.get("decimal_places", 3))
                        dt = d.get('types', {}).get(str(reg), "FLOAT32")
                        try:
                            poll_interval = float(d.get('poll_interval', 1.0))
                        except:
                            poll_interval = 1.0
                        target_dev = d
                        break
                if dt == "BOOLEAN":
                    value = random.choice([0, 1])
                    label_0 = target_dev.get('ops', {}).get(str(reg), "OFF") if target_dev else "OFF"
                    label_1 = target_dev.get('factors', {}).get(str(reg), "ON") if target_dev else "ON"
                    val_to_log = label_0 if value == 0 else label_1
                    csv_val = str(value)
                else:
                    value = random.uniform(20.0, 80.0)
                    val_to_log = f"{float(value):.{prec}f}"
                    csv_val = val_to_log
                log_ui(f"Reg {reg} \u2192 {val_to_log}")
                
                # Throttle CSV writes based on poll_interval
                now = time.time()
                last_t = last_csv_write.get(reg, 0.0)
                if now - last_t >= poll_interval:
                    is_lora = False
                    if target_dev:
                        is_lora = ("LORA" in str(target_dev.get('node_type', '')).upper()) or ("LORA" in str(target_dev.get('device_name', '')).upper())
                    should_write = True
                    if is_lora:
                        try:
                            if float(csv_val) <= 0:
                                should_write = False
                        except Exception:
                            pass
                    if should_write:
                        write_csv(host, port, unit_id, reg, csv_val)
                        last_csv_write[reg] = now
                
                # Check Email Alerts
                for alert in app.email_settings.get('alerts', []):
                    if (str(alert.get('device_ip')) == str(host) and 
                        str(alert.get('port')) == str(port) and 
                        str(alert.get('slave_id', 1)) == str(unit_id) and 
                        str(alert.get('reg')) == str(reg)):
                        cond = alert.get('condition')
                        try:
                            thresh = float(alert.get('threshold'))
                            triggered = False
                            if is_lora and value <= 0:
                                triggered = False
                            elif cond == ">" and value > thresh: triggered = True
                            elif cond == "<" and value < thresh: triggered = True
                            elif cond == "==" and value == thresh: triggered = True
                            app.check_alert_logic(alert, value, triggered)
                        except:
                            continue

                # Check SMS Alerts
                for alert in app.sms_settings.get('alerts', []):
                    if (str(alert.get('device_ip')) == str(host) and 
                        str(alert.get('port')) == str(port) and 
                        str(alert.get('slave_id', 1)) == str(unit_id) and 
                        str(alert.get('reg')) == str(reg)):
                        cond = alert.get('condition')
                        try:
                            thresh = float(alert.get('threshold'))
                            triggered = False
                            if is_lora and value <= 0:
                                triggered = False
                            elif cond == ">" and value > thresh: triggered = True
                            elif cond == "<" and value < thresh: triggered = True
                            elif cond == "==" and value == thresh: triggered = True
                            app.check_sms_alert_logic(alert, value, triggered)
                        except:
                            continue

        except Exception as e:
            log_ui(f"Sim Error: {e}")

        if refresh_event:
            refresh_event.wait(timeout=1.0)
            refresh_event.clear()
        else:
            time.sleep(1.0)

    log_ui(f"Simulation Stopped")

# -------------------------
# GUI APPLICATION
# -------------------------
# -------------------------
# GUI APPLICATION
# -------------------------
class ModbusApp(BackupMixin):
    # Professional Light Theme Colors
    COLORS = {
        "bg_dark": "#F0F2F5",      # Modern Light Grey
        "bg_surface": "#FFFFFF",   # Pure White
        "bg_highlight": "#E4E6EB", # Light Grey Hover
        "accent": "#005CB8",       # Professional Blue
        "accent_glow": "#E1F0FF",  # Soft Blue Glow
        "text_main": "#1C1E21",    # Dark Charcoal
        "text_dim": "#65676B",     # Medium Grey
        "success": "#28A745",      # Success Green
        "danger": "#D73A49",       # Alert Red
        "warning": "#F29900"       # Safety Orange
    }

    FONTS = {
        "h1": ("Segoe UI", 24, "bold"),
        "h2": ("Segoe UI", 18, "bold"),
        "h3": ("Segoe UI", 14, "bold"),
        "body": ("Segoe UI", 10),
        "button": ("Segoe UI", 10, "bold"),
        "code": ("Consolas", 10)
    }

    DEFAULT_REGISTER_NAMES = [
        "Voltage R", "Voltage Y", "Voltage B",
        "Current R", "Current Y", "Current B",
        "Power Factor", "Frequency", "Active Power", "Reactive Power"
    ]

    DEFAULT_REGISTER_UNITS = [
        "V", "V", "V",
        "A", "A", "A",
        "PF", "Hz", "kW", "kVAR"
    ]

    DATA_TYPES = [
        "FLOAT32", "INT32", "UINT32", 
        "DOUBLE INTEGER", "UNSIGNED DOUBLE INTEGER",
        "INT16", "UINT16", "BCD", "UNSIGNED INT", "BOOLEAN"
    ]
    DEFAULT_REGISTER_TYPES = ["FLOAT32"] * 10
    DEFAULT_REGISTER_DECIMALS = ["3"] * 10

    COMMON_UNITS = ["V", "A", "Hz", "kW", "kVAR", "kWh", "PF", "%", "\u00b0C", "m\u00b3/h", "bar"]

    def __init__(self, root):
        self.root = root
        self.root.title("TempTrek Environmental Monitoring System")
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)
        self.root.configure(bg=self.COLORS["bg_dark"])
        
        # High DPI Support for Windows
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass

        # Load Icon
        try:
            icon_ico = resource_path("icon.ico")
            if os.path.exists(icon_ico):
                self.root.wm_iconbitmap(default=icon_ico)
            self.icon_img = tk.PhotoImage(file=resource_path("icon.png"))
            self.root.iconphoto(True, self.icon_img)
        except Exception as e:
            try:
                self.icon_img = tk.PhotoImage(file=resource_path("icon.png"))
                self.root.iconphoto(True, self.icon_img)
            except:
                self.icon_img = None

        self.devices = load_devices()
        self.device_threads = {}
        self.stop_events = {}
        self.global_log = []
        self.live_labels = {}
        self.node_tree = None
        self.mini_tree = None
        self.last_values = {} # { lid: "string_val" }
        self.last_update_times = {} # { key: "timestamp" }
        self.refresh_events = {}
        
        # Email Settings
        self.email_settings = load_email_settings()
        self.email_cooldowns = {}
        self.active_alerts = {}
        self.alert_lock = threading.Lock()

        # SMS Settings
        self.sms_settings = load_sms_settings()
        self.sms_cooldowns = {}
        self.sms_active_alerts = {}
        self.sms_lock = threading.Lock()

        # Company Details (Report Header Line 1, Line 2, Line 3)
        self.company_details = load_company_details()

        # Communication Locks (per IP:Port) to prevent collisions
        self.comms_lock = threading.Lock()
        self.ip_locks = {}
        self.shared_clients = {} # (host, port) -> ModbusTcpClient
        
        # User Authentication & Audit Log
        self.users = load_users()
        self.current_user = None
        self.global_audit_log = deque(maxlen=2000)

        # Viewed-data audit and automatic backup/restore state
        self.viewed_records = load_viewed_records()
        self.backup_settings = load_backup_settings()
        self.backup_history = load_backup_history()
        self.backup_lock = threading.RLock()
        self.restore_in_progress = False
        self.restart_required = False
        self.backup_in_progress = False

        # Live Real-time Analytics
        self.data_history = {}  # { lid: [val1, val2, ...] }
        self.graph_canvas = None
        self.chat_box = None
        self.selected_graph_lid = None
        
        # Trend Settings
        self.trend_settings = load_trend_settings()

        # Force encode all config files on startup (Migration)
        # Validate remaining files before rewriting any existing configuration.
        approvals = load_approvals()
        save_devices(self.devices)
        save_email_settings(self.email_settings)
        save_sms_settings(self.sms_settings)
        save_users(self.users)
        save_trend_settings(self.trend_settings)
        save_viewed_records(self.viewed_records)
        save_backup_settings(self.backup_settings)
        save_backup_history(self.backup_history)
        save_approvals(approvals)
        save_company_details(self.company_details)

        # Start Flow
        self.edit_index = None
        self.active_view = "live_data"
        self.check_initial_license()
        self.periodic_refresher()

    def log_audit(self, action, category, details, user=None, device_info="",
                  previous_value="", updated_value="", status="SUCCESS"):
        username = user if user else (getattr(self, 'current_user', None) or 'SYSTEM')
        entry = write_audit_log_csv(username, action, category, details,
                                    device_info=device_info,
                                    previous_value=previous_value,
                                    updated_value=updated_value,
                                    status=status)
        self.global_audit_log.append(entry)
        if hasattr(self, 'audit_tree') and self.audit_tree and self.audit_tree.winfo_exists():
            self.root.after(0, lambda: self._append_audit_live(entry))

    def _append_audit_live(self, entry):
        try:
            if hasattr(self, 'audit_tree') and self.audit_tree and self.audit_tree.winfo_exists():
                self.audit_tree.insert("", 0, values=entry[:9])
        except Exception:
            pass

    def _authenticate_current_user(self, title="Authorization Required"):
        entered_password = simpledialog.askstring(
            title,
            f"Enter the password for '{self.current_user}' to continue:",
            show="*", parent=self.root
        )
        if entered_password is None:
            return False
        expected = self.users.get(self.current_user, {}).get("password", "")
        valid = verify_password(entered_password, expected)
        if not valid:
            self.log_audit("AUTHORIZATION_DENIED", "SECURITY", title,
                           status="DENIED")
            messagebox.showerror("Authentication Failed", "Incorrect password.")
        return valid

    def _backup_runtime(self):
        return globals()

    def _reload_restored_configuration(self):
        self.devices = load_devices()
        self.email_settings = load_email_settings()
        self.sms_settings = load_sms_settings()
        self.users = load_users()
        self.company_details = load_company_details()
        self.trend_settings = load_trend_settings()
        self.viewed_records = load_viewed_records()
        self.backup_settings = load_backup_settings()
        self.backup_history = load_backup_history()

    def _backup_interval_seconds(self):
        return {
            "5 Mins": 300,
            "30 Mins": 1800,
            "1 Hour": 3600,
            "6 Hours": 21600,
            "12 Hours": 43200,
            "24 Hours": 86400
        }.get(self.backup_settings.get("interval", "6 Hours"), 21600)

    def _backup_storage_path(self):
        require_windows_admin()
        root = os.path.abspath(secure_backup_root())
        location = os.path.abspath(os.path.expandvars(str(self.backup_settings.get("storage_location") or root)))
        if os.path.commonpath([root, location]) != root:
            raise ValueError(f"For administrator-only deletion, select a folder inside {root}.")
        # Reject links/junctions before granting protection or following the path.
        current = Path(root)
        for part in [None] + list(Path(location).relative_to(root).parts):
            if part is not None:
                current = current / part
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise ValueError("Backup storage cannot contain links or junctions.")
            current.mkdir(exist_ok=True)
            protect_admin_path(str(current), is_directory=True)
        return location

    def _count_backup_content(self):
        recorded = 0
        audit_records = 0
        config_items = 0
        for source_name in ("config", "live", "reports"):
            source_dir = os.path.join(DATA_PATH, source_name)
            if not os.path.isdir(source_dir):
                continue
            for root_dir, _, files in os.walk(source_dir):
                for filename in files:
                    full_path = os.path.join(root_dir, filename)
                    if source_name == "config":
                        config_items += 1
                    if source_name == "live" and filename.lower().endswith(".csv"):
                        try:
                            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                                count = max(0, sum(1 for _ in f) - 1)
                            if filename.startswith("Audit_Logs_"):
                                audit_records += count
                            elif not filename.startswith(("Email_Logs_", "SMS_Logs_", "Alarm_")):
                                recorded += count
                        except Exception:
                            pass
        return recorded, config_items, audit_records

    def _record_backup_history(self, entry):
        self.backup_history.append(entry)
        max_history = max(1, int(self.backup_settings.get("max_history", 30)))
        self.backup_history = self.backup_history[-max_history:]
        save_backup_history(self.backup_history)

    def _enforce_backup_retention(self, storage, protected=()):
        protected = {os.path.abspath(p) for p in protected}
        limit = max(1, int(self.backup_settings.get("max_history", 30)))
        max_bytes = max(1, int(self.backup_settings.get("max_storage_mb", 2048))) * 1024 * 1024
        files = sorted((os.path.getmtime(os.path.join(storage, n)), os.path.join(storage, n), os.path.getsize(os.path.join(storage, n)))
                       for n in os.listdir(storage) if n.startswith("TempTrek_Backup_") and n.lower().endswith((".zip", ".ttbackup")) and os.path.isfile(os.path.join(storage, n)))
        total = sum(item[2] for item in files)
        count = len(files)
        for _, path, size in files:
            if count <= limit and total <= max_bytes:
                break
            if os.path.abspath(path) in protected or count <= 1:
                continue
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            os.remove(path)
            total -= size
            count -= 1

    def browse_backup_location(self):
        selected = filedialog.askdirectory(
            title="Select Local Backup Storage Folder",
            initialdir=self.backup_location_var.get() or DATA_PATH
        )
        if selected:
            self.backup_location_var.set(os.path.abspath(selected))

    def save_backup_settings_ui(self):
        if not self._authenticate_current_user("Backup Settings Authorization"):
            return
        if not self._ensure_backup_password():
            return
        try:
            location = os.path.abspath(os.path.expandvars(self.backup_location_var.get().strip()))
            max_history = int(self.backup_history_limit_var.get().strip())
            max_storage_mb = int(self.backup_storage_limit_var.get().strip())
            if not location or max_history < 1 or max_storage_mb < 1:
                raise ValueError("Storage location and positive retention limits are required.")
            old_settings = dict(self.backup_settings)
            self.backup_settings.update({
                "enabled": self.backup_enabled_var.get(),
                "interval": self.backup_interval_var.get(),
                "storage_location": location,
                "max_history": max_history,
                "max_storage_mb": max_storage_mb
            })
            self._backup_storage_path()
            save_backup_settings(self.backup_settings)
            self.log_audit("BACKUP_CONFIG_UPDATE", "BACKUP", "Updated automatic backup settings",
                           previous_value=json.dumps({k: v for k, v in old_settings.items() if k != "backup_password"}, sort_keys=True),
                           updated_value=json.dumps({k: v for k, v in self.backup_settings.items() if k != "backup_password"}, sort_keys=True))
            self.refresh_backup_view()
            messagebox.showinfo("Saved", "Automatic backup settings saved successfully.")
        except Exception as exc:
            if "old_settings" in locals():
                self.backup_settings = old_settings
            messagebox.showerror("Invalid Backup Settings", str(exc))

    def refresh_backup_view(self):
        if hasattr(self, "backup_status_label") and self.backup_status_label.winfo_exists():
            status = self.backup_settings.get("last_status", "NOT RUN")
            last_success = self.backup_settings.get("last_success", "Never") or "Never"
            error = self.backup_settings.get("last_error", "")
            status_text = f"STATUS: {status}   |   LAST SUCCESS: {last_success}"
            if error:
                status_text += f"   |   ERROR: {error}"
            color = self.COLORS["success"] if status == "SUCCESS" else (self.COLORS["danger"] if status == "FAILED" else self.COLORS["warning"])
            self.backup_status_label.config(text=status_text, fg=color)
        if hasattr(self, "backup_progress") and self.backup_progress.winfo_exists() and not self.backup_in_progress:
            self.backup_progress.stop()
        if not hasattr(self, "backup_tree") or not self.backup_tree.winfo_exists():
            return
        for item in self.backup_tree.get_children():
            self.backup_tree.delete(item)
        rows = list(reversed(self.backup_history))
        known_paths = {str(row.get("location", "")) for row in rows}
        try:
            storage = self.backup_settings.get("storage_location", secure_backup_root())
            for filename in os.listdir(storage):
                path = os.path.join(storage, filename)
                if filename.lower().endswith((".zip", ".ttbackup")) and path not in known_paths:
                    try:
                        if filename.lower().endswith(".ttbackup"):
                            rows.append({"type": "DISCOVERED", "file_name": filename, "date_time": "",
                                         "size": os.path.getsize(path), "status": "VERIFY ON RESTORE", "location": path})
                            continue
                        manifest = self._load_backup_manifest(path)
                        rows.append({
                            "type": manifest.get("trigger", "DISCOVERED"),
                            "file_name": filename,
                            "date_time": manifest.get("created_at", ""),
                            "size": os.path.getsize(path),
                            "status": "SUCCESS",
                            "includes": ", ".join(manifest.get("includes", [])),
                            "location": path
                        })
                    except Exception:
                        rows.append({"type": "UNKNOWN", "file_name": filename, "date_time": "",
                                     "size": os.path.getsize(path), "status": "INVALID", "location": path})
        except Exception:
            pass
        for row in rows:
            if row.get("status") == "SUCCESS" and not os.path.isfile(row.get("location", "")):
                row = dict(row, status="MISSING")
            size_mb = float(row.get("size", 0)) / (1024 * 1024)
            self.backup_tree.insert("", "end", values=(
                row.get("type", ""), row.get("file_name", ""), row.get("date_time", ""),
                f"{size_mb:.2f} MB", row.get("status", ""),
                row.get("includes", "Recorded data, configuration, users, notifications, approvals, audit" if row.get("status") == "SUCCESS" else "Not available"),
                row.get("location", "")
            ))

    def show_backup_restore(self):
        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        if "backup_restore" not in user_perms:
            messagebox.showerror("Access Denied", "You do not have permission to manage backup and restore.")
            self.navigate("live_data")
            return

        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True, padx=30, pady=20)

        settings = tk.Frame(layout, bg=self.COLORS["bg_surface"], padx=18, pady=15,
                            highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        settings.pack(fill="x", pady=(0, 12))
        tk.Label(settings, text="AUTOMATIC DATA BACKUP & RESTORE SETTINGS", font=self.FONTS["h3"],
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["accent"]).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 12))

        self.backup_enabled_var = tk.BooleanVar(value=self.backup_settings.get("enabled", True))
        tk.Checkbutton(settings, text="ENABLE AUTOMATIC BACKUP", variable=self.backup_enabled_var,
                       bg=self.COLORS["bg_surface"], fg=self.COLORS["text_main"],
                       selectcolor=self.COLORS["bg_dark"]).grid(row=1, column=0, sticky="w", padx=(0, 12))

        tk.Label(settings, text="INTERVAL", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).grid(row=1, column=1, sticky="w")
        self.backup_interval_var = tk.StringVar(value=self.backup_settings.get("interval", "6 Hours"))
        ttk.Combobox(settings, textvariable=self.backup_interval_var, state="readonly", width=12,
                     values=["5 Mins", "30 Mins", "1 Hour", "6 Hours", "12 Hours", "24 Hours"]).grid(row=2, column=1, sticky="ew", padx=(0, 12))

        tk.Label(settings, text="MAX BACKUPS", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).grid(row=1, column=2, sticky="w")
        self.backup_history_limit_var = tk.StringVar(value=str(self.backup_settings.get("max_history", 30)))
        tk.Entry(settings, textvariable=self.backup_history_limit_var, width=10).grid(row=2, column=2, sticky="ew", padx=(0, 12))

        tk.Label(settings, text="MAX STORAGE (MB)", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).grid(row=1, column=3, sticky="w")
        self.backup_storage_limit_var = tk.StringVar(value=str(self.backup_settings.get("max_storage_mb", 2048)))
        tk.Entry(settings, textvariable=self.backup_storage_limit_var, width=12).grid(row=2, column=3, sticky="ew", padx=(0, 12))

        tk.Label(settings, text="ADMINISTRATOR BACKUP LOCATION", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.backup_location_var = tk.StringVar(value=self.backup_settings.get("storage_location", secure_backup_root()))
        tk.Entry(settings, textvariable=self.backup_location_var).grid(row=4, column=0, columnspan=4, sticky="ew", padx=(0, 8))
        tk.Button(settings, text="BROWSE", command=self.browse_backup_location,
                  bg=self.COLORS["bg_highlight"], fg=self.COLORS["text_main"], relief="flat").grid(row=4, column=4, padx=4)
        tk.Button(settings, text="SAVE SETTINGS", command=self.save_backup_settings_ui,
                  bg=self.COLORS["accent"], fg="white", relief="flat").grid(row=4, column=5, padx=4)
        for col in range(4):
            settings.grid_columnconfigure(col, weight=1)

        self.backup_status_label = tk.Label(layout, text="", bg=self.COLORS["bg_surface"],
                                            fg=self.COLORS["text_dim"], font=("Segoe UI", 9, "bold"), padx=12, pady=8)
        self.backup_status_label.pack(fill="x")
        self.backup_progress = ttk.Progressbar(layout, mode="indeterminate")
        self.backup_progress.pack(fill="x", pady=(0, 10))

        actions = tk.Frame(layout, bg=self.COLORS["bg_dark"])
        actions.pack(fill="x", pady=(0, 10))
        tk.Button(actions, text="CREATE BACKUP NOW", command=self.start_manual_backup,
                  bg=self.COLORS["success"], fg="white", font=self.FONTS["button"], relief="flat", padx=16, pady=7).pack(side="left")
        tk.Button(actions, text="EXPORT SELECTED BACKUP", command=self.export_selected_backup,
                  bg=self.COLORS["accent"], fg="white", font=self.FONTS["button"], relief="flat", padx=12, pady=7).pack(side="left", padx=8)
        tk.Button(actions, text="RESTORE SELECTED BACKUP", command=self.restore_selected_backup,
                  bg=self.COLORS["warning"], fg="black", font=self.FONTS["button"], relief="flat", padx=16, pady=7).pack(side="right")
        tk.Button(actions, text="SELECT BACKUP FILE", command=self.browse_restore_file,
                  bg=self.COLORS["bg_highlight"], fg="white", relief="flat", padx=12, pady=7).pack(side="right", padx=8)

        table_frame = tk.Frame(layout, bg=self.COLORS["bg_surface"])
        table_frame.pack(fill="both", expand=True)
        columns = ("type", "file", "date", "size", "status", "contents", "location")
        self.backup_tree = ttk.Treeview(table_frame, columns=columns, show="headings", style="Treeview")
        labels = {"type": "TYPE", "file": "BACKUP FILE", "date": "DATE / TIME", "size": "SIZE",
                  "status": "STATUS", "contents": "INCLUDED DATA", "location": "LOCATION"}
        widths = {"type": 100, "file": 250, "date": 160, "size": 90, "status": 90,
                  "contents": 300, "location": 350}
        for column in columns:
            self.backup_tree.heading(column, text=labels[column])
            self.backup_tree.column(column, width=widths[column], anchor="w")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.backup_tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.backup_tree.xview)
        self.backup_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        y_scroll.pack(side="right", fill="y")
        x_scroll.pack(side="bottom", fill="x")
        self.backup_tree.pack(fill="both", expand=True)
        self.refresh_backup_view()

    def compute_device_diff(self, old_dev, new_dev):
        changes = []
        # Header level checks
        if old_dev.get("device_name") != new_dev.get("device_name"):
            changes.append(f"Name: '{old_dev.get('device_name')}' -> '{new_dev.get('device_name')}'")
        if old_dev.get("ip") != new_dev.get("ip") or old_dev.get("port") != new_dev.get("port"):
            changes.append(f"Network: {old_dev.get('ip')}:{old_dev.get('port')} -> {new_dev.get('ip')}:{new_dev.get('port')}")
        if old_dev.get("slave_id") != new_dev.get("slave_id"):
            changes.append(f"Slave ID: {old_dev.get('slave_id')} -> {new_dev.get('slave_id')}")
        if str(old_dev.get("poll_interval")) != str(new_dev.get("poll_interval")):
            changes.append(f"Poll Interval: {old_dev.get('poll_interval')}s -> {new_dev.get('poll_interval')}s")

        # Register level checks
        old_regs = [str(r) for r in old_dev.get("registers", [])]
        new_regs = [str(r) for r in new_dev.get("registers", [])]

        added_regs = [r for r in new_regs if r not in old_regs]
        removed_regs = [r for r in old_regs if r not in new_regs]

        if added_regs:
            changes.append(f"Added Regs: [{', '.join(added_regs)}]")
        if removed_regs:
            changes.append(f"Removed Regs: [{', '.join(removed_regs)}]")

        code_map = {"FC01": "Output Coil", "FC02": "Input Coil", "FC03": "Holding Register", "FC04": "Input Register"}

        common_regs = [r for r in new_regs if r in old_regs]
        for r in common_regs:
            reg_diffs = []
            # Name
            old_n = old_dev.get("names", {}).get(r, "")
            new_n = new_dev.get("names", {}).get(r, "")
            if old_n != new_n:
                reg_diffs.append(f"Name: '{old_n}' -> '{new_n}'")

            # Unit
            old_u = old_dev.get("units", {}).get(r, "")
            new_u = new_dev.get("units", {}).get(r, "")
            if old_u != new_u:
                reg_diffs.append(f"Unit: '{old_u}' -> '{new_u}'")

            # Type
            old_t = old_dev.get("types", {}).get(r, "FLOAT32")
            new_t = new_dev.get("types", {}).get(r, "FLOAT32")
            if old_t != new_t:
                reg_diffs.append(f"Type: '{old_t}' -> '{new_t}'")

            # Mode
            old_m_raw = old_dev.get("modes", {}).get(r, "FC04")
            new_m_raw = new_dev.get("modes", {}).get(r, "FC04")
            old_m = code_map.get(old_m_raw, old_m_raw)
            new_m = code_map.get(new_m_raw, new_m_raw)
            if old_m != new_m:
                reg_diffs.append(f"Mode: '{old_m}' -> '{new_m}'")

            # Math Formula (op & factor)
            old_op = old_dev.get("ops", {}).get(r, "/")
            old_f = old_dev.get("factors", {}).get(r, "1")
            new_op = new_dev.get("ops", {}).get(r, "/")
            new_f = new_dev.get("factors", {}).get(r, "1")
            old_math = f"0={old_op}/1={old_f}" if old_t == "BOOLEAN" else f"{old_op} {old_f}"
            new_math = f"0={new_op}/1={new_f}" if new_t == "BOOLEAN" else f"{new_op} {new_f}"
            if old_math != new_math:
                reg_diffs.append(f"Math: '{old_math}' -> '{new_math}'")

            # Decimals
            old_d = str(old_dev.get("decimals", {}).get(r, "3"))
            new_d = str(new_dev.get("decimals", {}).get(r, "3"))
            if old_d != new_d:
                reg_diffs.append(f"Decimals: '{old_d}' -> '{new_d}'")

            if reg_diffs:
                changes.append(f"Reg {r} ({', '.join(reg_diffs)})")

        return "; ".join(changes) if changes else "No parameter changes"

    def check_initial_license(self):
        """ Checks for local license; if not found, shows activation screen. """
        if load_local_license():
            self.show_splash()
        else:
            self.show_activation()

    def show_activation(self):
        """ Premium Activation Window keeping the existing UI style """
        self.clear_window()
        self.root.configure(bg=self.COLORS["bg_dark"])
        
        card = tk.Frame(self.root, bg=self.COLORS["bg_surface"], padx=50, pady=50, 
                        highlightbackground=self.COLORS["accent_glow"], highlightthickness=1)
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(card, text="SYSTEM LOCKED", font=self.FONTS["h2"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["danger"]).pack(pady=(0, 10))
        
        tk.Label(card, text="ONLINE ACTIVATION REQUIRED", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(pady=(0, 25))

        # Hardware ID Display
        hwid = get_hwid()
        hwid_box = tk.Frame(card, bg=self.COLORS["bg_dark"], padx=15, pady=10)
        hwid_box.pack(fill="x", pady=(0, 20))
        tk.Label(hwid_box, text=f"DEVICE HWID: {hwid}", font=self.FONTS["code"], 
                 bg=self.COLORS["bg_dark"], fg=self.COLORS["accent"]).pack()

        tk.Label(card, text="ENTER LICENSE KEY", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        
        self.lic_entry = tk.Entry(card, font=self.FONTS["body"], bg=self.COLORS["bg_dark"], 
                           fg=self.COLORS["text_main"], relief="flat", insertbackground="black")
        self.lic_entry.pack(fill="x", pady=(5, 30), ipady=8)
        self.lic_entry.focus()
        
        # Underline accent
        uline = tk.Frame(card, bg=self.COLORS["accent"], height=2)
        uline.place(relx=0, rely=0.75, relwidth=1)

        def process_activation():
            key = self.lic_entry.get().strip().upper()
            if not key:
                messagebox.showwarning("Warning", "Please enter a license key")
                return
            
            btn.config(text="VERIFYING...", state="disabled")
            self.root.update()
            
            # Online Check
            is_valid, server_msg = check_license_online(key)
            
            if is_valid:
                if save_local_license(key):
                    messagebox.showinfo("Success", "Activation successful!")
                    self.show_splash()
                else:
                    messagebox.showerror("Error", "Failed to save local license cache.")
                    btn.config(text="ACTIVATE SYSTEM", state="normal")
            else:
                messagebox.showerror("Activation Failed", f"{server_msg}")
                btn.config(text="ACTIVATE SYSTEM", state="normal")

        btn = tk.Button(card, text="ACTIVATE SYSTEM", bg=self.COLORS["accent"], fg="black", 
                        font=self.FONTS["button"], relief="flat", cursor="hand2",
                        activebackground=self.COLORS["accent_glow"], command=process_activation)
        btn.pack(fill="x", ipady=12)

    def create_scrollable_panel(self, parent, bg=None, pad_x=20, pad_y=20, width=460):
        if bg is None:
            bg = self.COLORS["bg_surface"]
        outer = tk.Frame(parent, bg=bg, width=width, highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        outer.pack_propagate(False)
        
        canvas = tk.Canvas(outer, bg=bg, width=width, highlightthickness=0)
        v_sb = ttk.Scrollbar(outer, orient="vertical", style="Vertical.TScrollbar", command=canvas.yview)
        inner = tk.Frame(canvas, bg=bg, padx=pad_x, pady=pad_y)
        
        def _on_inner_config(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            w = max(width - 20, e.width)
            canvas.itemconfig(win_id, width=w)

        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", _on_inner_config)
        canvas.configure(yscrollcommand=v_sb.set)

        v_sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _on_mousewheel(e):
            try:
                canvas.yview_scroll(int(-1*(e.delta/120)), "units")
            except:
                pass
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        return outer, inner

    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    # -------------------------
    # 1. SPLASH SCREEN
    # -------------------------
    def show_splash(self):
        self.clear_window()
        self.root.configure(bg=self.COLORS["bg_dark"])
        
        container = tk.Frame(self.root, bg=self.COLORS["bg_dark"])
        container.place(relx=0.5, rely=0.5, anchor="center")
        
        # Logo Icon
        if self.icon_img:
            tk.Label(container, image=self.icon_img, bg=self.COLORS["bg_dark"]).pack(pady=20)
        
        # Title with Glow Effect
        tk.Label(container, text="TempTrek Environmental Monitoring System", font=("Segoe UI", 32, "bold"), 
                 fg=self.COLORS["accent"], bg=self.COLORS["bg_dark"]).pack()
        
        self.splash_status = tk.Label(container, text="INITIALIZING SYSTEM...", 
                                     font=self.FONTS["body"], fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"])
        self.splash_status.pack(pady=15)
        
        # Minimalist Progress Bar
        progress_bg = tk.Frame(container, bg=self.COLORS["bg_surface"], width=300, height=4)
        progress_bg.pack(pady=10)
        progress_bg.pack_propagate(False)
        
        self.progress_bar = tk.Frame(progress_bg, bg=self.COLORS["accent"], width=0, height=4)
        self.progress_bar.place(x=0, y=0)

        self.animate_splash(0)

    def animate_splash(self, val):
        if val <= 300:
            self.progress_bar.config(width=val)
            if val == 100: self.splash_status.config(text="CONNECTING TO CORE...")
            if val == 200: self.splash_status.config(text="LOADING DEVICE CONFIGS...")
            self.root.after(10, lambda: self.animate_splash(val + 5))
        else:
            self.show_login()

    # -------------------------
    # 2. LOGIN SCREEN
    # -------------------------
    def show_login(self):
        if hasattr(self, 'current_user') and self.current_user:
            self.log_audit("LOGOUT", "AUTHENTICATION", f"User '{self.current_user.upper()}' logged out")
            self.current_user = None
        self.clear_window()
        self.root.configure(bg=self.COLORS["bg_dark"])
        
        card = tk.Frame(self.root, bg=self.COLORS["bg_surface"], padx=50, pady=50, 
                        highlightbackground=self.COLORS["accent_glow"], highlightthickness=1)
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(card, text="SECURE ACCESS", font=self.FONTS["h2"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_main"]).pack(pady=(0, 30))
        
        tk.Label(card, text="SYSTEM OPERATOR", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        self.u_entry = tk.Entry(card, font=self.FONTS["body"], bg=self.COLORS["bg_dark"], 
                          fg=self.COLORS["text_main"], relief="flat", insertbackground="black")
        self.u_entry.pack(fill="x", pady=(5, 20), ipady=8)

        tk.Label(card, text="PASSCODE", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        self.pass_entry = tk.Entry(card, show="*", font=self.FONTS["body"], bg=self.COLORS["bg_dark"], 
                                  fg=self.COLORS["text_main"], relief="flat", insertbackground="black", bd=0)
        self.pass_entry.pack(fill="x", pady=(5, 30), ipady=8)
        self.u_entry.focus()
        
        # Underline accent
        uline = tk.Frame(card, bg=self.COLORS["accent"], height=2)
        uline.place(relx=0, rely=0.74, relwidth=1)

        self.pass_entry.bind("<Return>", lambda e: self.check_password())

        btn = tk.Button(card, text="AUTHENTICATE", bg=self.COLORS["accent"], fg="black", 
                        font=self.FONTS["button"], relief="flat", cursor="hand2", 
                        activebackground=self.COLORS["accent_glow"], command=self.check_password)
        btn.pack(fill="x", ipady=10)

        self.lbl_error = tk.Label(card, text="", fg=self.COLORS["danger"], 
                                 bg=self.COLORS["bg_surface"], font=self.FONTS["body"])
        self.lbl_error.pack(pady=10)

    def check_password(self):
        username = self.u_entry.get().lower()
        password = self.pass_entry.get()
        
        if username in self.users:
            if verify_password(password, self.users[username]["password"]):
                if not self.users[username]["password"].startswith("pbkdf2_sha256$"):
                    self.users[username]["password"] = hash_password(password)
                    save_users(self.users)
                self.current_user = username
                self.log_audit("LOGIN", "AUTHENTICATION", f"User '{username.upper()}' logged in successfully", user=username)
                self.build_main_ui()
            else:
                self.log_audit("LOGIN_FAILED", "AUTHENTICATION", f"Failed login attempt for user '{username.upper()}' (invalid passcode)", user=username)
                self.lbl_error.config(text="ACCESS DENIED: INVALID PASSCODE")
                self.pass_entry.delete(0, tk.END)
        else:
            self.log_audit("LOGIN_FAILED", "AUTHENTICATION", f"Failed login attempt for unknown user '{username.upper()}'", user=username)
            self.lbl_error.config(text="ACCESS DENIED: UNKNOWN USER")
            self.pass_entry.delete(0, tk.END)

    # -------------------------
    # 3. MAIN UI
    # -------------------------
    def build_main_ui(self):
        self.clear_window()
        self.root.configure(bg=self.COLORS["bg_dark"])
        self.setup_styles()

        # Sidebar
        self.sidebar = tk.Frame(self.root, bg=self.COLORS["bg_surface"], width=240)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        logo_frame = tk.Frame(self.sidebar, bg=self.COLORS["bg_surface"], pady=30)
        logo_frame.pack(fill="x")
        tk.Label(logo_frame, text="Temp", font=("Segoe UI", 16, "bold"), 
                 fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(30, 0))
        tk.Label(logo_frame, text="Trek", font=("Segoe UI", 16), 
                 fg=self.COLORS["text_main"], bg=self.COLORS["bg_surface"]).pack(side="left")

        nav_area = tk.Frame(self.sidebar, bg=self.COLORS["bg_surface"])
        nav_area.pack(fill="both", expand=True)
        self.nav_canvas = tk.Canvas(nav_area, bg=self.COLORS["bg_surface"],
                                    highlightthickness=0, width=220, height=1)
        nav_scrollbar = ttk.Scrollbar(nav_area, orient="vertical", command=self.nav_canvas.yview)
        nav_scrollbar.pack(side="right", fill="y")
        self.nav_canvas.pack(side="left", fill="both", expand=True)
        self.nav_canvas.configure(yscrollcommand=nav_scrollbar.set)
        self.nav_container = tk.Frame(self.nav_canvas, bg=self.COLORS["bg_surface"], pady=10)
        nav_window = self.nav_canvas.create_window((0, 0), window=self.nav_container, anchor="nw")
        self.nav_container.bind("<Configure>",
                                lambda event: self.nav_canvas.configure(scrollregion=self.nav_canvas.bbox("all")))
        self.nav_canvas.bind("<Configure>",
                             lambda event: self.nav_canvas.itemconfigure(nav_window, width=event.width))

        self.nav_btns = {}
        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        is_superadmin = (self.current_user == "superadmin") or self.users.get(self.current_user, {}).get("is_superadmin", False)
        
        if "live_data" in user_perms:
            self.create_nav_item("live_data", "DASHBOARD")
        if "live_log" in user_perms:
            self.create_nav_item("live_log", "Live Log")

        if "view_data" in user_perms or "approve_data" in user_perms:
            self.create_nav_header("DATA")
            if "view_data" in user_perms:
                self.create_nav_item("view_data", "View Data", indent=True)
            if "approve_data" in user_perms:
                self.create_nav_item("approve_data", "Approved Data", indent=True)

        if "export_data" in user_perms or "data_trends" in user_perms:
            self.create_nav_header("REPORTS / PDF")
            if "export_data" in user_perms:
                self.create_nav_item("export_data", "Export / Download", indent=True)
            if "data_trends" in user_perms:
                self.create_nav_item("data_trends", "Historic Trends", indent=True)

        if "sms_config" in user_perms or "email_config" in user_perms:
            self.create_nav_header("NOTIFICATIONS")
            if "email_config" in user_perms:
                self.create_nav_item("email_config", "Email Configuration", indent=True)
            if "sms_config" in user_perms:
                self.create_nav_item("sms_config", "SMS Alert", indent=True)

        admin_perms = ["add_device", "user_manager", "company_details", "audit_log", "backup_restore"]
        if any(p in user_perms for p in admin_perms):
            self.create_nav_header("ADMINISTRATION")
            if "add_device" in user_perms and is_superadmin:
                self.create_nav_item("add_device", "Device Manager", indent=True)
            if "user_manager" in user_perms:
                self.create_nav_item("user_manager", "System Users", indent=True)
            if "company_details" in user_perms:
                self.create_nav_item("company_details", "Company Details", indent=True)
            if "audit_log" in user_perms:
                self.create_nav_item("audit_log", "Audit Log", indent=True)
            if "backup_restore" in user_perms:
                self.create_nav_item("backup_restore", "Backup & Restore", indent=True)

        self.create_nav_item("change_password", "Change Password")

        footer = tk.Frame(self.sidebar, bg=self.COLORS["bg_surface"], pady=20)
        footer.pack(side="bottom", fill="x", before=nav_area)
        
        status_frame = tk.Frame(footer, bg=self.COLORS["bg_surface"])
        status_frame.pack(side="left", padx=20)
        
        status_dot = tk.Label(status_frame, text="●", fg=self.COLORS["success"], bg=self.COLORS["bg_surface"])
        status_dot.pack(side="left")
        tk.Label(status_frame, text=self.current_user.upper(), font=("Segoe UI", 8, "bold"), 
                 fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(5, 0))

        tk.Button(footer, text="↪ LOGOUT", font=("Segoe UI", 8, "bold"), fg=self.COLORS["danger"],
                  bg=self.COLORS["bg_surface"], relief="flat", cursor="hand2",
                  command=self.show_login).pack(side="right", padx=20)

        # Bind only sidebar widgets so scrolling does not affect the active page.
        def scroll_navigation(event):
            if self.nav_container.winfo_reqheight() > self.nav_canvas.winfo_height():
                if getattr(event, "num", None) == 4:
                    units = -1
                elif getattr(event, "num", None) == 5:
                    units = 1
                else:
                    delta = getattr(event, "delta", 0)
                    units = (-1 if delta > 0 else 1) * max(1, abs(int(delta / 120))) if delta else 0
                self.nav_canvas.yview_scroll(units, "units")
            return "break"

        def bind_navigation_scroll(widget):
            widget.bind("<MouseWheel>", scroll_navigation)
            widget.bind("<Button-4>", scroll_navigation)
            widget.bind("<Button-5>", scroll_navigation)
            for child in widget.winfo_children():
                bind_navigation_scroll(child)

        bind_navigation_scroll(nav_area)

        # Main Content Area
        self.main_area = tk.Frame(self.root, bg=self.COLORS["bg_dark"])
        self.main_area.pack(side="left", fill="both", expand=True)

        self.top_bar = tk.Frame(self.main_area, bg=self.COLORS["bg_dark"], height=60)
        self.top_bar.pack(side="top", fill="x", padx=30)
        
        self.view_title = tk.Label(self.top_bar, text="Live Dashboard", font=self.FONTS["h2"], 
                                  fg=self.COLORS["text_main"], bg=self.COLORS["bg_dark"])
        self.view_title.pack(side="left", pady=15)

        self.content_frame = tk.Frame(self.main_area, bg=self.COLORS["bg_dark"])
        self.content_frame.pack(side="top", fill="both", expand=True)

        self.navigate("live_data")

    def create_nav_header(self, text):
        header_frame = tk.Frame(self.nav_container, bg=self.COLORS["bg_surface"])
        header_frame.pack(fill="x", padx=20, pady=(15, 6))
        lbl = tk.Label(header_frame, text=text, font=("Segoe UI", 8, "bold"), 
                       fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"], anchor="w")
        lbl.pack(side="left")
        sep = tk.Frame(header_frame, bg=self.COLORS["bg_highlight"], height=1)
        sep.pack(side="left", fill="x", expand=True, padx=(8, 0))

    def create_nav_item(self, view_id, text, indent=False):
        icons = {
            "live_data": "▦",
            "live_log": "≡",
            "view_data": "▤",
            "approve_data": "✓",
            "export_data": "↓",
            "data_trends": "↗",
            "email_config": "✉",
            "sms_config": "☎",
            "add_device": "⚙",
            "user_manager": "👤",
            "company_details": "▥",
            "audit_log": "☷",
            "backup_restore": "⟳",
            "change_password": "🔒",
        }
        left_pad = 22 if indent else 16
        label = f"{icons.get(view_id, '•')}  {text}"
        btn = tk.Button(self.nav_container, text=label, font=("Segoe UI", 10),
                        fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"], 
                        relief="flat", anchor="w", padx=left_pad, pady=10, cursor="hand2",
                        activebackground=self.COLORS["bg_highlight"], activeforeground="white",
                        command=lambda: self.navigate(view_id))
        btn.pack(fill="x")
        self.nav_btns[view_id] = btn

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        # Treeview Styles
        style.configure("Treeview", 
                        background=self.COLORS["bg_surface"], 
                        foreground=self.COLORS["text_main"],
                        fieldbackground=self.COLORS["bg_surface"],
                        font=self.FONTS["body"],
                        rowheight=48,
                        borderwidth=0)
        
        style.configure("Treeview.Heading", 
                        background=self.COLORS["bg_highlight"], 
                        foreground=self.COLORS["accent"],
                        font=("Segoe UI", 10, "bold"),
                        relief="flat",
                        padding=12)
        
        style.map("Treeview", 
                  background=[('selected', self.COLORS["accent_glow"])],
                  foreground=[('selected', self.COLORS["accent"])])

        # Modern Scrollbar Styles
        style.configure("Vertical.TScrollbar", 
                        gripcount=0,
                        background=self.COLORS["bg_surface"],
                        troughcolor=self.COLORS["bg_dark"],
                        bordercolor=self.COLORS["bg_highlight"],
                        lightcolor=self.COLORS["bg_surface"],
                        darkcolor=self.COLORS["bg_surface"],
                        arrowcolor=self.COLORS["accent"])
        
        style.layout("Vertical.TScrollbar", [
            ('Vertical.Scrollbar.trough', {
                'children': [('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})],
                'sticky': 'nswe'
            })
        ])

        style.configure("Horizontal.TScrollbar", 
                        gripcount=0,
                        background=self.COLORS["bg_surface"],
                        troughcolor=self.COLORS["bg_dark"],
                        bordercolor=self.COLORS["bg_highlight"],
                        lightcolor=self.COLORS["bg_surface"],
                        darkcolor=self.COLORS["bg_surface"],
                        arrowcolor=self.COLORS["accent"])
        
        style.layout("Horizontal.TScrollbar", [
            ('Horizontal.Scrollbar.trough', {
                'children': [('Horizontal.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})],
                'sticky': 'nswe'
            })
        ])
        
        # Tag styles for status
        style.configure("Status.Online", foreground=self.COLORS["success"])
        style.configure("Status.Offline", foreground=self.COLORS["text_dim"])

        # Combobox Styles
        style.configure("TCombobox", 
                        fieldbackground=self.COLORS["bg_dark"], 
                        background=self.COLORS["bg_surface"], 
                        foreground=self.COLORS["text_main"],
                        arrowcolor=self.COLORS["accent"],
                        bordercolor=self.COLORS["bg_highlight"],
                        lightcolor=self.COLORS["bg_highlight"],
                        darkcolor=self.COLORS["bg_surface"],
                        insertcolor="black")
        
        style.map("TCombobox", 
                  background=[('active', self.COLORS["bg_highlight"]), ('pressed', self.COLORS["bg_dark"])],
                  fieldbackground=[('readonly', self.COLORS["bg_dark"]), ('focus', self.COLORS["bg_dark"])],
                  foreground=[('readonly', self.COLORS["text_main"])],
                  arrowcolor=[('active', self.COLORS["accent"]), ('pressed', "white")])
        
        # Override the popdown (listbox) style globally
        self.root.option_add("*TCombobox*Listbox.background", self.COLORS["bg_surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", self.COLORS["text_main"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.COLORS["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "black")
        self.root.option_add("*TCombobox*Listbox.font", self.FONTS["body"])
        self.root.option_add("*TCombobox*Listbox.relief", "flat")
        self.root.option_add("*TCombobox*Listbox.borderwidth", "0")
        self.root.option_add("*TCombobox*Listbox.width", "25")

    def navigate(self, view_name):
        if self.restore_in_progress or self.restart_required:
            messagebox.showwarning("Restore", "Wait for restoration to finish, then restart TempTrek.")
            return
        self.active_view = view_name
        for name, btn in self.nav_btns.items():
            if name == view_name:
                btn.config(bg=self.COLORS["accent"], fg="white", font=("Segoe UI", 10, "bold"))
            else:
                btn.config(bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], font=("Segoe UI", 10))

        titles = {
            "live_data": "MONITORING DASHBOARD", 
            "add_device": "DEVICE CONFIGURATION", 
            "live_log": "SYSTEM ANALYTICS",
            "view_data": "VIEW DATA",
            "approve_data": "APPROVED DATA",
            "email_config": "ALARM CONFIGURATION",
            "sms_config": "SMS ALERT CONFIGURATION",
            "export_data": "DATA EXPORT",
            "data_trends": "HISTORIC TREND ANALYTICS",
            "user_manager": "USER MANAGEMENT",
            "company_details": "COMPANY DETAILS",
            "email_alerts": "ALARM HISTORY",
            "email_logs_view": "EMAIL SEND LOGS",
            "audit_log": "USER AUDIT LOG",
            "backup_restore": "DATA BACKUP & RESTORE",
            "change_password": "CHANGE PASSWORD"
        }
        self.view_title.config(text=titles.get(view_name, ""))

        for widget in self.content_frame.winfo_children():
            widget.destroy()

        if view_name == "live_data":
            self.show_live_data()
        elif view_name == "add_device":
            is_superadmin = (self.current_user == "superadmin") or self.users.get(self.current_user, {}).get("is_superadmin", False)
            if is_superadmin:
                self.show_add_device()
            else:
                messagebox.showerror("Access Denied", "Only Super Admin has access to Device Manager.")
                self.navigate("live_data")
        elif view_name == "live_log":
            self.show_live_log()
        elif view_name == "view_data":
            self.show_view_data()
        elif view_name == "approve_data":
            self.show_approve_data()
        elif view_name == "email_config":
            self.show_email_config()
        elif view_name == "sms_config":
            self.show_sms_config()
        elif view_name == "export_data":
            self.show_export_data()
        elif view_name == "data_trends":
            self.show_data_trends()
        elif view_name == "email_alerts":
            self.show_email_alerts_log()
        elif view_name == "email_logs_view":
            self.show_email_logs_history()
        elif view_name == "user_manager":
            if "user_manager" in self.users.get(self.current_user, {}).get("permissions", []):
                self.show_user_manager()
            else:
                messagebox.showerror("Access Denied", "You do not have permission to manage system users.")
                self.navigate("live_data")
        elif view_name == "company_details":
            self.show_company_details()
        elif view_name == "audit_log":
            self.show_audit_log()
        elif view_name == "backup_restore":
            self.show_backup_restore()
        elif view_name == "change_password":
            self.show_change_password()

    def show_change_password(self):
        if not self.current_user:
            return
        card = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], padx=30, pady=25)
        card.pack(fill="x", padx=30, pady=20)
        tk.Label(card, text="Change your password", font=self.FONTS["h2"],
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_main"]).pack(anchor="w")
        tk.Label(card, text="Use at least 12 characters for your new password.",
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w", pady=(5, 15))
        entries = []
        for label in ("Current password", "New password", "Confirm new password"):
            tk.Label(card, text=label, font=self.FONTS["body"],
                     bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
            entry = tk.Entry(card, show="*", font=self.FONTS["body"], width=35)
            entry.pack(anchor="w", pady=(5, 15), ipady=6)
            entries.append(entry)

        def save_password():
            if not self.current_user or self.restore_in_progress or self.restart_required:
                return
            current, new, confirmation = [entry.get() for entry in entries]
            user = self.users[self.current_user]
            if not verify_password(current, user["password"]):
                messagebox.showerror("Change Password", "Current password is incorrect.")
                entries[0].delete(0, tk.END)
                entries[0].focus()
                return
            if len(new) < 12:
                messagebox.showerror("Change Password", "Use a new password of at least 12 characters.")
                return
            if new != confirmation:
                messagebox.showerror("Change Password", "New passwords do not match.")
                return
            if new == current:
                messagebox.showerror("Change Password", "Choose a password different from your current password.")
                return
            try:
                updated_users = {**self.users, self.current_user: {**user, "password": hash_password(new)}}
                save_users(updated_users)
            except Exception as exc:
                messagebox.showerror("Change Password", f"Could not save your password: {exc}")
                return
            self.users = updated_users
            for entry in entries:
                entry.delete(0, tk.END)
            self.log_audit("PASSWORD_CHANGE", "AUTHENTICATION", "User changed their own password")
            messagebox.showinfo("Change Password", "Your password has been changed successfully.")

        tk.Button(card, text="CHANGE PASSWORD", command=save_password,
                  bg=self.COLORS["accent"], fg="white", font=self.FONTS["button"],
                  relief="flat", padx=16, pady=10).pack(anchor="w")
        entries[-1].bind("<Return>", lambda event: save_password())
        entries[0].focus()

    # -------------------------
    # VIEW: LIVE DATA
    # -------------------------
    def show_live_data(self):
        # Premium Control Bar (Live Stop and Start)
        control_bar = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], pady=20, padx=30,
                              highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        control_bar.pack(side="top", fill="x", pady=(0, 15))

        # Status Badge
        active_nodes = sum(1 for k in self.device_threads if self.device_threads[k].is_alive())
        total_nodes = len(self.devices)
        
        status_frame = tk.Frame(control_bar, bg=self.COLORS["bg_surface"])
        status_frame.pack(side="left")
        
        tk.Label(status_frame, text="LIVE CONTROL PANEL", font=("Segoe UI", 8, "bold"), 
                 fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left")
        
        status_text = f"{active_nodes} / {total_nodes} NODES ONLINE"
        tk.Label(status_frame, text=status_text, font=("Segoe UI", 10, "bold"), 
                 fg=self.COLORS["accent"] if active_nodes > 0 else self.COLORS["text_dim"], 
                 bg=self.COLORS["bg_surface"], padx=10).pack(side="left")

        # Master Actions
        actions_frame = tk.Frame(control_bar, bg=self.COLORS["bg_surface"])
        actions_frame.pack(side="right")

        tk.Button(actions_frame, text="▶ START ALL NODES", 
                  bg=self.COLORS["success"], fg="white", 
                  font=self.FONTS["button"], relief="flat", cursor="hand2",
                  padx=25, pady=8, command=self.start_all_devices).pack(side="left", padx=5)

        tk.Button(actions_frame, text="■ STOP ALL NODES", 
                   bg=self.COLORS["danger"], fg="white", 
                   font=self.FONTS["button"], relief="flat", cursor="hand2",
                   padx=25, pady=8, command=self.stop_all_devices).pack(side="left", padx=5)

        # -------------------------
        # LIVE TREND ANALYTICS (Bottom Panel)
        # -------------------------
        graph_frame = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], height=340,
                               highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        graph_frame.pack(side="bottom", fill="x", pady=(15, 0))
        graph_frame.pack_propagate(False)

        header_frame = tk.Frame(graph_frame, bg=self.COLORS["bg_surface"])
        header_frame.pack(fill="x", padx=15, pady=(8, 4))

        tk.Label(header_frame, text="LIVE TREND ANALYTICS", font=("Segoe UI", 9, "bold"), 
                 fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(side="left")

        self.graph_title = tk.Label(header_frame, text="SELECT A CARD TO VIEW TREND", font=("Segoe UI", 8, "bold"), 
                                   fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"])
        self.graph_title.pack(side="right")

        self.graph_canvas = tk.Canvas(graph_frame, bg=self.COLORS["bg_dark"], highlightthickness=0)
        self.graph_canvas.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        
        def _on_live_graph_resize(e):
            lid = getattr(self, 'selected_graph_lid', None)
            nm = getattr(self, 'selected_graph_name', 'Live Data')
            if lid:
                self.draw_live_graph(lid, nm)
        self.graph_canvas.bind("<Configure>", _on_live_graph_resize)

        self.chat_box = None

        # Scrollable Area (Main Grid)
        container = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg=self.COLORS["bg_dark"], highlightthickness=0)
        v_sb = ttk.Scrollbar(container, orient="vertical", style="Vertical.TScrollbar", command=canvas.yview)
        
        self.grid_frame = tk.Frame(canvas, bg=self.COLORS["bg_dark"])
        
        # Window configuration
        def _configure_window(event):
            canvas.itemconfig(canvas_window, width=event.width)
        
        canvas_window = canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        canvas.bind("<Configure>", _configure_window)
        self.grid_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=v_sb.set)

        v_sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # Mouse Wheel Support (Local)
        def _on_mousewheel(event):
            try:
                canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            except:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        if not self.devices:
            tk.Label(self.grid_frame, text="NO DEVICES CONFIGURED", font=self.FONTS["body"], 
                     fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"]).pack(pady=100)
        else:
            for dev in self.devices:
                self.render_device_block(self.grid_frame, dev)
        
        # Final Scrollregion update
        self.grid_frame.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

    # -------------------------
    # VIEW: TREND ANALYTICS
    # -------------------------
    def show_data_trends(self):
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True, padx=30, pady=10)
        
        # Selection Bar (Three Rows for better layout and visibility)
        sel_bar = tk.Frame(layout, bg=self.COLORS["bg_surface"], padx=25, pady=10, 
                          highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        sel_bar.pack(fill="x", pady=(0, 15))
        
        row1 = tk.Frame(sel_bar, bg=self.COLORS["bg_surface"])
        row1.pack(fill="x", pady=5)
        
        row2 = tk.Frame(sel_bar, bg=self.COLORS["bg_surface"])
        row2.pack(fill="x", pady=5)

        row3 = tk.Frame(sel_bar, bg=self.COLORS["bg_surface"])
        row3.pack(fill="x", pady=5)
        
        # Row 1: Node and Registers
        tk.Label(row1, text="SELECT NODE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left")
        
        dev_options = [d.get('device_name') if d.get('device_name') else f"{d['ip']} @ {d.get('slave_id', 1)}" for d in self.devices]
        
        self.trend_dev_cb = ttk.Combobox(row1, values=dev_options, width=30, font=self.FONTS["body"])
        self.trend_dev_cb.pack(side="left", padx=15)
        
        tk.Label(row1, text="PARAMETERS", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(10, 0))
        
        # Custom Multi-select Dropdown (Matching Image)
        combo_cnt = tk.Frame(row1, bg=self.COLORS["bg_surface"])
        combo_cnt.pack(side="left", padx=10)
        
        # Display Box
        self.combo_val_var = tk.StringVar(value="Select Parameters")
        self.combo_box = tk.Frame(combo_cnt, bg="#F0F0F0", highlightbackground="black", highlightthickness=1, cursor="hand2")
        self.combo_box.pack(side="left", ipady=2)
        
        self.combo_label = tk.Label(self.combo_box, textvariable=self.combo_val_var, width=35, anchor="w", 
                                    bg="#F0F0F0", font=("Segoe UI", 9), padx=5)
        self.combo_label.pack(side="left", pady=1)
        

        # Popup Frame (Initially Hidden)
        self.dropdown_popup = tk.Frame(layout, bg="#F0F0F0", highlightbackground="grey", highlightthickness=1)
        self.dropdown_popup_visible = False
        
        # Inner Scrollable Frame for Checkboxes
        self.pop_canvas = tk.Canvas(self.dropdown_popup, bg="#F0F0F0", height=150, width=285, highlightthickness=0)
        pop_scroll = ttk.Scrollbar(self.dropdown_popup, orient="vertical", command=self.pop_canvas.yview)
        self.pop_inner = tk.Frame(self.pop_canvas, bg="#F0F0F0")
        
        self.pop_canvas.create_window((0,0), window=self.pop_inner, anchor="nw")
        self.pop_canvas.configure(yscrollcommand=pop_scroll.set)
        self.pop_canvas.pack(side="left", fill="both", expand=True)
        pop_scroll.pack(side="right", fill="y")
        
        def _update_pop_scroll(e=None):
            self.pop_canvas.configure(scrollregion=self.pop_canvas.bbox("all"))
        self.pop_inner.bind("<Configure>", _update_pop_scroll)

        def _toggle_popup(e=None):
            if self.dropdown_popup_visible:
                self.dropdown_popup.place_forget()
                self.dropdown_popup_visible = False
            else:
                # Force update of tasks to get accurate winfo values
                self.root.update_idletasks()
                
                # Get root coordinates of the combo box
                bx_root_x = self.combo_box.winfo_rootx()
                bx_root_y = self.combo_box.winfo_rooty()
                
                # Get root coordinates of the layout frame (parent of the popup)
                layout_root_x = layout.winfo_rootx()
                layout_root_y = layout.winfo_rooty()
                
                # Calculate relative position
                rel_x = bx_root_x - layout_root_x
                rel_y = bx_root_y - layout_root_y + self.combo_box.winfo_height()
                
                self.dropdown_popup.place(x=rel_x, y=rel_y)
                self.dropdown_popup.lift()
                self.dropdown_popup_visible = True

        self.combo_box.bind("<Button-1>", _toggle_popup)
        self.combo_label.bind("<Button-1>", _toggle_popup)

        self.trend_reg_vars = [] # Store (addr, BooleanVar)

        def _select_all_regs():
            for _, var in self.trend_reg_vars:
                var.set(True)
            _on_reg_change()

        tk.Button(row1, text="SET ALL", bg=self.COLORS["bg_highlight"], fg=self.COLORS["accent"], 
                  font=("Segoe UI", 8, "bold"), relief="flat", padx=10,
                  command=_select_all_regs).pack(side="left")

        # Row 2: Date Selection
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0)
        
        def trend_dt_selector(label, initial_dt, parent):
            f = tk.Frame(parent, bg=self.COLORS["bg_surface"])
            f.pack(side="left", padx=(0, 20))
            tk.Label(f, text=label, font=("Segoe UI", 7, "bold"), bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(side="left", padx=5)
            
            years = [str(y) for y in range(2024, 2031)]
            months = [f"{i:02d}" for i in range(1, 13)]
            days = [f"{i:02d}" for i in range(1, 32)]
            hours = [f"{i:02d}" for i in range(0, 24)]
            minutes = [f"{i:02d}" for i in range(0, 60)]
            
            sel = {}
            sel['y'] = ttk.Combobox(f, values=years, width=5, font=("Consolas", 9)); sel['y'].set(initial_dt.strftime("%Y")); sel['y'].pack(side="left")
            sel['m'] = ttk.Combobox(f, values=months, width=3, font=("Consolas", 9)); sel['m'].set(initial_dt.strftime("%m")); sel['m'].pack(side="left", padx=2)
            sel['d'] = ttk.Combobox(f, values=days, width=3, font=("Consolas", 9)); sel['d'].set(initial_dt.strftime("%d")); sel['d'].pack(side="left")
            tk.Label(f, text="@", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(side="left", padx=2)
            sel['h'] = ttk.Combobox(f, values=hours, width=3, font=("Consolas", 9)); sel['h'].set(initial_dt.strftime("%H")); sel['h'].pack(side="left")
            tk.Label(f, text=":", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(side="left")
            sel['min'] = ttk.Combobox(f, values=minutes, width=3, font=("Consolas", 9)); sel['min'].set(initial_dt.strftime("%M")); sel['min'].pack(side="left")
            return sel

        self.trend_from_dt = trend_dt_selector("FROM", start_of_day, row2)
        self.trend_to_dt = trend_dt_selector("TO", now, row2)

        tk.Button(row2, text="🔍 QUERY DATA", bg="#0288D1", fg="white", 
                  font=("Segoe UI", 8, "bold"), relief="flat", padx=15, pady=2, cursor="hand2",
                  command=self.sync_trend_history).pack(side="left", padx=10)

        # Row 3: Limits and PDF
        tk.Label(row3, text="LOW LIMIT", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left")
        self.trend_low_limit = tk.Entry(row3, width=8, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=("Consolas", 9))
        self.trend_low_limit.insert(0, self.trend_settings.get("low_limit", ""))
        self.trend_low_limit.pack(side="left", padx=5)

        tk.Label(row3, text="UPPER LIMIT", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(10, 0))
        self.trend_upper_limit = tk.Entry(row3, width=8, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=("Consolas", 9))
        self.trend_upper_limit.insert(0, self.trend_settings.get("upper_limit", ""))
        self.trend_upper_limit.pack(side="left", padx=5)

        tk.Label(row3, text="CONDITION", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(10, 0))
        self.trend_cond_cb = ttk.Combobox(row3, values=["Both Limits", "Upper Limit Only", "Low Limit Only", "No Limits"], width=13, style="TCombobox")
        self.trend_cond_cb.set(self.trend_settings.get("visible_condition", "Both Limits"))
        self.trend_cond_cb.pack(side="left", padx=5)

        tk.Label(row3, text="INTERVAL", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(10, 0))
        self.trend_interval_val = tk.Entry(row3, width=5, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], font=("Consolas", 10), relief="flat")
        self.trend_interval_val.insert(0, "1")
        self.trend_interval_val.pack(side="left", padx=5)
        self.trend_interval_unit = ttk.Combobox(row3, values=["Raw", "Sec", "Min", "Hr"], width=5, style="TCombobox")
        self.trend_interval_unit.set("Raw")
        self.trend_interval_unit.pack(side="left", padx=(0, 5))


        tk.Label(row3, text="PDF TYPE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(10, 0))
        self.trend_pdf_type = ttk.Combobox(row3, values=["Line Graph", "Bar Chart"], width=10, style="TCombobox")
        self.trend_pdf_type.set("Line Graph")
        self.trend_pdf_type.pack(side="left", padx=5)

        tk.Button(row3, text="📄 EXPORT PDF", bg="#FF5722", fg="white", 
                  font=("Segoe UI", 8, "bold"), relief="flat", padx=15, pady=2,
                  command=self.export_trend_pdf).pack(side="right")
        
        # Main Display: Notebook for Graph and Table
        self.trend_notebook = ttk.Notebook(layout)
        self.trend_notebook.pack(fill="both", expand=True)

        # Tab 1: Graph View
        self.trend_graph_tab = tk.Frame(self.trend_notebook, bg=self.COLORS["bg_dark"])
        self.trend_notebook.add(self.trend_graph_tab, text=" GRAPH VIEW ")

        # Tab 2: Table View
        self.trend_table_tab = tk.Frame(self.trend_notebook, bg=self.COLORS["bg_dark"])
        self.trend_notebook.add(self.trend_table_tab, text=" DATA TABLE ")

        # Tab 3: Bar Chart View
        self.trend_bar_tab = tk.Frame(self.trend_notebook, bg=self.COLORS["bg_dark"])
        self.trend_notebook.add(self.trend_bar_tab, text=" BAR CHART ")

        # Graph View Content
        graph_cnt = tk.Frame(self.trend_graph_tab, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        graph_cnt.pack(fill="both", expand=True)
        
        self.large_graph_canvas = tk.Canvas(graph_cnt, bg=self.COLORS["bg_dark"], highlightthickness=0)
        self.large_graph_canvas.pack(fill="both", expand=True, padx=20, pady=20)
        self.large_graph_canvas.create_text(400, 200, text="Select Node & Parameters, set Date Range, then click '🔍 QUERY DATA' to display graph.", font=("Segoe UI", 11, "bold"), fill=self.COLORS["text_dim"])

        def _on_lg_resize(e):
            lids = getattr(self, 'selected_trend_lids', [])
            if lids and self.active_view == "data_trends":
                self.draw_large_graph(lids)
        self.large_graph_canvas.bind("<Configure>", _on_lg_resize)

        # Table View Content
        table_cnt = tk.Frame(self.trend_table_tab, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        table_cnt.pack(fill="both", expand=True)
        
        self.trend_tree_frame = tk.Frame(table_cnt, bg=self.COLORS["bg_surface"])
        self.trend_tree_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.trend_tree = ttk.Treeview(self.trend_tree_frame, show="headings", style="Treeview")
        self.trend_tree.tag_configure("alert_row", foreground="#E53935", font=("Segoe UI", 9, "bold"))
        self.trend_tree.pack(side="left", fill="both", expand=True)

        tt_scroll_y = ttk.Scrollbar(self.trend_tree_frame, orient="vertical", command=self.trend_tree.yview)
        tt_scroll_y.pack(side="right", fill="y")
        self.trend_tree.config(yscrollcommand=tt_scroll_y.set)

        tt_scroll_x = ttk.Scrollbar(table_cnt, orient="horizontal", command=self.trend_tree.xview)
        tt_scroll_x.pack(side="bottom", fill="x")
        self.trend_tree.config(xscrollcommand=tt_scroll_x.set)

        # Bar Chart Content
        bar_cnt = tk.Frame(self.trend_bar_tab, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        bar_cnt.pack(fill="both", expand=True)

        self.bar_chart_canvas = tk.Canvas(bar_cnt, bg=self.COLORS["bg_dark"], highlightthickness=0)
        self.bar_chart_canvas.pack(fill="both", expand=True, padx=20, pady=20)

        def _on_bc_resize(e):
            lids = getattr(self, 'selected_trend_lids', [])
            if lids and self.active_view == "data_trends":
                self.draw_bar_chart(lids)
        self.bar_chart_canvas.bind("<Configure>", _on_bc_resize)
        
        def _on_dev_change(e):
            idx = self.trend_dev_cb.current()
            if idx >= 0:
                d = self.devices[idx]
                for widget in self.pop_inner.winfo_children():
                    widget.destroy()
                self.trend_reg_vars = []
                
                for r in d['registers']:
                    name = d.get('names', {}).get(str(r), f"Reg {r}")
                    var = tk.BooleanVar(value=False)
                    cb = tk.Checkbutton(self.pop_inner, text=name.upper(), variable=var,
                                        bg="#F0F0F0", fg=self.COLORS["text_main"],
                                        activebackground="#E4E6EB", font=("Segoe UI", 9),
                                        anchor="w", command=_on_reg_change)
                    cb.pack(fill="x", padx=5, pady=2)
                    self.trend_reg_vars.append((r, var))
                
                # Auto select first if available
                if self.trend_reg_vars:
                    self.trend_reg_vars[0][1].set(True)
                
                _on_reg_change()

        def _on_reg_change(e=None):
            dev_idx = self.trend_dev_cb.current()
            if dev_idx >= 0:
                d = self.devices[dev_idx]
                lids = []
                selected_names = []
                for r_addr, var in self.trend_reg_vars:
                    if var.get():
                        lid = f"{d['ip']}:{d['port']}:{d.get('slave_id', 1)}:{r_addr}"
                        lids.append(lid)
                        nm = d.get('names', {}).get(str(r_addr), f"Reg {r_addr}")
                        selected_names.append(nm.upper())
                
                # Update Combo Box Label
                if not selected_names:
                    self.combo_val_var.set("Select Parameters")
                elif len(selected_names) == 1:
                    self.combo_val_var.set(selected_names[0])
                else:
                    self.combo_val_var.set(f"{len(selected_names)} Parameters Selected")

                self.selected_trend_lids = lids

        self.trend_dev_cb.bind("<<ComboboxSelected>>", _on_dev_change)
        
        if dev_options:
            self.trend_dev_cb.current(0)
            _on_dev_change(None)

    @storage_operation
    def export_trend_pdf(self, dev_override=None):
        if not HAS_REPORTLAB:
            messagebox.showerror("Error", "Feature unavailable: reportlab library not found.")
            return

        if dev_override:
            dev = dev_override
            lids = [f"{dev['ip']}:{dev['port']}:{dev.get('slave_id', 1)}:{r}" for r in dev['registers']]
        else:
            lids = getattr(self, 'selected_trend_lids', [])
            if not lids:
                messagebox.showwarning("No Data", "Please select parameters to export.")
                return
            idx = self.trend_dev_cb.current()
            if idx < 0: return
            dev = self.devices[idx]
        d_alias = dev.get('device_name', dev['ip'])
        
        # Sanitize d_alias for filename
        clean_alias = d_alias
        for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|', ' ']:
            clean_alias = clean_alias.replace(char, '_')
            
        # Select Export Folder
        export_dir = filedialog.askdirectory(title="Select Folder to Save PDF Report", initialdir=DATA_PATH)
        if not export_dir: return

        # Create professional filename in the selected directory
        target_file = os.path.join(export_dir, f"TrendReport_{clean_alias}_{datetime.now().strftime('%H%M%S')}.pdf")
        
        reg_names = []
        for lid in lids:
            parts = str(lid).split(":", 3)
            r_addr = parts[3] if len(parts) >= 4 else str(lid).split(":")[-1]
            reg_names.append(dev.get('names', {}).get(r_addr, f"Reg {r_addr}"))

        try:
            num_params = len(reg_names)
            if num_params > 4:
                p_size = landscape(letter)
                col_spacing = (p_size[0] - 200) / num_params
                f_size = 9 if num_params <= 6 else (8 if num_params <= 10 else 7)
            else:
                p_size = letter
                col_spacing = 100
                f_size = 10

            c = canvas.Canvas(target_file, pagesize=p_size)
            w, h = p_size
            
            # Parse From/To range for Filtering
            try:
                f_dt = datetime.strptime(f"{self.trend_from_dt['y'].get()}-{self.trend_from_dt['m'].get()}-{self.trend_from_dt['d'].get()} {self.trend_from_dt['h'].get()}:{self.trend_from_dt['min'].get()}:00", "%Y-%m-%d %H:%M:%S")
                t_dt = datetime.strptime(f"{self.trend_to_dt['y'].get()}-{self.trend_to_dt['m'].get()}-{self.trend_to_dt['d'].get()} {self.trend_to_dt['h'].get()}:{self.trend_to_dt['min'].get()}:59", "%Y-%m-%d %H:%M:%S")
            except: 
                f_dt, t_dt = datetime.min, datetime.max

            # Header
            c.setFillColorRGB(0.0, 0.36, 0.72); c.setFont("Helvetica-Bold", 18)
            c.drawString(40, h-50, self.company_details.get("company_name", "TempTrek Environmental Monitoring System"))
            c.setFont("Helvetica", 9); c.setFillColorRGB(0.5, 0.5, 0.5)
            c.drawString(40, h-70, f"DEVICE: {d_alias} | EXPORTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            c.drawString(40, h-85, f"RANGE: {f_dt.strftime('%Y-%m-%d %H:%M')} TO {t_dt.strftime('%Y-%m-%d %H:%M')}")
            c.line(40, h-95, w-40, h-95)

            # --- DRAW GRAPH PREVIEW ---
            # Graph dimensions
            gw, gh = w - 80, 220
            gx, gy = 40, h - 330
            
            # Background for graph area
            c.setFillColorRGB(1.0, 0.95, 0.95) # Very light red tint
            c.rect(gx, gy, gw, gh, fill=1, stroke=0)
            
            # Draw Axis
            c.setStrokeColorRGB(0.4, 0.4, 0.4); c.setLineWidth(0.5)
            c.line(gx+60, gy+40, gx+gw-150, gy+40)  # X-axis
            c.line(gx+60, gy+40, gx+60, gy+gh-30) # Y-axis
            
            all_series = []
            for lid in lids:
                d = self.data_history.get(lid, [])
                if d: all_series.append((lid, d))
            
            if all_series:
                # Global limits for scaling
                vals = [v for s in all_series for v in s[1]]
                glob_min, glob_max = min(vals), max(vals)
                if glob_min == glob_max: glob_min -= 5; glob_max += 5
                v_range = glob_max - glob_min
                max_len = max([len(s[1]) for s in all_series])
                
                plot_w = gw - 60 - 160
                plot_h = gh - 40 - 30
                
                # Y-Axis Labels & Grid
                c.setFont("Helvetica", 7); c.setFillColorRGB(0.4, 0.4, 0.4)
                for i in range(6):
                    tick_val = glob_min + (i * v_range / 5)
                    py = gy + 40 + (i * plot_h / 5)
                    c.setStrokeColorRGB(0.85, 0.85, 0.85)
                    c.line(gx+60, py, gx+60+plot_w, py)
                    prec = 2 # Fixed precision for Y-axis labels
                    c.drawString(gx+15, py-2, f"{tick_val:.{prec}f}")

                # Draw Visual Limits (Dashed Lines)
                try:
                    l_limit = self.trend_low_limit.get().strip()
                    h_limit = self.trend_upper_limit.get().strip()
                    v_cond = self.trend_cond_cb.get()
                    
                    if l_limit and v_cond in ["Both Limits", "Low Limit Only"]:
                        lv = float(l_limit)
                        ly = gy + 40 + ((lv - glob_min) * plot_h / v_range)
                        if gy+40 <= ly <= gy+gh-30:
                            c.setStrokeColorRGB(0.83, 0.18, 0.18); c.setDash(3, 3)
                            c.line(gx+60, ly, gx+60+plot_w, ly)
                            c.drawString(gx+60+plot_w+5, ly-2, f"LOW: {lv}")
                            c.setDash([])
                    if h_limit and v_cond in ["Both Limits", "Upper Limit Only"]:
                        hv = float(h_limit)
                        hy = gy + 40 + ((hv - glob_min) * plot_h / v_range)
                        if gy+40 <= hy <= gy+gh-30:
                            c.setStrokeColorRGB(0.8, 0.2, 0.0); c.setDash(3, 3)
                            c.line(gx+60, hy, gx+60+plot_w, hy)
                            c.drawString(gx+60+plot_w+5, hy-2, f"UPPER: {hv}")
                            c.setDash([])
                except: pass

                # Draw Data (Line or Bar)
                palette = ["#D32F2F", "#28A745", "#4285F4", "#F29900", "#6F42C1"]
                p_type = getattr(self, 'trend_pdf_type', None)
                p_type_val = p_type.get() if p_type else "Line Graph"
                
                if p_type_val == "Bar Chart":
                    num_series = len(all_series)
                    group_w = plot_w / (max_len if max_len > 0 else 1)
                    bar_w = (group_w * 0.8) / num_series
                    
                    # Ensure bars start from 0 if possible
                    bar_min = glob_min if glob_min < 0 else 0
                    
                    for idx, (lid, data) in enumerate(all_series):
                        color = palette[idx % len(palette)]
                        r_col = int(color[1:3], 16)/255.0
                        g_col = int(color[3:5], 16)/255.0
                        b_col = int(color[5:7], 16)/255.0
                        c.setFillColorRGB(r_col, g_col, b_col)
                        
                        for i, v in enumerate(data):
                            bx = gx + 60 + (i * group_w) + (idx * bar_w) + (group_w * 0.1)
                            by_top = gy + 40 + ((v - glob_min) * plot_h / v_range)
                            by_bottom = gy + 40 + ((0 - glob_min) * plot_h / v_range) if glob_min <= 0 else gy + 40
                            
                            bh = by_top - by_bottom
                            c.rect(bx, by_bottom, bar_w - 1, bh, fill=1, stroke=0)
                else:
                    # Default: Line Graph
                    for idx, (lid, data) in enumerate(all_series):
                        color = palette[idx % len(palette)]
                        r_col = int(color[1:3], 16)/255.0
                        g_col = int(color[3:5], 16)/255.0
                        b_col = int(color[5:7], 16)/255.0
                        
                        c.setStrokeColorRGB(r_col, g_col, b_col); c.setLineWidth(1.5)
                        p = c.beginPath()
                        for i, v in enumerate(data):
                            px = gx + 60 + (i * plot_w / (max_len-1 if max_len > 1 else 1))
                            py = gy + 40 + ((v - glob_min) * plot_h / v_range)
                            if i == 0: p.moveTo(px, py)
                            else: p.lineTo(px, py)
                        c.drawPath(p)
                    
                # Legend
                for idx, (lid, data) in enumerate(all_series):
                    color = palette[idx % len(palette)]
                    r_col = int(color[1:3], 16)/255.0
                    g_col = int(color[3:5], 16)/255.0
                    b_col = int(color[5:7], 16)/255.0
                    
                    parts = str(lid).split(":", 3)
                    r_addr = parts[3] if len(parts) >= 4 else str(lid).split(":")[-1]
                    leg_name = dev.get('names', {}).get(r_addr, f"Reg {r_addr}")
                    ly_leg = gy + gh - 50 - (idx * 15)
                    c.setFillColorRGB(r_col, g_col, b_col); c.rect(gx + gw - 140, ly_leg, 8, 8, fill=1, stroke=0)
                    c.setFillColorRGB(0.2, 0.2, 0.2); c.setFont("Helvetica-Bold", 8)
                    c.drawString(gx + gw - 125, ly_leg + 1, leg_name.upper())
            
            # --- DATA LOG TABLE ---
            curr_y = gy - 40
            c.setFillColorRGB(0.1, 0.1, 0.1); c.setFont("Helvetica-Bold", 10)
            c.drawString(40, curr_y, "HISTORICAL DATA LOG (SELECTED RANGE)")
            
            curr_y -= 25
            c.line(40, curr_y+15, w-40, curr_y+15)
            c.setFont("Helvetica-Bold", f_size)
            c.drawString(40, curr_y, "TIMESTAMP")
            for i, rname in enumerate(reg_names): # Removed limit for width
                c.drawString(180 + (i*col_spacing), curr_y, rname.upper())
            
            curr_y -= 35
            c.line(40, curr_y+20, w-40, curr_y+20)
            c.setFont("Helvetica", 9)
            
            ip, port, slave = dev['ip'], dev['port'], dev.get('slave_id', 1)
            
            # Find all files for this device in the 'live' folder
            prefix = f"{ip.replace('.', '_')}_{port}_s{slave}"
            all_files = []
            live_dir = os.path.join(DATA_PATH, "live")
            if os.path.exists(live_dir):
                for f in os.listdir(live_dir):
                    if f.startswith(prefix) and f.endswith(".csv"):
                        all_files.append(os.path.join(live_dir, f))
            all_files.sort()

            # Interval Logic for PDF
            try:
                ival = float(self.trend_interval_val.get())
                iunit = self.trend_interval_unit.get()
                if iunit == "Sec": isec = ival
                elif iunit == "Min": isec = ival * 60
                elif iunit == "Hr": isec = ival * 3600
                else: isec = 0
            except: isec = 0

            rows_found = 0
            if all_files:
                all_rows = []
                # Read last 500 rows across ALL historical files
                for sf in all_files:
                    try:
                        with open(sf, 'r', encoding='utf-8', errors='replace') as f:
                            reader = csv.reader((line.replace('\0', '') for line in f)); next(reader, None)
                            for row in reader: all_rows.append(row)
                    except: continue
                
                pivoted = {}
                target_regs = [lid.split(":")[-1] for lid in lids]
                for row in all_rows:
                    try:
                        row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                        if f_dt <= row_dt <= t_dt:
                            ts, r_addr, val = row[0], str(row[4]), row[5]
                            if isec > 0:
                                rounded_ts = int(row_dt.timestamp() // isec * isec)
                                ts = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                            
                            if r_addr in target_regs:
                                is_lora = ("LORA" in str(dev.get('node_type', '')).upper()) or ("LORA" in str(dev.get('device_name', '')).upper())
                                val_str = str(val).strip()
                                is_v = True
                                try:
                                    fv = float(val_str)
                                    if math.isnan(fv) or math.isinf(fv): is_v = False
                                    elif is_lora and fv <= 0: is_v = False
                                except Exception:
                                    if val_str.lower() in ["nan", "none", "null", ""]: is_v = False
                                if is_v:
                                    if ts not in pivoted: pivoted[ts] = {}
                                    pivoted[ts][r_addr] = val
                    except: continue
                
                # Get active email rules for the selected device
                active_alerts = []
                ip_addr = dev['ip']
                slave_id = str(dev.get('slave_id', 1))
                for alert in self.email_settings.get('alerts', []):
                    if str(alert.get('device_ip')) == str(ip_addr) and str(alert.get('slave_id', 1)) == slave_id:
                        active_alerts.append(alert)

                for ts in sorted(pivoted.keys(), reverse=True):
                    row_vals_only = [pivoted[ts].get(r_addr, "---") for r_addr in target_regs]

                    if curr_y < 60:
                        c.showPage(); curr_y = h - 60
                        # Re-draw Header on new page
                        c.setFillColorRGB(0.1, 0.1, 0.1); c.setFont("Helvetica-Bold", f_size)
                        c.drawString(40, curr_y, "TIMESTAMP")
                        for i, rname in enumerate(reg_names):
                            c.drawString(180 + (i*col_spacing), curr_y, rname.upper())
                        curr_y -= 35
                        c.line(40, curr_y+20, w-40, curr_y+20)
                        c.setFont("Helvetica", f_size)
                    
                    # Check if any value in this row triggers an alert
                    row_has_alert = False
                    for i, r_addr in enumerate(target_regs):
                        val = row_vals_only[i]
                        if val != "---":
                            try:
                                f_val = float(val)
                                for alert in active_alerts:
                                    if str(alert.get('reg')) == str(r_addr):
                                        cond = alert.get('condition')
                                        thresh = float(alert.get('threshold'))
                                        if (cond == ">" and f_val > thresh) or \
                                           (cond == "<" and f_val < thresh) or \
                                           (cond == "==" and f_val == thresh):
                                            row_has_alert = True; break
                                if row_has_alert: break
                            except: pass

                    # Apply color for the whole row
                    if row_has_alert:
                        c.setFillColorRGB(0.85, 0.1, 0.1) # Bold Red
                        c.setFont("Helvetica-Bold", f_size)
                    else:
                        c.setFillColorRGB(0.3, 0.3, 0.3)
                        c.setFont("Helvetica", f_size)

                    c.drawString(40, curr_y, ts)

                    if not row_has_alert:
                        c.setFillColorRGB(0.1, 0.1, 0.1)

                    for i, r_addr in enumerate(target_regs):
                        v = pivoted[ts].get(r_addr, "---")
                        if v != "---":
                            if str(v).strip().lower() in ["nan", "none", "null", "", "inf", "-inf"]:
                                v = "---"
                            else:
                                try:
                                    if dev.get('types', {}).get(str(r_addr)) == "BOOLEAN":
                                        label_0 = dev.get('ops', {}).get(str(r_addr), "OFF")
                                        label_1 = dev.get('factors', {}).get(str(r_addr), "ON")
                                        v = label_0 if int(float(v)) == 0 else label_1
                                    else:
                                        prec = int(dev.get('decimals', {}).get(str(r_addr), 3))
                                        fv = float(v)
                                        v = "---" if (math.isnan(fv) or math.isinf(fv)) else f"{fv:.{prec}f}"
                                except: pass
                        c.drawString(180 + (i*col_spacing), curr_y, v)
                    curr_y -= 15
                    rows_found += 1

            if rows_found == 0:
                c.drawString(40, curr_y, "No historical data available for selected registers.")

            c.save()
            self.log_audit("TREND_EXPORT", "EXPORT", f"Exported trend report PDF for device '{clean_alias}' ({rows_found} records)")
            messagebox.showinfo("Success", f"Trend report saved to {target_file}")
            webbrowser.open(target_file)
            
        except Exception as e:
            messagebox.showerror("Error", f"PDF Export failed: {e}")

    def sync_trend_history(self):
        # Auto-save limits
        if hasattr(self, 'trend_low_limit'):
            self.trend_settings["low_limit"] = self.trend_low_limit.get()
            self.trend_settings["upper_limit"] = self.trend_upper_limit.get()
            self.trend_settings["visible_condition"] = self.trend_cond_cb.get()
            save_trend_settings(self.trend_settings)

        lids = getattr(self, 'selected_trend_lids', [])
        if not lids: return
        
        # Parse From/To range
        try:
            f_dt = datetime.strptime(f"{self.trend_from_dt['y'].get()}-{self.trend_from_dt['m'].get()}-{self.trend_from_dt['d'].get()} {self.trend_from_dt['h'].get()}:{self.trend_from_dt['min'].get()}:00", "%Y-%m-%d %H:%M:%S")
            t_dt = datetime.strptime(f"{self.trend_to_dt['y'].get()}-{self.trend_to_dt['m'].get()}-{self.trend_to_dt['d'].get()} {self.trend_to_dt['h'].get()}:{self.trend_to_dt['min'].get()}:59", "%Y-%m-%d %H:%M:%S")
        except: 
            f_dt, t_dt = datetime.min, datetime.max

        pivoted_data = {} # { timestamp: { reg_addr: value } }
        target_regs = {}
        for lid in lids:
            parts = str(lid).split(":", 3)
            r_id = parts[3] if len(parts) >= 4 else str(lid).split(":")[-1]
            target_regs[r_id] = lid

        # We assume all selected lids are from the same device
        first_lid_parts = str(lids[0]).split(":", 3)
        ip, port, slave = first_lid_parts[0], first_lid_parts[1], first_lid_parts[2]
        
        # Find device object to get names
        active_dev = None
        for d in self.devices:
            if d['ip'] == ip and d['port'] == int(port) and d.get('slave_id', 1) == int(slave):
                active_dev = d
                break

        addr_to_name = {}
        for r_addr in target_regs.keys():
            if active_dev:
                nm = active_dev.get('names', {}).get(str(r_addr), f"Reg {r_addr}")
            else:
                nm = f"Reg {r_addr}"
            addr_to_name[r_addr] = nm

        prefix = f"{ip.replace('.', '_')}_{port}_s{slave}"
        all_files = []
        live_dir = os.path.join(DATA_PATH, "live")
        if os.path.exists(live_dir):
            for f in os.listdir(live_dir):
                if f.startswith(prefix) and f.endswith(".csv"):
                    all_files.append(os.path.join(live_dir, f))
        all_files.sort()

        # Clear existing data history for these lids
        for lid in lids: self.data_history[lid] = []

        # Interval Logic
        try:
            val = float(self.trend_interval_val.get())
            unit = self.trend_interval_unit.get()
            if unit == "Sec": interval_sec = val
            elif unit == "Min": interval_sec = val * 60
            elif unit == "Hr": interval_sec = val * 3600
            else: interval_sec = 0
        except:
            interval_sec = 0

        for fname in all_files:
            try:
                with open(fname, 'r', encoding='utf-8', errors='replace') as f:
                    reader = csv.reader((line.replace('\0', '') for line in f))
                    next(reader, None) 
                    for row in reader:
                        if len(row) > 5:
                            r_addr_raw = str(row[4]).strip()
                            matched_addr = None

                            if r_addr_raw in target_regs:
                                matched_addr = r_addr_raw
                            else:
                                r_digits = ''.join(filter(str.isdigit, r_addr_raw))
                                for t_addr in target_regs:
                                    t_digits = ''.join(filter(str.isdigit, str(t_addr)))
                                    if r_addr_raw == str(t_addr) or (r_digits and r_digits == t_digits):
                                        matched_addr = t_addr
                                        break

                            if matched_addr:
                                try:
                                    row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                                    if f_dt <= row_dt <= t_dt:
                                        ts_key = row[0]
                                        if interval_sec > 0:
                                            rounded_ts = int(row_dt.timestamp() // interval_sec * interval_sec)
                                            ts_key = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                                        
                                        val = float(row[5])
                                        if ts_key not in pivoted_data: pivoted_data[ts_key] = {}
                                        pivoted_data[ts_key][matched_addr] = val
                                except: pass
            except: continue

        # Limit Graph Data to last 300 points
        sorted_ts = sorted(pivoted_data.keys())
        self.trend_timestamps = sorted_ts[-300:] 
        
        for lid in lids:
            parts = str(lid).split(":", 3)
            r_addr = parts[3] if len(parts) >= 4 else str(lid).split(":")[-1]
            
            matched_key = r_addr
            if pivoted_data:
                sample_p = next(iter(pivoted_data.values()))
                if r_addr not in sample_p:
                    r_digits = ''.join(filter(str.isdigit, str(r_addr)))
                    for pk in sample_p.keys():
                        if ''.join(filter(str.isdigit, str(pk))) == r_digits:
                            matched_key = pk
                            break

            self.data_history[lid] = [pivoted_data[ts].get(matched_key, pivoted_data[ts].get(r_addr, 0.0)) for ts in self.trend_timestamps]

        self.trend_addr_to_name = addr_to_name
        self.draw_large_graph(lids)
        self.draw_bar_chart(lids)
        self.update_trend_table(pivoted_data, addr_to_name)



    def update_trend_table(self, pivoted_data, addr_to_name):
        if not hasattr(self, 'trend_tree'): return
        
        # Clear existing
        for i in self.trend_tree.get_children(): self.trend_tree.delete(i)
        
        # Set Columns
        sorted_addrs = sorted(addr_to_name.keys(), key=lambda x: int(x) if x.isdigit() else x)
        
        # Columns internally use addresses, but display names in headings
        cols = ["TIMESTAMP"] + sorted_addrs
        self.trend_tree["columns"] = cols
        
        self.trend_tree.heading("TIMESTAMP", text="TIMESTAMP")
        self.trend_tree.column("TIMESTAMP", width=160, anchor="center")
        
        for addr in sorted_addrs:
            name = addr_to_name[addr]
            self.trend_tree.heading(addr, text=name.upper())
            self.trend_tree.column(addr, width=120, anchor="center")
        
        # Add Rows with Limit Check
        sorted_ts = sorted(pivoted_data.keys(), reverse=True)
        
        # Add Rows with Alert Rule Check
        sorted_ts = sorted(pivoted_data.keys(), reverse=True)
        
        # Get active email rules for the selected device
        idx = self.trend_dev_cb.current()
        active_alerts = []
        if idx >= 0:
            dev = self.devices[idx]
            ip = dev['ip']
            slave = str(dev.get('slave_id', 1))
            for alert in self.email_settings.get('alerts', []):
                if str(alert.get('device_ip')) == str(ip) and str(alert.get('slave_id', 1)) == slave:
                    active_alerts.append(alert)

        for ts in sorted_ts[:1000]: # Limit to 1000 rows
            row_vals = [ts]
            has_alert = False
            all_zero = True
            
            for addr in sorted_addrs:
                val = pivoted_data[ts].get(addr, "---")
                row_vals.append(val)
                
                if val != "---":
                    try:
                        f_val = float(val)
                        if f_val != 0: all_zero = False
                        
                        # Apply decimal formatting for display in table
                        if dev.get('types', {}).get(str(addr)) == "BOOLEAN":
                            label_0 = dev.get('ops', {}).get(str(addr), "OFF")
                            label_1 = dev.get('factors', {}).get(str(addr), "ON")
                            row_vals[-1] = label_0 if int(f_val) == 0 else label_1
                        else:
                            prec = int(dev.get('decimals', {}).get(str(addr), 3))
                            row_vals[-1] = f"{f_val:.{prec}f}"
                        
                        # Check if this specific cell triggers an email alert rule
                        for alert in active_alerts:
                            if str(alert.get('reg')) == str(addr):
                                cond = alert.get('condition')
                                thresh = float(alert.get('threshold'))
                                if (cond == ">" and f_val > thresh) or \
                                   (cond == "<" and f_val < thresh) or \
                                   (cond == "==" and f_val == thresh):
                                    has_alert = True
                                    break
                    except: pass
            
            if all_zero and any(pivoted_data[ts].get(a, "---") != "---" for a in sorted_addrs):
                continue
                
            tag = ("alert_row",) if has_alert else ()
            self.trend_tree.insert("", "end", values=row_vals, tags=tag)

    def draw_large_graph(self, lids):
        if self.active_view != "data_trends" or not hasattr(self, 'large_graph_canvas'): return
        if not self.large_graph_canvas.winfo_exists(): return
        
        self.large_graph_canvas.delete("all")
        if not lids: return
        
        w = self.large_graph_canvas.winfo_width()
        h = self.large_graph_canvas.winfo_height()
        if w < 100: w = 1100 
        if h < 100: h = 550
        
        padding_l = 80
        padding_r = 180 # Extra space for legend
        padding_tb = 60
        
        usable_w = w - padding_l - padding_r
        usable_h = h - (padding_tb * 2)
        
        # Axis lines
        self.large_graph_canvas.create_line(padding_l, h-padding_tb, w-padding_r+10, h-padding_tb, fill=self.COLORS["text_dim"], width=1, arrow=tk.LAST)
        self.large_graph_canvas.create_line(padding_l, padding_tb-10, padding_l, h-padding_tb, fill=self.COLORS["text_dim"], width=1, arrow=tk.FIRST)
        
        all_series = []
        for lid in lids:
            d = self.data_history.get(lid, [])
            if d: all_series.append((lid, d))
            
        if not all_series:
            self.large_graph_canvas.create_text(w/2, h/2, text="NO DATA SELECTED OR AVAILABLE", fill=self.COLORS["text_dim"], font=("Segoe UI", 10, "italic"))
            return

        # Calculate Global Min/Max
        glob_min = min([min(s[1]) for s in all_series])
        glob_max = max([max(s[1]) for s in all_series])
        if glob_min == glob_max: glob_min -= 5; glob_max += 5
        val_range = glob_max - glob_min
        max_len = max([len(s[1]) for s in all_series])
        
        # Grid
        for i in range(11):
            val = glob_min + (i * val_range / 10)
            y = h - padding_tb - (i * usable_h / 10)
            self.large_graph_canvas.create_line(padding_l, y, w-padding_r, y, fill=self.COLORS["bg_highlight"], width=1)
            prec = int(self.trend_settings.get("decimal_places", 3))
            self.large_graph_canvas.create_text(padding_l-15, y, text=f"{val:.{prec}f}", fill=self.COLORS["text_dim"], anchor="e", font=("Consolas", 8))

        # Colors for multiple lines
        palette = ["#D32F2F", "#28A745", "#4285F4", "#F29900", "#6F42C1", "#E83E8C", "#20C997", "#FD7E14"]
        
        # X-Axis Timestamps
        trend_ts = getattr(self, 'trend_timestamps', [])
        if trend_ts and max_len > 1:
            step = max(1, max_len // 6) # Aim for about 6-8 labels
            for i in range(0, max_len, step):
                x = padding_l + (i * usable_w / (max_len - 1))
                ts_str = trend_ts[i].split(" ")[1][:5] # Show HH:MM
                self.large_graph_canvas.create_text(x, h - padding_tb + 20, text=ts_str, fill=self.COLORS["text_dim"], font=("Segoe UI", 8))
                self.large_graph_canvas.create_line(x, h - padding_tb, x, h - padding_tb + 5, fill=self.COLORS["text_dim"])

        # Draw Lines
        legend_y = padding_tb
        for idx, (lid, data) in enumerate(all_series):
            color = palette[idx % len(palette)]
            points = []
            for i, v in enumerate(data):
                x = padding_l + (i * usable_w / (max_len-1 if max_len > 1 else 1))
                y = h - padding_tb - ((v - glob_min) * usable_h / val_range)
                points.extend([x, y])
            
            if len(points) >= 4:
                # Main Trend Line (no area fill for multiple lines to avoid clutter)
                self.large_graph_canvas.create_line(points, fill=color, width=3, smooth=True, joinstyle=tk.ROUND, tags=f"series_{idx}")
                
                # Highlight Current Point
                curr_x, curr_y = points[-2], points[-1]
                self.large_graph_canvas.create_oval(curr_x-4, curr_y-4, curr_x+4, curr_y+4, fill=color, outline=self.COLORS["bg_surface"], width=1)

            # Draw Legend Item
            parts = str(lid).split(":", 3)
            reg_addr = parts[3] if len(parts) >= 4 else str(lid).split(":")[-1]
            display_name = getattr(self, 'trend_addr_to_name', {}).get(reg_addr, f"REG {reg_addr}")
            self.large_graph_canvas.create_rectangle(w-padding_r+30, legend_y, w-padding_r+45, legend_y+15, fill=color, outline="")
            self.large_graph_canvas.create_text(w-padding_r+55, legend_y+7, text=display_name.upper(), anchor="w", fill=self.COLORS["text_main"], font=("Segoe UI", 9, "bold"))
            legend_y += 25

        # Updated Crosshair for multiple series
        def _on_hover(event):
            if not all_series: return
            self.large_graph_canvas.delete("crosshair")
            if event.x < padding_l or event.x > w - padding_r: return
            
            rel_x = (event.x - padding_l) / usable_w
            
            # Vertical guide line
            self.large_graph_canvas.create_line(event.x, padding_tb, event.x, h-padding_tb, fill=self.COLORS["text_dim"], dash=(2, 2), tags="crosshair")
            
            tooltip_y = padding_tb - 30
            for idx, (lid, data) in enumerate(all_series):
                color = palette[idx % len(palette)]
                data_idx = int(rel_x * (len(data) - 1))
                data_idx = max(0, min(len(data) - 1, data_idx))
                
                px = padding_l + (data_idx * usable_w / (max_len-1 if max_len > 1 else 1))
                py = h - padding_tb - ((data[data_idx] - glob_min) * usable_h / val_range)
                
                # Show nearest point for current series
                self.large_graph_canvas.create_oval(px-3, py-3, px+3, py+3, fill=color, outline="white", tags="crosshair")
        
        # Draw Visual Limits (Horizontal Lines)
        try:
            low_v = self.trend_low_limit.get().strip()
            high_v = self.trend_upper_limit.get().strip()
            
            # Recalculate glob_min/max if limits are outside data range to ensure they are visible
            needed_min, needed_max = glob_min, glob_max
            l_val, h_val = None, None
            v_cond = self.trend_cond_cb.get()
            
            if low_v and v_cond in ["Both Limits", "Low Limit Only"]:
                l_val = float(low_v)
                needed_min = min(needed_min, l_val)
                needed_max = max(needed_max, l_val)
            if high_v and v_cond in ["Both Limits", "Upper Limit Only"]:
                h_val = float(high_v)
                needed_min = min(needed_min, h_val)
                needed_max = max(needed_max, h_val)
            
            # Draw them
            if l_val is not None:
                ly = h - padding_tb - ((l_val - glob_min) * usable_h / val_range)
                if padding_tb <= ly <= h - padding_tb:
                    self.large_graph_canvas.create_line(padding_l, ly, w-padding_r, ly, fill="#D32F2F", width=2, dash=(4, 4))
                    self.large_graph_canvas.create_text(w-padding_r+10, ly, text=f"LOW: {l_val}", fill="#D32F2F", anchor="w", font=("Segoe UI", 8, "bold"))
            
            if h_val is not None:
                hy = h - padding_tb - ((h_val - glob_min) * usable_h / val_range)
                if padding_tb <= hy <= h - padding_tb:
                    self.large_graph_canvas.create_line(padding_l, hy, w-padding_r, hy, fill="#D35400", width=2, dash=(4, 4))
                    self.large_graph_canvas.create_text(w-padding_r+10, hy, text=f"UPPER: {h_val}", fill="#D35400", anchor="w", font=("Segoe UI", 8, "bold"))
                    
        except: pass

        self.large_graph_canvas.bind("<Motion>", _on_hover)
        self.large_graph_canvas.bind("<Leave>", lambda e: self.large_graph_canvas.delete("crosshair"))

    def draw_bar_chart(self, lids):
        if self.active_view != "data_trends" or not hasattr(self, 'bar_chart_canvas'): return
        if not self.bar_chart_canvas.winfo_exists(): return
        
        self.bar_chart_canvas.delete("all")
        if not lids: return
        
        w = self.bar_chart_canvas.winfo_width()
        h = self.bar_chart_canvas.winfo_height()
        if w < 100: w = 1100 
        if h < 100: h = 550
        
        padding_l = 80
        padding_r = 180
        padding_tb = 60
        
        usable_w = w - padding_l - padding_r
        usable_h = h - (padding_tb * 2)
        
        # Axis lines
        self.bar_chart_canvas.create_line(padding_l, h-padding_tb, w-padding_r+10, h-padding_tb, fill=self.COLORS["text_dim"], width=1, arrow=tk.LAST)
        self.bar_chart_canvas.create_line(padding_l, padding_tb-10, padding_l, h-padding_tb, fill=self.COLORS["text_dim"], width=1, arrow=tk.FIRST)
        
        all_series = []
        for lid in lids:
            d = self.data_history.get(lid, [])
            if d: all_series.append((lid, d))
            
        if not all_series:
            self.bar_chart_canvas.create_text(w/2, h/2, text="NO DATA SELECTED OR AVAILABLE", fill=self.COLORS["text_dim"], font=("Segoe UI", 10, "italic"))
            return

        # Calculate Global Min/Max
        glob_min = min([min(s[1]) for s in all_series])
        glob_max = max([max(s[1]) for s in all_series])
        if glob_min == glob_max: glob_min -= 5; glob_max += 5
        # If min is positive, start Y axis from 0 for better bar representation
        if glob_min > 0: glob_min = 0
        
        val_range = glob_max - glob_min
        max_len = max([len(s[1]) for s in all_series])
        
        # Grid
        for i in range(11):
            val = glob_min + (i * val_range / 10)
            y = h - padding_tb - (i * usable_h / 10)
            self.bar_chart_canvas.create_line(padding_l, y, w-padding_r, y, fill=self.COLORS["bg_highlight"], width=1)
            prec = int(self.trend_settings.get("decimal_places", 3))
            self.bar_chart_canvas.create_text(padding_l-15, y, text=f"{val:.{prec}f}", fill=self.COLORS["text_dim"], anchor="e", font=("Consolas", 8))

        palette = ["#D32F2F", "#28A745", "#4285F4", "#F29900", "#6F42C1", "#E83E8C", "#20C997", "#FD7E14"]
        
        num_series = len(all_series)
        group_width = usable_w / (max_len if max_len > 0 else 1)
        bar_width = (group_width * 0.8) / num_series
        
        # X-Axis Timestamps
        trend_ts = getattr(self, 'trend_timestamps', [])
        if trend_ts and max_len > 0:
            step = max(1, max_len // 6)
            for i in range(0, max_len, step):
                x = padding_l + (i * group_width) + (group_width / 2)
                ts_str = trend_ts[i].split(" ")[1][:5] # Show HH:MM
                self.bar_chart_canvas.create_text(x, h - padding_tb + 20, text=ts_str, fill=self.COLORS["text_dim"], font=("Segoe UI", 8))
                self.bar_chart_canvas.create_line(x, h - padding_tb, x, h - padding_tb + 5, fill=self.COLORS["text_dim"])

        legend_y = padding_tb
        for idx, (lid, data) in enumerate(all_series):
            color = palette[idx % len(palette)]
            for i, v in enumerate(data):
                # Calculate bar position
                group_x = padding_l + (i * group_width)
                bar_x = group_x + (idx * bar_width) + (group_width * 0.1)
                
                # Calculate bar height
                bar_y_top = h - padding_tb - ((v - glob_min) * usable_h / val_range)
                bar_y_bottom = h - padding_tb - ((0 - glob_min) * usable_h / val_range) if glob_min <= 0 else h - padding_tb
                
                self.bar_chart_canvas.create_rectangle(bar_x, bar_y_top, bar_x + bar_width - 2, bar_y_bottom, 
                                                      fill=color, outline="", tags=f"bar_{idx}")

            # Draw Legend Item
            parts = str(lid).split(":", 3)
            reg_addr = parts[3] if len(parts) >= 4 else str(lid).split(":")[-1]
            display_name = getattr(self, 'trend_addr_to_name', {}).get(reg_addr, f"REG {reg_addr}")
            self.bar_chart_canvas.create_rectangle(w-padding_r+30, legend_y, w-padding_r+45, legend_y+15, fill=color, outline="")
            self.bar_chart_canvas.create_text(w-padding_r+55, legend_y+7, text=display_name.upper(), anchor="w", fill=self.COLORS["text_main"], font=("Segoe UI", 9, "bold"))
            legend_y += 25

    def show_user_manager(self):
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True, padx=30, pady=20)

        outer_left, left = self.create_scrollable_panel(layout, bg=self.COLORS["bg_surface"], pad_x=20, pad_y=15, width=340)
        outer_left.pack(side="left", fill="y", anchor="n")

        tk.Label(left, text="ADD NEW USER", font=self.FONTS["h3"], fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(anchor="w", pady=(0, 15))

        def fld(lbl, parent=left, show=None):
            f = tk.Frame(parent, bg=self.COLORS["bg_surface"])
            f.pack(fill="x", pady=8)
            tk.Label(f, text=lbl, font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
            e = tk.Entry(f, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], 
                         relief="flat", font=self.FONTS["body"], insertbackground="#1C1E21", show=show)
            e.pack(fill="x", pady=(2, 0), ipady=6)
            tk.Frame(f, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")
            return e

        self.u_manager_name = fld("USERNAME")
        self.u_manager_pass = fld("PASSWORD", show="*")

        # Approval authority can be granted if current user is Super Admin or possesses approve_data permission.
        self.u_manager_approver = tk.BooleanVar(value=False)
        current_is_superadmin = (self.current_user == "superadmin") or self.users.get(self.current_user, {}).get("is_superadmin", False)
        manager_perms = self.users.get(self.current_user, {}).get("permissions", [])
        can_assign_view = current_is_superadmin or ("view_data" in manager_perms)
        can_assign_approve = current_is_superadmin or ("approve_data" in manager_perms)

        self.u_manager_approver_cb = tk.Checkbutton(
            left, text="APPROVER (may approve data after password verification)",
            variable=self.u_manager_approver, bg=self.COLORS["bg_surface"],
            fg=self.COLORS["warning"], selectcolor=self.COLORS["bg_dark"],
            activebackground=self.COLORS["bg_surface"], activeforeground="white",
            font=("Segoe UI", 8, "bold"), relief="flat", borderwidth=0,
            state="normal" if can_assign_approve else "disabled"
        )
        self.u_manager_approver_cb.pack(anchor="w", pady=(10, 0))
        
        tk.Label(left, text="PERMISSIONS", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w", pady=(15, 5))
        
        self.perm_vars = {}
        perms = [
            ("live_data", "Live Dashboard"),
            ("live_log", "Live Log"),
            ("export_data", "Data Export"),
            ("email_config", "Email Alerts"),
            ("email_smtp_config", "Gmail Configuration"),
            ("sms_config", "SMS Alerts & Fast2SMS API"),
            ("data_trends", "Historic Trends"),
            ("user_manager", "User Manager"),
            ("audit_log", "Audit Log"),
            ("backup_restore", "Backup & Restore")
        ]
        if can_assign_view:
            perms.insert(2, ("view_data", "View Data"))
        if can_assign_approve:
            insert_idx = 3 if can_assign_view else 2
            perms.insert(insert_idx, ("approve_data", "Approve Data"))
        
        for pid, pname in perms:
            var = tk.BooleanVar(value=pid not in ["user_manager", "backup_restore"])
            self.perm_vars[pid] = var
            cb = tk.Checkbutton(left, text=pname, variable=var, bg=self.COLORS["bg_surface"], 
                               fg=self.COLORS["text_main"], selectcolor=self.COLORS["bg_dark"],
                               activebackground=self.COLORS["bg_surface"], activeforeground="white",
                               font=self.FONTS["body"], relief="flat", borderwidth=0)
            cb.pack(anchor="w")

        tk.Button(left, text="+ CREATE USER", bg=self.COLORS["accent"], fg="black", 
                  font=self.FONTS["button"], relief="flat", cursor="hand2", 
                  padx=20, pady=10, command=self.add_user_submit).pack(fill="x", pady=20)

        # Right side: User Table
        right = tk.Frame(layout, bg=self.COLORS["bg_dark"])
        right.pack(side="left", fill="both", expand=True, padx=(30, 0))

        tk.Label(right, text="ACTIVE SYSTEM USERS", font=self.FONTS["h3"], fg=self.COLORS["text_main"], bg=self.COLORS["bg_dark"]).pack(anchor="w", pady=(0, 15))

        table_frame = tk.Frame(right, bg=self.COLORS["bg_surface"])
        table_frame.pack(fill="both", expand=True)

        self.user_tree = ttk.Treeview(table_frame, columns=("user", "type", "perms"), show="headings", style="Treeview")
        self.user_tree.heading("user", text="USERNAME")
        self.user_tree.heading("type", text="ACCOUNT TYPE")
        self.user_tree.heading("perms", text="PERMISSIONS")
        
        self.user_tree.column("user", width=130, minwidth=110, anchor="w", stretch=False)
        self.user_tree.column("type", width=140, minwidth=120, anchor="center", stretch=False)
        self.user_tree.column("perms", width=300, minwidth=220, anchor="w", stretch=True)
        
        v_sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.user_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.user_tree.xview, style="Horizontal.TScrollbar")
        self.user_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.user_tree.pack(side="left", fill="both", expand=True)

        self.user_tree.bind("<<TreeviewSelect>>", self._on_user_select)

        # Remove button
        tk.Button(right, text="REMOVE SELECTED USER", bg=self.COLORS["danger"], fg="white", 
                  font=self.FONTS["button"], relief="flat", padx=20, pady=10, command=self.remove_user_submit).pack(anchor="e", pady=10)

        self.refresh_user_list()

    def refresh_user_list(self):
        for i in self.user_tree.get_children():
            self.user_tree.delete(i)
        
        PERM_LABELS = {
            "live_data": "Dashboard",
            "live_log": "Live Log",
            "view_data": "View Data",
            "approve_data": "Approve Data",
            "email_config": "Email Alerts",
            "email_smtp_config": "Gmail SMTP",
            "sms_config": "SMS Alerts",
            "export_data": "Data Export",
            "data_trends": "Historic Trends",
            "user_manager": "User Mgr",
            "audit_log": "Audit Log",
            "backup_restore": "Backup/Restore"
        }

        for uname, udata in self.users.items():
            raw_perms = udata.get("permissions", [])
            clean_perms = [p for p in raw_perms if p != "add_device"]
            if udata.get("is_superadmin") or uname == "superadmin":
                utype = "SUPER ADMIN"
                perms = "ALL MODULES (FULL ACCESS)"
            elif udata.get("is_admin") or uname == "admin":
                utype = "ADMIN / APPROVER" if udata.get("is_approver") else "ADMIN"
                perms = "ALL MODULES (EXCEPT DEVICE MGR)"
            elif udata.get("is_approver"):
                utype = "APPROVER"
                perms = "APPROVAL AUTHORITY; " + (f"{len(clean_perms)} MODULES ACCESS" if clean_perms else "NO MODULE ACCESS")
            else:
                utype = "OPERATOR"
                if len(clean_perms) >= 5:
                    perms = f"{len(clean_perms)} MODULES ACCESS"
                elif clean_perms:
                    perms = ", ".join([PERM_LABELS.get(p, p) for p in clean_perms])
                else:
                    perms = "NO ACCESS"
            self.user_tree.insert("", "end", values=(uname.upper(), utype, perms))

    def _on_user_select(self, event):
        selected = self.user_tree.selection()
        if not selected: return
        
        uname = self.user_tree.item(selected[0])['values'][0].lower()
        if uname in self.users:
            udata = self.users[uname]
            self.u_manager_name.delete(0, tk.END)
            self.u_manager_name.insert(0, uname)
            
            # Clear password for security
            self.u_manager_pass.delete(0, tk.END)
            
            # Sync permissions
            up = udata.get("permissions", [])
            for pid, var in self.perm_vars.items():
                var.set(True if pid in up else False)
            self.u_manager_approver.set(bool(udata.get("is_approver", False)))

    def add_user_submit(self):
        uname = self.u_manager_name.get().lower().strip()
        upass = self.u_manager_pass.get().strip()
        
        if not uname:
            messagebox.showerror("Error", "Username required")
            return
        
        is_update = False
        if uname in self.users:
            if not messagebox.askyesno("Update User", f"User '{uname}' already exists. Update password and permissions?"):
                return
            is_update = True

        if not upass and not is_update:
            messagebox.showerror("Error", "Password required for new user")
            return

        if upass and len(upass) < 12:
            messagebox.showerror("Password", "Use at least 12 characters.")
            return

        h = hash_password(upass) if upass else (self.users[uname]["password"] if is_update else "")
        selected_perms = [pid for pid, var in self.perm_vars.items() if var.get()]
        
        # Maintain superadmin / admin status if updating
        is_superadmin = self.users[uname].get("is_superadmin", False) if is_update else (uname == "superadmin")
        is_admin = self.users[uname].get("is_admin", False) if is_update else (uname in ["admin", "superadmin"])
        manager_is_superadmin = (self.current_user == "superadmin") or self.users.get(self.current_user, {}).get("is_superadmin", False)
        manager_perms = self.users.get(self.current_user, {}).get("permissions", [])
        can_manage_approver = manager_is_superadmin or ("approve_data" in manager_perms)
        old_is_approver = self.users.get(uname, {}).get("is_approver", False)
        is_approver = self.u_manager_approver.get() if can_manage_approver else old_is_approver

        # Preserve existing sensitive permissions if current manager cannot assign them
        if not (manager_is_superadmin or "view_data" in manager_perms) and is_update:
            old_perms = self.users.get(uname, {}).get("permissions", [])
            if "view_data" in old_perms and "view_data" not in selected_perms:
                selected_perms.append("view_data")

        if not (manager_is_superadmin or "approve_data" in manager_perms) and is_update:
            old_perms = self.users.get(uname, {}).get("permissions", [])
            if "approve_data" in old_perms and "approve_data" not in selected_perms:
                selected_perms.append("approve_data")

        # Device Manager (add_device) permission is strictly reserved for superadmin
        if is_superadmin or uname == "superadmin":
            if "add_device" not in selected_perms:
                selected_perms.append("add_device")
        else:
            if "add_device" in selected_perms:
                selected_perms.remove("add_device")
        
        if is_update:
            old_user = self.users.get(uname, {})
            old_perms = old_user.get("permissions", [])
            added = [p for p in selected_perms if p not in old_perms]
            removed = [p for p in old_perms if p not in selected_perms]
            changes = []
            if upass:
                changes.append("Passcode updated")
            if added:
                changes.append(f"Added perms: [{', '.join(added)}]")
            if removed:
                changes.append(f"Removed perms: [{', '.join(removed)}]")
            if old_is_approver != is_approver:
                changes.append(f"Approver role: {'enabled' if is_approver else 'disabled'}")
            diff_str = "; ".join(changes) if changes else "No permission/passcode changes"
            audit_msg = f"Updated user '{uname.upper()}': {diff_str}"
            act = "USER_UPDATE"
        else:
            audit_msg = f"Created user '{uname.upper()}' (Permissions: [{', '.join(selected_perms)}])"
            act = "USER_CREATE"

        self.users[uname] = {
            "password": h,
            "permissions": selected_perms,
            "is_admin": is_admin,
            "is_superadmin": is_superadmin,
            "is_approver": is_approver
        }
        
        save_users(self.users)
        self.log_audit(act, "USER_MGMT", audit_msg)
        self.refresh_user_list()
        self.u_manager_name.delete(0, tk.END)
        self.u_manager_pass.delete(0, tk.END)
        self.u_manager_approver.set(False)
        
        msg = f"User {uname} updated successfully" if is_update else f"User {uname} created successfully"
        messagebox.showinfo("Success", msg)

    def remove_user_submit(self):
        selected = self.user_tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select a user to remove")
            return
        
        uname = self.user_tree.item(selected[0])['values'][0].lower()
        if uname in ["admin", "superadmin"]:
            messagebox.showerror("Error", f"Cannot remove primary {uname}")
            return
        
        if messagebox.askyesno("Confirm", f"Remove user {uname.upper()}?"):
            removed_user = self.users.pop(uname, {})
            old_perms = removed_user.get("permissions", [])
            save_users(self.users)
            self.log_audit("USER_DELETE", "USER_MGMT", f"Deleted user '{uname.upper()}' (Had permissions: [{', '.join(old_perms)}])")
            self.refresh_user_list()

    def render_device_block(self, parent, dev):
        ip, port, slave_id = dev['ip'], dev['port'], dev.get('slave_id', 1)
        key = f"{ip}:{port}:{slave_id}"
        is_running = key in self.device_threads and self.device_threads[key].is_alive()
        
        device_frame = tk.Frame(parent, bg=self.COLORS["bg_dark"], pady=20, padx=30)
        device_frame.pack(fill="x")

        # Header with individual controls
        head = tk.Frame(device_frame, bg=self.COLORS["bg_surface"], padx=20, pady=10,
                        highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        head.pack(fill="x", pady=(0, 10))
        
        status_color = self.COLORS["success"] if is_running else self.COLORS["text_dim"]
        tk.Label(head, text="●", fg=status_color, bg=self.COLORS["bg_surface"]).pack(side="left", padx=(0, 10))
        slave_id = dev.get('slave_id', 1)
        d_name = dev.get('device_name', '')
        header_text = d_name.upper() if d_name else f"NODE: {ip}"
        
        info_frame = tk.Frame(head, bg=self.COLORS["bg_surface"])
        info_frame.pack(side="left")

        tk.Label(info_frame, text=header_text, font=self.FONTS["h3"], 
                 fg=self.COLORS["text_main"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        
        ts_lbl = tk.Label(info_frame, text=f"Last Sync: {self.last_update_times.get(key, 'NEVER')}", 
                         font=("Segoe UI", 7), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"])
        ts_lbl.pack(anchor="w")
        
        # Store for background updates
        self.live_labels[f"{key}:sync"] = ts_lbl
        
        # Control Buttons
        def _refresh_node():
            if key in self.refresh_events: self.refresh_events[key].set()

        tk.Button(head, text="\u21bb REFRESH", bg=self.COLORS["bg_highlight"], fg=self.COLORS["accent"], 
                  font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", padx=10, pady=5,
                  command=_refresh_node).pack(side="right", padx=5)

        tk.Button(head, text="▶ START", bg=self.COLORS["success"], fg="white", 
                  font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=5,
                  command=lambda d=dev: self.start_device(d)).pack(side="right", padx=5)
        tk.Button(head, text="■ STOP", bg=self.COLORS["danger"], fg="white", 
                  font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=5,
                  command=lambda d=dev: self.stop_device(d)).pack(side="right", padx=5)

        cards = tk.Frame(device_frame, bg=self.COLORS["bg_dark"])
        cards.pack(fill="x")

        card_frames = []
        for i, reg in enumerate(dev['registers']):
            r_frame = tk.Frame(cards, bg=self.COLORS["bg_surface"], height=130,
                               highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
            r_frame.pack_propagate(False)
            card_frames.append(r_frame)

            # Priority: 1. User specified name, 2. Default name (first 10), 3. Generic REG #
            name = dev.get("names", {}).get(str(reg))
            unit = dev.get("units", {}).get(str(reg), "")
            
            if not name:
                if i < len(self.DEFAULT_REGISTER_NAMES):
                    name = self.DEFAULT_REGISTER_NAMES[i]
                else:
                    name = f"REG {reg}"
            
            dtype = dev.get("types", {}).get(str(reg), "FLOAT32")
            if dtype == "BOOLEAN":
                unit = "na"
            
            if not unit:
                if i < len(self.DEFAULT_REGISTER_UNITS) and i < len(self.DEFAULT_REGISTER_NAMES):
                    # Only default unit if it's one of the standard first 10
                    unit = self.DEFAULT_REGISTER_UNITS[i]

            tk.Label(r_frame, text=str(name).upper(), font=("Segoe UI", 11, "bold"), 
                     fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(pady=(10, 0))
            
            val_id = f"{ip}:{port}:{slave_id}:{reg}"
            cached_val = self.last_values.get(val_id, "---")
            cached_ts = self.last_update_times.get(val_id, "")
            
            val_container = tk.Frame(r_frame, bg=self.COLORS["bg_surface"])
            val_container.pack(expand=True)
            
            lbl = tk.Label(val_container, text=cached_val, font=("Segoe UI", 22, "bold"), 
                           fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"])
            lbl.pack(side="left")
            
            if unit and unit != "na" and dtype != "BOOLEAN":
                tk.Label(val_container, text=unit, font=("Segoe UI", 12, "bold"), 
                         fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(5, 0), pady=(8, 0))
            
            ts_text = f"Last Update: {cached_ts}" if cached_ts else "Last Update: --"
            time_lbl = tk.Label(r_frame, text=ts_text, font=("Segoe UI", 8), 
                                fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"])
            time_lbl.pack(pady=(0, 6))

            self.live_labels[val_id] = lbl
            self.live_labels[f"{val_id}:time"] = time_lbl
            
            # Graph Selection Support
            def _select_graph(e, lid=val_id, nm=name):
                self.selected_graph_lid = lid
                self.selected_graph_name = nm
                self.draw_live_graph(lid, nm)
                # Visual feedback on card
                for card in cards.winfo_children():
                    card.config(highlightbackground=self.COLORS["bg_highlight"])
                r_frame.config(highlightbackground=self.COLORS["accent"])

            r_frame.bind("<Button-1>", _select_graph)
            for child in r_frame.winfo_children():
                child.bind("<Button-1>", _select_graph)
                if isinstance(child, tk.Frame):
                    for grandchild in child.winfo_children():
                        grandchild.bind("<Button-1>", _select_graph)

        def _regrid_cards(e=None):
            w = cards.winfo_width()
            if w <= 10:
                w = parent.winfo_width()
            if w <= 10:
                cols = 4
            else:
                cols = max(1, min(6, w // 200))
            for idx, rf in enumerate(card_frames):
                rf.grid(row=idx // cols, column=idx % cols, padx=5, pady=5, sticky="nsew")
            for c in range(max(8, cols + 1)):
                if c < cols:
                    cards.columnconfigure(c, weight=1)
                else:
                    cards.columnconfigure(c, weight=0)

        cards.bind("<Configure>", _regrid_cards)
        cards.after(10, _regrid_cards)

    # -------------------------
    # VIEW: ADD DEVICE
    # -------------------------
    def show_add_device(self):
        is_superadmin = (self.current_user == "superadmin") or self.users.get(self.current_user, {}).get("is_superadmin", False)
        if not is_superadmin:
            messagebox.showerror("Access Denied", "Only Super Admin has access to Device Manager.")
            self.navigate("live_data")
            return
        self.edit_index = None
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True)

        outer_left, left = self.create_scrollable_panel(layout, bg=self.COLORS["bg_surface"], pad_x=25, pad_y=15)
        outer_left.pack(side="left", fill="y", anchor="n")

        hdr_frame = tk.Frame(left, bg=self.COLORS["bg_surface"])
        hdr_frame.pack(fill="x", pady=(0, 15))
        tk.Label(hdr_frame, text="NODE CONFIG", font=self.FONTS["h3"], fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(side="left")
        tk.Button(hdr_frame, text="+ NEW NODE", bg=self.COLORS["success"], fg="white", font=("Segoe UI", 8, "bold"), 
                  relief="flat", padx=10, cursor="hand2", command=self.clear_add_device_form).pack(side="right")

        def fld(lbl, parent=left):
            f = tk.Frame(parent, bg=self.COLORS["bg_surface"])
            f.pack(fill="x", pady=8)
            tk.Label(f, text=lbl, font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
            e = tk.Entry(f, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
            e.pack(fill="x", pady=(2, 0), ipady=6)
            tk.Frame(f, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")
            return e

        f_row0 = tk.Frame(left, bg=self.COLORS["bg_surface"])
        f_row0.pack(fill="x", pady=4)
        
        f_name_cnt = tk.Frame(f_row0, bg=self.COLORS["bg_surface"])
        f_name_cnt.pack(side="left", fill="x", expand=True, padx=(0, 5))
        tk.Label(f_name_cnt, text="DEVICE ALIAS / NAME", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_device_name = tk.Entry(f_name_cnt, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.entry_device_name.pack(fill="x", pady=(2, 0), ipady=6)
        tk.Frame(f_name_cnt, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        f_node_type = tk.Frame(f_row0, bg=self.COLORS["bg_surface"])
        f_node_type.pack(side="left", fill="x", padx=(5, 0))
        tk.Label(f_node_type, text="NODE TYPE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_node_type = ttk.Combobox(f_node_type, values=["Standard / PLC Node", "LORA NODE"], style="TCombobox", width=18, font=self.FONTS["body"])
        self.entry_node_type.set("Standard / PLC Node")
        self.entry_node_type.pack(fill="x", pady=(2, 0))
        tk.Frame(f_node_type, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Horizontal row for IP Address and Port
        f_row1 = tk.Frame(left, bg=self.COLORS["bg_surface"])
        f_row1.pack(fill="x", pady=4)
        
        f_ip = tk.Frame(f_row1, bg=self.COLORS["bg_surface"])
        f_ip.pack(side="left", fill="x", expand=True, padx=(0, 5))
        tk.Label(f_ip, text="IP ADDRESS", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_ip = tk.Entry(f_ip, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.entry_ip.pack(fill="x", pady=(2, 0), ipady=6)
        tk.Frame(f_ip, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        f_port = tk.Frame(f_row1, bg=self.COLORS["bg_surface"])
        f_port.pack(side="left", fill="x", expand=True, padx=(5, 0))
        tk.Label(f_port, text="PORT", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_port = tk.Entry(f_port, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.entry_port.pack(fill="x", pady=(2, 0), ipady=6)
        tk.Frame(f_port, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Horizontal row for Slave ID and Time Interval
        f_row2 = tk.Frame(left, bg=self.COLORS["bg_surface"])
        f_row2.pack(fill="x", pady=4)

        f_slave = tk.Frame(f_row2, bg=self.COLORS["bg_surface"])
        f_slave.pack(side="left", fill="x", expand=True, padx=(0, 5))
        tk.Label(f_slave, text="SLAVE ID", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_slave_id = tk.Entry(f_slave, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.entry_slave_id.pack(fill="x", pady=(2, 0), ipady=6)
        tk.Frame(f_slave, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        f_poll = tk.Frame(f_row2, bg=self.COLORS["bg_surface"])
        f_poll.pack(side="left", fill="x", expand=True, padx=(5, 0))
        tk.Label(f_poll, text="TIME INTERVAL TO STORE (SEC)", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_poll_interval = tk.Entry(f_poll, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.entry_poll_interval.pack(fill="x", pady=(2, 0), ipady=6)
        tk.Frame(f_poll, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")
        self.entry_poll_interval.insert(0, "1.0")
        
        # Mapping Section
        tk.Label(left, text="REGISTER MAPPING", font=("Segoe UI", 9, "bold"), fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(anchor="w", pady=(15, 5))
        
        map_row1 = tk.Frame(left, bg=self.COLORS["bg_surface"])
        map_row1.pack(fill="x", pady=2)

        # Address Type (null / MKT / MKRH)
        f_addr_prefix = tk.Frame(map_row1, bg=self.COLORS["bg_surface"])
        f_addr_prefix.pack(side="left", fill="x", padx=(0, 4))
        tk.Label(f_addr_prefix, text="ADDR TYPE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_addr_prefix = ttk.Combobox(f_addr_prefix, values=["null", "MKT", "MKRH"], style="TCombobox", width=6, font=self.FONTS["body"])
        self.entry_reg_addr_prefix.set("null")
        self.entry_reg_addr_prefix.pack(fill="x", pady=(2, 0))
        tk.Frame(f_addr_prefix, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Address
        f_addr = tk.Frame(map_row1, bg=self.COLORS["bg_surface"])
        f_addr.pack(side="left", fill="x", padx=2)
        tk.Label(f_addr, text="ADDR", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_addr = tk.Entry(f_addr, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black", width=6)
        self.entry_reg_addr.pack(fill="x", pady=(2, 0), ipady=4)
        tk.Frame(f_addr, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Name Selection
        f_name = tk.Frame(map_row1, bg=self.COLORS["bg_surface"])
        f_name.pack(side="left", fill="x", expand=True, padx=2)
        tk.Label(f_name, text="REGISTER NAME", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_name = ttk.Combobox(f_name, values=self.DEFAULT_REGISTER_NAMES, style="TCombobox", font=self.FONTS["body"])
        self.entry_reg_name.pack(fill="x", pady=(2, 0))
        tk.Frame(f_name, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Unit Selection
        f_unit = tk.Frame(map_row1, bg=self.COLORS["bg_surface"])
        f_unit.pack(side="left", fill="x", padx=(2, 0))
        tk.Label(f_unit, text="UNIT", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_unit = ttk.Combobox(f_unit, values=self.COMMON_UNITS, style="TCombobox", width=6, font=self.FONTS["body"])
        self.entry_reg_unit.pack(fill="x", pady=(2, 0))
        tk.Frame(f_unit, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Row 2: Mode & Type Selection
        map_row2 = tk.Frame(left, bg=self.COLORS["bg_surface"])
        map_row2.pack(fill="x", pady=4)

        # Mode Selection
        f_mode = tk.Frame(map_row2, bg=self.COLORS["bg_surface"])
        f_mode.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Label(f_mode, text="REGISTER MODE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_mode = ttk.Combobox(f_mode, values=["Output Coil", "Input Coil", "Holding Register", "Input Register"], style="TCombobox", font=self.FONTS["body"])
        self.entry_reg_mode.set("Input Register")
        self.entry_reg_mode.pack(fill="x", pady=(2, 0))
        tk.Frame(f_mode, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Type Selection
        f_type = tk.Frame(map_row2, bg=self.COLORS["bg_surface"])
        f_type.pack(side="left", fill="x", expand=True, padx=(2, 0))
        tk.Label(f_type, text="TYPE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")

        def _fix_type_popdown():
            try:
                popdown = self.entry_reg_type.tk.call('ttk::combobox::PopdownWindow', self.entry_reg_type)
                self.root.tk.call(f"{popdown}.f.l", 'configure', '-width', 25)
            except: pass

        self.entry_reg_type = ttk.Combobox(f_type, values=self.DATA_TYPES, style="TCombobox", width=24, font=self.FONTS["body"], postcommand=_fix_type_popdown)
        self.entry_reg_type.set("FLOAT32")
        self.entry_reg_type.pack(fill="x", pady=(2, 0))
        tk.Frame(f_type, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Row 3: Math & Decimals Section
        map_row3 = tk.Frame(left, bg=self.COLORS["bg_surface"])
        map_row3.pack(fill="x", pady=4)

        # Math Section
        f_math = tk.Frame(map_row3, bg=self.COLORS["bg_surface"])
        f_math.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Label(f_math, text="MATH (OP/FACTOR)", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        
        f_math_inner = tk.Frame(f_math, bg=self.COLORS["bg_surface"])
        f_math_inner.pack(fill="x", pady=(2, 0))
        
        self.entry_reg_op = ttk.Combobox(f_math_inner, values=["/", "*"], style="TCombobox", width=2, font=self.FONTS["body"])
        self.entry_reg_op.set("/")
        self.entry_reg_op.pack(side="left")
        
        self.entry_reg_factor = tk.Entry(f_math_inner, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], width=8)
        self.entry_reg_factor.insert(0, "1")
        self.entry_reg_factor.pack(side="left", padx=(2, 0), ipady=3)
        tk.Frame(f_math, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Decimal Point (Precision)
        f_prec = tk.Frame(map_row3, bg=self.COLORS["bg_surface"])
        f_prec.pack(side="left", fill="x", padx=(2, 0))
        tk.Label(f_prec, text="DECIMALS", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_prec = ttk.Combobox(f_prec, values=["0", "1", "2", "3", "4"], style="TCombobox", width=5, font=self.FONTS["body"])
        self.entry_reg_prec.set("3")
        self.entry_reg_prec.pack(fill="x", pady=(2, 0))
        tk.Frame(f_prec, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")
        self.map_frame = map_row3

        # Boolean Value Names (Only visible when BOOLEAN type is selected)
        self.f_bool_labels = tk.Frame(left, bg=self.COLORS["bg_surface"])
        
        f_bool0 = tk.Frame(self.f_bool_labels, bg=self.COLORS["bg_surface"])
        f_bool0.pack(side="left", fill="x", expand=True, padx=(0, 5))
        tk.Label(f_bool0, text="0 VALUE NAME", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_bool0 = tk.Entry(f_bool0, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.entry_bool0.pack(fill="x", pady=(2, 0), ipady=3)
        self.entry_bool0.insert(0, "OFF")
        tk.Frame(f_bool0, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")
        
        f_bool1 = tk.Frame(self.f_bool_labels, bg=self.COLORS["bg_surface"])
        f_bool1.pack(side="left", fill="x", expand=True, padx=(5, 0))
        tk.Label(f_bool1, text="1 VALUE NAME", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_bool1 = tk.Entry(f_bool1, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.entry_bool1.pack(fill="x", pady=(2, 0), ipady=3)
        self.entry_bool1.insert(0, "ON")
        tk.Frame(f_bool1, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        def _on_type_change(event=None):
            if self.entry_reg_type.get() == "BOOLEAN":
                self.f_bool_labels.pack(fill="x", pady=5, after=self.map_frame)
                self.entry_reg_mode.set("Output Coil")
                self.entry_reg_unit.set("na")
            else:
                self.f_bool_labels.pack_forget()
                if self.entry_reg_mode.get() in ["Output Coil", "Input Coil"]:
                    self.entry_reg_mode.set("Input Register")
        self.entry_reg_type.bind("<<ComboboxSelected>>", _on_type_change)
        
        # Auto-fill unit when name is selected
        def _on_name_change(event):
            name = self.entry_reg_name.get()
            if name in self.DEFAULT_REGISTER_NAMES:
                idx = self.DEFAULT_REGISTER_NAMES.index(name)
                self.entry_reg_unit.set(self.DEFAULT_REGISTER_UNITS[idx])
                self.entry_reg_type.set(self.DEFAULT_REGISTER_TYPES[idx])
                _on_type_change(None)
        
        self.entry_reg_name.bind("<<ComboboxSelected>>", _on_name_change)
        
        self.btn_add_reg_main = tk.Button(left, text="+ ADD REGISTER", bg=self.COLORS["bg_highlight"], fg=self.COLORS["accent"], 
                                font=("Segoe UI", 8, "bold"), relief="flat", pady=5, command=self.add_reg_to_mini)
        self.btn_add_reg_main.pack(fill="x", pady=5)

        # Mini List of Registers
        mini_cnt = tk.Frame(left, bg=self.COLORS["bg_dark"], height=150)
        mini_cnt.pack(fill="x", pady=10)
        mini_cnt.pack_propagate(False)
        
        self.mini_tree = ttk.Treeview(mini_cnt, columns=("addr", "name", "unit", "type", "mode", "math", "prec"), show="headings", style="Treeview")
        self.mini_tree.heading("addr", text="ADDRESS")
        self.mini_tree.heading("name", text="REGISTER NAME")
        self.mini_tree.heading("unit", text="UNIT")
        self.mini_tree.heading("type", text="DATA TYPE")
        self.mini_tree.heading("mode", text="REGISTER MODE")
        self.mini_tree.heading("math", text="MATH (OP/FACTOR)")
        self.mini_tree.heading("prec", text="DECIMALS")
        
        self.mini_tree.column("addr", width=100, minwidth=100, anchor="center", stretch=False)
        self.mini_tree.column("name", width=170, minwidth=170, anchor="w", stretch=False)
        self.mini_tree.column("unit", width=75, minwidth=75, anchor="center", stretch=False)
        self.mini_tree.column("type", width=110, minwidth=110, anchor="center", stretch=False)
        self.mini_tree.column("mode", width=160, minwidth=160, anchor="w", stretch=False)
        self.mini_tree.column("math", width=190, minwidth=190, anchor="center", stretch=False)
        self.mini_tree.column("prec", width=130, minwidth=130, anchor="center", stretch=False)
        v_sb = ttk.Scrollbar(mini_cnt, orient="vertical", command=self.mini_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(mini_cnt, orient="horizontal", command=self.mini_tree.xview, style="Horizontal.TScrollbar")
        self.mini_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.mini_tree.pack(side="left", fill="both", expand=True)
        
        self.mini_tree.bind("<<TreeviewSelect>>", self.on_mini_select)
        
        tk.Button(left, text="REMOVE SELECTED REG", bg=self.COLORS["danger"], fg="white", 
                  font=self.FONTS["button"], relief="flat", cursor="hand2", 
                  command=self.remove_mini_reg).pack(anchor="e", pady=10)

        self.btn_add = tk.Button(left, text="SAVE NODE", bg=self.COLORS["accent"], fg="black", font=self.FONTS["button"], 
                                relief="flat", padx=20, pady=10, command=self.add_device_submit)
        self.btn_add.pack(fill="x", pady=20)


        right = tk.Frame(layout, bg=self.COLORS["bg_dark"])
        right.pack(side="left", fill="both", expand=True, padx=(30, 0))

        # Table Header
        header_frame = tk.Frame(right, bg=self.COLORS["bg_dark"])
        header_frame.pack(fill="x", pady=(15, 10), padx=30)
        
        tk.Label(header_frame, text="STORED DEVICES", font=self.FONTS["h3"], 
                 fg=self.COLORS["text_main"], bg=self.COLORS["bg_dark"]).pack(side="left")

        # Import/Export buttons
        btn_cnt = tk.Frame(header_frame, bg=self.COLORS["bg_dark"])
        btn_cnt.pack(side="right")
        
        tk.Button(btn_cnt, text="EXPORT CONFIG", bg=self.COLORS["accent"], fg="black", 
                  font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", padx=10,
                  command=self.export_device_config).pack(side="left", padx=5)
                  
        tk.Button(btn_cnt, text="IMPORT CONFIG", bg=self.COLORS["accent"], fg="black", 
                  font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", padx=10,
                  command=self.import_device_config).pack(side="left", padx=5)

        # Main Table Container
        tree_cnt = tk.Frame(right, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        tree_cnt.pack(fill="both", expand=True, padx=30)

        # Updated Columns: ID, DEVICE, SLAVE, REG NAME, REG ADDR, UNIT, TYPE, MATH, MODE, STATUS
        cols = ("id", "addr", "slave", "reg_name", "reg_addr", "reg_unit", "reg_type", "math", "mode", "prec", "status")
        self.node_tree = ttk.Treeview(tree_cnt, columns=cols, show="headings", style="Treeview")
        
        self.node_tree.heading("id", text="ID")
        self.node_tree.heading("addr", text="DEVICE NAME")
        self.node_tree.heading("slave", text="SLAVE ID")
        self.node_tree.heading("reg_name", text="REGISTER NAME")
        self.node_tree.heading("reg_addr", text="ADDRESS")
        self.node_tree.heading("reg_unit", text="UNIT")
        self.node_tree.heading("reg_type", text="DATA TYPE")
        self.node_tree.heading("math", text="MATH (OP/FACTOR)")
        self.node_tree.heading("mode", text="REGISTER MODE")
        self.node_tree.heading("prec", text="DECIMALS")
        self.node_tree.heading("status", text="STATUS")
        
        self.node_tree.column("id", width=45, minwidth=40, anchor="center", stretch=False)
        self.node_tree.column("addr", width=130, minwidth=110, anchor="w", stretch=False)
        self.node_tree.column("slave", width=70, minwidth=60, anchor="center", stretch=False)
        self.node_tree.column("reg_name", width=140, minwidth=120, anchor="w", stretch=False)
        self.node_tree.column("reg_addr", width=95, minwidth=80, anchor="center", stretch=False)
        self.node_tree.column("reg_unit", width=60, minwidth=50, anchor="center", stretch=False)
        self.node_tree.column("reg_type", width=95, minwidth=80, anchor="center", stretch=False)
        self.node_tree.column("math", width=130, minwidth=100, anchor="center", stretch=False)
        self.node_tree.column("mode", width=130, minwidth=110, anchor="w", stretch=False)
        self.node_tree.column("prec", width=80, minwidth=65, anchor="center", stretch=False)
        self.node_tree.column("status", width=100, minwidth=85, anchor="center", stretch=False)
        
        v_sb = ttk.Scrollbar(tree_cnt, orient="vertical", command=self.node_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(tree_cnt, orient="horizontal", command=self.node_tree.xview, style="Horizontal.TScrollbar")
        self.node_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.node_tree.pack(side="left", fill="both", expand=True)
        self.node_tree.bind("<Double-1>", self.view_all_device_details)

        acts = tk.Frame(right, bg=self.COLORS["bg_dark"], pady=20)
        acts.pack(side="bottom", fill="x", padx=30)
        
        tk.Button(acts, text="🗑 DELETE NODE", bg=self.COLORS["danger"], fg="white", 
                  font=self.FONTS["button"], relief="flat", padx=15, pady=8, 
                  cursor="hand2", command=self.remove_device_submit).pack(side="right", padx=5)
        
        tk.Button(acts, text="📝 EDIT NODE", bg=self.COLORS["bg_highlight"], fg=self.COLORS["accent"], 
                  font=self.FONTS["button"], relief="flat", padx=15, pady=8, 
                  cursor="hand2", command=self.load_edit_device).pack(side="right", padx=5)

        tk.Button(acts, text="👁 VIEW ALL DETAILS", bg=self.COLORS["accent"], fg="black", 
                  font=self.FONTS["button"], relief="flat", padx=15, pady=8, 
                  cursor="hand2", command=self.view_all_device_details).pack(side="right", padx=5)

        self.refresh_device_list()

    # -------------------------
    # VIEW: EMAIL CONFIG
    # -------------------------
    def show_email_config(self):
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True)

        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        
        # Only admin/superadmin or users with specific permission can see SMTP Configuration
        if self.current_user in ["admin", "superadmin"] or "email_smtp_config" in user_perms:
            # Left Panel: Server Settings
            outer_left, left = self.create_scrollable_panel(layout, bg=self.COLORS["bg_surface"], pad_x=25, pad_y=15)
            outer_left.pack(side="left", fill="y", anchor="n")

            tk.Label(left, text="SERVER CONFIGURATION", font=self.FONTS["h3"], fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(anchor="w", pady=(0, 15))

            def fld(lbl, parent=left, initial="", show=None):
                f = tk.Frame(parent, bg=self.COLORS["bg_surface"])
                f.pack(fill="x", pady=6)
                tk.Label(f, text=lbl, font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
                e = tk.Entry(f, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black", show=show)
                e.insert(0, str(initial))
                e.pack(fill="x", pady=(2, 0), ipady=6)
                tk.Frame(f, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")
                return e, f

            s = self.email_settings
            
            # Server Type Selection
            f_type = tk.Frame(left, bg=self.COLORS["bg_surface"])
            f_type.pack(fill="x", pady=6)
            tk.Label(f_type, text="SERVER TYPE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
            self.server_type_cb = ttk.Combobox(f_type, values=["Gmail", "SMTP", "IMAP", "POP3"], style="TCombobox", state="readonly")
            self.server_type_cb.set(s.get('server_type', 'Gmail'))
            self.server_type_cb.pack(fill="x", pady=(2, 0))
            
            # Host and Port
            self.email_host, self.f_host = fld("SERVER HOST / IP", initial=s.get('smtp_server', ''))
            self.email_port, self.f_port = fld("PORT", initial=s.get('smtp_port', ''))

            self.sender_email, _ = fld("EMAIL / USERNAME", initial=s.get('sender_email', ''))
            self.sender_pass, _ = fld("PASSWORD / APP KEY", initial=s.get('sender_password', ''), show="*")
            self.recipient_email, self.f_recip = fld(
                f"RECIPIENT EMAILS (Comma/semicolon separated, max {MAX_EMAIL_RECIPIENTS})",
                initial=s.get('recipient_email', '')
            )
            self.email_subject, self.f_sub = fld("EMAIL SUBJECT", initial=s.get('subject', ''))

            # Scheduled PDF Report Interval Selection
            f_pdf_sched = tk.Frame(left, bg=self.COLORS["bg_surface"])
            f_pdf_sched.pack(fill="x", pady=6)
            tk.Label(f_pdf_sched, text="SCHEDULED PDF REPORT INTERVAL", font=("Segoe UI", 8, "bold"), fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
            self.scheduled_pdf_cb = ttk.Combobox(f_pdf_sched, values=["Disabled", "5 Mins", "30 Mins", "1 Hour", "6 Hours", "12 Hours", "24 Hours"], style="TCombobox", state="readonly")
            self.scheduled_pdf_cb.set(s.get('scheduled_pdf_interval', 'Disabled'))
            self.scheduled_pdf_cb.pack(fill="x", pady=(2, 0))

            def update_server_fields(event=None):
                stype = self.server_type_cb.get()
                self.email_host.delete(0, tk.END)
                self.email_port.delete(0, tk.END)
                
                if stype == "Gmail":
                    self.email_host.insert(0, "smtp.gmail.com")
                    self.email_port.insert(0, "587")
                    self.f_host.pack_forget()
                    self.f_port.pack_forget()
                    self.f_recip.pack(fill="x", pady=6)
                    self.f_sub.pack(fill="x", pady=6)
                elif stype == "SMTP":
                    self.email_host.insert(0, s.get('smtp_server', ''))
                    self.email_port.insert(0, s.get('smtp_port', ''))
                    self.f_host.pack(fill="x", pady=6, after=f_type)
                    self.f_port.pack(fill="x", pady=6, after=self.f_host)
                    self.f_recip.pack(fill="x", pady=6)
                    self.f_sub.pack(fill="x", pady=6)
                elif stype == "IMAP":
                    self.email_host.insert(0, s.get('imap_server', 'imap.gmail.com'))
                    self.email_port.insert(0, s.get('imap_port', '993'))
                    self.f_host.pack(fill="x", pady=6, after=f_type)
                    self.f_port.pack(fill="x", pady=6, after=self.f_host)
                    self.f_recip.pack_forget()
                    self.f_sub.pack_forget()
                elif stype == "POP3":
                    self.email_host.insert(0, s.get('pop3_server', 'pop.gmail.com'))
                    self.email_port.insert(0, s.get('pop3_port', '995'))
                    self.f_host.pack(fill="x", pady=6, after=f_type)
                    self.f_port.pack(fill="x", pady=6, after=self.f_host)
                    self.f_recip.pack_forget()
                    self.f_sub.pack_forget()

            self.server_type_cb.bind("<<ComboboxSelected>>", update_server_fields)
            update_server_fields() # Initial call

            tk.Button(left, text="SAVE SETTINGS", bg=self.COLORS["accent"], fg="black", font=self.FONTS["button"], 
                      relief="flat", padx=20, pady=10, command=self.save_email_settings_ui).pack(fill="x", pady=(20, 5))
            
            tk.Button(left, text="TEST MAIL / CONNECTION", bg=self.COLORS["success"], fg="white", font=self.FONTS["button"], 
                      relief="flat", padx=20, pady=8, command=self.test_email_config).pack(fill="x", pady=4)

            tk.Button(left, text="📄 SEND SCHEDULED PDF REPORT NOW", bg="#0288D1", fg="white", font=self.FONTS["button"], 
                      relief="flat", padx=20, pady=8, command=self.send_scheduled_pdf_now).pack(fill="x", pady=4)
        
            # Email Enable Toggle
            self.email_enabled_var = tk.BooleanVar(value=s.get('email_enabled', True))
            tk.Checkbutton(left, text="ENABLE EMAIL NOTIFICATIONS", variable=self.email_enabled_var,
                           font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"],
                           activebackground=self.COLORS["bg_surface"], selectcolor=self.COLORS["bg_dark"]).pack(anchor="w", pady=10)
            
            info_text = "Note: Gmail requires an 'App Password'.\nSMTP uses TLS/587 or SSL/465.\nIMAP/POP3 test checks connection."
            info_lbl = tk.Label(left, text=info_text, 
                               font=("Segoe UI", 8, "italic"), fg=self.COLORS["warning"], bg=self.COLORS["bg_surface"], justify="left")
            info_lbl.pack(fill="x", pady=10)
        else:
            # If no permission, show a message in place of SMTP config
            left_info = tk.Frame(layout, bg=self.COLORS["bg_dark"], padx=30, pady=20)
            left_info.pack(side="left", fill="y", anchor="n")
            tk.Label(left_info, text="Mail settings restricted.\nContact Admin for access.", 
                     font=("Segoe UI", 9, "italic"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"], justify="left").pack()

        # Right Panel: Alert Rules
        right = tk.Frame(layout, bg=self.COLORS["bg_dark"])
        right.pack(side="left", fill="both", expand=True, padx=(30, 0))

        tk.Label(right, text="ALERT RULES", font=self.FONTS["h3"], fg=self.COLORS["text_main"], bg=self.COLORS["bg_dark"]).pack(anchor="w", pady=(15, 5), padx=30)

        # Move REMOVE button to a more visible top position
        tree_bar = tk.Frame(right, bg=self.COLORS["bg_dark"])
        tree_bar.pack(fill="x", padx=30, pady=(0, 10))
        
        tk.Button(tree_bar, text="🗑 REMOVE SELECTED RULE", bg=self.COLORS["danger"], fg="white", 
                  font=("Segoe UI", 8, "bold"), relief="flat", padx=15, pady=6, 
                  command=self.remove_alert_rule).pack(side="left")

        # Import/Export buttons
        btn_cnt = tk.Frame(tree_bar, bg=self.COLORS["bg_dark"])
        btn_cnt.pack(side="right")
        
        tk.Button(btn_cnt, text="EXPORT ALERTS", bg=self.COLORS["accent"], fg="black", 
                  font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", padx=15, pady=6, 
                  command=self.export_alert_config).pack(side="left", padx=5)
                  
        tk.Button(btn_cnt, text="IMPORT ALERTS", bg=self.COLORS["accent"], fg="black", 
                  font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", padx=15, pady=6, 
                  command=self.import_alert_config).pack(side="left", padx=5)

        add_rule_frame = tk.Frame(right, bg=self.COLORS["bg_surface"], padx=20, pady=20,
                                 highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        add_rule_frame.pack(fill="x", padx=30, pady=(0, 20))

        # Fields for new rule
        row1 = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"])
        row1.pack(fill="x")

        f_ip = tk.Frame(row1, bg=self.COLORS["bg_surface"])
        f_ip.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_ip, text="SELECT DEVICE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        display_names = [d.get('device_name') if d.get('device_name') else f"{d['ip']} @ {d.get('slave_id', 1)}" for d in self.devices]
        self.rule_ip = ttk.Combobox(f_ip, values=display_names, style="TCombobox")
        self.rule_ip.pack(fill="x", pady=(2, 0))

        f_reg = tk.Frame(row1, bg=self.COLORS["bg_surface"])
        f_reg.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_reg, text="REGISTER", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_reg = ttk.Combobox(f_reg, style="TCombobox")
        self.rule_reg.pack(fill="x", pady=(2, 0))

        self.rule_ip.bind("<<ComboboxSelected>>", self.update_regs)

        row2 = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"], pady=10)
        row2.pack(fill="x")

        f_cond = tk.Frame(row2, bg=self.COLORS["bg_surface"])
        f_cond.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_cond, text="CONDITION", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_cond = ttk.Combobox(f_cond, values=["Greater Than (>)", "Less Than (<)", "Upper Limit (>)", "Low Limit (<)", "Equal To (==)"], style="TCombobox")
        self.rule_cond.pack(fill="x", pady=(2, 0))

        f_thresh = tk.Frame(row2, bg=self.COLORS["bg_surface"])
        f_thresh.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_thresh, text="PROCESS VALUE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_thresh = tk.Entry(f_thresh, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.rule_thresh.pack(fill="x", pady=(2, 0), ipady=6)

        f_interval = tk.Frame(row2, bg=self.COLORS["bg_surface"])
        f_interval.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_interval, text="INTERVAL (MINS)", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_interval = tk.Entry(f_interval, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.rule_interval.insert(0, "0")
        self.rule_interval.pack(fill="x", pady=(2, 0), ipady=6)

        row3 = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"])
        row3.pack(fill="x", pady=10)
        
        tk.Label(row3, text="CUSTOM MESSAGE TEMPLATE (Use {value}, {time}, {name}, {ip})", font=("Segoe UI", 8, "bold"), 
                 fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_msg = tk.Entry(row3, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.rule_msg.insert(0, "Alert: {name} reached {value} at {time}")
        self.rule_msg.pack(fill="x", pady=(5, 0), ipady=8)

        btn_row = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"])
        btn_row.pack(fill="x", pady=10)
        
        self.btn_add_alert = tk.Button(btn_row, text="+ ADD ALERT RULE", bg=self.COLORS["bg_highlight"], fg=self.COLORS["accent"], 
                  font=self.FONTS["button"], relief="flat", pady=8, command=self.add_alert_rule)
        self.btn_add_alert.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        tk.Button(btn_row, text="CLEAR / CANCEL", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], 
                  font=self.FONTS["button"], relief="flat", pady=8, 
                  command=lambda: (self.rules_tree.selection_remove(self.rules_tree.selection()), self.on_rule_select(None))).pack(side="left", fill="x", expand=True, padx=(5, 0))

        tree_cnt = tk.Frame(right, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        tree_cnt.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        self.rules_tree = ttk.Treeview(tree_cnt, columns=("ip", "reg", "cond", "thresh", "interval", "msg"), show="headings", style="Treeview")
        self.rules_tree.heading("ip", text="DEVICE NAME")
        self.rules_tree.heading("reg", text="REGISTER NAME")
        self.rules_tree.heading("cond", text="CONDITION")
        self.rules_tree.heading("thresh", text="PROCESS VALUE")
        self.rules_tree.heading("interval", text="INTERVAL (SEC)")
        self.rules_tree.heading("msg", text="ALERT TEMPLATE MESSAGE")
        
        self.rules_tree.column("ip", width=160, minwidth=130, stretch=True)
        self.rules_tree.column("reg", width=180, minwidth=150, stretch=True)
        self.rules_tree.column("cond", width=110, minwidth=100, stretch=False)
        self.rules_tree.column("thresh", width=150, minwidth=130, stretch=False)
        self.rules_tree.column("interval", width=150, minwidth=130, stretch=False)
        self.rules_tree.column("msg", width=300, minwidth=200, stretch=True)
        
        v_sb = ttk.Scrollbar(tree_cnt, orient="vertical", command=self.rules_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(tree_cnt, orient="horizontal", command=self.rules_tree.xview, style="Horizontal.TScrollbar")
        self.rules_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.rules_tree.pack(side="left", fill="both", expand=True)

        self.rules_tree.bind("<<TreeviewSelect>>", self.on_rule_select)

        self.refresh_rules_list()

    def save_email_settings_ui(self):
        old_settings = dict(self.email_settings)
        stype = self.server_type_cb.get()
        if stype in ["Gmail", "SMTP"]:
            try:
                recipients = parse_email_recipients(self.recipient_email.get())
                if self.email_enabled_var.get() and not recipients:
                    raise ValueError("At least one recipient email address is required.")
            except ValueError as exc:
                messagebox.showerror("Invalid Recipients", str(exc))
                return
        self.email_settings['server_type'] = stype
        self.email_settings['sender_email'] = self.sender_email.get().strip()
        self.email_settings['sender_password'] = self.sender_pass.get().strip()
        
        if stype in ["Gmail", "SMTP"]:
            self.email_settings['recipient_email'] = self.recipient_email.get().strip()
            self.email_settings['subject'] = self.email_subject.get().strip()
            self.email_settings['smtp_server'] = self.email_host.get().strip()
            self.email_settings['smtp_port'] = self.email_port.get().strip()
        elif stype == "IMAP":
            self.email_settings['imap_server'] = self.email_host.get().strip()
            self.email_settings['imap_port'] = self.email_port.get().strip()
        elif stype == "POP3":
            self.email_settings['pop3_server'] = self.email_host.get().strip()
            self.email_settings['pop3_port'] = self.email_port.get().strip()
            
        self.email_settings['email_enabled'] = self.email_enabled_var.get()
        if hasattr(self, 'scheduled_pdf_cb'):
            self.email_settings['scheduled_pdf_interval'] = self.scheduled_pdf_cb.get()

        save_email_settings(self.email_settings)

        changes = []
        if old_settings.get('server_type') != stype:
            changes.append(f"Type: {old_settings.get('server_type')} -> {stype}")
        if old_settings.get('sender_email') != self.email_settings['sender_email']:
            changes.append(f"Sender: '{old_settings.get('sender_email')}' -> '{self.email_settings['sender_email']}'")
        if stype in ["Gmail", "SMTP"]:
            if old_settings.get('recipient_email') != self.email_settings.get('recipient_email'):
                changes.append(f"Recipients: '{old_settings.get('recipient_email')}' -> '{self.email_settings.get('recipient_email')}'")
            if old_settings.get('smtp_server') != self.email_settings.get('smtp_server') or old_settings.get('smtp_port') != self.email_settings.get('smtp_port'):
                changes.append(f"Server: {old_settings.get('smtp_server')}:{old_settings.get('smtp_port')} -> {self.email_settings.get('smtp_server')}:{self.email_settings.get('smtp_port')}")
        if old_settings.get('email_enabled') != self.email_settings['email_enabled']:
            changes.append(f"Notifications: {'Enabled' if self.email_settings['email_enabled'] else 'Disabled'}")
        if old_settings.get('scheduled_pdf_interval') != self.email_settings.get('scheduled_pdf_interval'):
            changes.append(f"Scheduled PDF Report: '{old_settings.get('scheduled_pdf_interval', 'Disabled')}' -> '{self.email_settings.get('scheduled_pdf_interval')}'")

        diff_str = "; ".join(changes) if changes else "Saved email settings"
        self.log_audit("EMAIL_CONFIG_SAVE", "EMAIL", f"Updated Email Settings ({stype}): {diff_str}",
                       previous_value=json.dumps({k: v for k, v in old_settings.items() if k != "sender_password"}, sort_keys=True),
                       updated_value=json.dumps({k: v for k, v in self.email_settings.items() if k != "sender_password"}, sort_keys=True))
        messagebox.showinfo("Success", f"{stype} settings saved successfully.")

    def test_email_config(self):
        stype = self.server_type_cb.get()
        host = self.email_host.get().strip()
        port_raw = self.email_port.get().strip()
        user = self.sender_email.get().strip()
        pwd = self.sender_pass.get().strip()
        
        if not host or not port_raw or not user or not pwd:
            messagebox.showwarning("Incomplete", "Please fill host, port, user and password.")
            return
            
        try:
            port = int(port_raw)
        except ValueError:
            messagebox.showerror("Error", "Port must be a number.")
            return

        def run_test():
            def try_smtp_connect(target_host, target_port):
                try:
                    return connect_smtp(target_host, target_port, user, pwd), target_port, None
                except Exception as exc:
                    return None, target_port, exc

            try:
                if stype in ["Gmail", "SMTP"]:
                    # Support multiple recipients (comma or semicolon)
                    raw_to = self.recipient_email.get().strip()
                    if not raw_to:
                        self.root.after(0, lambda: messagebox.showwarning("Incomplete", "Please specify at least one recipient email address."))
                        return
                    # Replace semicolon with comma then split
                    recipients = parse_email_recipients(raw_to)
                    if not recipients:
                        self.root.after(0, lambda: messagebox.showwarning("Incomplete", "Please specify a valid recipient email address."))
                        return
                    
                    msg = MIMEMultipart()
                    msg['From'] = user
                    msg['To'] = raw_to
                    msg['Subject'] = "TempTrek Environmental Monitoring System Test Mail"
                    msg.attach(MIMEText("This is a test email from the TempTrek Environmental Monitoring System.", 'plain'))
                    
                    # Build list of host/port combinations to try automatically
                    domain = user.split('@')[-1] if '@' in user else ''
                    
                    candidate_targets = [(host, port)]
                    server = None
                    used_host = host
                    used_port = port
                    last_err = None

                    for h_cand, p_cand in candidate_targets:
                        srv, p_res, err = try_smtp_connect(h_cand, p_cand)
                        if srv is not None:
                            server = srv
                            used_host = h_cand
                            used_port = p_cand
                            last_err = None
                            break
                        else:
                            last_err = err

                    if server is None:
                        if isinstance(last_err, ssl.SSLCertVerificationError):
                            err_msg = (
                                f"Cannot verify the mail server certificate ({host}:{port}).\n\n"
                                "Check the server hostname and the computer's date and time. "
                                "If this persists, ask your IT administrator to check the server's "
                                "certificate chain and any antivirus or network TLS inspection. "
                                "A private CA must be trusted in the Windows certificate store.\n\n"
                                f"Details: {last_err}"
                            )
                            self.root.after(0, lambda msg=err_msg: messagebox.showerror("Failure", msg))
                        elif isinstance(last_err, smtplib.SMTPServerDisconnected):
                            err_msg = (f"Connection unexpectedly closed by mail server ({host}:{port}).\n\n"
                                       "Most Likely Causes & Solutions:\n\n"
                                       "1. INCORRECT USERNAME OR PASSWORD:\n"
                                       "   GoDaddy and security-hardened mail servers immediately drop the connection when given invalid credentials.\n\n"
                                       "2. SERVER HOST / DOMAIN CONFIGURATION:\n"
                                       "   - GoDaddy cPanel Webmail: Host is usually 'mail." + (domain if domain else "yourdomain.com") + "' (Port 465 or 587).\n"
                                       "   - GoDaddy Microsoft 365: Host is 'smtp.office365.com' (Port 587 TLS).\n"
                                       "   - GoDaddy Workspace: Host is 'smtpout.secureserver.net' (Port 587 or 465).\n\n"
                                       "3. GMAIL / GOOGLE WORKSPACE:\n"
                                       "   Must use a 16-character 'App Password' instead of your standard password.\n\n"
                                       "4. PORT & FIREWALL:\n"
                                       "   Check if antivirus or network firewall is blocking outgoing SMTP traffic.")
                            self.root.after(0, lambda: messagebox.showerror("Failure", err_msg))
                        elif isinstance(last_err, smtplib.SMTPAuthenticationError):
                            err_msg = f"Authentication failed for {user}.\nPlease check your username and password (or Gmail App Password)."
                            self.root.after(0, lambda: messagebox.showerror("Failure", err_msg))
                        else:
                            self.root.after(0, lambda: messagebox.showerror("Failure", f"Test failed: {str(last_err)}"))
                        return

                    # Send individual emails to ensure delivery with strict servers
                    for recipient in recipients:
                        # Update the To header for each recipient
                        msg.replace_header('To', recipient)
                        server.sendmail(user, recipient, msg.as_string())
                        
                    server.quit()
                    
                    if used_host != host or used_port != port:
                        def apply_auto_discovered():
                            self.email_host.delete(0, tk.END)
                            self.email_host.insert(0, used_host)
                            self.email_port.delete(0, tk.END)
                            self.email_port.insert(0, str(used_port))
                            messagebox.showinfo("Success", f"Test email sent successfully using Host: {used_host} (Port {used_port})!\n\nHost and Port fields have been auto-updated.")
                        self.root.after(0, apply_auto_discovered)
                    else:
                        self.root.after(0, lambda: messagebox.showinfo("Success", "Test email sent successfully!"))
                elif stype == "IMAP":
                    if port == 993:
                        server = imaplib.IMAP4_SSL(host, port, timeout=15)
                    else:
                        try:
                            server = imaplib.IMAP4_SSL(host, port, timeout=15)
                        except Exception:
                            server = imaplib.IMAP4(host, port, timeout=15)
                            server.starttls()
                    server.login(user, pwd)
                    server.logout()
                    self.root.after(0, lambda: messagebox.showinfo("Success", "IMAP connection successful!"))
                elif stype == "POP3":
                    if port == 995:
                        server = poplib.POP3_SSL(host, port, timeout=15)
                    else:
                        try:
                            server = poplib.POP3_SSL(host, port, timeout=15)
                        except Exception:
                            server = poplib.POP3(host, port, timeout=15)
                    server.user(user)
                    server.pass_(pwd)
                    server.quit()
                    self.root.after(0, lambda: messagebox.showinfo("Success", "POP3 connection successful!"))
            except Exception as e:
                self.root.after(0, lambda msg=str(e): messagebox.showerror("Failure", f"Test failed: {msg}"))

        threading.Thread(target=run_test, daemon=True).start()

    def add_alert_rule(self):
        idx = self.rule_ip.current()
        reg_raw = self.rule_reg.get().strip()
        cond = self.rule_cond.get().strip()
        thresh = self.rule_thresh.get().strip()
        interval = self.rule_interval.get().strip()
        msg_tpl = self.rule_msg.get().strip()

        if idx < 0 or not reg_raw or not cond or not thresh:
            messagebox.showwarning("Incomplete", "Please fill all required fields.")
            return
        
        try:
            if interval:
                float(interval)
            else:
                interval = "0"
        except ValueError:
            messagebox.showwarning("Invalid Input", "Interval must be a numeric value (minutes).")
            return

        dev = self.devices[idx]
        ip = dev['ip']
        port = str(dev.get('port', 502))
        slave_id = str(dev.get('slave_id', 1))
        dev_alias = dev.get('device_name', ip)

        reg = reg_raw.split(" ")[0]
        
        # Extract symbol from "Greater Than (>)" etc.
        cond_symbol = cond
        if "(" in cond and ")" in cond:
            cond_symbol = cond[cond.find("(")+1 : cond.find(")")]
        elif "Greater" in cond: cond_symbol = ">"
        elif "Less" in cond: cond_symbol = "<"
        elif "Equal" in cond: cond_symbol = "=="

        new_rule = {
            "device_ip": ip,
            "device_alias": dev_alias,
            "port": port, 
            "slave_id": slave_id,
            "reg": reg,
            "condition": cond_symbol,
            "threshold": thresh,
            "interval": interval,
            "reg_name": reg_raw,
            "template": msg_tpl if msg_tpl else "{name} alert: {value}"
        }

        if 'alerts' not in self.email_settings:
            self.email_settings['alerts'] = []
            
        sel = self.rules_tree.selection()
        if sel:
            idx = self.rules_tree.index(sel[0])
            old_rule = self.email_settings['alerts'][idx]
            changes = []
            if old_rule.get('device_alias') != dev_alias:
                changes.append(f"Device: '{old_rule.get('device_alias')}' -> '{dev_alias}'")
            if str(old_rule.get('reg')) != str(reg) or old_rule.get('reg_name') != reg_raw:
                changes.append(f"Register: {old_rule.get('reg')} -> {reg}")
            if old_rule.get('condition') != cond_symbol or str(old_rule.get('threshold')) != str(thresh):
                changes.append(f"Condition: ({old_rule.get('condition')} {old_rule.get('threshold')}) -> ({cond_symbol} {thresh})")
            if str(old_rule.get('interval')) != str(interval):
                changes.append(f"Interval: {old_rule.get('interval')}m -> {interval}m")
            if old_rule.get('template') != (msg_tpl if msg_tpl else "{name} alert: {value}"):
                changes.append("Template updated")

            diff_str = "; ".join(changes) if changes else "Saved alert rule"
            self.email_settings['alerts'][idx] = new_rule
            self.log_audit("ALERT_RULE_UPDATE", "ALARM", f"Updated alert rule for '{dev_alias}': {diff_str}")
            messagebox.showinfo("Success", "Alert rule updated.")
            self.rules_tree.selection_remove(sel[0])
        else:
            self.email_settings['alerts'].append(new_rule)
            self.log_audit("ALERT_RULE_ADD", "ALARM", f"Added alert rule for '{dev_alias}' Reg {reg} (IF Value {cond_symbol} {thresh} THEN alert every {interval}m)")
            messagebox.showinfo("Success", "Alert rule added.")
            
        save_email_settings(self.email_settings)
        self.refresh_rules_list()
        self.btn_add_alert.config(text="+ ADD ALERT RULE", bg=self.COLORS["bg_highlight"])

    def update_regs(self, event=None):
        idx = self.rule_ip.current()
        if idx < 0: return
        d = self.devices[idx]
        regs = []
        for r in d['registers']:
            name = d.get('names', {}).get(str(r), f"Reg {r}")
            regs.append(f"{r} ({name}) @ {d.get('slave_id', 1)}")
        self.rule_reg['values'] = list(dict.fromkeys(regs)) # unique

    def on_rule_select(self, event):
        sel = self.rules_tree.selection()
        if not sel:
            self.btn_add_alert.config(text="+ ADD ALERT RULE", bg=self.COLORS["bg_highlight"])
            return
            
        idx = self.rules_tree.index(sel[0])
        alert = self.email_settings['alerts'][idx]
        
        # Match back to device index for the dropdown
        matched_idx = -1
        for i, d in enumerate(self.devices):
            if d['ip'] == alert.get('device_ip') and str(d.get('slave_id', 1)) == str(alert.get('slave_id', 1)):
                matched_idx = i
                break
        
        if matched_idx >= 0:
            self.rule_ip.current(matched_idx)
            self.update_regs() 
            self.rule_reg.set(alert.get('reg_name', ''))
        
        cond_sym = alert.get('condition', '')
        cond_map = {
            ">": "Upper Limit (>)", 
            "<": "Low Limit (<)", 
            "==": "Equal To (==)"
        }
        # Try to match the exact string if possible, otherwise default to the symbol map
        self.rule_cond.set(cond_map.get(cond_sym, cond_sym))
        
        self.rule_thresh.delete(0, tk.END)
        self.rule_thresh.insert(0, alert.get('threshold', ''))
        self.rule_interval.delete(0, tk.END)
        self.rule_interval.insert(0, alert.get('interval', '0'))
        self.rule_msg.delete(0, tk.END)
        self.rule_msg.insert(0, alert.get('template', ''))
        
        self.btn_add_alert.config(text="UPDATE ALERT RULE", bg=self.COLORS["warning"])

    def remove_alert_rule(self):
        sel = self.rules_tree.selection()
        if not sel: return
        idx = self.rules_tree.index(sel[0])
        removed = self.email_settings['alerts'].pop(idx)
        alias = removed.get('device_alias', removed.get('device_ip'))
        self.log_audit("ALERT_RULE_DELETE", "ALARM", f"Deleted alert rule for '{alias}' Reg {removed.get('reg')} (Condition: {removed.get('condition')} {removed.get('threshold')}, Interval: {removed.get('interval', '0')}m)")
        save_email_settings(self.email_settings)
        self.refresh_rules_list()

    def refresh_rules_list(self):
        for item in self.rules_tree.get_children():
            self.rules_tree.delete(item)
        def get_display_cond(sym):
            m = {">": "Upper Limit", "<": "Low Limit", "==": "Equal To"}
            return m.get(sym, sym)

        for alert in self.email_settings.get('alerts', []):
            dev_ip = alert.get('device_ip', '')
            slave_id = str(alert.get('slave_id', 1))
            dev_alias = alert.get('device_alias', '')

            matched_dev = None
            for d in self.devices:
                if str(d.get('ip')) == str(dev_ip) and str(d.get('slave_id', 1)) == slave_id:
                    matched_dev = d
                    break

            if matched_dev and matched_dev.get('device_name'):
                device_display = matched_dev.get('device_name')
            elif dev_alias:
                device_display = dev_alias
            else:
                device_display = dev_ip

            reg_id = str(alert.get('reg', ''))
            reg_display = ""
            if matched_dev:
                reg_display = matched_dev.get('names', {}).get(reg_id, '')

            if not reg_display:
                reg_info = alert.get('reg_name', '')
                if "(" in reg_info and ")" in reg_info:
                    reg_display = reg_info[reg_info.find("(")+1 : reg_info.find(")")]
                elif reg_info:
                    reg_display = reg_info
                else:
                    reg_display = f"Reg {reg_id}" if reg_id else "N/A"

            self.rules_tree.insert("", "end", values=(
                device_display,
                reg_display,
                get_display_cond(alert.get('condition')),
                alert.get('threshold'),
                f"{alert.get('interval', '0')} min",
                alert.get('template', '{name} alert: {value}')
            ))

    def export_device_config(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")],
            title="Export Device Configuration"
        )
        if not filepath:
            return
        try:
            with open(filepath, "w") as f:
                json.dump(self.devices, f, indent=4)
            self.log_audit("DEVICE_EXPORT", "DEVICE", f"Exported device configuration to '{os.path.basename(filepath)}'")
            messagebox.showinfo("Success", "Device configuration exported successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export configuration: {e}")

    def import_device_config(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("JSON Files", "*.json")],
            title="Import Device Configuration"
        )
        if not filepath:
            return
        try:
            with open(filepath, "r") as f:
                imported_devices = json.load(f)
            
            if not isinstance(imported_devices, list):
                raise ValueError("Device configuration must be a JSON list.")
            
            # Simple structure validation
            for dev in imported_devices:
                if not isinstance(dev, dict) or "ip" not in dev or "port" not in dev or "registers" not in dev:
                    raise ValueError("Invalid device configuration structure.")
            
            ans = messagebox.askyesnocancel(
                "Import Options",
                "Would you like to REPLACE the current configuration (Yes) or MERGE with it (No)?"
            )
            if ans is None: # Cancel
                return
            
            # Stop all running device threads before mutating devices
            self.stop_all_devices()
            
            if ans is True: # Replace
                self.devices = imported_devices
            else: # Merge
                for dev in imported_devices:
                    ip, port, slave = dev['ip'], dev['port'], dev.get('slave_id', 1)
                    # Check if exists
                    exists_idx = None
                    for idx, d in enumerate(self.devices):
                        if d['ip'] == ip and d['port'] == port and d.get('slave_id', 1) == slave:
                            exists_idx = idx
                            break
                    if exists_idx is not None:
                        self.devices[exists_idx] = dev
                    else:
                        self.devices.append(dev)
            
            save_devices(self.devices)
            self.refresh_device_list()
            self.log_audit("DEVICE_IMPORT", "DEVICE", f"Imported device config ({'Replace' if ans else 'Merge'}) from '{os.path.basename(filepath)}'")
            messagebox.showinfo("Success", "Device configuration imported successfully. All active polling threads have been stopped. Please restart them as needed.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to import configuration: {e}")

    def export_alert_config(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")],
            title="Export Alarm/Alert Configuration"
        )
        if not filepath:
            return
        try:
            alerts = self.email_settings.get('alerts', [])
            with open(filepath, "w") as f:
                json.dump(alerts, f, indent=4)
            self.log_audit("ALERT_RULE_EXPORT", "ALARM", f"Exported alarm rules to '{os.path.basename(filepath)}'")
            messagebox.showinfo("Success", "Alarm rules exported successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export alarm rules: {e}")

    def import_alert_config(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("JSON Files", "*.json")],
            title="Import Alarm/Alert Configuration"
        )
        if not filepath:
            return
        try:
            with open(filepath, "r") as f:
                imported_alerts = json.load(f)
            
            if not isinstance(imported_alerts, list):
                raise ValueError("Alarm configuration must be a JSON list of rules.")
            
            # Validation
            for rule in imported_alerts:
                if not isinstance(rule, dict) or "device_ip" not in rule or "reg" not in rule or "condition" not in rule or "threshold" not in rule:
                    raise ValueError("Invalid alarm rule structure.")
            
            ans = messagebox.askyesnocancel(
                "Import Options",
                "Would you like to REPLACE the current alarm rules (Yes) or MERGE/Append them (No)?"
            )
            if ans is None:
                return
            
            if ans is True: # Replace
                self.email_settings['alerts'] = imported_alerts
            else: # Merge
                # Check for duplicates (same device_ip, port, slave_id, reg, condition, threshold)
                current_alerts = self.email_settings.get('alerts', [])
                for rule in imported_alerts:
                    exists = False
                    for curr in current_alerts:
                        if (curr.get('device_ip') == rule.get('device_ip') and 
                            curr.get('port') == rule.get('port') and 
                            curr.get('slave_id', 1) == rule.get('slave_id', 1) and 
                            curr.get('reg') == rule.get('reg') and 
                            curr.get('condition') == rule.get('condition') and 
                            curr.get('threshold') == rule.get('threshold')):
                            exists = True
                            break
                    if not exists:
                        current_alerts.append(rule)
                self.email_settings['alerts'] = current_alerts
            
            save_email_settings(self.email_settings)
            self.refresh_rules_list()
            self.log_audit("ALERT_RULE_IMPORT", "ALARM", f"Imported alarm rules ({'Replace' if ans else 'Merge'}) from '{os.path.basename(filepath)}'")
            messagebox.showinfo("Success", "Alarm rules imported successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to import alarm rules: {e}")

    def is_lora_alert(self, alert):
        dev_ip = str(alert.get('device_ip', ''))
        slave_id = str(alert.get('slave_id', 1))
        for d in list(self.devices):
            if str(d.get('ip')) == dev_ip and str(d.get('slave_id', 1)) == slave_id:
                if ("LORA" in str(d.get('node_type', '')).upper()) or ("LORA" in str(d.get('device_name', '')).upper()):
                    return True
        return False

    def check_sms_alert_logic(self, alert, current_value, is_triggered):
        if is_triggered and self.is_lora_alert(alert) and float(current_value) <= 0:
            is_triggered = False
        alert_id = f"sms_{alert.get('device_ip')}_{alert.get('reg')}_{alert.get('condition')}_{alert.get('threshold')}"
        
        try:
            interval_mins = float(alert.get('interval', 0))
        except:
            interval_mins = 0
        interval_secs = interval_mins * 60

        with self.sms_lock:
            was_active = self.sms_active_alerts.get(alert_id, False)
            last_sent_time = self.sms_cooldowns.get(alert_id, 0)
            
            if is_triggered:
                if time.time() - last_sent_time < interval_secs:
                    return

                if not was_active or (interval_secs > 0 and time.time() - last_sent_time >= interval_secs):
                    self.sms_active_alerts[alert_id] = True
                    self.sms_cooldowns[alert_id] = time.time()
                    formatted_val = safe_format_val(current_value)
                    self.log(f"SMS ALERT DETECTED: {alert.get('device_ip')} Reg {alert.get('reg')} value {formatted_val}")
                    threading.Thread(target=self.send_sms_async, args=(alert, current_value), daemon=True).start()
                
            elif not is_triggered and was_active:
                self.sms_active_alerts[alert_id] = False
                formatted_val = safe_format_val(current_value)
                self.log(f"SMS NORMAL RESTORED: {alert.get('device_ip')} Reg {alert.get('reg')} value {formatted_val}")

    def send_sms_async(self, alert, current_value):
        s = self.sms_settings
        
        formatted_val = safe_format_val(current_value)
        formatted_thresh = safe_format_val(alert.get('threshold'))

        reg_info = alert.get('reg_name', f"Register {alert.get('reg')}")
        only_reg_name = reg_info
        if "(" in reg_info and ")" in reg_info:
            only_reg_name = reg_info[reg_info.find("(")+1 : reg_info.find(")")]
        
        dev_alias = alert.get('device_alias', '')
        if not dev_alias:
            for d in list(self.devices):
                if str(d['ip']) == str(alert.get('device_ip')) and str(d.get('slave_id', 1)) == str(alert.get('slave_id', 1)):
                    dev_alias = d.get('device_name', '')
                    break
        
        full_name = f"{dev_alias} {only_reg_name}" if dev_alias else only_reg_name
        
        if not s.get('sms_enabled', True):
            return

        try:
            api_key = s.get('api_key', '').strip()
            route = s.get('route', 'q').strip()
            sender_id = s.get('sender_id', '').strip()
            template_id = s.get('template_id', '').strip()
            entity_id = s.get('entity_id', '').strip()
            numbers = s.get('recipient_numbers', '').strip()
            
            if not api_key or not numbers:
                write_sms_log_csv(dev_alias, only_reg_name, numbers, "FAILED (Config Missing)", "API Key or Mobile Numbers missing")
                return

            ts_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            v1_setting = alert.get('var1', '').strip()
            v2_setting = alert.get('var2', '').strip()
            tpl = alert.get('template', '').strip()

            if not tpl or (v1_setting or v2_setting):
                v1_raw = v1_setting if v1_setting else "{name}"
                v2_raw = v2_setting if v2_setting else "{value}"
                tpl = f"{v1_raw}|{v2_raw}"

            msg_text = (tpl
                .replace("{name}", full_name)
                .replace("{dev}", dev_alias)
                .replace("{device}", dev_alias)
                .replace("{reg}", only_reg_name)
                .replace("{var1}", full_name)
                .replace("{var2}", formatted_val)
                .replace("{var3}", formatted_thresh)
                .replace("{value1}", formatted_val)
                .replace("{value2}", formatted_thresh)
                .replace("{value}", formatted_val)
                .replace("{val}", formatted_val)
                .replace("{threshold}", formatted_thresh)
                .replace("{thresh}", formatted_thresh)
                .replace("{condition}", str(alert.get('condition', '')))
                .replace("{cond}", str(alert.get('condition', '')))
                .replace("{time}", ts_str)
                .replace("{ip}", str(alert.get('device_ip', '0.0.0.0'))))

            success, response_msg = send_fast2sms(api_key, route, numbers, msg_text, sender_id=sender_id, template_id=template_id, entity_id=entity_id)
            status_str = "SUCCESS" if success else f"FAILED ({response_msg})"
            
            write_sms_log_csv(dev_alias, only_reg_name, numbers, status_str, msg_text)
            self.log(f"Fast2SMS Result [{full_name}]: {status_str}")
            self.log_audit("SMS_ALERT_SENT" if success else "SMS_ALERT_FAILED", "SMS",
                           f"SMS alert for {full_name} to {numbers}: {status_str}",
                           device_info=f"{platform.node()}; monitored_device={alert.get('device_ip', '')}",
                           updated_value=msg_text, status="SUCCESS" if success else "FAILED")
        except Exception as e:
            write_sms_log_csv(dev_alias, only_reg_name, s.get('recipient_numbers', ''), f"FAILED ({e})", "")
            self.log_audit("SMS_ALERT_FAILED", "SMS", str(e),
                           device_info=f"{platform.node()}; monitored_device={alert.get('device_ip', '')}",
                           status="FAILED")

    # -------------------------
    # VIEW: SMS CONFIG & ALERTS (FAST2SMS)
    # -------------------------
    def show_sms_config(self):
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True)

        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        
        # Left Panel: Fast2SMS Settings
        outer_left, left = self.create_scrollable_panel(layout, bg=self.COLORS["bg_surface"], pad_x=25, pad_y=15)
        outer_left.pack(side="left", fill="y", anchor="n")

        tk.Label(left, text="FAST2SMS CONFIGURATION", font=self.FONTS["h3"], fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(anchor="w", pady=(0, 15))

        def fld(lbl, parent=left, initial="", show=None):
            f = tk.Frame(parent, bg=self.COLORS["bg_surface"])
            f.pack(fill="x", pady=6)
            tk.Label(f, text=lbl, font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
            e = tk.Entry(f, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black", show=show)
            e.insert(0, str(initial))
            e.pack(fill="x", pady=(2, 0), ipady=6)
            tk.Frame(f, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")
            return e, f

        s = self.sms_settings
        
        # API Key entry
        self.sms_api_key, _ = fld("FAST2SMS API KEY", initial=s.get('api_key', ''))
        
        # Route selection
        f_route = tk.Frame(left, bg=self.COLORS["bg_surface"])
        f_route.pack(fill="x", pady=6)
        tk.Label(f_route, text="FAST2SMS ROUTE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.sms_route_cb = ttk.Combobox(f_route, values=["q (Quick SMS)", "dlt (DLT SMS)", "otp (OTP SMS)"], style="TCombobox", state="readonly")
        cur_r = s.get('route', 'q')
        if cur_r.startswith('dlt'):
            self.sms_route_cb.set("dlt (DLT SMS)")
        elif cur_r.startswith('otp'):
            self.sms_route_cb.set("otp (OTP SMS)")
        else:
            self.sms_route_cb.set("q (Quick SMS)")
        self.sms_route_cb.pack(fill="x", pady=(2, 0))

        # Sender ID entry (Optional/DLT Header)
        self.sms_sender_id, _ = fld("FAST2SMS SENDER ID (DLT Header, e.g. FSTSMS)", initial=s.get('sender_id', ''))

        # DLT Template ID entry
        self.sms_template_id, _ = fld("DLT TEMPLATE ID (Message ID - Required for DLT Route)", initial=s.get('template_id', ''))

        # DLT Entity ID entry
        self.sms_entity_id, _ = fld("DLT ENTITY ID (Optional)", initial=s.get('entity_id', ''))

        # Recipient Numbers
        self.sms_recipients, _ = fld("RECIPIENT MOBILE NUMBERS (Comma Separated)", initial=s.get('recipient_numbers', ''))

        # Enable SMS Toggle
        self.sms_enabled_var = tk.BooleanVar(value=s.get('sms_enabled', True))
        tk.Checkbutton(left, text="ENABLE SMS NOTIFICATIONS", variable=self.sms_enabled_var,
                       font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"],
                       activebackground=self.COLORS["bg_surface"], selectcolor=self.COLORS["bg_dark"]).pack(anchor="w", pady=10)

        tk.Button(left, text="SAVE SMS SETTINGS", bg=self.COLORS["accent"], fg="black", font=self.FONTS["button"], 
                  relief="flat", padx=20, pady=10, command=self.save_sms_settings_ui).pack(fill="x", pady=(15, 5))
        
        tk.Button(left, text="📱 TEST FAST2SMS ALERT", bg=self.COLORS["success"], fg="white", font=self.FONTS["button"], 
                  relief="flat", padx=20, pady=8, command=self.test_sms_config).pack(fill="x", pady=4)

        tk.Button(left, text="📜 VIEW SMS SEND LOGS", bg="#0288D1", fg="white", font=self.FONTS["button"], 
                  relief="flat", padx=20, pady=8, command=self.show_sms_logs_history).pack(fill="x", pady=4)

        info_text = "Note: Fast2SMS requires a valid API Key from fast2sms.com.\nIf you do not have a registered DLT Sender ID & Template ID, please select 'q (Quick SMS)' route.\nMultiple numbers should be separated by commas (e.g. 9876543210, 9123456789)."
        info_lbl = tk.Label(left, text=info_text, 
                           font=("Segoe UI", 8, "italic"), fg=self.COLORS["warning"], bg=self.COLORS["bg_surface"], justify="left")
        info_lbl.pack(fill="x", pady=15)

        # Right Panel: SMS Alert Rules
        right = tk.Frame(layout, bg=self.COLORS["bg_dark"])
        right.pack(side="left", fill="both", expand=True, padx=(30, 0))

        tk.Label(right, text="SMS ALERT RULES", font=self.FONTS["h3"], fg=self.COLORS["text_main"], bg=self.COLORS["bg_dark"]).pack(anchor="w", pady=(15, 5), padx=30)

        tree_bar = tk.Frame(right, bg=self.COLORS["bg_dark"])
        tree_bar.pack(fill="x", padx=30, pady=(0, 10))
        
        tk.Button(tree_bar, text="🗑 REMOVE SELECTED RULE", bg=self.COLORS["danger"], fg="white", 
                  font=("Segoe UI", 8, "bold"), relief="flat", padx=15, pady=6, 
                  command=self.remove_sms_alert_rule).pack(side="left")

        btn_cnt = tk.Frame(tree_bar, bg=self.COLORS["bg_dark"])
        btn_cnt.pack(side="right")
        
        tk.Button(btn_cnt, text="EXPORT SMS ALERTS", bg=self.COLORS["accent"], fg="black", 
                  font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", padx=15, pady=6, 
                  command=self.export_sms_alert_config).pack(side="left", padx=5)
                  
        tk.Button(btn_cnt, text="IMPORT SMS ALERTS", bg=self.COLORS["accent"], fg="black", 
                  font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", padx=15, pady=6, 
                  command=self.import_sms_alert_config).pack(side="left", padx=5)

        add_rule_frame = tk.Frame(right, bg=self.COLORS["bg_surface"], padx=20, pady=20,
                                 highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        add_rule_frame.pack(fill="x", padx=30, pady=(0, 20))

        row1 = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"])
        row1.pack(fill="x")

        f_ip = tk.Frame(row1, bg=self.COLORS["bg_surface"])
        f_ip.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_ip, text="SELECT DEVICE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        display_names = [d.get('device_name') if d.get('device_name') else f"{d['ip']} @ {d.get('slave_id', 1)}" for d in self.devices]
        self.rule_sms_ip = ttk.Combobox(f_ip, values=display_names, style="TCombobox")
        self.rule_sms_ip.pack(fill="x", pady=(2, 0))

        f_reg = tk.Frame(row1, bg=self.COLORS["bg_surface"])
        f_reg.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_reg, text="REGISTER", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_sms_reg = ttk.Combobox(f_reg, style="TCombobox")
        self.rule_sms_reg.pack(fill="x", pady=(2, 0))

        self.rule_sms_ip.bind("<<ComboboxSelected>>", self.update_sms_regs)

        row2 = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"], pady=10)
        row2.pack(fill="x")

        f_cond = tk.Frame(row2, bg=self.COLORS["bg_surface"])
        f_cond.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_cond, text="CONDITION", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_sms_cond = ttk.Combobox(f_cond, values=["Greater Than (>)", "Less Than (<)", "Upper Limit (>)", "Low Limit (<)", "Equal To (==)"], style="TCombobox")
        self.rule_sms_cond.pack(fill="x", pady=(2, 0))

        f_thresh = tk.Frame(row2, bg=self.COLORS["bg_surface"])
        f_thresh.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_thresh, text="PROCESS VALUE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_sms_thresh = tk.Entry(f_thresh, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.rule_sms_thresh.pack(fill="x", pady=(2, 0), ipady=6)

        f_interval = tk.Frame(row2, bg=self.COLORS["bg_surface"])
        f_interval.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_interval, text="INTERVAL (MINS)", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_sms_interval = tk.Entry(f_interval, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.rule_sms_interval.insert(0, "0")
        self.rule_sms_interval.pack(fill="x", pady=(2, 0), ipady=6)

        row_vars = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"], pady=5)
        row_vars.pack(fill="x")

        f_var1 = tk.Frame(row_vars, bg=self.COLORS["bg_surface"])
        f_var1.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_var1, text="DLT VARIABLE 1 (VAR 1)", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_sms_var1 = tk.Entry(f_var1, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.rule_sms_var1.insert(0, "{name}")
        self.rule_sms_var1.pack(fill="x", pady=(2, 0), ipady=6)

        f_var2 = tk.Frame(row_vars, bg=self.COLORS["bg_surface"])
        f_var2.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(f_var2, text="DLT VARIABLE 2 (VAR 2)", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_sms_var2 = tk.Entry(f_var2, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.rule_sms_var2.insert(0, "{value}")
        self.rule_sms_var2.pack(fill="x", pady=(2, 0), ipady=6)

        row3 = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"])
        row3.pack(fill="x", pady=5)
        
        tk.Label(row3, text="FULL SMS TEMPLATE / DLT COMBINED MESSAGE", font=("Segoe UI", 8, "bold"), 
                 fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.rule_sms_msg = tk.Entry(row3, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"], relief="flat", font=self.FONTS["body"], insertbackground="black")
        self.rule_sms_msg.insert(0, "{name}|{value}")
        self.rule_sms_msg.pack(fill="x", pady=(2, 0), ipady=6)

        btn_row = tk.Frame(add_rule_frame, bg=self.COLORS["bg_surface"])
        btn_row.pack(fill="x", pady=10)
        
        self.btn_add_sms_alert = tk.Button(btn_row, text="+ ADD SMS ALERT RULE", bg=self.COLORS["bg_highlight"], fg=self.COLORS["accent"], 
                  font=self.FONTS["button"], relief="flat", pady=8, command=self.add_sms_alert_rule)
        self.btn_add_sms_alert.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        tk.Button(btn_row, text="CLEAR / CANCEL", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], 
                  font=self.FONTS["button"], relief="flat", pady=8, 
                  command=lambda: (self.sms_rules_tree.selection_remove(self.sms_rules_tree.selection()), self.on_sms_rule_select(None))).pack(side="left", fill="x", expand=True, padx=(5, 0))

        tree_cnt = tk.Frame(right, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        tree_cnt.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        self.sms_rules_tree = ttk.Treeview(tree_cnt, columns=("ip", "reg", "cond", "thresh", "var1", "var2", "msg"), show="headings", style="Treeview")
        self.sms_rules_tree.heading("ip", text="DEVICE NAME")
        self.sms_rules_tree.heading("reg", text="REGISTER NAME")
        self.sms_rules_tree.heading("cond", text="CONDITION")
        self.sms_rules_tree.heading("thresh", text="PROCESS VALUE")
        self.sms_rules_tree.heading("var1", text="VARIABLE 1")
        self.sms_rules_tree.heading("var2", text="VARIABLE 2")
        self.sms_rules_tree.heading("msg", text="SMS TEMPLATE / DLT")
        
        self.sms_rules_tree.column("ip", width=140, minwidth=110, stretch=True)
        self.sms_rules_tree.column("reg", width=150, minwidth=120, stretch=True)
        self.sms_rules_tree.column("cond", width=100, minwidth=90, stretch=False)
        self.sms_rules_tree.column("thresh", width=120, minwidth=100, stretch=False)
        self.sms_rules_tree.column("var1", width=130, minwidth=100, stretch=True)
        self.sms_rules_tree.column("var2", width=130, minwidth=100, stretch=True)
        self.sms_rules_tree.column("msg", width=220, minwidth=160, stretch=True)
        
        v_sb = ttk.Scrollbar(tree_cnt, orient="vertical", command=self.sms_rules_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(tree_cnt, orient="horizontal", command=self.sms_rules_tree.xview, style="Horizontal.TScrollbar")
        self.sms_rules_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.sms_rules_tree.pack(side="left", fill="both", expand=True)

        self.sms_rules_tree.bind("<<TreeviewSelect>>", self.on_sms_rule_select)

        self.refresh_sms_rules_list()

    def save_sms_settings_ui(self):
        old_settings = dict(self.sms_settings)
        route_val = self.sms_route_cb.get().split()[0].strip()
        self.sms_settings['api_key'] = self.sms_api_key.get().strip()
        self.sms_settings['route'] = route_val
        self.sms_settings['sender_id'] = self.sms_sender_id.get().strip()
        self.sms_settings['template_id'] = self.sms_template_id.get().strip()
        self.sms_settings['entity_id'] = self.sms_entity_id.get().strip()
        self.sms_settings['recipient_numbers'] = self.sms_recipients.get().strip()
        self.sms_settings['sms_enabled'] = self.sms_enabled_var.get()

        save_sms_settings(self.sms_settings)

        self.log_audit("SMS_CONFIG_UPDATE", "SMS", f"Updated Fast2SMS configuration (Route: {route_val}, Recipients: {self.sms_settings['recipient_numbers']})",
                       previous_value=json.dumps({k: v for k, v in old_settings.items() if k != "api_key"}, sort_keys=True),
                       updated_value=json.dumps({k: v for k, v in self.sms_settings.items() if k != "api_key"}, sort_keys=True))
        messagebox.showinfo("Success", "Fast2SMS configuration saved successfully.")

    def test_sms_config(self):
        api_key = self.sms_api_key.get().strip()
        route = self.sms_route_cb.get().split()[0].strip()
        sender_id = self.sms_sender_id.get().strip()
        template_id = self.sms_template_id.get().strip()
        entity_id = self.sms_entity_id.get().strip()
        numbers = self.sms_recipients.get().strip()
        
        if not api_key:
            messagebox.showerror("Error", "Please enter a Fast2SMS API Key.")
            return
        if not numbers:
            messagebox.showerror("Error", "Please enter recipient mobile number(s).")
            return
            
        test_msg = f"TempTrek Test SMS Alert at {datetime.now().strftime('%H:%M:%S')}"
        
        def run_test():
            success, result_msg = send_fast2sms(api_key, route, numbers, test_msg, sender_id=sender_id, template_id=template_id, entity_id=entity_id)
            if success:
                self.root.after(0, lambda: messagebox.showinfo("Test SMS Success", f"Fast2SMS Response:\n{result_msg}"))
            else:
                self.root.after(0, lambda: messagebox.showerror("Test SMS Failed", f"Could not send SMS via Fast2SMS:\n{result_msg}"))
                
        threading.Thread(target=run_test, daemon=True).start()

    def add_sms_alert_rule(self):
        idx = self.rule_sms_ip.current()
        if idx < 0:
            messagebox.showerror("Error", "Please select a device.")
            return
            
        dev = self.devices[idx]
        ip = dev['ip']
        dev_alias = dev.get('device_name', f"{ip} @ {dev.get('slave_id', 1)}")
        port = dev['port']
        slave_id = dev.get('slave_id', 1)
        
        reg_raw = self.rule_sms_reg.get().strip()
        if not reg_raw:
            messagebox.showerror("Error", "Please select a register.")
            return
            
        reg = reg_raw.split(" ")[0]
            
        cond = self.rule_sms_cond.get()
        cond_symbol = ">"
        if "<" in cond: cond_symbol = "<"
        elif "==" in cond: cond_symbol = "=="
        elif ">" in cond: cond_symbol = ">"
        
        try:
            thresh = float(self.rule_sms_thresh.get().strip())
        except:
            messagebox.showerror("Error", "Invalid process value / threshold.")
            return
            
        try:
            interval = float(self.rule_sms_interval.get().strip())
        except:
            interval = 0
            
        var1_val = self.rule_sms_var1.get().strip()
        var2_val = self.rule_sms_var2.get().strip()
        msg_tpl = self.rule_sms_msg.get().strip()
        
        if (var1_val or var2_val) and (not msg_tpl or msg_tpl == "Alert: {name} reached {value} at {time}"):
            v1 = var1_val if var1_val else "{name}"
            v2 = var2_val if var2_val else "{value}"
            msg_tpl = f"{v1}|{v2}"

        new_rule = {
            "device_ip": ip,
            "device_alias": dev_alias,
            "port": port, 
            "slave_id": slave_id,
            "reg": reg,
            "condition": cond_symbol,
            "threshold": thresh,
            "interval": interval,
            "reg_name": reg_raw,
            "var1": var1_val,
            "var2": var2_val,
            "template": msg_tpl if msg_tpl else f"{var1_val if var1_val else '{name}'}|{var2_val if var2_val else '{value}'}"
        }

        if 'alerts' not in self.sms_settings:
            self.sms_settings['alerts'] = []
            
        sel = self.sms_rules_tree.selection()
        if sel:
            idx_rule = self.sms_rules_tree.index(sel[0])
            self.sms_settings['alerts'][idx_rule] = new_rule
            self.log_audit("SMS_RULE_UPDATE", "ALARM", f"Updated SMS alert rule for '{dev_alias}' Reg {reg}")
            messagebox.showinfo("Success", "SMS alert rule updated.")
            self.sms_rules_tree.selection_remove(sel[0])
        else:
            self.sms_settings['alerts'].append(new_rule)
            self.log_audit("SMS_RULE_ADD", "ALARM", f"Added SMS alert rule for '{dev_alias}' Reg {reg}")
            messagebox.showinfo("Success", "SMS alert rule added.")
            
        save_sms_settings(self.sms_settings)
        self.refresh_sms_rules_list()
        self.btn_add_sms_alert.config(text="+ ADD SMS ALERT RULE", bg=self.COLORS["bg_highlight"])

    def update_sms_regs(self, event=None):
        idx = self.rule_sms_ip.current()
        if idx < 0: return
        d = self.devices[idx]
        regs = []
        for r in d['registers']:
            name = d.get('names', {}).get(str(r), f"Reg {r}")
            regs.append(f"{r} ({name}) @ {d.get('slave_id', 1)}")
        self.rule_sms_reg['values'] = list(dict.fromkeys(regs))

    def on_sms_rule_select(self, event):
        sel = self.sms_rules_tree.selection()
        if not sel:
            self.btn_add_sms_alert.config(text="+ ADD SMS ALERT RULE", bg=self.COLORS["bg_highlight"])
            return
            
        idx = self.sms_rules_tree.index(sel[0])
        alert = self.sms_settings['alerts'][idx]
        
        matched_idx = -1
        for i, d in enumerate(self.devices):
            if d['ip'] == alert.get('device_ip') and str(d.get('slave_id', 1)) == str(alert.get('slave_id', 1)):
                matched_idx = i
                break
        
        if matched_idx >= 0:
            self.rule_sms_ip.current(matched_idx)
            self.update_sms_regs() 
            self.rule_sms_reg.set(alert.get('reg_name', ''))
        
        cond_sym = alert.get('condition', '')
        cond_map = {
            ">": "Upper Limit (>)", 
            "<": "Low Limit (<)", 
            "==": "Equal To (==)"
        }
        self.rule_sms_cond.set(cond_map.get(cond_sym, cond_sym))
        
        self.rule_sms_thresh.delete(0, tk.END)
        self.rule_sms_thresh.insert(0, alert.get('threshold', ''))
        self.rule_sms_interval.delete(0, tk.END)
        self.rule_sms_interval.insert(0, alert.get('interval', '0'))
        
        v1 = alert.get('var1', '')
        v2 = alert.get('var2', '')
        tpl = alert.get('template', '')
        if not v1 and not v2 and '|' in tpl:
            parts = tpl.split('|', 1)
            v1, v2 = parts[0], parts[1]
            
        self.rule_sms_var1.delete(0, tk.END)
        self.rule_sms_var1.insert(0, v1 if v1 else "{name}")
        self.rule_sms_var2.delete(0, tk.END)
        self.rule_sms_var2.insert(0, v2 if v2 else "{value}")

        self.rule_sms_msg.delete(0, tk.END)
        self.rule_sms_msg.insert(0, tpl if tpl else f"{v1 if v1 else '{name}'}|{v2 if v2 else '{value}'}")
        
        self.btn_add_sms_alert.config(text="UPDATE SMS RULE", bg=self.COLORS["warning"])

    def remove_sms_alert_rule(self):
        sel = self.sms_rules_tree.selection()
        if not sel: return
        idx = self.sms_rules_tree.index(sel[0])
        removed = self.sms_settings['alerts'].pop(idx)
        alias = removed.get('device_alias', removed.get('device_ip'))
        self.log_audit("SMS_RULE_DELETE", "ALARM", f"Deleted SMS alert rule for '{alias}' Reg {removed.get('reg')}")
        save_sms_settings(self.sms_settings)
        self.refresh_sms_rules_list()

    def refresh_sms_rules_list(self):
        for item in self.sms_rules_tree.get_children():
            self.sms_rules_tree.delete(item)
        def get_display_cond(sym):
            m = {">": "Upper Limit", "<": "Low Limit", "==": "Equal To"}
            return m.get(sym, sym)

        for a in self.sms_settings.get('alerts', []):
            alias = a.get('device_alias', a.get('device_ip'))
            reg_disp = a.get('reg_name', f"Reg {a.get('reg')}")
            tpl = a.get('template', '')
            v1 = a.get('var1', '')
            v2 = a.get('var2', '')
            if not v1 and not v2 and '|' in tpl:
                parts = tpl.split('|', 1)
                v1, v2 = parts[0], parts[1]
            if not v1: v1 = "{name}"
            if not v2: v2 = "{value}"

            self.sms_rules_tree.insert("", "end", values=(
                alias,
                reg_disp,
                get_display_cond(a.get('condition')),
                a.get('threshold'),
                v1,
                v2,
                tpl if tpl else f"{v1}|{v2}"
            ))

    def export_sms_alert_config(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")],
            initialfile="sms_alerts_backup.json",
            title="Export SMS Alarm Rules"
        )
        if not filepath:
            return
        try:
            alerts = self.sms_settings.get('alerts', [])
            with open(filepath, "w") as f:
                json.dump(alerts, f, indent=4)
            self.log_audit("SMS_RULE_EXPORT", "ALARM", f"Exported SMS alarm rules to '{os.path.basename(filepath)}'")
            messagebox.showinfo("Success", "SMS alarm rules exported successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export SMS alarm rules: {e}")

    def import_sms_alert_config(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("JSON Files", "*.json")],
            title="Import SMS Alarm Configuration"
        )
        if not filepath:
            return
        try:
            with open(filepath, "r") as f:
                imported_alerts = json.load(f)
            
            if not isinstance(imported_alerts, list):
                raise ValueError("SMS configuration must be a JSON list of rules.")
            
            for rule in imported_alerts:
                if not isinstance(rule, dict) or "device_ip" not in rule or "reg" not in rule or "condition" not in rule or "threshold" not in rule:
                    raise ValueError("Invalid SMS alert rule structure.")
            
            ans = messagebox.askyesnocancel(
                "Import Options",
                "Would you like to REPLACE the current SMS alarm rules (Yes) or MERGE/Append them (No)?"
            )
            if ans is None:
                return
            
            if ans is True:
                self.sms_settings['alerts'] = imported_alerts
            else:
                current_alerts = self.sms_settings.get('alerts', [])
                for rule in imported_alerts:
                    exists = False
                    for curr in current_alerts:
                        if (curr.get('device_ip') == rule.get('device_ip') and 
                            curr.get('port') == rule.get('port') and 
                            curr.get('slave_id', 1) == rule.get('slave_id', 1) and 
                            curr.get('reg') == rule.get('reg') and 
                            curr.get('condition') == rule.get('condition') and 
                            curr.get('threshold') == rule.get('threshold')):
                            exists = True
                            break
                    if not exists:
                        current_alerts.append(rule)
                self.sms_settings['alerts'] = current_alerts
            
            save_sms_settings(self.sms_settings)
            self.refresh_sms_rules_list()
            self.log_audit("SMS_RULE_IMPORT", "ALARM", f"Imported SMS alarm rules from '{os.path.basename(filepath)}'")
            messagebox.showinfo("Success", "SMS alarm rules imported successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to import SMS alarm rules: {e}")

    def show_sms_logs_history(self):
        bar = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        bar.pack(side="top", fill="x", pady=10, padx=30)
        
        tk.Button(bar, text="⬅ BACK TO SMS CONFIG", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], font=self.FONTS["button"], relief="flat", padx=12, command=lambda: self.navigate("sms_config")).pack(side="left", padx=5)
        
        date_str = datetime.now().strftime("%Y-%m-%d")
        tk.Label(bar, text=f"SMS Sent Logs: {date_str}", font=("Segoe UI", 10, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"]).pack(side="left", padx=20)
        
        cnt = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        cnt.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        cols = ("time", "device", "field", "recipient", "status", "message")
        tree = ttk.Treeview(cnt, columns=cols, show="headings", style="Treeview")
        
        tree.heading("time", text="TIMESTAMP")
        tree.heading("device", text="DEVICE NAME")
        tree.heading("field", text="FIELD NAME")
        tree.heading("recipient", text="RECIPIENT NUMBER")
        tree.heading("status", text="DELIVERY STATUS")
        tree.heading("message", text="MESSAGE CONTENT")
        
        tree.column("time", width=160, minwidth=140, anchor="center", stretch=False)
        tree.column("device", width=160, minwidth=130, anchor="w", stretch=True)
        tree.column("field", width=160, minwidth=130, anchor="w", stretch=True)
        tree.column("recipient", width=160, minwidth=140, anchor="w", stretch=False)
        tree.column("status", width=130, minwidth=100, anchor="center", stretch=False)
        tree.column("message", width=300, minwidth=200, anchor="w", stretch=True)
        
        v_sb = ttk.Scrollbar(cnt, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(cnt, orient="horizontal", command=tree.xview, style="Horizontal.TScrollbar")
        tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        tree.pack(side="left", fill="both", expand=True)

        filename = os.path.join(DATA_PATH, "live", f"SMS_Logs_{date_str}.csv")
        if os.path.exists(filename):
            try:
                with open(filename, "r", newline="") as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    for row in reader:
                        if len(row) >= 5:
                            msg_val = row[5] if len(row) > 5 else ""
                            tree.insert("", 0, values=(row[0], row[1], row[2], row[3], row[4], msg_val))
            except Exception as e:
                self.log(f"Error reading SMS logs: {e}")

    def check_alert_logic(self, alert, current_value, is_triggered):
        if is_triggered and self.is_lora_alert(alert) and float(current_value) <= 0:
            is_triggered = False
        # Create a unique ID for this alert rule
        alert_id = f"{alert.get('device_ip')}_{alert.get('reg')}_{alert.get('condition')}_{alert.get('threshold')}"
        
        try:
            interval_mins = float(alert.get('interval', 0))
        except:
            interval_mins = 0
        interval_secs = interval_mins * 60

        with self.alert_lock:
            was_active = self.active_alerts.get(alert_id, False)
            last_sent_time = self.email_cooldowns.get(alert_id, 0)
            
            if is_triggered:
                # Check for cooldown based on when the last alert email was SENT
                if time.time() - last_sent_time < interval_secs:
                    # Still in cooldown window, skip email
                    return

                if not was_active or (interval_secs > 0 and time.time() - last_sent_time >= interval_secs):
                    self.active_alerts[alert_id] = True
                    self.email_cooldowns[alert_id] = time.time()
                    formatted_val = safe_format_val(current_value)
                    self.log(f"ALERT DETECTED: {alert.get('device_ip')} Reg {alert.get('reg')} value {formatted_val}")
                    threading.Thread(target=self.send_email_async, args=(alert, current_value), daemon=True).start()
                
            elif not is_triggered and was_active:
                # TRANSITION: Alert -> Normal (Reset state only, DO NOT overwrite last_sent_time cooldown clock)
                self.active_alerts[alert_id] = False
                formatted_val = safe_format_val(current_value)
                self.log(f"NORMAL RESTORED: {alert.get('device_ip')} Reg {alert.get('reg')} value {formatted_val}")

    def send_email_async(self, alert, current_value):
        s = self.email_settings
        
        formatted_val = safe_format_val(current_value)
        formatted_thresh = safe_format_val(alert.get('threshold'))

        # 1. Prepare Metadata for both Log and Email
        reg_info = alert.get('reg_name', f"Register {alert.get('reg')}")
        only_reg_name = reg_info
        if "(" in reg_info and ")" in reg_info:
            only_reg_name = reg_info[reg_info.find("(")+1 : reg_info.find(")")]
        
        dev_alias = alert.get('device_alias', '')
        if not dev_alias:
            for d in list(self.devices):
                if str(d['ip']) == str(alert.get('device_ip')) and str(d.get('slave_id', 1)) == str(alert.get('slave_id', 1)):
                    dev_alias = d.get('device_name', '')
                    break
        
        full_name = f"{dev_alias} {only_reg_name}" if dev_alias else only_reg_name
        
        # 2. ALWAYS Log to ALARM HISTORY
        write_alarm_history_csv(
            dev_alias,
            only_reg_name,
            alert.get('reg', 'N/A'),
            formatted_val,
            alert.get('condition', ''),
            formatted_thresh
        )
        # Live Update UI
        ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.root.after(0, lambda: self._append_alarm_live([
            ts_now, dev_alias, only_reg_name, alert.get('reg', 'N/A'),
            formatted_val, alert.get('condition', ''), formatted_thresh
        ]))

        # 3. Check if actual Email sending is enabled
        if not s.get('email_enabled', True):
            return

        # 4. Email Sending Logic
        try:
            raw_to = s.get('recipient_email', '')
            # Support multiple recipients (comma or semicolon)
            recipients = parse_email_recipients(raw_to)
            
            if not recipients:
                return

            msg = MIMEMultipart()
            msg['From'] = s.get('sender_email', '')
            msg['To'] = raw_to
            
            base_subject = s.get('subject', 'Modbus Alert')
            msg['Subject'] = f"ALERT: {base_subject}"
            
            ts_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            title_line = "SYSTEM ALERT NOTIFICATION"
            tpl = alert.get('template', "Alert: {name} reached {value} at {time}")
            status_block = tpl.replace("{name}", full_name).replace("{value}", formatted_val).replace("{time}", ts_str).replace("{ip}", alert.get('device_ip', '0.0.0.0'))

            body = (f"{title_line}\n"
                    f"================================\n"
                    f"{status_block}\n"
                    f"================================\n"
                    f"Condition: {alert.get('condition')} {formatted_thresh}\n"
                    f"Time of Event: {ts_str}\n"
                    f"================================")
            
            msg.attach(MIMEText(body, 'plain'))
            
            smtp_server = s.get('smtp_server', 'smtp.gmail.com')
            smtp_port_raw = s.get('smtp_port', 587)
            try: smtp_port = int(smtp_port_raw)
            except: smtp_port = 587
            
            sender_usr = s.get('sender_email', '').strip()
            sender_pwd = s.get('sender_password', '').strip()

            def try_smtp_connect(target_host, target_port):
                try:
                    return connect_smtp(target_host, target_port, sender_usr, sender_pwd), target_port, None
                except Exception as exc:
                    return None, target_port, exc

            domain = sender_usr.split('@')[-1] if '@' in sender_usr else ''
            candidate_targets = [(smtp_server, smtp_port)]
            server = None
            last_err = None
            for h_cand, p_cand in candidate_targets:
                srv, p_res, err = try_smtp_connect(h_cand, p_cand)
                if srv is not None:
                    server = srv
                    break
                else:
                    last_err = err

            if server is None:
                raise last_err if last_err else Exception("Could not establish connection to mail server.")

            # Send individual emails to ensure delivery with strict servers
            for recipient in recipients:
                # Update the To header for each recipient
                msg.replace_header('To', recipient)
                server.sendmail(sender_usr, recipient, msg.as_string())
                
            server.quit()
            
            # Log to EMAIL LOGS
            write_email_log_csv(dev_alias, only_reg_name, raw_to, "SENT")
            self.log_audit("EMAIL_ALERT_SENT", "EMAIL",
                           f"Alert email for {full_name} sent to {', '.join(recipients)}",
                           device_info=f"{platform.node()}; monitored_device={alert.get('device_ip', '')}")
            # Live Update UI
            ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.root.after(0, lambda: self._append_email_log_live([
                ts_now, dev_alias, only_reg_name, raw_to, "SENT"
            ]))
            
            self.root.after(0, lambda: self.log(f"SUCCESS: Alert email sent at {datetime.now().strftime('%H:%M:%S')} for {full_name}"))
        except Exception as e:
            self.root.after(0, lambda: self.log(f"FAILURE: Email send failed: {e}"))
            self.log_audit("EMAIL_ALERT_FAILED", "EMAIL", str(e),
                           device_info=f"{platform.node()}; monitored_device={alert.get('device_ip', '')}",
                           status="FAILED")

    def _connect_smtp(self, sender_usr, sender_pwd, smtp_server, smtp_port):
        def try_smtp_connect(target_host, target_port):
            try:
                return connect_smtp(target_host, target_port, sender_usr, sender_pwd), target_port, None
            except Exception as exc:
                return None, target_port, exc

        domain = sender_usr.split('@')[-1] if '@' in sender_usr else ''
        candidate_targets = [(smtp_server, smtp_port)]
        server = None
        last_err = None
        for h_cand, p_cand in candidate_targets:
            srv, p_res, err = try_smtp_connect(h_cand, p_cand)
            if srv is not None:
                server = srv
                break
            else:
                last_err = err

        return server, last_err

    def check_scheduled_pdf_email(self):
        s = self.email_settings
        interval_str = s.get('scheduled_pdf_interval', 'Disabled')
        if interval_str == 'Disabled' or not s.get('email_enabled', True):
            return

        interval_map = {
            "5 Mins": 300,
            "30 Mins": 1800,
            "1 Hour": 3600,
            "6 Hours": 21600,
            "12 Hours": 43200,
            "24 Hours": 86400
        }
        interval_sec = interval_map.get(interval_str, 0)
        if interval_sec <= 0:
            return

        last_sent = s.get('last_scheduled_pdf_time', 0)
        now_ts = time.time()

        if last_sent == 0:
            s['last_scheduled_pdf_time'] = now_ts
            save_email_settings(s)
            return

        if now_ts - last_sent >= interval_sec:
            s['last_scheduled_pdf_time'] = now_ts
            save_email_settings(s)
            threading.Thread(target=self.send_scheduled_pdf_email, args=(interval_str, interval_sec), daemon=True).start()

    def send_scheduled_pdf_now(self):
        interval_str = self.email_settings.get('scheduled_pdf_interval', '5 Mins')
        if interval_str == 'Disabled':
            interval_str = '5 Mins'
        interval_map = {"5 Mins": 300, "30 Mins": 1800, "1 Hour": 3600, "6 Hours": 21600, "12 Hours": 43200, "24 Hours": 86400}
        interval_sec = interval_map.get(interval_str, 3600)
        threading.Thread(target=self.send_scheduled_pdf_email, args=(interval_str, interval_sec), daemon=True).start()
        messagebox.showinfo("Report Email Dispatched", f"Generating PDF data report for the past {interval_str} and sending via email in the background...")

    @storage_operation
    def send_scheduled_pdf_email(self, interval_str, interval_sec):
        s = self.email_settings
        raw_to = s.get('recipient_email', '')
        try:
            recipients = parse_email_recipients(raw_to)
        except ValueError as exc:
            self.log_audit("SCHEDULED_REPORT_EMAIL", "EMAIL", str(exc), status="FAILED")
            self.root.after(0, lambda e=str(exc): self.log(f"Scheduled PDF email skipped: {e}"))
            return
        if not recipients:
            return

        to_dt = datetime.now()
        from_dt = to_dt - timedelta(seconds=interval_sec)
        
        reports_dir = os.path.join(DATA_PATH, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        pdf_fn = f"Scheduled_Report_{interval_str.replace(' ', '')}_{to_dt.strftime('%Y%m%d_%H%M%S')}.pdf"
        target_pdf = os.path.join(reports_dir, pdf_fn)

        try:
            if HAS_REPORTLAB:
                p_size = landscape(letter)
                c = canvas.Canvas(target_pdf, pagesize=p_size)
                w, h = p_size

                date_str_now = to_dt.strftime("%Y-%m-%d")
                date_str_prev = from_dt.strftime("%Y-%m-%d")
                target_dates = set([date_str_now, date_str_prev])
                live_dir = os.path.join(DATA_PATH, "live")
                approvals_for_report = load_approvals()
                scheduled_all_approved = True

                def draw_scheduled_approval_status(is_final):
                    if is_final:
                        c.setFillColorRGB(0.1, 0.55, 0.25)
                        c.setFont("Helvetica-Bold", 11)
                        c.drawRightString(w - 40, h - 50, "FINAL / APPROVED")
                        return
                    c.saveState()
                    c.setFillColorRGB(0.82, 0.82, 0.82)
                    c.setFont("Helvetica-Bold", 48)
                    c.translate(w / 2, h / 2)
                    c.rotate(35)
                    c.drawCentredString(0, 0, "DRAFT")
                    c.restoreState()
                    c.setFillColorRGB(0.8, 0.15, 0.15)
                    c.setFont("Helvetica-Bold", 11)
                    c.drawRightString(w - 40, h - 50, "DRAFT / UNAPPROVED")

                device_list = list(self.devices)
                comp_info = load_company_details()
                if not device_list:
                    scheduled_all_approved = False
                    c.setFillColorRGB(0.0, 0.36, 0.72); c.setFont("Helvetica-Bold", 18)
                    c.drawString(40, h-50, comp_info.get("company_name", "TempTrek Environmental Monitoring System"))
                    c.setFont("Helvetica", 9); c.setFillColorRGB(0.5, 0.5, 0.5)
                    c.drawString(40, h-70, f"SCHEDULED REPORT ({interval_str.upper()}) | GENERATED: {to_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                    c.drawString(40, h-85, f"RANGE: {from_dt.strftime('%Y-%m-%d %H:%M')} TO {to_dt.strftime('%Y-%m-%d %H:%M')}")
                    c.line(40, h-95, w-40, h-95)
                    c.drawString(40, h-130, "No configured devices found.")
                    draw_scheduled_approval_status(False)
                else:
                    for dev_idx, dev in enumerate(device_list):
                        if dev_idx > 0:
                            c.showPage()

                        ip, port, slave = dev['ip'], str(dev['port']), str(dev.get('slave_id', 1))
                        d_alias = dev.get('device_name', ip)
                        all_registers = [str(r) for r in dev.get('registers', [])]
                        registers = all_registers[:MAX_PDF_REGISTERS]
                        if len(all_registers) > MAX_PDF_REGISTERS:
                            self.log_audit("SCHEDULED_REPORT_LIMIT", "EXPORT",
                                           f"Device '{d_alias}' has {len(all_registers)} registers; PDF limit is {MAX_PDF_REGISTERS}",
                                           status="PARTIAL")
                        reg_names = [dev.get('names', {}).get(r, f"Reg {r}") for r in registers]
                        num_params = len(reg_names)

                        col_spacing = (w - 200) / (num_params if num_params > 0 else 1)
                        f_size = 9 if num_params <= 6 else (8 if num_params <= 10 else 7)

                        # Header
                        c.setFillColorRGB(0.0, 0.36, 0.72); c.setFont("Helvetica-Bold", 18)
                        c.drawString(40, h-50, comp_info.get("company_name", "TempTrek Environmental Monitoring System"))
                        c.setFont("Helvetica", 9); c.setFillColorRGB(0.5, 0.5, 0.5)
                        c.drawString(40, h-70, f"DEVICE: {d_alias} ({ip}:{port} @ Slave {slave}) | EXPORTED: {to_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                        c.drawString(40, h-85, f"PERIODIC REPORT ({interval_str.upper()}) | RANGE: {from_dt.strftime('%Y-%m-%d %H:%M')} TO {to_dt.strftime('%Y-%m-%d %H:%M')}")
                        c.setStrokeColorRGB(0.0, 0.36, 0.72); c.setLineWidth(1.5)
                        c.line(40, h-95, w-40, h-95)

                        # Read historical CSV files for this device
                        prefix = f"{ip.replace('.', '_')}_{port}_s{slave}"
                        all_rows = []
                        if os.path.exists(live_dir):
                            for f in os.listdir(live_dir):
                                if f.startswith(prefix) and any(d_str in f for d_str in target_dates) and f.endswith(".csv"):
                                    sf = os.path.join(live_dir, f)
                                    try:
                                        with open(sf, 'r', encoding='utf-8', errors='replace') as f_in:
                                            reader = csv.reader((line.replace('\0', '') for line in f_in))
                                            next(reader, None)
                                            for r_item in reader:
                                                if len(r_item) >= 6:
                                                    all_rows.append(r_item)
                                    except: pass

                        # Fallback/Merge with memory global_log for this device
                        if hasattr(self, 'global_log'):
                            for g in list(self.global_log):
                                try:
                                    if len(g) >= 9 and (g[2] == ip or g[1] == d_alias):
                                        ts_raw = str(g[8])
                                        ts_full = f"{date_str_now} {ts_raw}" if len(ts_raw) == 8 else ts_raw
                                        r_addr = str(g[4])
                                        val = str(g[5])
                                        all_rows.append([ts_full, ip, port, slave, r_addr, val])
                                except: pass

                        # Pivot data by timestamp
                        pivoted = {}
                        for row in all_rows:
                            try:
                                ts_str = row[0]
                                row_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                                if from_dt <= row_dt <= to_dt:
                                    r_addr, val = str(row[4]), str(row[5])
                                    if r_addr in registers:
                                        is_lora = ("LORA" in str(dev.get('node_type', '')).upper()) or ("LORA" in str(dev.get('device_name', '')).upper())
                                        val_str = str(val).strip()
                                        is_v = True
                                        try:
                                            fv = float(val_str)
                                            if math.isnan(fv) or math.isinf(fv): is_v = False
                                            elif is_lora and fv <= 0: is_v = False
                                        except Exception:
                                            if val_str.lower() in ["nan", "none", "null", ""]: is_v = False
                                        if is_v:
                                            if ts_str not in pivoted: pivoted[ts_str] = {}
                                            pivoted[ts_str][r_addr] = val
                            except: pass

                        # Build series data for Graph Preview
                        all_series = []
                        sorted_ts = sorted(pivoted.keys())
                        if len(sorted_ts) > MAX_PDF_RECORDS:
                            allowed_timestamps = set(sorted_ts[-MAX_PDF_RECORDS:])
                            pivoted = {key: value for key, value in pivoted.items() if key in allowed_timestamps}
                            sorted_ts = sorted(pivoted.keys())
                            self.log_audit("SCHEDULED_REPORT_LIMIT", "EXPORT",
                                           f"Scheduled PDF limited to {MAX_PDF_RECORDS} records for '{d_alias}'",
                                           status="PARTIAL")
                        approval_flags = []
                        for timestamp in sorted_ts:
                            try:
                                approval_flags.append(is_timestamp_approved(
                                    approvals_for_report, ip, port, slave,
                                    datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                                ))
                            except Exception:
                                approval_flags.append(False)
                        device_report_approved = bool(approval_flags) and all(approval_flags)
                        scheduled_all_approved = scheduled_all_approved and device_report_approved
                        draw_scheduled_approval_status(device_report_approved)
                        for r_addr in registers:
                            ser_vals = []
                            for ts in sorted_ts:
                                v_raw = pivoted[ts].get(r_addr)
                                if v_raw is not None and v_raw != "---":
                                    try: ser_vals.append(float(v_raw))
                                    except: pass
                            if ser_vals:
                                all_series.append((r_addr, ser_vals))

                        # --- Draw Graph Preview ---
                        gw, gh = w - 80, 200
                        gx, gy = 40, h - 310

                        c.setFillColorRGB(0.98, 0.98, 0.99)
                        c.rect(gx, gy, gw, gh, fill=1, stroke=1)
                        c.setStrokeColorRGB(0.85, 0.85, 0.85); c.setLineWidth(0.5)

                        c.setStrokeColorRGB(0.4, 0.4, 0.4); c.setLineWidth(0.5)
                        c.line(gx+60, gy+40, gx+gw-150, gy+40)  # X-axis
                        c.line(gx+60, gy+40, gx+60, gy+gh-30) # Y-axis

                        if all_series:
                            all_vals = [v for s in all_series for v in s[1]]
                            glob_min, glob_max = min(all_vals), max(all_vals)
                            if glob_min == glob_max: glob_min -= 5; glob_max += 5
                            v_range = glob_max - glob_min
                            max_len = max([len(s[1]) for s in all_series])

                            plot_w = gw - 60 - 160
                            plot_h = gh - 40 - 30

                            # Y-Axis Labels & Grid
                            c.setFont("Helvetica", 7); c.setFillColorRGB(0.4, 0.4, 0.4)
                            for i in range(6):
                                tick_val = glob_min + (i * v_range / 5)
                                py = gy + 40 + (i * plot_h / 5)
                                c.setStrokeColorRGB(0.88, 0.88, 0.88)
                                c.line(gx+60, py, gx+60+plot_w, py)
                                c.drawString(gx+15, py-2, f"{tick_val:.2f}")

                            # Draw Data Lines
                            palette = ["#D32F2F", "#28A745", "#4285F4", "#F29900", "#6F42C1"]
                            for idx, (r_addr, s_data) in enumerate(all_series):
                                color = palette[idx % len(palette)]
                                r_col = int(color[1:3], 16)/255.0
                                g_col = int(color[3:5], 16)/255.0
                                b_col = int(color[5:7], 16)/255.0

                                c.setStrokeColorRGB(r_col, g_col, b_col); c.setLineWidth(1.5)
                                path = c.beginPath()
                                for i, v in enumerate(s_data):
                                    px = gx + 60 + (i * plot_w / (max_len-1 if max_len > 1 else 1))
                                    py = gy + 40 + ((v - glob_min) * plot_h / v_range)
                                    if i == 0: path.moveTo(px, py)
                                    else: path.lineTo(px, py)
                                c.drawPath(path)

                            # Legend
                            for idx, (r_addr, s_data) in enumerate(all_series):
                                color = palette[idx % len(palette)]
                                r_col = int(color[1:3], 16)/255.0
                                g_col = int(color[3:5], 16)/255.0
                                b_col = int(color[5:7], 16)/255.0

                                leg_name = dev.get('names', {}).get(r_addr, f"Reg {r_addr}")
                                ly_leg = gy + gh - 45 - (idx * 14)
                                c.setFillColorRGB(r_col, g_col, b_col); c.rect(gx + gw - 140, ly_leg, 8, 8, fill=1, stroke=0)
                                c.setFillColorRGB(0.2, 0.2, 0.2); c.setFont("Helvetica-Bold", 8)
                                c.drawString(gx + gw - 125, ly_leg + 1, leg_name.upper())

                        # --- DATA LOG TABLE ---
                        curr_y = gy - 35
                        c.setFillColorRGB(0.1, 0.1, 0.1); c.setFont("Helvetica-Bold", 10)
                        c.drawString(40, curr_y, f"HISTORICAL DATA LOG (TIME WINDOW: {interval_str.upper()})")

                        curr_y -= 25
                        c.line(40, curr_y+15, w-40, curr_y+15)
                        c.setFont("Helvetica-Bold", f_size)
                        c.drawString(40, curr_y, "TIMESTAMP")
                        for i, rname in enumerate(reg_names):
                            c.drawString(180 + (i*col_spacing), curr_y, rname.upper())

                        curr_y -= 30
                        c.line(40, curr_y+18, w-40, curr_y+18)
                        c.setFont("Helvetica", f_size)

                        active_alerts = []
                        for alert in self.email_settings.get('alerts', []):
                            if str(alert.get('device_ip')) == str(ip) and str(alert.get('slave_id', 1)) == slave:
                                active_alerts.append(alert)

                        rows_found = 0
                        for ts in sorted(pivoted.keys(), reverse=True):
                            if curr_y < 50:
                                c.showPage(); curr_y = h - 60
                                draw_scheduled_approval_status(device_report_approved)
                                c.setFillColorRGB(0.1, 0.1, 0.1); c.setFont("Helvetica-Bold", f_size)
                                c.drawString(40, curr_y, "TIMESTAMP")
                                for i, rname in enumerate(reg_names):
                                    c.drawString(180 + (i*col_spacing), curr_y, rname.upper())
                                curr_y -= 30
                                c.line(40, curr_y+18, w-40, curr_y+18)
                                c.setFont("Helvetica", f_size)

                            row_has_alert = False
                            for r_addr in registers:
                                val = pivoted[ts].get(r_addr, "---")
                                if val != "---":
                                    try:
                                        f_val = float(val)
                                        for alert in active_alerts:
                                            if str(alert.get('reg')) == str(r_addr):
                                                cond = alert.get('condition')
                                                thresh = float(alert.get('threshold'))
                                                if (cond == ">" and f_val > thresh) or \
                                                   (cond == "<" and f_val < thresh) or \
                                                   (cond == "==" and f_val == thresh) or \
                                                   ("Greater" in cond and f_val > thresh) or \
                                                   ("Less" in cond and f_val < thresh) or \
                                                   ("Upper" in cond and f_val > thresh) or \
                                                   ("Low" in cond and f_val < thresh):
                                                    row_has_alert = True; break
                                        if row_has_alert: break
                                    except: pass

                            if row_has_alert:
                                c.setFillColorRGB(0.85, 0.1, 0.1)
                                c.setFont("Helvetica-Bold", f_size)
                            else:
                                c.setFillColorRGB(0.2, 0.2, 0.2)
                                c.setFont("Helvetica", f_size)

                            c.drawString(40, curr_y, ts)

                            for i, r_addr in enumerate(registers):
                                v = pivoted[ts].get(r_addr, "---")
                                if v != "---":
                                    if str(v).strip().lower() in ["nan", "none", "null", "", "inf", "-inf"]:
                                        v = "---"
                                    else:
                                        try:
                                            if dev.get('types', {}).get(str(r_addr)) == "BOOLEAN":
                                                label_0 = dev.get('ops', {}).get(str(r_addr), "OFF")
                                                label_1 = dev.get('factors', {}).get(str(r_addr), "ON")
                                                v = label_0 if int(float(v)) == 0 else label_1
                                            else:
                                                prec = int(dev.get('decimals', {}).get(str(r_addr), 3))
                                                fv = float(v)
                                                v = "---" if (math.isnan(fv) or math.isinf(fv)) else f"{fv:.{prec}f}"
                                        except: pass
                                c.drawString(180 + (i*col_spacing), curr_y, v)

                            curr_y -= 15
                            rows_found += 1

                        if rows_found == 0:
                            c.drawString(40, curr_y, "No historical data logged for this device in the specified interval.")

                c.save()
        except Exception as ex:
            self.root.after(0, lambda: self.log(f"Scheduled PDF generation failed: {ex}"))
            return

        try:
            msg = MIMEMultipart()
            msg['From'] = s.get('sender_email', '')
            msg['To'] = raw_to
            base_sub = s.get('subject', 'TempTrek System Report')
            approval_label = "FINAL / APPROVED" if locals().get("scheduled_all_approved", False) else "DRAFT / UNAPPROVED"
            msg['Subject'] = f"{approval_label} - PERIODIC DATA REPORT ({interval_str}): {base_sub}"

            body = (f"TempTrek Environmental Monitoring System\n"
                    f"=========================================\n"
                    f"SCHEDULED PDF DATA REPORT ({interval_str.upper()})\n"
                    f"=========================================\n"
                    f"Report Time Window: {from_dt.strftime('%Y-%m-%d %H:%M:%S')} to {to_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Generated At: {to_dt.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"Approval Status: {approval_label}\n\n"
                    f"Attached is the automated PDF data report for all monitored devices covering the past {interval_str}.\n"
                    f"=========================================")
            msg.attach(MIMEText(body, 'plain'))

            if os.path.exists(target_pdf):
                with open(target_pdf, 'rb') as f_pdf:
                    part = MIMEApplication(f_pdf.read(), Name=os.path.basename(target_pdf))
                part['Content-Disposition'] = f'attachment; filename="{os.path.basename(target_pdf)}"'
                msg.attach(part)

            sender_usr = s.get('sender_email', '').strip()
            sender_pwd = s.get('sender_password', '').strip()
            smtp_server = s.get('smtp_server', 'smtp.gmail.com')
            try: smtp_port = int(s.get('smtp_port', 587))
            except: smtp_port = 587

            server, err = self._connect_smtp(sender_usr, sender_pwd, smtp_server, smtp_port)
            if server:
                sent_recipients = []
                failed_recipients = []
                for r in recipients:
                    try:
                        msg.replace_header('To', r)
                    except Exception:
                        msg['To'] = r
                    try:
                        server.sendmail(sender_usr, [r], msg.as_string())
                        sent_recipients.append(r)
                        write_email_log_csv("System Periodic Report", f"{interval_str} PDF Report", r, "SUCCESS")
                        ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.root.after(0, lambda r_tgt=r: self._append_email_log_live([ts_now, "System Periodic Report", f"{interval_str} PDF Report", r_tgt, "SUCCESS"]))
                    except Exception as send_err:
                        failed_recipients.append(r)
                        write_email_log_csv("System Periodic Report", f"{interval_str} PDF Report", r, f"FAIL: {send_err}")
                        self.log_audit("SCHEDULED_REPORT_EMAIL", "EMAIL",
                                       f"Failed sending scheduled report to {r}: {send_err}", status="FAILED")
                try: server.quit()
                except: pass

                delivery_status = "SUCCESS" if not failed_recipients else ("PARTIAL" if sent_recipients else "FAILED")
                self.log_audit("SCHEDULED_REPORT_EMAIL", "EMAIL",
                               f"Scheduled {interval_str} report: sent [{', '.join(sent_recipients)}], failed [{', '.join(failed_recipients)}]",
                               updated_value=approval_label, status=delivery_status)
                self.root.after(0, lambda: self.log(f"SCHEDULED PDF EMAIL {delivery_status} ({interval_str}); sent to {', '.join(sent_recipients)}"))
            else:
                for r in recipients:
                    write_email_log_csv("System Periodic Report", f"{interval_str} PDF Report", r, f"FAIL: {err}")
                self.log_audit("SCHEDULED_REPORT_EMAIL", "EMAIL", f"SMTP connection failed: {err}", status="FAILED")
                self.root.after(0, lambda: self.log(f"SCHEDULED PDF EMAIL FAILED: {err}"))
        except Exception as ex:
            self.log_audit("SCHEDULED_REPORT_EMAIL", "EMAIL", str(ex), status="FAILED")
            self.root.after(0, lambda: self.log(f"Scheduled PDF email dispatch failed: {ex}"))

    # -------------------------
    # VIEW: VIEW DATA
    # -------------------------
    def record_viewed_records(self, dev, timestamps):
        if not timestamps:
            return
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        username = self.current_user or "SYSTEM"
        workstation = platform.node()
        ip, port, slave = dev['ip'], dev['port'], dev.get('slave_id', 1)
        for timestamp in timestamps:
            key = f"{ip}:{port}:{slave}_{timestamp}"
            entry = self.viewed_records.setdefault(key, {
                "device_name": dev.get("device_name", ip),
                "device_ip": ip,
                "port": port,
                "slave_id": slave,
                "timestamp": timestamp,
                "first_viewed_by": username,
                "first_viewed_at": now_text,
                "views": []
            })
            entry["last_viewed_by"] = username
            entry["last_viewed_at"] = now_text
            entry.setdefault("views", []).append({
                "username": username,
                "viewed_at": now_text,
                "workstation": workstation
            })
        save_viewed_records(self.viewed_records)
        self.log_audit(
            "DATA_VIEWED", "DATA", f"Viewed {len(timestamps)} records for '{dev.get('device_name', ip)}'",
            device_info=f"{workstation}; device={ip}:{port}; slave={slave}",
            updated_value=f"{timestamps[0]} to {timestamps[-1]}"
        )

    def show_view_data(self):
        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        if "view_data" not in user_perms:
            messagebox.showerror("Access Denied", "You do not have permission to access View Data.")
            self.navigate("live_data")
            return

        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True, padx=30, pady=20)

        # Top Control Card (Device, Date Range, Interval & Query)
        card = tk.Frame(layout, bg=self.COLORS["bg_surface"], padx=20, pady=15,
                        highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        card.pack(fill="x", pady=(0, 15))

        top_row = tk.Frame(card, bg=self.COLORS["bg_surface"])
        top_row.pack(fill="x", pady=5)

        # Device Combobox
        f_dev = tk.Frame(top_row, bg=self.COLORS["bg_surface"])
        f_dev.pack(side="left", padx=(0, 20))
        tk.Label(f_dev, text="SELECT DEVICE", font=("Segoe UI", 8, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        display_names = [d.get('device_name') if d.get('device_name') else f"{d['ip']} @ {d.get('slave_id', 1)}" for d in self.devices]
        self.view_node_cb = ttk.Combobox(f_dev, values=display_names, style="TCombobox", font=self.FONTS["body"], width=22)
        if display_names: self.view_node_cb.current(0)
        self.view_node_cb.pack(pady=(5, 0), ipady=4)

        # Date Selectors Helper
        dt_cnt = tk.Frame(top_row, bg=self.COLORS["bg_surface"])
        dt_cnt.pack(side="left", fill="x", expand=True)

        def dt_selector(parent, label, initial_dt):
            f = tk.Frame(parent, bg=self.COLORS["bg_surface"])
            f.pack(side="left", padx=10)
            tk.Label(f, text=label, font=("Segoe UI", 8, "bold"), 
                     bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
            box = tk.Frame(f, bg=self.COLORS["bg_dark"], padx=5, pady=3)
            box.pack(pady=(5, 0))
            
            years = [str(y) for y in range(2024, 2031)]
            months = [f"{i:02d}" for i in range(1, 13)]
            days = [f"{i:02d}" for i in range(1, 32)]
            hours = [f"{i:02d}" for i in range(0, 24)]
            mins = [f"{i:02d}" for i in range(0, 61)]
            
            sel = {}
            y_cb = ttk.Combobox(box, values=years, width=5, font=("Consolas", 9))
            y_cb.set(initial_dt.strftime("%Y"))
            y_cb.pack(side="left")
            tk.Label(box, text="-", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            m_cb = ttk.Combobox(box, values=months, width=3, font=("Consolas", 9))
            m_cb.set(initial_dt.strftime("%m"))
            m_cb.pack(side="left")
            tk.Label(box, text="-", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            d_cb = ttk.Combobox(box, values=days, width=3, font=("Consolas", 9))
            d_cb.set(initial_dt.strftime("%d"))
            d_cb.pack(side="left")
            
            tk.Label(box, text="  ", bg=self.COLORS["bg_dark"]).pack(side="left")
            h_cb = ttk.Combobox(box, values=hours, width=3, font=("Consolas", 9))
            h_cb.set(initial_dt.strftime("%H"))
            h_cb.pack(side="left")
            tk.Label(box, text=":", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            min_cb = ttk.Combobox(box, values=mins, width=3, font=("Consolas", 9))
            min_cb.set(initial_dt.strftime("%M"))
            min_cb.pack(side="left")
            
            sel['y'], sel['m'], sel['d'], sel['h'], sel['min'] = y_cb, m_cb, d_cb, h_cb, min_cb
            return sel

        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0)
        self.view_from_dt = dt_selector(dt_cnt, "FROM DATE & TIME", start_of_day)
        self.view_to_dt = dt_selector(dt_cnt, "TO DATE & TIME", now)

        # Interval and Action Buttons Row
        action_row = tk.Frame(card, bg=self.COLORS["bg_surface"])
        action_row.pack(fill="x", pady=(15, 0))

        f_int = tk.Frame(action_row, bg=self.COLORS["bg_surface"])
        f_int.pack(side="left")
        tk.Label(f_int, text="INTERVAL", font=("Segoe UI", 8, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(side="left", padx=(0, 5))
        self.view_interval_cb = ttk.Combobox(f_int, values=["Raw", "1 Min", "5 Min", "15 Min", "30 Min", "1 Hour"], width=10, style="TCombobox")
        self.view_interval_cb.set("Raw")
        self.view_interval_cb.pack(side="left", padx=5)

        tk.Button(action_row, text="🔍 LOAD DATA", bg=self.COLORS["accent"], fg="black", 
                  font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=6,
                  command=self.load_view_data_records).pack(side="left", padx=10)

        current_user_data = self.users.get(self.current_user, {})
        is_superadmin = (self.current_user == "superadmin") or current_user_data.get("is_superadmin", False)
        is_approver = current_user_data.get("is_approver", False)
        if is_approver or is_superadmin:
            tk.Button(action_row, text="✓ APPROVE DATA", bg=self.COLORS["success"], fg="white", 
                      font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=6,
                      command=self.submit_data_approval_from_view_data).pack(side="left", padx=10)

        if "export_data" in user_perms:
            tk.Button(action_row, text="📥 EXCEL EXPORT", bg=self.COLORS["bg_highlight"], fg="white", 
                      font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=6,
                      command=lambda: self.perform_export("excel")).pack(side="right", padx=5)

            tk.Button(action_row, text="📄 PDF EXPORT (DRAFT)", bg="#FF5722", fg="white", 
                      font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=6,
                      command=lambda: self.perform_export("pdf")).pack(side="right", padx=5)

        # Status Banner
        self.view_status_lbl = tk.Label(layout, text="APPROVAL STATUS: DRAFT / PENDING APPROVAL", 
                                        font=("Segoe UI", 10, "bold"), fg="#FF9800", bg=self.COLORS["bg_surface"],
                                        padx=15, pady=8, highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        self.view_status_lbl.pack(fill="x", pady=(0, 10))

        # Data Table
        table_frame = tk.Frame(layout, bg=self.COLORS["bg_surface"])
        table_frame.pack(fill="both", expand=True)

        # Register columns are configured for the selected device when LOAD DATA
        # is clicked. Up to eight readings are displayed as individual columns.
        initial_reg_names = []
        if self.devices:
            first_dev = self.devices[0]
            first_regs = sorted(first_dev.get("registers", []), key=lambda value: (int(''.join(filter(str.isdigit, str(value)))) if any(c.isdigit() for c in str(value)) else 0, str(value)))[:8]
            initial_reg_names = [first_dev.get("names", {}).get(str(reg), f"Reg {reg}") for reg in first_regs]
        register_columns = tuple(f"reg_{i}" for i in range(len(initial_reg_names)))
        self.view_tree = ttk.Treeview(table_frame, columns=("ts", "dev") + register_columns + ("status",), show="headings", style="Treeview")
        self.view_tree.heading("ts", text="TIMESTAMP")
        self.view_tree.heading("dev", text="DEVICE NAME")
        self.view_tree.heading("status", text="APPROVAL STATUS")

        self.view_tree.column("ts", width=160, anchor="center")
        self.view_tree.column("dev", width=160, anchor="w")
        self.view_tree.column("status", width=150, anchor="center")
        for i, reg_name in enumerate(initial_reg_names):
            col = f"reg_{i}"
            self.view_tree.heading(col, text=str(reg_name).upper())
            self.view_tree.column(col, width=125, minwidth=90, anchor="center")

        v_sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.view_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.view_tree.xview, style="Horizontal.TScrollbar")
        self.view_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)

        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.view_tree.pack(side="left", fill="both", expand=True)

        # Keep this view static until the operator explicitly clicks LOAD DATA.
        # This avoids silently changing the displayed approval/export snapshot.
        self.view_status_lbl.config(
            text="SELECT DEVICE / DATE RANGE, THEN CLICK LOAD DATA",
            fg=self.COLORS["text_dim"]
        )

    def submit_data_approval_from_view_data(self):
        current_user_data = self.users.get(self.current_user, {})
        is_superadmin = (self.current_user == "superadmin") or current_user_data.get("is_superadmin", False)
        if not is_superadmin and not current_user_data.get("is_approver", False):
            self.log_audit("DATA_APPROVAL_DENIED", "SECURITY", f"Unauthorized approval attempt by '{self.current_user}'")
            messagebox.showerror("Access Denied", "Only a designated Approver or Super Admin can approve data.")
            return

        entered_password = simpledialog.askstring(
            "Approval Authentication",
            f"Enter the password for '{self.current_user}' to approve this data:",
            show="*", parent=self.root
        )
        if entered_password is None:
            return
        if not verify_password(entered_password, current_user_data.get("password", "")):
            self.log_audit("DATA_APPROVAL_DENIED", "SECURITY", f"Incorrect approval password entered for '{self.current_user}'")
            messagebox.showerror("Authentication Failed", "Incorrect password. Data was not approved.")
            return

        idx = self.view_node_cb.current()
        if idx < 0 or idx >= len(self.devices):
            messagebox.showerror("Error", "Select a valid device.")
            return

        dev = self.devices[idx]
        ip, port, slave_id_part = dev['ip'], dev['port'], dev.get('slave_id', 1)
        dev_alias = dev.get('device_name', ip)

        try:
            get_dt = lambda s: datetime.strptime(f"{s['y'].get()}-{s['m'].get()}-{s['d'].get()} {s['h'].get()}:{s['min'].get()}:00", "%Y-%m-%d %H:%M:%S")
            from_dt = get_dt(self.view_from_dt)
            to_dt = get_dt(self.view_to_dt)
            if to_dt < from_dt:
                messagebox.showwarning("Date Range", "TO date/time must be after FROM date/time.")
                return
        except Exception as e:
            messagebox.showerror("Error", f"Invalid date selection: {e}")
            return

        loaded_context = getattr(self, "view_loaded_context", None)
        expected_device = f"{ip}:{port}:{slave_id_part}"
        if (not loaded_context or loaded_context.get("device") != expected_device or
                loaded_context.get("from") != from_dt.strftime("%Y-%m-%d %H:%M:%S") or
                loaded_context.get("to") != to_dt.strftime("%Y-%m-%d %H:%M:%S") or
                not loaded_context.get("timestamps")):
            messagebox.showwarning("View Required", "Load and review this exact device and date range before approving it.")
            return

        cur_d = from_dt.date()
        end_d = to_dt.date()
        approvals = load_approvals()

        count = 0
        while cur_d <= end_d:
            d_str = cur_d.strftime("%Y-%m-%d")
            app_key = f"{ip}:{port}:{slave_id_part}_{d_str}"
            entry = approvals.setdefault(app_key, {"ranges": []})
            # Convert/replace new entries without marking the whole date approved.
            entry.pop("status", None)
            ranges = entry.setdefault("ranges", [])
            range_record = {
                "from": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "to": to_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "approved_by": self.current_user,
                "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "approved_workstation": platform.node(),
                "record_count": len(loaded_context.get("timestamps", [])),
                "notes": f"Approved via View Data ({from_dt.strftime('%H:%M:%S')} to {to_dt.strftime('%H:%M:%S')})"
            }
            if not any(r.get("from") == range_record["from"] and r.get("to") == range_record["to"] for r in ranges):
                ranges.append(range_record)
            cur_d += timedelta(days=1)
            count += 1

        save_approvals(approvals)
        self.log_audit("DATA_APPROVAL", "APPROVE", f"Approved data for device '{dev_alias}' from {from_dt} to {to_dt}",
                       device_info=f"{platform.node()}; device={ip}:{port}; slave={slave_id_part}",
                       previous_value="VIEWED / DRAFT", updated_value="APPROVED")
        messagebox.showinfo("Success", f"Data for '{dev_alias}' ({from_dt.strftime('%Y-%m-%d')} to {to_dt.strftime('%Y-%m-%d')}) has been APPROVED and moved to 'Approve Data'!")
        self.load_view_data_records()

    def load_view_data_records(self):
        if not hasattr(self, 'view_tree'): return
        for i in self.view_tree.get_children():
            self.view_tree.delete(i)

        idx = self.view_node_cb.current()
        if idx < 0 or idx >= len(self.devices):
            return

        dev = self.devices[idx]
        ip, port, slave_id_part = dev['ip'], dev['port'], dev.get('slave_id', 1)
        dev_alias = dev.get('device_name', ip)
        dev_names = dev.get('names', {})
        dev_units = dev.get('units', {})
        dev_decimals = dev.get('decimals', {})

        # Build stable register columns in configured address order. Values for
        # each timestamp are placed below their corresponding register name.
        configured_regs = sorted(dev.get("registers", []), key=lambda value: (int(''.join(filter(str.isdigit, str(value)))) if any(c.isdigit() for c in str(value)) else 0, str(value)))[:8]
        configured_reg_keys = {str(reg) for reg in configured_regs}
        register_names = [dev_names.get(str(reg), f"Reg {reg}") for reg in configured_regs]
        register_columns = tuple(f"reg_{i}" for i in range(len(register_names)))
        self.view_tree.configure(columns=("ts", "dev") + register_columns + ("status",))
        self.view_tree.heading("ts", text="TIMESTAMP")
        self.view_tree.heading("dev", text="DEVICE NAME")
        self.view_tree.heading("status", text="APPROVAL STATUS")
        self.view_tree.column("ts", width=160, minwidth=145, anchor="center", stretch=False)
        self.view_tree.column("dev", width=140, minwidth=110, anchor="w", stretch=False)
        self.view_tree.column("status", width=130, minwidth=105, anchor="center", stretch=False)
        for i, reg_name in enumerate(register_names):
            col = f"reg_{i}"
            self.view_tree.heading(col, text=str(reg_name).upper())
            self.view_tree.column(col, width=125, minwidth=90, anchor="center", stretch=True)

        try:
            get_dt = lambda s: datetime.strptime(f"{s['y'].get()}-{s['m'].get()}-{s['d'].get()} {s['h'].get()}:{s['min'].get()}:00", "%Y-%m-%d %H:%M:%S")
            from_dt = get_dt(self.view_from_dt)
            to_dt = get_dt(self.view_to_dt)
        except Exception as e:
            messagebox.showerror("Error", f"Invalid date selection: {e}")
            return

        approvals = load_approvals()

        # Read CSV logs
        prefix = f"{ip.replace('.', '_')}_{port}_s{slave_id_part}"
        live_dir = os.path.join(DATA_PATH, "live")
        all_files = []
        if os.path.exists(live_dir):
            for f in os.listdir(live_dir):
                if f.startswith(prefix) and f.endswith(".csv"):
                    all_files.append(os.path.join(live_dir, f))

        pivoted_data = {}
        interval_str = self.view_interval_cb.get()
        interval_map = {"1 Min": 60, "5 Min": 300, "15 Min": 900, "30 Min": 1800, "1 Hour": 3600}
        interval_sec = interval_map.get(interval_str, 0)

        skipped_approved_count = 0

        for source_file in all_files:
            try:
                with open(source_file, 'r', encoding='utf-8', errors='replace') as f_in:
                    reader = csv.reader((line.replace('\0', '') for line in f_in))
                    next(reader, None)
                    for row in reader:
                        try:
                            row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                            if from_dt <= row_dt <= to_dt:
                                row_date_str = row_dt.strftime("%Y-%m-%d")
                                row_app_key = f"{ip}:{port}:{slave_id_part}_{row_date_str}"
                                
                                # EXCLUDE APPROVED DATA FROM VIEW DATA AREA!
                                if is_timestamp_approved(approvals, ip, port, slave_id_part, row_dt):
                                    skipped_approved_count += 1
                                    continue

                                ts = row[0]
                                if interval_sec > 0:
                                    rounded_ts = int(row_dt.timestamp() // interval_sec * interval_sec)
                                    ts = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                                reg_addr = str(row[4])
                                if reg_addr not in configured_reg_keys:
                                    continue
                                reg_name = dev_names.get(reg_addr, f"Reg {reg_addr}")
                                unit = dev_units.get(reg_addr, "")
                                if ts not in pivoted_data: pivoted_data[ts] = {}
                                
                                is_lora = ("LORA" in str(dev.get('node_type', '')).upper()) or ("LORA" in str(dev.get('device_name', '')).upper())
                                val_str = str(row[5]).strip()
                                is_valid = True
                                try:
                                    f_val = float(val_str)
                                    if math.isnan(f_val) or math.isinf(f_val):
                                        is_valid = False
                                    elif is_lora and f_val <= 0:
                                        is_valid = False
                                except Exception:
                                    if val_str.lower() in ["nan", "none", "null", ""]:
                                        is_valid = False

                                if not is_valid:
                                    continue

                                if dev.get('types', {}).get(reg_addr) == "BOOLEAN":
                                    label_0 = dev.get('ops', {}).get(reg_addr, "OFF")
                                    label_1 = dev.get('factors', {}).get(reg_addr, "ON")
                                    try: val_str = label_0 if int(float(val_str)) == 0 else label_1
                                    except: pass
                                else:
                                    try:
                                        prec = int(dev_decimals.get(reg_addr, 3))
                                        val_str = f"{float(val_str):.{prec}f}"
                                    except: pass
                                
                                pivoted_data[ts][reg_name] = f"{val_str}{unit}"
                        except: continue
            except: continue

        inserted_count = 0
        if pivoted_data:
            visible_timestamps = []
            for ts in sorted(pivoted_data.keys()):
                reading_values = [pivoted_data[ts].get(name, "-") for name in register_names]
                if all(v == "-" or v == "" for v in reading_values):
                    continue
                visible_timestamps.append(ts)
                self.view_tree.insert("", "end", values=(ts, dev_alias, *reading_values, "VIEWED / DRAFT"))
                inserted_count += 1
            self.view_loaded_context = {
                "device": f"{ip}:{port}:{slave_id_part}",
                "from": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "to": to_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamps": list(visible_timestamps)
            }
            self.record_viewed_records(dev, visible_timestamps)

        if inserted_count > 0:
            self.view_status_lbl.config(
                text="VIEW DATA AREA: DRAFT / UNAPPROVED RECORDS ONLY ⚠️ (Select range and click 'APPROVE DATA' to move data to Approve Data)",
                fg="#FF9800"
            )
        else:
            if skipped_approved_count > 0:
                self.view_status_lbl.config(
                    text="NOTICE: All data for this selection has been APPROVED and stored in 'Approve Data' section.",
                    fg=self.COLORS["success"]
                )
            else:
                self.view_status_lbl.config(
                    text="NO UNAPPROVED DRAFT LOGS FOUND FOR THIS RANGE",
                    fg=self.COLORS["text_dim"]
                )

    # -------------------------
    # VIEW: APPROVE DATA (EXCLUSIVELY FOR APPROVED DATA & EXPORT)
    # -------------------------
    def show_approve_data(self):
        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        if "approve_data" not in user_perms:
            messagebox.showerror("Access Denied", "You do not have permission to access Approve Data.")
            self.navigate("live_data")
            return

        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True, padx=30, pady=20)

        # Top Control Card (Device, Date Range, Interval & Export Options)
        card = tk.Frame(layout, bg=self.COLORS["bg_surface"], padx=20, pady=15,
                        highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        card.pack(fill="x", pady=(0, 15))

        top_row = tk.Frame(card, bg=self.COLORS["bg_surface"])
        top_row.pack(fill="x", pady=5)

        # Device Combobox
        f_dev = tk.Frame(top_row, bg=self.COLORS["bg_surface"])
        f_dev.pack(side="left", padx=(0, 20))
        tk.Label(f_dev, text="SELECT DEVICE", font=("Segoe UI", 8, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        display_names = [d.get('device_name') if d.get('device_name') else f"{d['ip']} @ {d.get('slave_id', 1)}" for d in self.devices]
        self.app_node_cb = ttk.Combobox(f_dev, values=display_names, style="TCombobox", font=self.FONTS["body"], width=22)
        if display_names: self.app_node_cb.current(0)
        self.app_node_cb.pack(pady=(5, 0), ipady=4)

        # Date Selectors Helper
        dt_cnt = tk.Frame(top_row, bg=self.COLORS["bg_surface"])
        dt_cnt.pack(side="left", fill="x", expand=True)

        def dt_selector(parent, label, initial_dt):
            f = tk.Frame(parent, bg=self.COLORS["bg_surface"])
            f.pack(side="left", padx=10)
            tk.Label(f, text=label, font=("Segoe UI", 8, "bold"), 
                     bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
            box = tk.Frame(f, bg=self.COLORS["bg_dark"], padx=5, pady=3)
            box.pack(pady=(5, 0))
            
            years = [str(y) for y in range(2024, 2031)]
            months = [f"{i:02d}" for i in range(1, 13)]
            days = [f"{i:02d}" for i in range(1, 32)]
            hours = [f"{i:02d}" for i in range(0, 24)]
            mins = [f"{i:02d}" for i in range(0, 61)]
            
            sel = {}
            y_cb = ttk.Combobox(box, values=years, width=5, font=("Consolas", 9))
            y_cb.set(initial_dt.strftime("%Y"))
            y_cb.pack(side="left")
            tk.Label(box, text="-", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            m_cb = ttk.Combobox(box, values=months, width=3, font=("Consolas", 9))
            m_cb.set(initial_dt.strftime("%m"))
            m_cb.pack(side="left")
            tk.Label(box, text="-", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            d_cb = ttk.Combobox(box, values=days, width=3, font=("Consolas", 9))
            d_cb.set(initial_dt.strftime("%d"))
            d_cb.pack(side="left")
            
            tk.Label(box, text="  ", bg=self.COLORS["bg_dark"]).pack(side="left")
            h_cb = ttk.Combobox(box, values=hours, width=3, font=("Consolas", 9))
            h_cb.set(initial_dt.strftime("%H"))
            h_cb.pack(side="left")
            tk.Label(box, text=":", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            min_cb = ttk.Combobox(box, values=mins, width=3, font=("Consolas", 9))
            min_cb.set(initial_dt.strftime("%M"))
            min_cb.pack(side="left")
            
            sel['y'], sel['m'], sel['d'], sel['h'], sel['min'] = y_cb, m_cb, d_cb, h_cb, min_cb
            return sel

        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0)
        self.app_from_dt = dt_selector(dt_cnt, "FROM DATE & TIME", start_of_day)
        self.app_to_dt = dt_selector(dt_cnt, "TO DATE & TIME", now)

        # Interval and Export Buttons Row (ONLY EXPORT OPTIONS!)
        action_row = tk.Frame(card, bg=self.COLORS["bg_surface"])
        action_row.pack(fill="x", pady=(15, 0))

        f_int = tk.Frame(action_row, bg=self.COLORS["bg_surface"])
        f_int.pack(side="left")
        tk.Label(f_int, text="INTERVAL", font=("Segoe UI", 8, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(side="left", padx=(0, 5))
        self.app_interval_cb = ttk.Combobox(f_int, values=["Raw", "1 Min", "5 Min", "15 Min", "30 Min", "1 Hour"], width=10, style="TCombobox")
        self.app_interval_cb.set("Raw")
        self.app_interval_cb.pack(side="left", padx=5)

        tk.Button(action_row, text="🔍 LOAD APPROVED DATA", bg=self.COLORS["accent"], fg="black", 
                  font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=6,
                  command=self.load_approve_data_records).pack(side="left", padx=15)

        # EXPORT OPTIONS FOR APPROVED DATA ONLY
        if "export_data" in user_perms:
            tk.Button(action_row, text="📥 EXPORT TO EXCEL", bg=self.COLORS["success"], fg="white", 
                      font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=6,
                      command=lambda: self.perform_export_from_approved("excel")).pack(side="right", padx=5)

            tk.Button(action_row, text="📄 EXPORT TO PDF", bg="#0288D1", fg="white", 
                      font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15, pady=6,
                      command=lambda: self.perform_export_from_approved("pdf")).pack(side="right", padx=5)

        # Status Banner
        self.app_status_lbl = tk.Label(layout, text="APPROVED DATA STORED AREA ✓", 
                                       font=("Segoe UI", 10, "bold"), fg=self.COLORS["success"], bg=self.COLORS["bg_surface"],
                                       padx=15, pady=8, highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        self.app_status_lbl.pack(fill="x", pady=(0, 10))

        # Approved Data Table
        table_frame = tk.Frame(layout, bg=self.COLORS["bg_surface"])
        table_frame.pack(fill="both", expand=True)

        initial_reg_names = []
        if self.devices:
            first_dev = self.devices[0]
            first_regs = sorted(first_dev.get("registers", []), key=lambda value: (int(''.join(filter(str.isdigit, str(value)))) if any(c.isdigit() for c in str(value)) else 0, str(value)))[:8]
            initial_reg_names = [first_dev.get("names", {}).get(str(reg), f"Reg {reg}") for reg in first_regs]
        register_columns = tuple(f"reg_{i}" for i in range(len(initial_reg_names)))
        self.app_tree = ttk.Treeview(table_frame, columns=("ts", "dev") + register_columns + ("status",), show="headings", style="Treeview")
        self.app_tree.heading("ts", text="TIMESTAMP")
        self.app_tree.heading("dev", text="DEVICE NAME")
        self.app_tree.heading("status", text="APPROVAL STATUS")

        self.app_tree.column("ts", width=160, anchor="center")
        self.app_tree.column("dev", width=160, anchor="w")
        self.app_tree.column("status", width=150, anchor="center")
        for i, reg_name in enumerate(initial_reg_names):
            col = f"reg_{i}"
            self.app_tree.heading(col, text=str(reg_name).upper())
            self.app_tree.column(col, width=125, minwidth=90, anchor="center")

        v_sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.app_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.app_tree.xview, style="Horizontal.TScrollbar")
        self.app_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)

        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.app_tree.pack(side="left", fill="both", expand=True)

        self.load_approve_data_records()

    def load_approve_data_records(self):
        if not hasattr(self, 'app_tree'): return
        for i in self.app_tree.get_children():
            self.app_tree.delete(i)

        idx = self.app_node_cb.current()
        if idx < 0 or idx >= len(self.devices): return

        dev = self.devices[idx]
        ip, port, slave_id_part = dev['ip'], dev['port'], dev.get('slave_id', 1)
        dev_alias = dev.get('device_name', ip)
        dev_names = dev.get('names', {})
        dev_units = dev.get('units', {})
        dev_decimals = dev.get('decimals', {})

        configured_regs = sorted(dev.get("registers", []), key=lambda value: (int(''.join(filter(str.isdigit, str(value)))) if any(c.isdigit() for c in str(value)) else 0, str(value)))[:8]
        configured_reg_keys = {str(reg) for reg in configured_regs}
        register_names = [dev_names.get(str(reg), f"Reg {reg}") for reg in configured_regs]
        register_columns = tuple(f"reg_{i}" for i in range(len(register_names)))
        self.app_tree.configure(columns=("ts", "dev") + register_columns + ("status",))
        self.app_tree.heading("ts", text="TIMESTAMP")
        self.app_tree.heading("dev", text="DEVICE NAME")
        self.app_tree.heading("status", text="APPROVAL STATUS")
        self.app_tree.column("ts", width=160, minwidth=145, anchor="center", stretch=False)
        self.app_tree.column("dev", width=140, minwidth=110, anchor="w", stretch=False)
        self.app_tree.column("status", width=130, minwidth=105, anchor="center", stretch=False)
        for i, reg_name in enumerate(register_names):
            col = f"reg_{i}"
            self.app_tree.heading(col, text=str(reg_name).upper())
            self.app_tree.column(col, width=125, minwidth=90, anchor="center", stretch=True)

        try:
            get_dt = lambda s: datetime.strptime(f"{s['y'].get()}-{s['m'].get()}-{s['d'].get()} {s['h'].get()}:{s['min'].get()}:00", "%Y-%m-%d %H:%M:%S")
            from_dt = get_dt(self.app_from_dt)
            to_dt = get_dt(self.app_to_dt)
        except Exception as e:
            messagebox.showerror("Error", f"Invalid date selection: {e}")
            return

        approvals = load_approvals()

        prefix = f"{ip.replace('.', '_')}_{port}_s{slave_id_part}"
        live_dir = os.path.join(DATA_PATH, "live")
        all_files = []
        if os.path.exists(live_dir):
            for f in os.listdir(live_dir):
                if f.startswith(prefix) and f.endswith(".csv"):
                    all_files.append(os.path.join(live_dir, f))

        pivoted_data = {}
        interval_str = self.app_interval_cb.get()
        interval_map = {"1 Min": 60, "5 Min": 300, "15 Min": 900, "30 Min": 1800, "1 Hour": 3600}
        interval_sec = interval_map.get(interval_str, 0)

        for source_file in all_files:
            try:
                with open(source_file, 'r', encoding='utf-8', errors='replace') as f_in:
                    reader = csv.reader((line.replace('\0', '') for line in f_in))
                    next(reader, None)
                    for row in reader:
                        try:
                            row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                            if from_dt <= row_dt <= to_dt:
                                row_date_str = row_dt.strftime("%Y-%m-%d")
                                row_app_key = f"{ip}:{port}:{slave_id_part}_{row_date_str}"
                                
                                # INCLUDE ONLY APPROVED DATA IN APPROVE DATA AREA!
                                if not is_timestamp_approved(approvals, ip, port, slave_id_part, row_dt):
                                    continue

                                ts = row[0]
                                if interval_sec > 0:
                                    rounded_ts = int(row_dt.timestamp() // interval_sec * interval_sec)
                                    ts = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                                reg_addr = str(row[4])
                                if reg_addr not in configured_reg_keys:
                                    continue
                                reg_name = dev_names.get(reg_addr, f"Reg {reg_addr}")
                                unit = dev_units.get(reg_addr, "")
                                if ts not in pivoted_data: pivoted_data[ts] = {}
                                
                                is_lora = ("LORA" in str(dev.get('node_type', '')).upper()) or ("LORA" in str(dev.get('device_name', '')).upper())
                                val_str = str(row[5]).strip()
                                is_valid = True
                                try:
                                    f_val = float(val_str)
                                    if math.isnan(f_val) or math.isinf(f_val):
                                        is_valid = False
                                    elif is_lora and f_val <= 0:
                                        is_valid = False
                                except Exception:
                                    if val_str.lower() in ["nan", "none", "null", ""]:
                                        is_valid = False

                                if not is_valid:
                                    continue

                                if dev.get('types', {}).get(reg_addr) == "BOOLEAN":
                                    label_0 = dev.get('ops', {}).get(reg_addr, "OFF")
                                    label_1 = dev.get('factors', {}).get(reg_addr, "ON")
                                    try: val_str = label_0 if int(float(val_str)) == 0 else label_1
                                    except: pass
                                else:
                                    try:
                                        prec = int(dev_decimals.get(reg_addr, 3))
                                        val_str = f"{float(val_str):.{prec}f}"
                                    except: pass
                                
                                pivoted_data[ts][reg_name] = f"{val_str}{unit}"
                        except: continue
            except: continue

        inserted_count = 0
        if pivoted_data:
            for ts in sorted(pivoted_data.keys()):
                reading_values = [pivoted_data[ts].get(name, "-") for name in register_names]
                if all(v == "-" or v == "" for v in reading_values):
                    continue
                self.app_tree.insert("", "end", values=(ts, dev_alias, *reading_values, "APPROVED"))
                inserted_count += 1

        if inserted_count > 0:
            self.app_status_lbl.config(
                text="APPROVED DATA STORED AREA ✓ (OFFICIAL APPROVED LOGS)",
                fg=self.COLORS["success"]
            )
        else:
            self.app_status_lbl.config(
                text="NO APPROVED DATA FOUND FOR THIS RANGE. (Approve data from View Data screen to store data here).",
                fg=self.COLORS["text_dim"]
            )

    def perform_export_from_approved(self, fmt="excel"):
        # perform_export reads the active APPROVE DATA controls directly.
        self.perform_export(fmt)

    # -------------------------
    # VIEW: DATA EXPORT
    # -------------------------
    def show_export_data(self):
        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        if "export_data" not in user_perms:
            messagebox.showerror("Access Denied", "You do not have permission to access Data Export.")
            self.navigate("live_data")
            return
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True)

        card = tk.Frame(layout, bg=self.COLORS["bg_surface"], padx=50, pady=40,
                        highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        card.place(relx=0.5, rely=0.4, anchor="center")

        tk.Label(card, text="EXPORT HISTORICAL DATA", font=self.FONTS["h2"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["accent"]).pack(pady=(0, 30))

        # Device Selection
        tk.Label(card, text="SELECT DEVICE", font=("Segoe UI", 8, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        
        # Use Alias if available, else IP:Slave
        display_names = [d.get('device_name') if d.get('device_name') else f"{d['ip']} @ {d.get('slave_id', 1)}" for d in self.devices]
        self.export_node = ttk.Combobox(card, values=display_names, style="TCombobox", font=self.FONTS["body"])
        if display_names: self.export_node.current(0)
        self.export_node.pack(fill="x", pady=(5, 20), ipady=6)

        # Date/Time Selection
        dt_cnt = tk.Frame(card, bg=self.COLORS["bg_surface"])
        dt_cnt.pack(fill="x", pady=5)

        def dt_selector(label, initial_dt):
            f = tk.Frame(dt_cnt, bg=self.COLORS["bg_surface"])
            f.pack(side="left", fill="x", expand=True, padx=5)
            tk.Label(f, text=label, font=("Segoe UI", 8, "bold"), 
                     bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
            
            box = tk.Frame(f, bg=self.COLORS["bg_dark"], padx=5, pady=5)
            box.pack(fill="x", pady=(5, 0))
            
            years = [str(y) for y in range(2024, 2031)]
            months = [f"{i:02d}" for i in range(1, 13)]
            days = [f"{i:02d}" for i in range(1, 32)]
            hours = [f"{i:02d}" for i in range(0, 24)]
            mins = [f"{i:02d}" for i in range(0, 61)]
            
            sel = {}
            # Layout: YYYY - MM - DD
            y_cb = ttk.Combobox(box, values=years, width=5, font=("Consolas", 9))
            y_cb.set(initial_dt.strftime("%Y"))
            y_cb.pack(side="left")
            tk.Label(box, text="-", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            m_cb = ttk.Combobox(box, values=months, width=3, font=("Consolas", 9))
            m_cb.set(initial_dt.strftime("%m"))
            m_cb.pack(side="left")
            tk.Label(box, text="-", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            d_cb = ttk.Combobox(box, values=days, width=3, font=("Consolas", 9))
            d_cb.set(initial_dt.strftime("%d"))
            d_cb.pack(side="left")
            
            # Layout: HH : MM
            tk.Label(box, text="  ", bg=self.COLORS["bg_dark"]).pack(side="left")
            h_cb = ttk.Combobox(box, values=hours, width=3, font=("Consolas", 9))
            h_cb.set(initial_dt.strftime("%H"))
            h_cb.pack(side="left")
            tk.Label(box, text=":", bg=self.COLORS["bg_dark"], fg="white").pack(side="left")
            min_cb = ttk.Combobox(box, values=mins, width=3, font=("Consolas", 9))
            min_cb.set(initial_dt.strftime("%M"))
            min_cb.pack(side="left")
            
            sel['y'], sel['m'], sel['d'], sel['h'], sel['min'] = y_cb, m_cb, d_cb, h_cb, min_cb
            return sel

        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0)
        
        self.export_from_selectors = dt_selector("FROM DATE & TIME", start_of_day)
        self.export_to_selectors = dt_selector("TO DATE & TIME", now)

        row_extra = tk.Frame(card, bg=self.COLORS["bg_surface"])
        row_extra.pack(fill="x", pady=(20, 0))

        tk.Label(row_extra, text="PDF TYPE", font=("Segoe UI", 8, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(side="left", padx=(10, 5))
        self.export_pdf_type = ttk.Combobox(row_extra, values=["Line Graph", "Bar Chart"], width=12, style="TCombobox")
        self.export_pdf_type.set("Line Graph")
        self.export_pdf_type.pack(side="left", padx=5)

        tk.Label(row_extra, text="TIME INTERVAL", font=("Segoe UI", 8, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(side="left", padx=(20, 5))
        self.export_interval_cb = ttk.Combobox(row_extra, values=["Raw", "1 Min", "5 Min", "15 Min", "30 Min", "1 Hour"], width=10, style="TCombobox")
        self.export_interval_cb.set("Raw")
        self.export_interval_cb.pack(side="left", padx=5)

        btn_frame = tk.Frame(card, bg=self.COLORS["bg_surface"])
        btn_frame.pack(fill="x", pady=(30, 0))

        tk.Button(btn_frame, text="EXPORT TO EXCEL", bg=self.COLORS["accent"], fg="black", 
                        font=self.FONTS["button"], relief="flat", cursor="hand2", 
                        command=lambda: self.perform_export("excel")).pack(side="left", fill="x", expand=True, padx=5, ipady=12)

        tk.Button(btn_frame, text="EXPORT TO PDF", bg="#FF5722", fg="white", 
                        font=self.FONTS["button"], relief="flat", cursor="hand2", 
                        command=lambda: self.perform_export("pdf")).pack(side="left", fill="x", expand=True, padx=5, ipady=12)

        tk.Label(card, text=f"Excel files are generated as CSV. PDF limit: {MAX_PDF_REGISTERS} registers and {MAX_PDF_RECORDS:,} records per report.",
                 font=("Segoe UI", 7), bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(pady=15)

    @storage_operation
    def perform_export(self, fmt="excel"):
        # The same exporter is available from both DATA EXPORT and VIEW DATA.
        # Use the controls belonging to the currently displayed screen; widgets
        # from the other screen are destroyed during navigation.
        exporting_from_view_data = (self.active_view == "view_data")
        exporting_from_approved = (self.active_view == "approve_data")
        if exporting_from_view_data:
            node_selector = getattr(self, "view_node_cb", None)
            from_selectors = getattr(self, "view_from_dt", None)
            to_selectors = getattr(self, "view_to_dt", None)
            interval_selector = getattr(self, "view_interval_cb", None)
        elif exporting_from_approved:
            node_selector = getattr(self, "app_node_cb", None)
            from_selectors = getattr(self, "app_from_dt", None)
            to_selectors = getattr(self, "app_to_dt", None)
            interval_selector = getattr(self, "app_interval_cb", None)
        else:
            node_selector = getattr(self, "export_node", None)
            from_selectors = getattr(self, "export_from_selectors", None)
            to_selectors = getattr(self, "export_to_selectors", None)
            interval_selector = getattr(self, "export_interval_cb", None)

        if node_selector is None or from_selectors is None or to_selectors is None:
            messagebox.showerror("Export Error", "Export controls are not available. Please reopen the page and try again.")
            return

        idx = node_selector.current()
        if idx < 0 or idx >= len(self.devices):
            messagebox.showwarning("Selection", "Please select a device.")
            return
        
        dev = self.devices[idx]
        ip, port, slave_id_part = dev['ip'], dev['port'], dev.get('slave_id', 1)
        
        # Find all files for this device in the 'live' folder
        prefix = f"{ip.replace('.', '_')}_{port}_s{slave_id_part}"
        all_files = []
        live_dir = os.path.join(DATA_PATH, "live")
        if os.path.exists(live_dir):
            for f in os.listdir(live_dir):
                if f.startswith(prefix) and f.endswith(".csv"):
                    all_files.append(os.path.join(live_dir, f))
        
        if not all_files:
            d_name = dev.get('device_name', ip)
            messagebox.showerror("Error", f"No data found for {d_name}")
            return
            
        try:
            get_dt = lambda s: datetime.strptime(f"{s['y'].get()}-{s['m'].get()}-{s['d'].get()} {s['h'].get()}:{s['min'].get()}:00", "%Y-%m-%d %H:%M:%S")
            from_dt = get_dt(from_selectors)
            to_dt = get_dt(to_selectors)
            if to_dt < from_dt:
                messagebox.showwarning("Date Range", "TO date/time must be after FROM date/time.")
                return
        except Exception as e:
            messagebox.showerror("Error", f"Invalid date selection: {e}")
            return

        # Find device alias and names for lookup
        dev_alias = ip
        dev_names = {}
        dev_units = {}
        dev_decimals = {}
        for d in self.devices:
            if d['ip'] == ip and str(d['port']) == str(port) and str(d.get('slave_id', 1)) == str(slave_id_part):
                dev_alias = d.get('device_name', ip)
                dev_names = d.get('names', {})
                dev_units = d.get('units', {})
                dev_decimals = d.get('decimals', {})
                break

        # Sanitize dev_alias for filename (removes \ / : * ? " < > | and spaces)
        clean_alias = dev_alias
        for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|', ' ']:
            clean_alias = clean_alias.replace(char, '_')

        # Prepare Register Headers (Sorted by address)
        sorted_regs = sorted(dev_names.keys(), key=lambda value: (int(''.join(filter(str.isdigit, str(value)))) if any(c.isdigit() for c in str(value)) else 0, str(value)))
        reg_headers = [dev_names.get(str(r), f"Reg {r}") for r in sorted_regs]
        
        # Select Export Folder
        export_dir = filedialog.askdirectory(title="Select Folder to Save Data", initialdir=DATA_PATH)
        if not export_dir: return

        if fmt == "excel":
            target_name = f"Export_{clean_alias}_{datetime.now().strftime('%H%M%S')}.csv"
            target_file = os.path.join(export_dir, target_name)
            
            # Pivot the reading data by timestamp
            pivoted_data = {} # {ts_string: {reg_name: val}}
            pivoted_approval = {}
            
            # Interval Logic
            interval_str = interval_selector.get() if interval_selector is not None else "Raw"
            interval_map = {"1 Min": 60, "5 Min": 300, "15 Min": 900, "30 Min": 1800, "1 Hour": 3600}
            interval_sec = interval_map.get(interval_str, 0)
            view_approvals = load_approvals()

            try:
                for source_file in all_files:
                    with open(source_file, 'r', encoding='utf-8', errors='replace') as f_in:
                        reader = csv.reader((line.replace('\0', '') for line in f_in))
                        next(reader, None) 
                        for row in reader:
                            try:
                                row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                                if from_dt <= row_dt <= to_dt:
                                    if exporting_from_view_data:
                                        row_key = f"{ip}:{port}:{slave_id_part}_{row_dt.strftime('%Y-%m-%d')}"
                                        if is_timestamp_approved(view_approvals, ip, port, slave_id_part, row_dt):
                                            continue
                                    elif exporting_from_approved:
                                        row_key = f"{ip}:{port}:{slave_id_part}_{row_dt.strftime('%Y-%m-%d')}"
                                        if not is_timestamp_approved(view_approvals, ip, port, slave_id_part, row_dt):
                                            continue
                                    ts = row[0]
                                    if interval_sec > 0:
                                        rounded_ts = int(row_dt.timestamp() // interval_sec * interval_sec)
                                        ts = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                                    raw_approved = is_timestamp_approved(view_approvals, ip, port, slave_id_part, row_dt)
                                    pivoted_approval[ts] = pivoted_approval.get(ts, True) and raw_approved
                                    
                                    reg_addr = str(row[4])
                                    is_lora = ("LORA" in str(dev.get('node_type', '')).upper()) or ("LORA" in str(dev.get('device_name', '')).upper())
                                    val_str = str(row[5]).strip()
                                    is_valid = True
                                    try:
                                        f_val = float(val_str)
                                        if math.isnan(f_val) or math.isinf(f_val):
                                            is_valid = False
                                        elif is_lora and f_val <= 0:
                                            is_valid = False
                                    except Exception:
                                        if val_str.lower() in ["nan", "none", "null", ""]:
                                            is_valid = False

                                    if not is_valid:
                                        continue

                                    reg_name = dev_names.get(reg_addr, f"Reg {reg_addr}")
                                    if ts not in pivoted_data: pivoted_data[ts] = {}
                                    if dev.get('types', {}).get(reg_addr) == "BOOLEAN":
                                        label_0 = dev.get('ops', {}).get(reg_addr, "OFF")
                                        label_1 = dev.get('factors', {}).get(reg_addr, "ON")
                                        try:
                                            pivoted_data[ts][reg_name] = label_0 if int(float(val_str)) == 0 else label_1
                                        except:
                                            pivoted_data[ts][reg_name] = val_str
                                    else:
                                        try:
                                            prec = int(dev_decimals.get(reg_addr, 3))
                                            pivoted_data[ts][reg_name] = f"{float(val_str):.{prec}f}"
                                        except:
                                            pivoted_data[ts][reg_name] = val_str
                            except: continue

                if pivoted_data:
                    with open(target_file, 'w', newline='') as f_out:
                        writer = csv.writer(f_out)
                        header = ["Timestamp", "Device Name"] + reg_headers + ["Approval Status"]
                        writer.writerow(header)
                        
                        # Sort by timestamp
                        for ts in sorted(pivoted_data.keys()):
                            row_vals = [ts, dev_alias]
                            for rname in reg_headers:
                                row_vals.append(pivoted_data[ts].get(rname, ""))
                            if all(v == "" or v == "-" for v in row_vals[2:]):
                                continue
                            row_vals.append("APPROVED" if pivoted_approval.get(ts, False) else "VIEWED / DRAFT")
                            writer.writerow(row_vals)
                    
                    messagebox.showinfo("Success", f"Data pivoted and exported to {target_file}")
                    approval_values = [pivoted_approval.get(ts, False) for ts in pivoted_data]
                    export_status = "APPROVED" if approval_values and all(approval_values) else "DRAFT / CONTAINS UNAPPROVED DATA"
                    self.log_audit("DATA_EXPORT", "EXPORT", f"Exported CSV data for device '{dev_alias}' ({len(pivoted_data)} records)",
                                   device_info=f"{platform.node()}; device={ip}:{port}; slave={slave_id_part}",
                                   updated_value=export_status)
                    webbrowser.open(target_file)
                else:
                    messagebox.showwarning("No Data", "No records found for the range.")
            except Exception as e: messagebox.showerror("Error", f"Excel Pivot Export failed: {e}")

        elif fmt == "pdf":
            if not HAS_REPORTLAB:
                messagebox.showerror("Error", "Feature unavailable: reportlab library not found.")
                return
            if len(sorted_regs) > MAX_PDF_REGISTERS:
                messagebox.showerror(
                    "PDF Register Limit",
                    f"A PDF report supports a maximum of {MAX_PDF_REGISTERS} registers. "
                    f"This device currently has {len(sorted_regs)} configured report registers."
                )
                return
            
            target_name = f"Report_{clean_alias}_{datetime.now().strftime('%H%M%S')}.pdf"
            target_file = os.path.join(export_dir, target_name)
            
            # Pivot the reading data for PDF
            pivoted_data = {} # {ts_string: {reg_name: val}}
            pivoted_approval = {}
            reg_names_only = [dev_names.get(str(r), f"Reg {r}") for r in sorted_regs]
            
            # Interval Logic
            interval_str = interval_selector.get() if interval_selector is not None else "Raw"
            interval_map = {"1 Min": 60, "5 Min": 300, "15 Min": 900, "30 Min": 1800, "1 Hour": 3600}
            interval_sec = interval_map.get(interval_str, 0)
            view_approvals = load_approvals()

            try:
                for source_file in all_files:
                    with open(source_file, 'r', encoding='utf-8', errors='replace') as f_in:
                        reader = csv.reader((line.replace('\0', '') for line in f_in))
                        next(reader, None) 
                        for row in reader:
                            try:
                                row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                                if from_dt <= row_dt <= to_dt:
                                    if exporting_from_view_data:
                                        row_key = f"{ip}:{port}:{slave_id_part}_{row_dt.strftime('%Y-%m-%d')}"
                                        if is_timestamp_approved(view_approvals, ip, port, slave_id_part, row_dt):
                                            continue
                                    elif exporting_from_approved:
                                        row_key = f"{ip}:{port}:{slave_id_part}_{row_dt.strftime('%Y-%m-%d')}"
                                        if not is_timestamp_approved(view_approvals, ip, port, slave_id_part, row_dt):
                                            continue
                                    ts = row[0]
                                    if interval_sec > 0:
                                        rounded_ts = int(row_dt.timestamp() // interval_sec * interval_sec)
                                        ts = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                                    raw_approved = is_timestamp_approved(view_approvals, ip, port, slave_id_part, row_dt)
                                    pivoted_approval[ts] = pivoted_approval.get(ts, True) and raw_approved
                                    
                                    reg_addr = str(row[4])
                                    is_lora = ("LORA" in str(dev.get('node_type', '')).upper()) or ("LORA" in str(dev.get('device_name', '')).upper())
                                    val_str = str(row[5]).strip()
                                    is_valid = True
                                    try:
                                        f_val = float(val_str)
                                        if math.isnan(f_val) or math.isinf(f_val):
                                            is_valid = False
                                        elif is_lora and f_val <= 0:
                                            is_valid = False
                                    except Exception:
                                        if val_str.lower() in ["nan", "none", "null", ""]:
                                            is_valid = False

                                    if not is_valid:
                                        continue

                                    reg_name = dev_names.get(reg_addr, f"Reg {reg_addr}")
                                    if ts not in pivoted_data: pivoted_data[ts] = {}
                                    if dev.get('types', {}).get(reg_addr) == "BOOLEAN":
                                        label_0 = dev.get('ops', {}).get(reg_addr, "OFF")
                                        label_1 = dev.get('factors', {}).get(reg_addr, "ON")
                                        try:
                                            pivoted_data[ts][reg_name] = label_0 if int(float(val_str)) == 0 else label_1
                                        except:
                                            pivoted_data[ts][reg_name] = val_str
                                    else:
                                        try:
                                            prec = int(dev_decimals.get(reg_addr, 3))
                                            pivoted_data[ts][reg_name] = f"{float(val_str):.{prec}f}"
                                        except:
                                            pivoted_data[ts][reg_name] = val_str
                            except: continue

                if not pivoted_data:
                    messagebox.showwarning("No Data", "No records found for the range.")
                    return
                if len(pivoted_data) > MAX_PDF_RECORDS:
                    messagebox.showerror(
                        "PDF Record Limit",
                        f"A single PDF supports a maximum of {MAX_PDF_RECORDS:,} records. "
                        "Select a smaller date range or a larger time interval."
                    )
                    return

                # VIEW DATA uses the controlled data-log layout represented by
                # the supplied reference PDF.  Keep the graph report for the
                # separate DATA EXPORT screen.
                if exporting_from_view_data or exporting_from_approved:
                    self._generate_view_data_log_pdf(
                        target_file, dev, dev_alias, from_dt, to_dt,
                        pivoted_data, sorted_regs, reg_names_only, dev_units,
                        force_approved=exporting_from_approved
                    )
                    report_kind = "Approved Data" if exporting_from_approved else "View Data"
                    messagebox.showinfo("Success", f"{report_kind} PDF log generated: {target_file}")
                    self.log_audit("DATA_EXPORT", "EXPORT", f"Exported {report_kind} PDF log for device '{dev_alias}' ({len(pivoted_data)} records)",
                                   device_info=f"{platform.node()}; device={ip}:{port}; slave={slave_id_part}",
                                   updated_value="FINAL / APPROVED" if exporting_from_approved else "DRAFT")
                    webbrowser.open(target_file)
                    return

                # A report is final only when every included system record is
                # inside an approved range. Mixed reports remain clearly DRAFT.
                approval_flags = [pivoted_approval.get(timestamp, False) for timestamp in pivoted_data.keys()]
                is_approved = bool(approval_flags) and all(approval_flags)

                # Generate Optimized PDF
                c = canvas.Canvas(target_file, pagesize=letter)
                w, h = letter
                
                def draw_draft_watermark(canvas_obj):
                    if not is_approved:
                        canvas_obj.saveState()
                        canvas_obj.setFont("Helvetica-Bold", 64)
                        canvas_obj.setFillColorRGB(0.88, 0.88, 0.88)
                        canvas_obj.translate(w / 2.0, h / 2.0)
                        canvas_obj.rotate(45)
                        canvas_obj.drawCentredString(0, 0, "DRAFT")
                        canvas_obj.restoreState()

                def draw_report_header(canvas_obj, page_num):
                    # Background
                    canvas_obj.setFillColorRGB(1, 1, 1); canvas_obj.rect(0, 0, w, h, fill=1)
                    
                    # DRAFT Diagonal Watermark (only if not approved)
                    draw_draft_watermark(canvas_obj)

                    # Title
                    canvas_obj.setFillColorRGB(0.01, 0.35, 0.73); canvas_obj.setFont("Helvetica-Bold", 18)
                    canvas_obj.drawString(40, h-50, self.company_details.get("company_name", "TempTrek Environmental Monitoring System"))
                    
                    # Header Stamp Badge (Top Right)
                    if is_approved:
                        canvas_obj.setFillColorRGB(0.15, 0.65, 0.25)
                        canvas_obj.rect(w - 150, h - 55, 110, 24, fill=1, stroke=0)
                        canvas_obj.setFillColorRGB(1, 1, 1)
                        canvas_obj.setFont("Helvetica-Bold", 11)
                        canvas_obj.drawCentredString(w - 95, h - 48, "APPROVED ✓")
                    else:
                        canvas_obj.setFillColorRGB(0.9, 0.25, 0.15)
                        canvas_obj.rect(w - 140, h - 55, 100, 24, fill=1, stroke=0)
                        canvas_obj.setFillColorRGB(1, 1, 1)
                        canvas_obj.setFont("Helvetica-Bold", 12)
                        canvas_obj.drawCentredString(w - 90, h - 48, "DRAFT")

                    # Metadata
                    canvas_obj.setFont("Helvetica", 9); canvas_obj.setFillColorRGB(0.5, 0.5, 0.5)
                    canvas_obj.drawString(40, h-70, f"DEVICE: {dev_alias} | GENERATED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    canvas_obj.drawString(40, h-85, f"RANGE: {from_dt} TO {to_dt}")
                    canvas_obj.line(40, h-95, w-40, h-95)
                    canvas_obj.setFont("Helvetica", 8); canvas_obj.drawRightString(w-40, 20, f"Page {page_num}")

                page = 1
                draw_report_header(c, page)
                
                # --- DRAW GRAPH PREVIEW (Only on Page 1) ---
                sorted_ts = sorted(pivoted_data.keys())
                all_series = []
                for rname in reg_names_only:
                    series = []
                    for ts in sorted_ts:
                        val = pivoted_data[ts].get(rname, 0)
                        try: series.append(float(val))
                        except: series.append(0)
                    all_series.append((rname, series))

                # Graph dimensions
                gw, gh = w - 80, 220
                gx, gy = 40, h - 360
                
                c.setFillColorRGB(0.97, 0.98, 1.0) # Very light blue tint
                c.rect(gx, gy, gw, gh, fill=1, stroke=0)
                
                # Draw Axis
                c.setStrokeColorRGB(0.4, 0.4, 0.4); c.setLineWidth(0.5)
                c.line(gx+60, gy+40, gx+gw-150, gy+40)  # X-axis
                c.line(gx+60, gy+40, gx+60, gy+gh-30) # Y-axis
                
                if all_series:
                    # Global limits for scaling
                    vals = [v for s in all_series for v in s[1]]
                    glob_min, glob_max = min(vals) if vals else 0, max(vals) if vals else 100
                    if glob_min == glob_max: glob_min -= 5; glob_max += 5
                    v_range = glob_max - glob_min
                    max_len = len(sorted_ts)
                    
                    plot_w = gw - 60 - 160
                    plot_h = gh - 40 - 30
                    
                    # Y-Axis Labels & Grid
                    c.setFont("Helvetica", 7); c.setFillColorRGB(0.4, 0.4, 0.4)
                    for i in range(6):
                        tick_val = glob_min + (i * v_range / 5)
                        py = gy + 40 + (i * plot_h / 5)
                        c.setStrokeColorRGB(0.85, 0.85, 0.85)
                        c.line(gx+60, py, gx+60+plot_w, py)
                        prec = 2 # Fixed precision for PDF axis
                        c.drawString(gx+15, py-2, f"{tick_val:.{prec}f}")

                    # Draw Data (Line or Bar)
                    palette = ["#005CB8", "#28A745", "#D73A49", "#F29900", "#6F42C1"]
                    p_type_val = self.export_pdf_type.get() if hasattr(self, 'export_pdf_type') else "Line Graph"
                    
                    if p_type_val == "Bar Chart":
                        num_series = len(all_series)
                        group_w = plot_w / (max_len if max_len > 0 else 1)
                        bar_w = (group_w * 0.8) / (num_series if num_series > 0 else 1)
                        for idx, (rname, data) in enumerate(all_series):
                            color = palette[idx % len(palette)]
                            r_col, g_col, b_col = int(color[1:3], 16)/255.0, int(color[3:5], 16)/255.0, int(color[5:7], 16)/255.0
                            c.setFillColorRGB(r_col, g_col, b_col)
                            for i, v in enumerate(data):
                                bx = gx + 60 + (i * group_w) + (idx * bar_w) + (group_w * 0.1)
                                by_top = gy + 40 + ((v - glob_min) * plot_h / (v_range if v_range > 0 else 1))
                                by_bottom = gy + 40 + ((0 - glob_min) * plot_h / (v_range if v_range > 0 else 1)) if glob_min <= 0 else gy + 40
                                c.rect(bx, by_bottom, bar_w - 1, by_top - by_bottom, fill=1, stroke=0)
                    else:
                        for idx, (rname, data) in enumerate(all_series):
                            color = palette[idx % len(palette)]
                            r_col, g_col, b_col = int(color[1:3], 16)/255.0, int(color[3:5], 16)/255.0, int(color[5:7], 16)/255.0
                            c.setStrokeColorRGB(r_col, g_col, b_col); c.setLineWidth(1.5)
                            p = c.beginPath()
                            for i, v in enumerate(data):
                                px = gx + 60 + (i * plot_w / (max_len-1 if max_len > 1 else 1))
                                py = gy + 40 + ((v - glob_min) * plot_h / (v_range if v_range > 0 else 1))
                                if i == 0: p.moveTo(px, py)
                                else: p.lineTo(px, py)
                            c.drawPath(p)
                    
                    # Legend
                    for idx, (rname, data) in enumerate(all_series[:10]):
                        color = palette[idx % len(palette)]
                        r_col, g_col, b_col = int(color[1:3], 16)/255.0, int(color[3:5], 16)/255.0, int(color[5:7], 16)/255.0
                        ly_leg = gy + gh - 50 - (idx * 15)
                        c.setFillColorRGB(r_col, g_col, b_col); c.rect(gx + gw - 140, ly_leg, 8, 8, fill=1, stroke=0)
                        c.setFillColorRGB(0.2, 0.2, 0.2); c.setFont("Helvetica-Bold", 8)
                        c.drawString(gx + gw - 125, ly_leg + 1, rname[:18].upper())

                # --- DATA TABLE ---
                y = gy - 40
                
                def draw_table_header(canvas_obj, y_pos):
                    canvas_obj.setFillColorRGB(0.1, 0.1, 0.1); canvas_obj.setFont("Helvetica-Bold", 10)
                    canvas_obj.drawString(40, y_pos, "TIMESTAMP")
                    canvas_obj.drawString(180, y_pos, "DEVICE NAME")
                    
                    x_start = 320
                    remaining_w = w - 40 - x_start
                    col_w = remaining_w / max(1, len(reg_names_only))
                    
                    for i, rname in enumerate(reg_names_only):
                        canvas_obj.drawString(x_start + (i*col_w), y_pos, rname[:12].upper())
                    
                    canvas_obj.setStrokeColorRGB(0.1, 0.1, 0.1); canvas_obj.setLineWidth(1)
                    canvas_obj.line(40, y_pos-5, w-40, y_pos-5)
                    return y_pos - 35

                y = draw_table_header(c, y)
                
                for ts in sorted_ts:
                    if y < 60:
                        c.showPage()
                        page += 1
                        draw_report_header(c, page)
                        y = h - 120
                        y = draw_table_header(c, y)
                    
                    c.setFillColorRGB(0.2, 0.2, 0.2); c.setFont("Helvetica", 9)
                    c.drawString(40, y, ts)
                    c.drawString(180, y, dev_alias[:20])
                    
                    x_start = 320
                    remaining_w = w - 40 - x_start
                    col_w = remaining_w / max(1, len(reg_names_only))
                    
                    for i, rname in enumerate(reg_names_only):
                        val_str = pivoted_data[ts].get(rname, "-")
                        addr = str(sorted_regs[i])
                        unit = dev_units.get(addr, "")
                        
                        # Alert highlight
                        is_alert = False
                        if val_str != "-":
                            try:
                                if dev.get('types', {}).get(addr) == "BOOLEAN":
                                    label_1 = dev.get('factors', {}).get(addr, "ON")
                                    v_float = 1.0 if val_str == label_1 else 0.0
                                else:
                                    v_float = float(val_str)
                                for alert in self.email_settings.get('alerts', []):
                                    if (str(alert.get('device_ip')) == str(ip) and 
                                        str(alert.get('slave_id', 1)) == str(slave_id_part) and 
                                        str(alert.get('reg')) == addr):
                                        cond = alert.get('condition')
                                        thresh = float(alert.get('threshold'))
                                        if (cond == ">" and v_float > thresh) or \
                                           (cond == "<" and v_float < thresh) or \
                                           (cond == "==" and v_float == thresh):
                                            is_alert = True; break
                            except: pass
                        
                        if is_alert:
                            c.setFillColorRGB(0.8, 0, 0); c.setFont("Helvetica-Bold", 9)
                        else:
                            c.setFillColorRGB(0.2, 0.2, 0.2); c.setFont("Helvetica", 9)
                            
                        display_text = f"{val_str}{unit}" if val_str != "-" else "-"
                        c.drawString(x_start + (i*col_w), y, display_text)
                    
                    # Row grid line
                    c.setStrokeColorRGB(0.9, 0.9, 0.9); c.setLineWidth(0.5)
                    c.line(40, y-5, w-40, y-5)
                    y -= 18

                c.save()
                messagebox.showinfo("Success", f"PDF report generated: {target_file}")
                self.log_audit("DATA_EXPORT", "EXPORT", f"Exported PDF report for device '{dev_alias}' ({len(sorted_ts)} records)",
                               device_info=f"{platform.node()}; device={ip}:{port}; slave={slave_id_part}",
                               updated_value="FINAL / APPROVED" if is_approved else "DRAFT")
                webbrowser.open(target_file)
            except Exception as e: messagebox.showerror("Error", f"PDF Export failed: {e}")

    @storage_operation
    def _generate_view_data_log_pdf(self, target_file, dev, dev_alias, from_dt, to_dt,
                                    pivoted_data, sorted_regs, reg_names_only, dev_units,
                                    force_approved=False):
        """Create the VIEW DATA report in the supplied controlled data-log style."""
        page_size = landscape((595.2756, 841.8898))  # A4 landscape
        c = canvas.Canvas(target_file, pagesize=page_size)
        w, h = page_size
        # Purge timestamps where all requested channels are missing / "-" / empty / invalid
        valid_ts = []
        for ts in sorted(pivoted_data.keys()):
            channel_vals = [pivoted_data[ts].get(name, "-") for name in reg_names_only]
            has_valid = False
            for v in channel_vals:
                v_str = str(v).strip().lower()
                if v_str not in ["-", "", "nan", "none", "null", "inf", "-inf"]:
                    has_valid = True
                    break
            if has_valid:
                valid_ts.append(ts)

        sorted_ts = valid_ts
        if not sorted_ts:
            messagebox.showwarning("No Data", "No valid records found for the range.")
            return

        rows_per_page = 14
        data_pages = max(1, (len(sorted_ts) + rows_per_page - 1) // rows_per_page)
        total_pages = 1 + data_pages
        printed_at = datetime.now()

        approvals = load_approvals()
        ip, port, slave = dev['ip'], dev['port'], dev.get('slave_id', 1)
        approved = force_approved
        day_cursor = from_dt.date()
        while not force_approved and day_cursor <= to_dt.date():
            key = f"{ip}:{port}:{slave}_{day_cursor.strftime('%Y-%m-%d')}"
            if approvals.get(key, {}).get("status") != "APPROVED":
                approved = False
                break
            day_cursor += timedelta(days=1)

        def box(x, y, bw, bh, fill=None, stroke=0.45):
            if fill is not None:
                c.setFillColorRGB(*fill)
                c.rect(x, y, bw, bh, fill=1, stroke=0)
            c.setStrokeColorRGB(stroke, stroke, stroke)
            c.setLineWidth(0.5)
            c.rect(x, y, bw, bh, fill=0, stroke=1)

        def text(x, y, value, bold=False, size=8, centered=False):
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            value = str(value)
            if centered:
                c.drawCentredString(x, y, value)
            else:
                c.drawString(x, y, value)

        def watermark():
            if approved:
                return
            c.saveState()
            c.setFillColorRGB(0.78, 0.78, 0.78)
            c.setFont("Helvetica-Bold", 50)
            c.translate(w / 2, h / 2)
            c.rotate(45)
            c.drawCentredString(0, 0, "DRAFT MODE")
            c.restoreState()

        def common_header(page_no):
            watermark()
            left, right = 25, w - 25
            top, header_bottom = h - 12, h - 140
            box(left, top - 66, right - left, 66)
            box(left, top - 66, 158, 66)
            box(right - 158, top - 66, 158, 66)
            comp_info = load_company_details()
            text(w / 2, top - 22, comp_info.get("company_name", "TempTrek Environmental Monitoring System"), True, 13, True)
            text(w / 2, top - 38, comp_info.get("department_name", "Environmental Monitoring Department"), True, 9, True)
            text(w / 2, top - 53, comp_info.get("report_title", "Temperature and Humidity Report"), False, 9, True)
            
            # Left side icon + Temp Trek logo
            icon_path = resource_path("icon.png")
            if not os.path.exists(icon_path):
                icon_path = resource_path("icon.ico")
            
            icon_drawn = False
            if os.path.exists(icon_path):
                try:
                    c.drawImage(icon_path, left + 8, top - 54, width=38, height=42, mask='auto', preserveAspectRatio=True)
                    icon_drawn = True
                except Exception:
                    pass

            left_text_x = (left + 48) if icon_drawn else (left + 10)
            left_text_w = 102 if icon_drawn else 138

            left_logo_path = None
            for candidate in [resource_path("temptrek_logo.png"), resource_path("left_logo.png")]:
                if os.path.exists(candidate) and candidate != icon_path:
                    left_logo_path = candidate
                    break

            logo_drawn = False
            if left_logo_path:
                try:
                    c.drawImage(left_logo_path, left_text_x, top - 56, width=left_text_w, height=46, mask='auto', preserveAspectRatio=True)
                    logo_drawn = True
                except Exception:
                    pass

            if not logo_drawn:
                sx = left_text_x + (5 if icon_drawn else 30)
                c.setFillColorRGB(0.0, 0.36, 0.72)
                c.setFont("Helvetica-Bold", 14)
                c.drawString(sx, top - 38, "Temp")
                c.setFillColorRGB(0.11, 0.12, 0.13)
                c.setFont("Helvetica", 14)
                c.drawString(sx + 38, top - 38, "Trek")

            # Right side logo option (draws uploaded logo image, or fallback DATA LOG text)
            right_logo_path = None
            for candidate in [os.path.join(DATA_PATH, "logo.png"), os.path.join(DATA_PATH, "config", "logo.png"), resource_path("logo.png"), resource_path("company_logo.png")]:
                if os.path.exists(candidate) and candidate != icon_path:
                    right_logo_path = candidate
                    break

            right_image_drawn = False
            if right_logo_path:
                try:
                    c.drawImage(right_logo_path, right - 148, top - 58, width=138, height=50, mask='auto', preserveAspectRatio=True)
                    right_image_drawn = True
                except Exception:
                    pass

            if not right_image_drawn:
                text(right - 79, top - 38, "DATA LOG", True, 12, True)

            meta_top = top - 74
            label_w, half_w = 142, (right - left) / 2
            rows = [
                ("Department", "Environmental Monitoring", "", ""),
                ("Filter From", from_dt.strftime("%d/%m/%Y %H:%M:%S"), "Filter To", to_dt.strftime("%d/%m/%Y %H:%M:%S")),
                ("Records From", sorted_ts[0] if sorted_ts else "-", "Records To", sorted_ts[-1] if sorted_ts else "-"),
                ("Equipment", str(dev_alias).upper(), "", ""),
            ]
            row_h = 14
            for i, (l1, v1, l2, v2) in enumerate(rows):
                yy = meta_top - ((i + 1) * row_h)
                box(left, yy, right-left, row_h)
                box(left, yy, label_w, row_h)
                if l2:
                    box(left + half_w, yy, 95, row_h)
                text(left + 3, yy + 4, l1, True, 8)
                text(left + label_w + 3, yy + 4, v1, False, 8)
                if l2:
                    text(left + half_w + 3, yy + 4, l2, True, 8)
                    text(left + half_w + 98, yy + 4, v2, False, 8)

            c.setStrokeColorRGB(0.35, 0.35, 0.35)
            c.line(left, 47, right, 47)
            channel_codes = []
            for i, name in enumerate(reg_names_only):
                unit = dev_units.get(str(sorted_regs[i]), "")
                channel_codes.append(f"{name} ({unit})" if unit else name)
            footer_channels = ", ".join(channel_codes)
            text(left + 2, 36, footer_channels[:145], False, 7)
            text(left + 2, 20, "TempTrek 1.0", False, 8)
            text(w / 2, 20, f"Printed By - {self.current_user} On {printed_at.strftime('%d/%m/%Y %H:%M:%S')}", False, 8, True)
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica", 8)
            c.drawRightString(right - 2, 20, f"Page {page_no} of {total_pages}")
            return header_bottom - 12

        # Page 1: Side-by-side MKT (Left Table) & MKRH (Right Table) Summary Report
        y = common_header(1)
        text(25, y, "CHANNEL SUMMARY REPORT", True, 10)
        y -= 16

        def _calc_mkt(val_list):
            if not val_list: return "-"
            try:
                DH_R = 10000.0
                sum_e = sum(math.exp(-DH_R / (v + 273.15)) for v in val_list)
                avg_e = sum_e / len(val_list)
                if avg_e <= 0: return "-"
                mkt_k = -DH_R / math.log(avg_e)
                return f"{(mkt_k - 273.15):.2f}"
            except:
                return "-"

        def _calc_mkrh(val_list):
            if not val_list: return "-"
            try:
                return f"{(sum(val_list)/len(val_list)):.2f}"
            except:
                return "-"

        mkt_channels = []
        mkrh_channels = []

        for i, name in enumerate(reg_names_only):
            unit = dev_units.get(str(sorted_regs[i]), "")
            reg_id_str = str(sorted_regs[i])

            vals = []
            for ts in sorted_ts:
                raw_v = pivoted_data[ts].get(name)
                if raw_v is not None and raw_v != "-":
                    try:
                        fv = float(raw_v)
                        if not (math.isnan(fv) or math.isinf(fv)):
                            vals.append(fv)
                    except (TypeError, ValueError):
                        pass

            minimum = f"{min(vals):.2f}" if vals else "-"
            maximum = f"{max(vals):.2f}" if vals else "-"
            average = f"{sum(vals)/len(vals):.2f}" if vals else "-"

            is_humidity = ("MKRH" in reg_id_str.upper() or 
                           unit.upper() in ["%", "%RH", "RH"] or 
                           "HUM" in name.upper() or 
                           "RH" in name.upper())

            chan_info = {
                "name": name,
                "unit": unit,
                "vals": vals,
                "min": minimum,
                "max": maximum,
                "avg": average,
            }

            if is_humidity:
                chan_info["code"] = f"RH{len(mkrh_channels)+1}"
                mkrh_channels.append(chan_info)
            else:
                chan_info["code"] = f"T{len(mkt_channels)+1}"
                mkt_channels.append(chan_info)

        left_x = [25, 171, 231, 291, 351]
        left_w = [146, 60, 60, 60, 60]
        right_x = [431, 577, 637, 697, 757]
        right_w = [146, 60, 60, 60, 60]
        left_headers = ["Channel Name", "Minimum", "Maximum", "Average", "MKT"]
        right_headers = ["Channel Name", "Minimum", "Maximum", "Average", "MKRH"]

        # Draw Headers
        for x, cw, label in zip(left_x, left_w, left_headers):
            box(x, y - 16, cw, 16, (0.83, 0.83, 0.83))
            text(x + 3, y - 11, label, True, 8)

        for x, cw, label in zip(right_x, right_w, right_headers):
            box(x, y - 16, cw, 16, (0.83, 0.83, 0.83))
            text(x + 3, y - 11, label, True, 8)

        y -= 16
        max_rows = max(len(mkt_channels), len(mkrh_channels), 1)

        for r in range(max_rows):
            # Left Table Row (MKT / Temperature)
            if r < len(mkt_channels):
                ch = mkt_channels[r]
                l_vals = [ch["name"], ch["min"], ch["max"], ch["avg"], _calc_mkt(ch["vals"])]
            else:
                l_vals = ["-", "-", "-", "-", "-"]

            for x, cw, val in zip(left_x, left_w, l_vals):
                box(x, y - 15, cw, 15)
                text(x + 3, y - 10, str(val)[:24], False, 8)

            # Right Table Row (MKRH / Humidity)
            if r < len(mkrh_channels):
                ch = mkrh_channels[r]
                r_vals = [ch["name"], ch["min"], ch["max"], ch["avg"], _calc_mkrh(ch["vals"])]
            else:
                r_vals = ["-", "-", "-", "-", "-"]

            for x, cw, val in zip(right_x, right_w, r_vals):
                box(x, y - 15, cw, 15)
                text(x + 3, y - 10, str(val)[:24], False, 8)

            y -= 15
            if y < 80:
                break

        c.showPage()

        # Remaining pages: bordered date/time/channel log table.
        usable_w = w - 50
        fixed = [42, 84, 52]
        channel_w = (usable_w - sum(fixed)) / max(1, len(reg_names_only))
        widths = fixed + [channel_w] * len(reg_names_only)
        headers = ["Sr. No.", "Log Date", "Time"] + [n[:12] for n in reg_names_only]

        for page_idx in range(data_pages):
            page_no = page_idx + 2
            y = common_header(page_no)
            x = 25
            for cw, label in zip(widths, headers):
                box(x, y - 25, cw, 25, (0.83, 0.83, 0.83))
                text(x + cw / 2, y - 16, label, True, 7, True)
                x += cw
            y -= 25
            batch = sorted_ts[page_idx * rows_per_page:(page_idx + 1) * rows_per_page]
            for row_offset, ts in enumerate(batch):
                try: dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except ValueError: dt = None
                row_cell_values = []
                for name in reg_names_only:
                    v = pivoted_data[ts].get(name, "-")
                    if str(v).strip().lower() in ["nan", "none", "null", "", "inf", "-inf"]:
                        v = "-"
                    else:
                        try:
                            fv = float(v)
                            if math.isnan(fv) or math.isinf(fv):
                                v = "-"
                        except (ValueError, TypeError):
                            pass
                    row_cell_values.append(v)

                values = [
                    str(page_idx * rows_per_page + row_offset + 1),
                    dt.strftime("%d/%m/%Y") if dt else ts[:10],
                    dt.strftime("%H:%M:%S") if dt else ts[11:19],
                ] + row_cell_values
                x = 25
                for cw, value in zip(widths, values):
                    box(x, y - 21, cw, 21)
                    text(x + cw / 2, y - 14, str(value)[:14], False, 7, True)
                    x += cw
                y -= 21
            if page_idx < data_pages - 1:
                c.showPage()
        c.save()

    def add_reg_to_mini(self):
        raw_addr = self.entry_reg_addr.get().strip()
        prefix = self.entry_reg_addr_prefix.get().strip() if hasattr(self, 'entry_reg_addr_prefix') else "null"
        name = self.entry_reg_name.get().strip()
        unit = self.entry_reg_unit.get().strip()
        dtype = self.entry_reg_type.get().strip() or "FLOAT32"
        reg_mode = self.entry_reg_mode.get().strip() or "Input Register"
        if dtype == "BOOLEAN":
            unit = "na"
            bool0 = self.entry_bool0.get().strip() or "OFF"
            bool1 = self.entry_bool1.get().strip() or "ON"
            math_str = f"0={bool0} / 1={bool1}"
        else:
            math_op = self.entry_reg_op.get().strip() or "/"
            math_factor = self.entry_reg_factor.get().strip() or "1"
            math_str = f"{math_op} {math_factor}"
        dec_prec = self.entry_reg_prec.get().strip() or "2"
        
        if prefix in ["MKT", "MKRH"]:
            addr = f"{prefix}:{raw_addr}" if raw_addr else prefix
        else:
            addr = raw_addr
            
        if not addr: return
        try:
            sel = self.mini_tree.selection()
            
            # If name is empty, try to get from defaults
            if not name:
                count = len(self.mini_tree.get_children())
                if sel: count = self.mini_tree.index(sel[0])
                
                if count < len(self.DEFAULT_REGISTER_NAMES):
                    name = self.DEFAULT_REGISTER_NAMES[count]
                    if not unit and dtype != "BOOLEAN": unit = self.DEFAULT_REGISTER_UNITS[count]
                    if dtype == "FLOAT32": dtype = self.DEFAULT_REGISTER_TYPES[count]
                    if dec_prec == "2": dec_prec = self.DEFAULT_REGISTER_DECIMALS[count]

            if dtype == "BOOLEAN":
                unit = "na"

            if sel:
                self.mini_tree.item(sel[0], values=(addr, name, unit, dtype, reg_mode, math_str, dec_prec))
                self.mini_tree.selection_remove(sel[0])
                self.btn_add_reg_main.config(text="+ ADD REGISTER", bg=self.COLORS["bg_highlight"])
            else:
                self.mini_tree.insert("", "end", values=(addr, name, unit, dtype, reg_mode, math_str, dec_prec))
            
            if hasattr(self, 'entry_reg_addr_prefix'): self.entry_reg_addr_prefix.set("null")
            self.entry_reg_addr.delete(0, tk.END)
            self.entry_reg_name.set("")
            self.entry_reg_unit.set("")
            self.entry_reg_type.set("FLOAT32")
            self.entry_reg_mode.set("Input Register")
            self.entry_reg_op.set("/")
            self.entry_reg_factor.delete(0, tk.END)
            self.entry_reg_factor.insert(0, "1")
            self.entry_reg_prec.set("3")
            self.entry_reg_addr.focus()
        except Exception as err: messagebox.showerror("Error", f"Invalid Address: {err}")

    def on_mini_select(self, event):
        sel = self.mini_tree.selection()
        if not sel:
            if hasattr(self, 'entry_reg_addr_prefix'): self.entry_reg_addr_prefix.set("null")
            self.entry_reg_addr.delete(0, tk.END)
            self.entry_reg_name.set("")
            self.entry_reg_unit.set("")
            self.entry_reg_type.set("FLOAT32")
            self.entry_reg_mode.set("Input Register")
            self.entry_reg_op.set("/")
            self.entry_reg_factor.delete(0, tk.END)
            self.entry_reg_factor.insert(0, "1")
            self.entry_reg_prec.set("3")
            self.btn_add_reg_main.config(text="+ ADD REGISTER", bg=self.COLORS["bg_highlight"])
            return
        item = self.mini_tree.item(sel[0])
        vals = item['values']
        addr_val = str(vals[0]) if len(vals) > 0 else ""
        name = vals[1] if len(vals) > 1 else ""
        
        prefix = "null"
        clean_addr = addr_val
        if addr_val.startswith("MKT:") or addr_val == "MKT":
            prefix = "MKT"
            clean_addr = addr_val.replace("MKT:", "").replace("MKT", "")
        elif addr_val.startswith("MKRH:") or addr_val == "MKRH":
            prefix = "MKRH"
            clean_addr = addr_val.replace("MKRH:", "").replace("MKRH", "")
            
        if hasattr(self, 'entry_reg_addr_prefix'): self.entry_reg_addr_prefix.set(prefix)
        self.entry_reg_addr.delete(0, tk.END)
        self.entry_reg_addr.insert(0, clean_addr)
        unit = vals[2] if len(vals) > 2 else ""
        dtype = vals[3] if len(vals) > 3 else "FLOAT32"
        reg_mode = vals[4] if len(vals) > 4 else "Input Register"
        code_map = {
            "FC01": "Output Coil",
            "FC02": "Input Coil",
            "FC03": "Holding Register",
            "FC04": "Input Register"
        }
        reg_mode = code_map.get(reg_mode, reg_mode)
        math_str = vals[5] if len(vals) > 5 else "/ 1"
        dec_prec = vals[6] if len(vals) > 6 else "2"
        
        self.entry_reg_name.set(name)
        self.entry_reg_unit.set(unit)
        self.entry_reg_type.set(dtype)
        self.entry_reg_mode.set(reg_mode)
        
        if dtype == "BOOLEAN":
            try:
                parts = str(math_str).split(" / ")
                b0 = parts[0].split("=")[-1]
                b1 = parts[1].split("=")[-1]
            except:
                b0, b1 = "OFF", "ON"
            self.entry_bool0.delete(0, tk.END)
            self.entry_bool0.insert(0, b0)
            self.entry_bool1.delete(0, tk.END)
            self.entry_bool1.insert(0, b1)
            self.f_bool_labels.pack(fill="x", pady=5, after=self.map_frame)
        else:
            if " " in str(math_str):
                op, fact = str(math_str).split(" ")
                self.entry_reg_op.set(op)
                self.entry_reg_factor.delete(0, tk.END)
                self.entry_reg_factor.insert(0, fact)
            else:
                self.entry_reg_op.set("/")
                self.entry_reg_factor.delete(0, tk.END)
                self.entry_reg_factor.insert(0, "1")
            self.f_bool_labels.pack_forget()
        self.entry_reg_prec.set(dec_prec)
        self.btn_add_reg_main.config(text="UPDATE REGISTER", bg=self.COLORS["warning"])

    def remove_mini_reg(self):
        sel = self.mini_tree.selection()
        if not sel: return
        if messagebox.askyesno("Confirm", "Are you sure you want to remove the selected register mapping?"):
            for s in sel: self.mini_tree.delete(s)
            self.on_mini_select(None)

    def refresh_device_list(self):
        if not self.node_tree or not self.node_tree.winfo_exists(): return
        for item in self.node_tree.get_children(): self.node_tree.delete(item)
        
        global_idx = 1
        for dev_idx, d in enumerate(self.devices):
            ip, port, slave = d['ip'], d['port'], d.get('slave_id', 1)
            addr_str = f"{ip}:{port}"
            key = f"{ip}:{port}:{slave}"
            
            names = d.get('names', {})
            units = d.get('units', {})
            types = d.get('types', {})
            modes = d.get('modes', {})
            ops = d.get('ops', {})
            facts = d.get('factors', {})
            divs = d.get('divisors', {})
            decimals = d.get('decimals', {})
            
            # Status check
            is_running = key in self.device_threads and self.device_threads[key].is_alive()
            status_text = "▶  ACTIVE" if is_running else "■  INACTIVE"
            status_tag = "online" if is_running else "offline"
            
            # Row coloring toggle by device
            row_tag = "even" if dev_idx % 2 == 0 else "odd"

            for i, reg in enumerate(d['registers']):
                name = names.get(str(reg))
                if not name and i < len(self.DEFAULT_REGISTER_NAMES):
                    name = self.DEFAULT_REGISTER_NAMES[i]
                
                dtype = types.get(str(reg), "FLOAT32")
                unit = units.get(str(reg), "")
                if dtype == "BOOLEAN":
                    unit = "na"
                elif not unit and i < len(self.DEFAULT_REGISTER_UNITS):
                    unit = self.DEFAULT_REGISTER_UNITS[i]
                
                mode_code = modes.get(str(reg), "FC04")
                code_map = {
                    "FC01": "Output Coil",
                    "FC02": "Input Coil",
                    "FC03": "Holding Register",
                    "FC04": "Input Register"
                }
                mode = code_map.get(mode_code, "Input Register")
                
                op = ops.get(str(reg), "/")
                fact = facts.get(str(reg), "1")
                if dtype == "BOOLEAN":
                    math_str = f"0={op} / 1={fact}"
                else:
                    math_str = f"{op} {fact}"
                
                dec_prec = decimals.get(str(reg), "3")
                
                # Legacy check
                if str(reg) in divs and str(reg) not in ops:
                    math_str = f"/ {divs[str(reg)]}"
                
                # Show Alias if available, else IP:Port
                d_alias = d.get('device_name', '')
                display_device = d_alias.upper() if d_alias else addr_str

                self.node_tree.insert("", "end", values=(
                    f"{global_idx:02d}",
                    display_device if i == 0 else "", 
                    d.get('slave_id', 1) if i == 0 else "",
                    str(name).upper() if name else f"REG {reg}",
                    reg,
                    unit,
                    dtype,
                    math_str,
                    mode,
                    dec_prec,
                    status_text if i == 0 else ""
                ), tags=(row_tag, status_tag, f"dev_idx_{dev_idx}"))
                global_idx += 1

    def add_device_submit(self):
        d_name = self.entry_device_name.get().strip()
        node_type_val = self.entry_node_type.get().strip() if hasattr(self, 'entry_node_type') else "Standard / PLC Node"
        ip, port = self.entry_ip.get().strip(), self.entry_port.get().strip()
        slave_id_raw = self.entry_slave_id.get().strip()
        poll_interval_raw = self.entry_poll_interval.get().strip()
        mini_items = self.mini_tree.get_children()
        
        try:
            poll_interval = float(poll_interval_raw)
            if poll_interval <= 0:
                raise ValueError()
        except:
            poll_interval = 1.0

        if not ip or not port or not mini_items:
            messagebox.showerror("Error", "IP, Port and at least one Register required")
            return
            
        try:
            p = int(port)
            
            # Parse Slave IDs (support "1", "1-31", "1,2,5")
            slave_ids = []
            if "-" in slave_id_raw:
                try:
                    start, end = map(int, slave_id_raw.split("-"))
                    slave_ids = list(range(start, end + 1))
                except: slave_ids = [1]
            elif "," in slave_id_raw:
                try:
                    slave_ids = [int(x.strip()) for x in slave_id_raw.split(",") if x.strip()]
                except: slave_ids = [1]
            else:
                slave_ids = [int(slave_id_raw)] if slave_id_raw else [1]

            # Common Register Data
            rl, rn, ru, rt, rm, rop, rf, rdec = [], {}, {}, {}, {}, {}, {}, {}
            for item in mini_items:
                vals = self.mini_tree.item(item)['values']
                addr, name, unit, dtype, reg_mode_ui, math_str, dec_prec = str(vals[0]), vals[1], vals[2], vals[3], str(vals[4]), str(vals[5]), str(vals[6])
                rl.append(addr)
                if dtype == "BOOLEAN":
                    unit = "na"
                if name: rn[str(addr)] = name
                if unit: ru[str(addr)] = unit
                if dtype: rt[str(addr)] = dtype
                
                # Map user friendly mode back to FC code
                mode_map = {
                    "Output Coil": "FC01",
                    "Input Coil": "FC02",
                    "Holding Register": "FC03",
                    "Input Register": "FC04"
                }
                rm[str(addr)] = mode_map.get(reg_mode_ui, reg_mode_ui)
                rdec[str(addr)] = dec_prec
                if dtype == "BOOLEAN":
                    try:
                        parts = str(math_str).split(" / ")
                        b0 = parts[0].split("=")[-1]
                        b1 = parts[1].split("=")[-1]
                    except:
                        b0, b1 = "OFF", "ON"
                    rop[str(addr)] = b0
                    rf[str(addr)] = b1
                else:
                    if " " in math_str:
                        op_val, fact_val = math_str.split(" ")
                        rop[str(addr)] = op_val
                        rf[str(addr)] = fact_val
                    else:
                        rop[str(addr)] = "/"
                        rf[str(addr)] = "1"
            
            audit_logs = []
            # Add/Update nodes for each slave ID
            for i, sid in enumerate(slave_ids):
                # If name is given and we have multiple slaves, append the slave ID to distinguish
                final_name = d_name
                if len(slave_ids) > 1 and d_name:
                    final_name = f"{d_name} S{sid}"
                
                dev = {
                    "device_name": final_name,
                    "node_type": node_type_val,
                    "ip": ip, 
                    "port": p, 
                    "slave_id": sid,
                    "poll_interval": poll_interval,
                    "registers": rl, 
                    "names": rn, 
                    "units": ru,
                    "types": rt,
                    "modes": rm,
                    "ops": rop,
                    "factors": rf,
                    "decimals": rdec
                }
                
                # If editing, we only update the first one or the specific index
                if self.edit_index is not None and i == 0:
                    if self.edit_index < len(self.devices):
                        old_dev = self.devices[self.edit_index]
                        diff_str = self.compute_device_diff(old_dev, dev)
                        audit_logs.append(("DEVICE_UPDATE", f"Updated device node '{final_name}': {diff_str}"))
                        self.devices[self.edit_index] = dev
                else:
                    # Check for duplicates before adding
                    exists = False
                    for existing in self.devices:
                        if existing['ip'] == ip and existing['port'] == p and existing['slave_id'] == sid:
                            exists = True
                            break
                    if not exists:
                        self.devices.append(dev)
                        code_map = {"FC01": "Output Coil", "FC02": "Input Coil", "FC03": "Holding Register", "FC04": "Input Register"}
                        reg_specs = []
                        for r in rl:
                            r_str = str(r)
                            n = rn.get(r_str, f"Reg {r}")
                            u = ru.get(r_str, "")
                            t = rt.get(r_str, "FLOAT32")
                            m = code_map.get(rm.get(r_str, "FC04"), "Input Register")
                            op = rop.get(r_str, "/")
                            f = rf.get(r_str, "1")
                            math_str = f"0={op}/1={f}" if t == "BOOLEAN" else f"{op}{f}"
                            d = rdec.get(r_str, "3")
                            reg_specs.append(f"Reg {r}[Name:'{n}', Unit:'{u}', Type:{t}, Mode:{m}, Math:{math_str}, Dec:{d}]")
                        reg_str = "; ".join(reg_specs) if reg_specs else "No registers"
                        audit_logs.append(("DEVICE_CREATE", f"Created device node '{final_name}' (IP: {ip}:{p}, Slave: {sid}, Poll: {poll_interval}s, Config: {reg_str})"))

            self.edit_index = None
            self.btn_add.config(text="SAVE NODE", bg=self.COLORS["accent"])
            save_devices(self.devices)
            for act, msg in audit_logs:
                self.log_audit(act, "DEVICE", msg)
            self.refresh_device_list()
            self.clear_add_device_form()
            messagebox.showinfo("Success", f"Added {len(slave_ids)} nodes successfully.")
        except Exception as e: 
            messagebox.showerror("Error", f"Invalid data: {e}")
            self.refresh_device_list()
            self.clear_add_device_form()
        except Exception as e: 
            messagebox.showerror("Error", f"Invalid data: {e}")

    def clear_add_device_form(self):
        """ Resets the entire form to 'Add' mode """
        self.edit_index = None
        self.entry_device_name.delete(0, tk.END)
        if hasattr(self, 'entry_node_type'):
            self.entry_node_type.set("Standard / PLC Node")
        self.entry_ip.delete(0, tk.END)
        self.entry_port.delete(0, tk.END)
        self.entry_port.insert(0, "502")
        self.entry_slave_id.delete(0, tk.END)
        self.entry_slave_id.insert(0, "1")
        self.entry_poll_interval.delete(0, tk.END)
        self.entry_poll_interval.insert(0, "1.0")
        
        self.entry_reg_addr.delete(0, tk.END)
        self.entry_reg_name.set("")
        self.entry_reg_unit.set("")
        self.entry_reg_type.set("FLOAT32")
        self.entry_reg_op.set("/")
        self.entry_reg_factor.delete(0, tk.END)
        self.entry_reg_factor.insert(0, "1")
        self.entry_reg_prec.set("3")
        if hasattr(self, 'entry_bool0'):
            self.entry_bool0.delete(0, tk.END)
            self.entry_bool0.insert(0, "OFF")
            self.entry_bool1.delete(0, tk.END)
            self.entry_bool1.insert(0, "ON")
            self.f_bool_labels.pack_forget()
        
        for item in self.mini_tree.get_children(): self.mini_tree.delete(item)
        self.btn_add.config(text="SAVE NODE", bg=self.COLORS["accent"])

    def view_all_device_details(self, event=None):
        sel = self.node_tree.selection()
        if not sel:
            messagebox.showinfo("View Details", "Please select a device node from the Stored Devices table to view full details.")
            return

        tags = self.node_tree.item(sel[0], "tags")
        dev_idx = None
        for t in tags:
            if t.startswith("dev_idx_"):
                dev_idx = int(t.split("_")[-1])
                break

        if dev_idx is None or dev_idx >= len(self.devices):
            return

        d = self.devices[dev_idx]
        ip, port, slave = d['ip'], d['port'], d.get('slave_id', 1)
        d_name = d.get('device_name', f"{ip}:{port}")
        key = f"{ip}:{port}:{slave}"
        is_running = key in self.device_threads and self.device_threads[key].is_alive()
        status_str = "ACTIVE" if is_running else "INACTIVE"

        top = tk.Toplevel(self.root)
        top.title(f"FULL NODE DETAILS - {d_name.upper()}")
        top.geometry("920x600")
        top.configure(bg=self.COLORS["bg_dark"])
        top.transient(self.root)
        top.grab_set()

        # Header Info Card
        hdr_frame = tk.Frame(top, bg=self.COLORS["bg_surface"], padx=20, pady=15, 
                             highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        hdr_frame.pack(fill="x", padx=20, pady=15)

        tk.Label(hdr_frame, text=f"NODE CONFIGURATION: {d_name.upper()}", font=("Segoe UI", 12, "bold"), 
                 fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(anchor="w", pady=(0, 10))

        info_grid = tk.Frame(hdr_frame, bg=self.COLORS["bg_surface"])
        info_grid.pack(fill="x")

        details = [
            ("IP Address:", f"{ip}"),
            ("Port:", f"{port}"),
            ("Slave ID:", f"{slave}"),
            ("Poll Interval:", f"{d.get('poll_interval', 1.0)} sec"),
            ("Node Type:", f"{d.get('node_type', 'Standard / PLC Node')}"),
            ("Status:", status_str),
            ("Total Registers:", f"{len(d.get('registers', []))}")
        ]

        for i, (k, v) in enumerate(details):
            col = i % 3
            row = i // 3
            cell = tk.Frame(info_grid, bg=self.COLORS["bg_surface"])
            cell.grid(row=row, column=col, sticky="w", padx=15, pady=4)
            tk.Label(cell, text=k, font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(0, 5))
            val_fg = self.COLORS["success"] if v == "ACTIVE" else (self.COLORS["danger"] if v == "INACTIVE" else self.COLORS["text_main"])
            tk.Label(cell, text=v, font=("Segoe UI", 9, "bold"), fg=val_fg, bg=self.COLORS["bg_surface"]).pack(side="left")

        # Register Mapping Table Header
        reg_title_frame = tk.Frame(top, bg=self.COLORS["bg_dark"])
        reg_title_frame.pack(fill="x", padx=20, pady=(10, 5))
        tk.Label(reg_title_frame, text="REGISTER MAPPING DETAILS", font=("Segoe UI", 10, "bold"), 
                 fg=self.COLORS["text_main"], bg=self.COLORS["bg_dark"]).pack(side="left")

        table_cnt = tk.Frame(top, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        table_cnt.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        cols = ("addr", "name", "unit", "type", "mode", "math", "prec")
        tree = ttk.Treeview(table_cnt, columns=cols, show="headings", style="Treeview")

        tree.heading("addr", text="ADDRESS")
        tree.heading("name", text="REGISTER NAME")
        tree.heading("unit", text="UNIT")
        tree.heading("type", text="DATA TYPE")
        tree.heading("mode", text="REGISTER MODE")
        tree.heading("math", text="MATH (OP/FACTOR)")
        tree.heading("prec", text="DECIMALS")

        tree.column("addr", width=110, anchor="center")
        tree.column("name", width=180, anchor="w")
        tree.column("unit", width=80, anchor="center")
        tree.column("type", width=120, anchor="center")
        tree.column("mode", width=160, anchor="w")
        tree.column("math", width=180, anchor="center")
        tree.column("prec", width=90, anchor="center")

        v_sb = ttk.Scrollbar(table_cnt, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(table_cnt, orient="horizontal", command=tree.xview, style="Horizontal.TScrollbar")
        tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)

        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        tree.pack(side="left", fill="both", expand=True)

        names = d.get('names', {})
        units = d.get('units', {})
        types = d.get('types', {})
        modes = d.get('modes', {})
        ops = d.get('ops', {})
        facts = d.get('factors', {})
        decimals = d.get('decimals', {})

        for i, reg in enumerate(d.get('registers', [])):
            nm = names.get(str(reg), "")
            if not nm and i < len(self.DEFAULT_REGISTER_NAMES): nm = self.DEFAULT_REGISTER_NAMES[i]
            un = units.get(str(reg), "")
            dt = types.get(str(reg), "FLOAT32")
            if dt == "BOOLEAN": un = "na"
            md_code = modes.get(str(reg), "FC04")
            code_map = {"FC01": "Output Coil", "FC02": "Input Coil", "FC03": "Holding Register", "FC04": "Input Register"}
            md = code_map.get(md_code, "Input Register")
            op = ops.get(str(reg), "/")
            fact = facts.get(str(reg), "1")
            math_str = f"0={op} / 1={fact}" if dt == "BOOLEAN" else f"{op} {fact}"
            dp = decimals.get(str(reg), "3")

            tree.insert("", "end", values=(reg, str(nm).upper() if nm else f"REG {reg}", un, dt, md, math_str, dp))

        # Close Button
        btn_bar = tk.Frame(top, bg=self.COLORS["bg_dark"], pady=10)
        btn_bar.pack(fill="x", padx=20)
        tk.Button(btn_bar, text="CLOSE", bg=self.COLORS["bg_highlight"], fg=self.COLORS["text_main"],
                  font=self.FONTS["button"], relief="flat", padx=25, pady=6, cursor="hand2", command=top.destroy).pack(side="right")

    def load_edit_device(self):
        sel = self.node_tree.selection()
        if not sel: return
        
        tags = self.node_tree.item(sel[0], "tags")
        dev_idx = None
        for t in tags:
            if t.startswith("dev_idx_"):
                dev_idx = int(t.split("_")[-1])
                break
        
        if dev_idx is None: return
        self.edit_index = dev_idx
        d = self.devices[dev_idx]
        
        self.entry_device_name.delete(0, tk.END); self.entry_device_name.insert(0, d.get('device_name', ''))
        if hasattr(self, 'entry_node_type'):
            self.entry_node_type.set(d.get('node_type', 'Standard / PLC Node'))
        self.entry_ip.delete(0, tk.END); self.entry_ip.insert(0, d['ip'])
        self.entry_port.delete(0, tk.END); self.entry_port.insert(0, str(d['port']))
        self.entry_slave_id.delete(0, tk.END); self.entry_slave_id.insert(0, str(d.get('slave_id', 1)))
        self.entry_poll_interval.delete(0, tk.END); self.entry_poll_interval.insert(0, str(d.get('poll_interval', '1.0')))
        
        # Clear and Fill Mini Map
        for item in self.mini_tree.get_children(): self.mini_tree.delete(item)
        names = d.get('names', {})
        units = d.get('units', {})
        types = d.get('types', {})
        decimals = d.get('decimals', {})
        for r in d['registers']:
            dtype = types.get(str(r), "FLOAT32")
            op = d.get('ops', {}).get(str(r), "/")
            factor = d.get('factors', {}).get(str(r), "1")
            if dtype == "BOOLEAN":
                math_str = f"0={op} / 1={factor}"
            else:
                math_str = f"{op} {factor}"
            
            # Legacy support
            if 'divisors' in d and str(r) in d['divisors'] and 'ops' not in d:
                div = d['divisors'][str(r)]
                math_str = f"/ {div}"

            mode_code = d.get('modes', {}).get(str(r), "FC04")
            code_map = {
                "FC01": "Output Coil",
                "FC02": "Input Coil",
                "FC03": "Holding Register",
                "FC04": "Input Register"
            }
            mode = code_map.get(mode_code, "Input Register")
            
            unit_val = units.get(str(r), "")
            if dtype == "BOOLEAN":
                unit_val = "na"

            self.mini_tree.insert("", "end", values=(
                r, 
                names.get(str(r), ""), 
                unit_val, 
                dtype,
                mode,
                math_str,
                decimals.get(str(r), "3")
            ))
            
        self.btn_add.config(text="UPDATE NODE", bg=self.COLORS["warning"])

    def logout_license(self):
        """ Clears activation and restarts to show activation screen """
        if messagebox.askyesno("Confirm License Reset", "This will deactivate the software on this PC. Are you sure?"):
            if os.path.exists(LICENSE_FILE):
                try:
                    os.remove(LICENSE_FILE)
                except: pass
            self.root.destroy()
            # Restart the app
            main()

    def remove_device_submit(self):
        sel = self.node_tree.selection()
        if not sel: return
        
        tags = self.node_tree.item(sel[0], "tags")
        dev_idx = None
        for t in tags:
            if t.startswith("dev_idx_"):
                dev_idx = int(t.split("_")[-1])
                break
        
        if dev_idx is not None:
            if messagebox.askyesno("Confirm", "Remove entire device configuration?"):
                removed_dev = self.devices.pop(dev_idx)
                dname = removed_dev.get('device_name', removed_dev.get('ip'))
                self.edit_index = None # Reset edit mode
                save_devices(self.devices)
                self.log_audit("DEVICE_DELETE", "DEVICE", f"Deleted device node '{dname}' (IP: {removed_dev.get('ip')}:{removed_dev.get('port', 502)}, Slave: {removed_dev.get('slave_id', 1)}, Regs: {removed_dev.get('registers', [])})")
                self.refresh_device_list()

    # -------------------------
    # VIEW: LIVE LOG
    # -------------------------
    def open_logs_folder(self):
        webbrowser.open(os.path.join(DATA_PATH, "live"))

    def open_email_alerts_log(self):
        self.navigate("email_alerts")

    def open_sent_email_logs(self):
        self.navigate("email_logs_view")

    def show_email_alerts_log(self):
        bar = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        bar.pack(side="top", fill="x", pady=10, padx=30)
        
        tk.Button(bar, text="⬅ BACK TO LOGS", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], font=self.FONTS["button"], relief="flat", padx=12, command=lambda: self.navigate("live_log")).pack(side="left", padx=5)
        
        date_str = datetime.now().strftime("%Y-%m-%d")
        tk.Label(bar, text=f"Alerts for: {date_str}", font=("Segoe UI", 10, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"]).pack(side="left", padx=20)
        
        tk.Button(bar, text="💾 EXPORT TO CSV", bg=self.COLORS["accent"], fg="black", font=self.FONTS["button"], relief="flat", padx=12, command=self.export_email_alerts_to_csv).pack(side="right", padx=5)

        cnt = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        cnt.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        cols = ("time", "device", "field", "reg", "val", "cond", "thresh")
        tree = ttk.Treeview(cnt, columns=cols, show="headings", style="Treeview")
        
        tree.heading("time", text="TIMESTAMP")
        tree.heading("device", text="DEVICE NAME")
        tree.heading("field", text="FIELD NAME")
        tree.heading("reg", text="ADDRESS")
        tree.heading("val", text="DATA READING")
        tree.heading("cond", text="CONDITION")
        tree.heading("thresh", text="THRESHOLD VALUE")
        
        tree.column("time", width=160, minwidth=140, anchor="center", stretch=False)
        tree.column("device", width=160, minwidth=130, anchor="w", stretch=True)
        tree.column("field", width=180, minwidth=150, anchor="w", stretch=True)
        tree.column("reg", width=100, minwidth=85, anchor="center", stretch=False)
        tree.column("val", width=130, minwidth=110, anchor="center", stretch=False)
        tree.column("cond", width=110, minwidth=95, anchor="center", stretch=False)
        tree.column("thresh", width=160, minwidth=140, anchor="center", stretch=False)
        
        v_sb = ttk.Scrollbar(cnt, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(cnt, orient="horizontal", command=tree.xview, style="Horizontal.TScrollbar")
        tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        tree.pack(side="left", fill="both", expand=True)
        self.alerts_live_tree = tree

        # Load Data
        fn = os.path.join(DATA_PATH, "live", f"Alarm_History_{date_str}.csv")
        if os.path.exists(fn):
            try:
                with open(fn, "r", encoding='utf-8', errors='replace') as f:
                    reader = csv.reader((line.replace('\0', '') for line in f))
                    header = next(reader, None) # Skip header
                    for row in reader:
                        if row: tree.insert("", "end", values=row)
            except Exception as e:
                tk.Label(cnt, text=f"Error reading log: {e}", fg="red").pack()
        else:
            tk.Label(cnt, text="No alarm history for today.", font=("Segoe UI", 12), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(pady=50)

    def export_email_alerts_to_csv(self):
        date_str = datetime.now().strftime("%Y-%m-%d")
        source_fn = os.path.join(DATA_PATH, "live", f"Alarm_History_{date_str}.csv")
        
        if not os.path.exists(source_fn):
            messagebox.showwarning("No Data", "No alarm history found to export.")
            return

        target_file = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=f"Alarm_History_{date_str}.csv",
            title="Export Alarm History"
        )
        
        if target_file:
            try:
                shutil.copy2(source_fn, target_file)
                messagebox.showinfo("Success", f"Alarm history exported to:\n{target_file}")
                webbrowser.open(target_file)
            except Exception as e:
                messagebox.showerror("Export Failed", f"Could not export file: {e}")

    def show_email_logs_history(self):
        bar = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        bar.pack(side="top", fill="x", pady=10, padx=30)
        
        tk.Button(bar, text="⬅ BACK TO LOGS", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], font=self.FONTS["button"], relief="flat", padx=12, command=lambda: self.navigate("live_log")).pack(side="left", padx=5)
        
        date_str = datetime.now().strftime("%Y-%m-%d")
        tk.Label(bar, text=f"Emails Sent: {date_str}", font=("Segoe UI", 10, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"]).pack(side="left", padx=20)
        
        tk.Button(bar, text="💾 EXPORT LOGS", bg=self.COLORS["success"], fg="white", font=self.FONTS["button"], relief="flat", padx=12, command=self.export_email_logs_to_csv).pack(side="right", padx=5)

        cnt = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        cnt.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        cols = ("time", "device", "field", "recipient", "status")
        tree = ttk.Treeview(cnt, columns=cols, show="headings", style="Treeview")
        
        tree.heading("time", text="TIMESTAMP")
        tree.heading("device", text="DEVICE NAME")
        tree.heading("field", text="FIELD NAME")
        tree.heading("recipient", text="RECIPIENT EMAIL")
        tree.heading("status", text="DELIVERY STATUS")
        
        tree.column("time", width=160, minwidth=140, anchor="center", stretch=False)
        tree.column("device", width=160, minwidth=130, anchor="w", stretch=True)
        tree.column("field", width=160, minwidth=130, anchor="w", stretch=True)
        tree.column("recipient", width=220, minwidth=180, anchor="w", stretch=False)
        tree.column("status", width=130, minwidth=100, anchor="center", stretch=False)
        
        v_sb = ttk.Scrollbar(cnt, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(cnt, orient="horizontal", command=tree.xview, style="Horizontal.TScrollbar")
        tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        tree.pack(side="left", fill="both", expand=True)
        self.email_logs_live_tree = tree

        fn = os.path.join(DATA_PATH, "live", f"Email_Logs_{date_str}.csv")
        if os.path.exists(fn):
            try:
                with open(fn, "r", encoding='utf-8', errors='replace') as f:
                    reader = csv.reader((line.replace('\0', '') for line in f))
                    header = next(reader, None)
                    for row in reader:
                        if row: tree.insert("", "end", values=row)
            except Exception as e:
                tk.Label(cnt, text=f"Error: {e}", fg="red").pack()
        else:
            tk.Label(cnt, text="No emails sent today.", font=("Segoe UI", 12), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(pady=50)

    def export_email_logs_to_csv(self):
        date_str = datetime.now().strftime("%Y-%m-%d")
        source_fn = os.path.join(DATA_PATH, "live", f"Email_Logs_{date_str}.csv")
        if not os.path.exists(source_fn): return
        target_file = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"Email_Logs_{date_str}.csv")
        if target_file:
            shutil.copy2(source_fn, target_file)
            messagebox.showinfo("Success", "Email logs exported.")
            webbrowser.open(target_file)

    def _append_alarm_live(self, row):
        if hasattr(self, 'alerts_live_tree') and self.alerts_live_tree.winfo_exists():
            self.alerts_live_tree.insert("", "end", values=row)
            self.alerts_live_tree.yview_moveto(1)

    def _append_email_log_live(self, row):
        if hasattr(self, 'email_logs_live_tree') and self.email_logs_live_tree.winfo_exists():
            self.email_logs_live_tree.insert("", "end", values=row)
            self.email_logs_live_tree.yview_moveto(1)

    @storage_operation
    def export_pdf(self):
        if not HAS_REPORTLAB: messagebox.showerror("Error", "reportlab not found"); return
        
        # Load All Data for Today from CSV files to ensure "All Data" is captured
        # even if the session just started.
        all_logs = []
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            live_dir = os.path.join(DATA_PATH, "live")
            if os.path.exists(live_dir):
                for f_name in os.listdir(live_dir):
                    # Look for daily log files: {ip}_{port}_s{slave}_{date}.csv
                    if date_str in f_name and f_name.endswith(".csv") and "Alarm" not in f_name and "Email" not in f_name:
                        full_path = os.path.join(live_dir, f_name)
                        try:
                            with open(full_path, 'r', encoding='utf-8', errors='replace') as f_in:
                                reader = csv.reader((line.replace('\0', '') for line in f_in))
                                header = next(reader, None)
                                for row in reader:
                                    # CSV row: [Timestamp, IP, Port, SlaveID, Register, Value]
                                    # Find device metadata for this row
                                    ip, port, slave = row[1], row[2], row[3]
                                    reg = row[4]
                                    val = row[5]
                                    ts = row[0].split(" ")[1] # Just time
                                    
                                    dev_alias = ip
                                    nm = f"REG {reg}"
                                    unit = ""
                                    dtype = "INT32"
                                    
                                    for d in self.devices:
                                        if d['ip'] == ip and str(d['port']) == str(port) and str(d.get('slave_id', 1)) == str(slave):
                                            dev_alias = d.get('device_name', ip)
                                            nm = d.get("names", {}).get(reg, f"Reg {reg}")
                                            unit = d.get("units", {}).get(reg, "")
                                            dtype = d.get("types", {}).get(reg, "INT32")
                                            break
                                            
                                    entry = ("---", str(dev_alias).upper(), ip, str(nm).upper(), reg, val, unit, dtype, ts)
                                    all_logs.append(entry)
                        except: continue
            
            # If CSVs are empty or not found, fallback to session log
            if not all_logs:
                all_logs = list(self.global_log)
            else:
                # Merge with session log for items not yet written to disk? 
                # (CSV is written immediately in write_csv, so duplicates might occur)
                # Let's just sort unified list by time
                all_logs.sort(key=lambda x: x[8])
        except Exception as e:
            print(f"History load failed: {e}")
            all_logs = list(self.global_log)

        if not all_logs:
            messagebox.showwarning("No Data", "No logs found for today to export.")
            return
        
        reports_dir = os.path.join(DATA_PATH, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        fn = f"DailyLog_Full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        target_file = os.path.join(reports_dir, fn)
        
        try:
            c = canvas.Canvas(target_file, pagesize=landscape(letter))
            w, h = landscape(letter)
            
            def draw_header(curr_page):
                c.setFillColorRGB(0.05, 0.08, 0.12); c.rect(0, 0, w, h, fill=1)
                c.setFillColorRGB(0, 0.7, 1.0); c.setFont("Helvetica-Bold", 20)
                c.drawString(40, h-45, self.company_details.get("company_name", "TempTrek Environmental Monitoring System"))
                c.setFillColorRGB(1, 1, 1); c.setFont("Helvetica", 10)
                c.drawString(40, h-65, f"COMPLETE DAILY LOG | Date: {datetime.now().strftime('%Y-%m-%d')} | Page: {curr_page}")
                c.setStrokeColorRGB(0, 0.7, 1.0); c.setLineWidth(1.5); c.line(40, h-75, w-40, h-75)
                
                c.setFont("Helvetica-Bold", 9); c.setFillColorRGB(0.85, 0.85, 0.85)
                yh = h - 95
                labels = [("S.NO", 40), ("DEVICE NAME", 85), ("IP", 210), ("FIELD NAME", 330), 
                          ("ADDR", 470), ("READING", 520), ("UNIT", 610), ("TYPE", 660), ("TIME", 725)]
                for lbl, lx in labels: c.drawString(lx, yh, lbl)
                c.setLineWidth(0.5); c.line(40, yh-5, w-40, yh-5)
                return yh - 22

            page_num = 1
            y = draw_header(page_num)
            
            for idx, e in enumerate(all_logs):
                # e = (old_sno, alias, ip, nm, reg, val, unit, dtype, ts)
                val_text = str(e[5])
                is_alert = any(k in val_text for k in ["SUCCESS:", "FAILURE:", "ALERT:"]) or "Error" in val_text
                
                c.setFillColorRGB(1, 0.2, 0.2) if is_alert else c.setFillColorRGB(0.95, 0.95, 0.95)
                c.setFont("Helvetica-Bold" if is_alert else "Helvetica", 8)
                
                c.drawString(40, y, f"{idx+1:03d}")
                c.drawString(85, y, str(e[1])[:22])
                c.drawString(210, y, str(e[2])[:22])
                c.drawString(330, y, str(e[3])[:28])
                c.drawString(470, y, str(e[4]))
                c.drawString(520, y, str(e[5])[:25])
                c.drawString(610, y, str(e[6]))
                c.drawString(660, y, str(e[7])[:15])
                c.drawString(725, y, str(e[8]))
                
                y -= 14
                if y < 45:
                    c.showPage()
                    page_num += 1
                    y = draw_header(page_num)
            
            c.save()
            self.log_audit("LOG_EXPORT", "EXPORT", f"Exported complete daily log PDF ({len(all_logs)} records)")
            messagebox.showinfo("Success", f"Full Daily Log Exported!\n\nLocation: {fn}")
            webbrowser.open(target_file)
        except Exception as ex:
            messagebox.showerror("Export Error", f"Failed to generate report: {ex}")

    def show_live_log(self):
        bar = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        bar.pack(side="top", fill="x", pady=10, padx=30)
        tk.Button(bar, text="📁 FOLDER", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], font=self.FONTS["button"], relief="flat", padx=12, command=self.open_logs_folder).pack(side="right", padx=5)
        tk.Button(bar, text="🔔 ALARM HISTORY", bg=self.COLORS["warning"], fg="black", font=self.FONTS["button"], relief="flat", padx=12, command=self.open_email_alerts_log).pack(side="right", padx=5)
        tk.Button(bar, text="📧 EMAIL LOGS", bg="#E1F5FE", fg="#0288D1", font=self.FONTS["button"], relief="flat", padx=12, command=self.open_sent_email_logs).pack(side="right", padx=5)
        tk.Button(bar, text="📄 PDF EXPORT", bg=self.COLORS["accent"], fg="black", font=self.FONTS["button"], relief="flat", padx=12, command=self.export_pdf).pack(side="right", padx=5)

        cnt = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        cnt.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        # Updated Columns: ID, DEVICE, NODE, NAME, ADDR, VALUE, UNIT, TYPE, TIME
        cols = ("sno", "device", "node", "name", "addr", "val", "unit", "dtype", "dt")
        self.log_tree = ttk.Treeview(cnt, columns=cols, show="headings", style="Treeview")
        
        self.log_tree.heading("sno", text="S.NO")
        self.log_tree.heading("device", text="DEVICE NAME")
        self.log_tree.heading("node", text="NODE ALIAS")
        self.log_tree.heading("name", text="FIELD NAME")
        self.log_tree.heading("addr", text="ADDRESS")
        self.log_tree.heading("val", text="DATA READING")
        self.log_tree.heading("unit", text="UNIT")
        self.log_tree.heading("dtype", text="DATA TYPE")
        self.log_tree.heading("dt", text="TIMESTAMP")
        
        self.log_tree.column("sno", width=65, minwidth=55, anchor="center", stretch=False)
        self.log_tree.column("device", width=160, minwidth=130, anchor="w", stretch=True)
        self.log_tree.column("node", width=160, minwidth=130, anchor="w", stretch=True)
        self.log_tree.column("name", width=180, minwidth=150, anchor="w", stretch=True)
        self.log_tree.column("addr", width=100, minwidth=85, anchor="center", stretch=False)
        self.log_tree.column("val", width=130, minwidth=110, anchor="center", stretch=False)
        self.log_tree.column("unit", width=75, minwidth=60, anchor="center", stretch=False)
        self.log_tree.column("dtype", width=110, minwidth=90, anchor="center", stretch=False)
        self.log_tree.column("dt", width=160, minwidth=140, anchor="center", stretch=False)
        
        v_sb = ttk.Scrollbar(cnt, orient="vertical", command=self.log_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(cnt, orient="horizontal", command=self.log_tree.xview, style="Horizontal.TScrollbar")
        self.log_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.log_tree.pack(side="left", fill="both", expand=True)
        
        # Mouse Wheel Support for Tree
        def _on_log_scroll(event):
            self.log_tree.yview_scroll(int(-1*(event.delta/120)), "units")
        self.log_tree.bind("<MouseWheel>", _on_log_scroll)
        
        # Red tags for highlights
        self.log_tree.tag_configure("alert_row", foreground="#E53935", font=("Segoe UI", 9, "bold"))
        
        for e in self.global_log[-100:]: 
            tag = ""
            if any(k in str(e[5]) for k in ["SUCCESS:", "FAILURE:", "ALERT:"]):
                tag = "alert_row"
            self.log_tree.insert("", "end", values=e, tags=(tag,))
        self.log_tree.yview_moveto(1)

    # -------------------------
    # CORE LOGIC
    # -------------------------
    def start_device(self, dev):
        if self.restore_in_progress or self.restart_required:
            raise RuntimeError("Restart TempTrek before starting monitoring.")
        ip, port, slave = dev['ip'], dev['port'], dev.get('slave_id', 1)
        key = f"{ip}:{port}:{slave}"
        if key in self.device_threads and self.device_threads[key].is_alive(): return
        ev = threading.Event()
        rev = threading.Event()
        self.stop_events[key] = ev
        self.refresh_events[key] = rev
        t = threading.Thread(target=read_modbus_device, args=(ip, port, dev['registers'], self, ev, slave, rev), daemon=True)
        t.start()
        self.device_threads[key] = t
        self.root.after(500, self.refresh_view_if_active)

    def start_simulation_device(self, dev):
        if self.restore_in_progress or self.restart_required:
            raise RuntimeError("Restart TempTrek before starting monitoring.")
        ip, port, slave = dev['ip'], dev['port'], dev.get('slave_id', 1)
        key = f"{ip}:{port}:{slave}"
        if key in self.device_threads and self.device_threads[key].is_alive(): return
        
        ev = threading.Event()
        rev = threading.Event()
        self.stop_events[key] = ev
        self.refresh_events[key] = rev
        
        t = threading.Thread(target=read_dummy_device, args=(ip, port, dev['registers'], self, ev, slave, rev), daemon=True)
        t.start()
        self.device_threads[key] = t
        self.root.after(500, self.refresh_view_if_active)

    def simulate_all_devices(self):
        for d in self.devices: self.start_simulation_device(d)

    def stop_device(self, dev):
        key = f"{dev['ip']}:{dev['port']}:{dev.get('slave_id', 1)}"
        if key in self.stop_events: self.stop_events[key].set()
        self.root.after(500, self.refresh_view_if_active)

    def start_all_devices(self):
        for d in self.devices: self.start_device(d)

    def stop_all_devices(self):
        for e in self.stop_events.values(): e.set()
        self.root.after(500, self.refresh_view_if_active)

    def refresh_view_if_active(self):
        if self.active_view == "live_data": self.navigate("live_data")

    def periodic_refresher(self):
        try:
            errors = []
            while len(errors) < 20:
                try:
                    errors.append(STORAGE_ERRORS.get_nowait())
                except queue.Empty:
                    break
            if errors:
                messagebox.showerror("Storage error — action required", "\n".join(dict.fromkeys(errors)))
            if not self.restore_in_progress and not self.restart_required:
                self.check_scheduled_pdf_email()
                self.check_automatic_backup()
        except Exception as exc:
            messagebox.showerror("Background operation failed", str(exc))
        finally:
            self.root.after(1000, self.periodic_refresher)

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        sno = len(self.global_log) + 1
        
        ip, dev_alias, rn, nm, unit, vn, dtype = "", "", "", "", "", msg, ""
        
        if msg.startswith("[") and "]" in msg:
            try:
                # Extract composite ID from [192.168.1.107:502:1]
                ip_end = msg.find("]")
                composite_id = msg[1:ip_end].strip()
                parts = composite_id.split(":")
                ip = parts[0]
                port = int(parts[1]) if len(parts) > 1 else 502
                slave = int(parts[2]) if len(parts) > 2 else 1
                
                content = msg[ip_end+1:].strip()
                
                if "Reg" in content:
                    # Data Line: "Reg 5 \u2192 100"
                    if "\u2192" in content:
                        parts_val = content.split("\u2192")
                        rn = parts_val[0].replace("Reg", "").strip()
                        vn = parts_val[1].strip()
                    # Error Line with address: "Read Error @ 5"
                    elif "@" in content:
                        rn = content.split("@")[-1].split(":")[0].strip()
                        vn = content
                    else:
                        vn = content
                else:
                    vn = content

                # Lookup metadata if we have address
                if ip and rn:
                    found_dev = None
                    for d in self.devices:
                        if d['ip'] == ip and d['port'] == port and d.get('slave_id', 1) == slave:
                            found_dev = d
                            dev_alias = d.get('device_name', '')
                            nm = d.get("names", {}).get(rn, "")
                            unit = d.get("units", {}).get(rn, "")
                            dtype = d.get("types", {}).get(rn, "INT32")
                            divisor = d.get("divisors", {}).get(rn, "1") # Get divisor
                            # If name still empty, check defaults
                            try:
                                if str(rn) in [str(x) for x in d['registers']]:
                                    idx = [str(x) for x in d['registers']].index(str(rn))
                                elif str(rn).isdigit() and int(rn) in d['registers']:
                                    idx = d['registers'].index(int(rn))
                                else:
                                    idx = -1
                                if idx != -1 and idx < len(self.DEFAULT_REGISTER_NAMES):
                                    if not nm: nm = self.DEFAULT_REGISTER_NAMES[idx]
                                    if not unit: unit = self.DEFAULT_REGISTER_UNITS[idx]
                                    if dtype == "INT32": dtype = self.DEFAULT_REGISTER_TYPES[idx]
                            except: pass
                            break
                    
                    # Update live dash
                    lid = f"{ip}:{port}:{slave}:{rn}"
                    
                    is_lora = False
                    if found_dev:
                        is_lora = ("LORA" in str(found_dev.get('node_type', '')).upper()) or ("LORA" in str(found_dev.get('device_name', '')).upper())
                    
                    should_update_ui = True
                    if is_lora:
                        try:
                            if float(vn) <= 0:
                                should_update_ui = False
                        except Exception:
                            pass

                    if should_update_ui and lid in self.live_labels:
                        try:
                            # Match view: Auto-format based on perceived type or value
                            display_val = vn
                            if dtype == "BOOLEAN":
                                pass
                            elif dtype == "FLOAT32" or "." in str(vn):
                                try:
                                    p_val = None
                                    if found_dev:
                                        p_val = found_dev.get('decimals', {}).get(str(rn))
                                    if p_val is not None:
                                        prec = int(p_val)
                                    else:
                                        prec = int(self.trend_settings.get("decimal_places", 3))
                                    display_val = f"{float(vn):.{prec}f}"
                                except: pass
                            
                            self.live_labels[lid].config(text=display_val)
                            
                            # Persistence
                            self.last_values[lid] = display_val
                            
                            # Update Register Card Last Update Date & Time
                            full_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self.last_update_times[lid] = full_ts
                            time_lid = f"{lid}:time"
                            if time_lid in self.live_labels:
                                self.live_labels[time_lid].config(text=f"Last Update: {full_ts}")

                            # Update Sync Time on Header
                            k_sync = f"{ip}:{port}:{slave}:sync"
                            if k_sync in self.live_labels:
                                self.last_update_times[f"{ip}:{port}:{slave}"] = ts
                                self.live_labels[k_sync].config(text=f"Last Sync: {ts}")
                            
                            # Update History for Graphing
                            try:
                                if dtype == "BOOLEAN":
                                    label_1 = found_dev.get('factors', {}).get(rn, "ON") if found_dev else "ON"
                                    val_float = 1.0 if vn == label_1 else 0.0
                                else:
                                    val_float = float(vn)
                                if lid not in self.data_history: self.data_history[lid] = []
                                self.data_history[lid].append(val_float)
                                if len(self.data_history[lid]) > 50: self.data_history[lid].pop(0)
                                
                                # Select first data point as active graph if none selected
                                if not self.selected_graph_lid: self.selected_graph_lid = lid
                                
                                if self.active_view == "live_data" and lid == self.selected_graph_lid:
                                    self.draw_live_graph(lid, nm if nm else f"REG {rn}")
                                elif self.active_view == "data_trends" and lid == getattr(self, 'selected_trend_lid', None):
                                    self.draw_large_graph(lid)
                            except: pass
                        except: pass
            except: pass
        elif "ALERT:" in msg: # Handle alert notification
             vn = msg

        e = (f"{sno:03d}", str(dev_alias).upper() if dev_alias else "", ip, str(nm).upper(), rn, vn, unit, dtype, ts)
        self.global_log.append(e)

        # Update Chat if visible
        if self.active_view == "live_data" and hasattr(self, 'chat_box') and self.chat_box:
            self.append_to_chat(e)

        if hasattr(self, 'log_tree') and self.log_tree.winfo_exists():
            tag = ""
            if any(k in msg for k in ["SUCCESS:", "FAILURE:", "ALERT:"]):
                tag = "alert_row"
            self.log_tree.insert("", "end", values=e, tags=(tag,))
            self.log_tree.yview_moveto(1)

    def update_chat_from_history(self):
        if not self.chat_box: return
        self.chat_box.config(state="normal")
        self.chat_box.delete("1.0", tk.END)
        for e in self.global_log[-20:]:
            self.append_to_chat(e, refresh=False)
        self.chat_box.config(state="disabled")
        self.chat_box.see(tk.END)

    def append_to_chat(self, entry, refresh=True):
        if not self.chat_box or not self.chat_box.winfo_exists(): return
        # entry: (sno, dev, ip, field, addr, val, unit, type, ts)
        sno, dev, ip, field, addr, val, unit, dtype, ts = entry
        
        self.chat_box.config(state="normal")
        
        # Check if this is an email success/failure or alert
        is_alert = any(k in str(val) for k in ["SUCCESS:", "FAILURE:", "ALERT:"])
        
        prefix = f"[{ts}] {dev if dev else ip}: "
        unit_str = f" {unit}" if unit and unit != "na" and dtype != "BOOLEAN" else ""
        content = f"{field if field else 'SYSTEM'} → {val}{unit_str}\n"
        
        if is_alert:
            self.chat_box.insert(tk.END, prefix, "alert_time")
            self.chat_box.insert(tk.END, content, "alert_text")
        else:
            self.chat_box.insert(tk.END, prefix, "dim")
            self.chat_box.insert(tk.END, content, "normal")
        
        self.chat_box.tag_configure("dim", foreground=self.COLORS["text_dim"])
        self.chat_box.tag_configure("normal", foreground=self.COLORS["text_main"])
        self.chat_box.tag_configure("alert_time", foreground="#E53935", font=("Segoe UI", 9, "bold"))
        self.chat_box.tag_configure("alert_text", foreground="#D32F2F", font=("Segoe UI", 9, "bold"))
        
        self.chat_box.config(state="disabled")
        if refresh: self.chat_box.see(tk.END)

    def draw_live_graph(self, lid, name):
        if not self.graph_canvas or not self.graph_canvas.winfo_exists(): return
        self.graph_canvas.delete("all")
        
        data = self.data_history.get(lid, [])
        if hasattr(self, 'graph_title'):
            self.graph_title.config(text=f"MONITORING: {name.upper()}")
        
        w = self.graph_canvas.winfo_width()
        h = self.graph_canvas.winfo_height()
        if w < 10: w = 350
        if h < 10: h = 220
        
        padding = 35
        usable_h = h - (padding * 2)
        usable_w = w - (padding * 3) 
        offset_x = padding + 10

        self.graph_canvas.create_line(offset_x, h-padding, w-padding, h-padding, fill=self.COLORS["text_dim"], width=1)
        self.graph_canvas.create_line(offset_x, padding, offset_x, h-padding, fill=self.COLORS["text_dim"], width=1)

        if not data:
            self.graph_canvas.create_text(w/2, h/2, text="WAITING FOR DATA...", fill=self.COLORS["text_dim"], font=("Segoe UI", 8))
            return

        v_min, v_max = min(data), max(data)
        if v_min == v_max: v_min -= 1; v_max += 1
        
        for i in range(5):
            val = v_min + (i * (v_max - v_min) / 4)
            y_g = h - padding - (i * usable_h / 4)
            self.graph_canvas.create_line(offset_x, y_g, w-padding, y_g, fill=self.COLORS["bg_highlight"], dash=(2, 2))
            prec = int(self.trend_settings.get("decimal_places", 3))
            self.graph_canvas.create_text(offset_x-5, y_g, text=f"{val:.{prec}f}", fill=self.COLORS["text_dim"], font=("Segoe UI", 6), anchor="e")

        points = []
        for i, v in enumerate(data):
            x = offset_x + (i * usable_w / (len(data)-1 if len(data) > 1 else 1))
            y = h - padding - ((v - v_min) * usable_h / (v_max - v_min))
            points.extend([x, y])
            
        if len(points) >= 4:
            fill_points = [offset_x, h-padding] + points + [points[-2], h-padding]
            self.graph_canvas.create_polygon(fill_points, fill=self.COLORS["accent_glow"], stipple="gray25")
            self.graph_canvas.create_line(points, fill=self.COLORS["accent"], width=2, smooth=True)
            curr_x, curr_y = points[-2], points[-1]
            self.graph_canvas.create_oval(curr_x-3, curr_y-3, curr_x+3, curr_y+3, fill=self.COLORS["accent"], outline=self.COLORS["bg_surface"])

    # -------------------------
    # VIEW: AUDIT LOG
    # -------------------------
    def show_audit_log(self):
        bar = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        bar.pack(side="top", fill="x", pady=10, padx=30)

        tk.Label(bar, text="CATEGORY:", font=("Segoe UI", 9, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"]).pack(side="left", padx=(0, 5))
        self.audit_cat_cb = ttk.Combobox(bar, values=["ALL", "AUTHENTICATION", "SECURITY", "DATA", "APPROVE", "DEVICE", "ALARM", "EMAIL", "SMS", "USER_MGMT", "CONFIG", "EXPORT", "BACKUP", "RESTORE"], width=15, style="TCombobox", state="readonly")
        self.audit_cat_cb.set("ALL")
        self.audit_cat_cb.pack(side="left", padx=5)

        tk.Label(bar, text="SEARCH:", font=("Segoe UI", 9, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"]).pack(side="left", padx=(15, 5))
        self.audit_search_entry = tk.Entry(bar, bg=self.COLORS["bg_surface"], fg=self.COLORS["text_main"], font=self.FONTS["body"], relief="flat", width=20, insertbackground="black")
        self.audit_search_entry.pack(side="left", padx=5, ipady=4)

        tk.Button(bar, text="🔍 FILTER", bg=self.COLORS["accent"], fg="black", font=self.FONTS["button"], relief="flat", padx=10, command=self.refresh_audit_log_view).pack(side="left", padx=5)
        tk.Button(bar, text="↺ RESET", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], font=self.FONTS["button"], relief="flat", padx=10, command=lambda: (self.audit_cat_cb.set("ALL"), self.audit_search_entry.delete(0, tk.END), self.refresh_audit_log_view())).pack(side="left", padx=5)

        tk.Button(bar, text="📄 EXPORT PDF", bg=self.COLORS["success"], fg="white", font=self.FONTS["button"], relief="flat", padx=12, command=self.export_audit_log_to_pdf).pack(side="right", padx=5)

        cnt = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        cnt.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        cols = ("timestamp", "user", "workstation", "action", "category", "previous", "updated", "status", "details")
        self.audit_tree = ttk.Treeview(cnt, columns=cols, show="headings", style="Treeview")
        audit_labels = {"timestamp": "TIMESTAMP", "user": "USER", "workstation": "DEVICE / WORKSTATION",
                        "action": "ACTION", "category": "CATEGORY", "previous": "PREVIOUS VALUE",
                        "updated": "UPDATED VALUE", "status": "STATUS", "details": "DETAILS"}
        audit_widths = {"timestamp": 155, "user": 110, "workstation": 200, "action": 150,
                        "category": 100, "previous": 180, "updated": 180, "status": 90, "details": 320}
        for column in cols:
            self.audit_tree.heading(column, text=audit_labels[column])
            self.audit_tree.column(column, width=audit_widths[column], minwidth=80, anchor="w", stretch=(column == "details"))

        v_sb = ttk.Scrollbar(cnt, orient="vertical", command=self.audit_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(cnt, orient="horizontal", command=self.audit_tree.xview, style="Horizontal.TScrollbar")
        self.audit_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.pack(side="right", fill="y")
        h_sb.pack(side="bottom", fill="x")
        self.audit_tree.pack(side="left", fill="both", expand=True)

        self.refresh_audit_log_view()

    @storage_operation
    def upload_report_logo(self):
        filepath = filedialog.askopenfilename(
            title="Select Right Header Logo Image for PDF Reports",
            filetypes=[
                ("Image Files", "*.png *.jpg *.jpeg *.bmp *.ico"),
                ("PNG Images", "*.png"),
                ("All Files", "*.*")
            ]
        )
        if not filepath:
            return

        try:
            target_png = os.path.join(DATA_PATH, "logo.png")
            config_dir = os.path.join(DATA_PATH, "config")
            os.makedirs(config_dir, exist_ok=True)
            config_png = os.path.join(config_dir, "logo.png")

            try:
                img = Image.open(filepath)
                img.save(target_png, "PNG")
                img.save(config_png, "PNG")
            except Exception:
                shutil.copy(filepath, target_png)
                shutil.copy(filepath, config_png)

            self.log_audit("LOGO_UPLOAD", "CONFIG", f"Uploaded new PDF report header logo '{os.path.basename(filepath)}'")
            messagebox.showinfo("Success", "Report logo updated successfully!\n\nIt will now appear on the right side of all exported PDF report headers.")
        except Exception as e:
            messagebox.showerror("Upload Error", f"Failed to save logo image: {e}")

    # -------------------------
    # VIEW: COMPANY DETAILS
    # -------------------------
    def show_company_details(self):
        container = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        container.pack(fill="both", expand=True, padx=30, pady=20)

        # Header / Intro
        header_frame = tk.Frame(container, bg=self.COLORS["bg_dark"])
        header_frame.pack(fill="x", pady=(0, 20))
        
        tk.Label(header_frame, text="COMPANY & REPORT HEADER CONFIGURATION", font=self.FONTS["h2"], 
                 fg=self.COLORS["text_main"], bg=self.COLORS["bg_dark"]).pack(anchor="w")
        tk.Label(header_frame, text="Configure the three header lines rendered at the top of PDF reports and system documents.", 
                 font=self.FONTS["body"], fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"]).pack(anchor="w", pady=(4, 0))

        # Main Layout Frame (2 Columns: Form Card on Left, Live Preview on Right)
        main_layout = tk.Frame(container, bg=self.COLORS["bg_dark"])
        main_layout.pack(fill="both", expand=True)

        # Form Card (Left Column)
        form_card = tk.Frame(main_layout, bg=self.COLORS["bg_surface"], padx=30, pady=25,
                             highlightbackground=self.COLORS["accent_glow"], highlightthickness=1)
        form_card.pack(side="left", fill="both", expand=True, padx=(0, 15))

        tk.Label(form_card, text="HEADER DETAILS FORM", font=("Segoe UI", 12, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["accent"]).pack(anchor="w", pady=(0, 20))

        # Line 1: System / Company Name
        tk.Label(form_card, text="Header Line 1 (System / Company Title):", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        self.comp_line1_entry = tk.Entry(form_card, font=self.FONTS["body"], bg=self.COLORS["bg_dark"], 
                                         fg=self.COLORS["text_main"], relief="flat", insertbackground="black")
        self.comp_line1_entry.insert(0, self.company_details.get("company_name", "TempTrek Environmental Monitoring System"))
        self.comp_line1_entry.pack(fill="x", pady=(5, 18), ipady=8)

        # Line 2: Department Name
        tk.Label(form_card, text="Header Line 2 (Department / Subtitle):", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        self.comp_line2_entry = tk.Entry(form_card, font=self.FONTS["body"], bg=self.COLORS["bg_dark"], 
                                         fg=self.COLORS["text_main"], relief="flat", insertbackground="black")
        self.comp_line2_entry.insert(0, self.company_details.get("department_name", "Environmental Monitoring Department"))
        self.comp_line2_entry.pack(fill="x", pady=(5, 18), ipady=8)

        # Line 3: Report Title / Description
        tk.Label(form_card, text="Header Line 3 (Report Title / Description):", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        self.comp_line3_entry = tk.Entry(form_card, font=self.FONTS["body"], bg=self.COLORS["bg_dark"], 
                                         fg=self.COLORS["text_main"], relief="flat", insertbackground="black")
        self.comp_line3_entry.insert(0, self.company_details.get("report_title", "Temperature and Humidity Report"))
        self.comp_line3_entry.pack(fill="x", pady=(5, 18), ipady=8)

        # PDF Report Logo Upload Section
        tk.Label(form_card, text="PDF Report Header Logo:", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        logo_frame = tk.Frame(form_card, bg=self.COLORS["bg_surface"])
        logo_frame.pack(fill="x", pady=(5, 20))
        
        logo_btn = tk.Button(logo_frame, text="🖼 UPLOAD REPORT LOGO", bg=self.COLORS["bg_highlight"], fg=self.COLORS["text_main"], 
                             font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15,
                             activebackground=self.COLORS["accent_glow"], command=self.upload_company_logo_action)
        logo_btn.pack(side="left", ipady=6)
        
        self.lbl_logo_status = tk.Label(logo_frame, text="", font=("Segoe UI", 9, "italic"), 
                                        bg=self.COLORS["bg_surface"], fg=self.COLORS["accent"])
        self.lbl_logo_status.pack(side="left", padx=15)

        # Bind key events for live preview update
        self.comp_line1_entry.bind("<KeyRelease>", lambda e: self.update_company_preview())
        self.comp_line2_entry.bind("<KeyRelease>", lambda e: self.update_company_preview())
        self.comp_line3_entry.bind("<KeyRelease>", lambda e: self.update_company_preview())

        # Buttons Frame
        btn_frame = tk.Frame(form_card, bg=self.COLORS["bg_surface"])
        btn_frame.pack(fill="x", pady=(10, 0))

        save_btn = tk.Button(btn_frame, text="💾 SAVE DETAILS", bg=self.COLORS["accent"], fg="black", 
                             font=self.FONTS["button"], relief="flat", cursor="hand2", padx=20,
                             activebackground=self.COLORS["accent_glow"], command=self.save_company_details_action)
        save_btn.pack(side="left", ipady=8)

        reset_btn = tk.Button(btn_frame, text="↺ RESET DEFAULTS", bg=self.COLORS["bg_dark"], fg=self.COLORS["text_dim"], 
                              font=self.FONTS["button"], relief="flat", cursor="hand2", padx=15,
                              command=self.reset_company_details_action)
        reset_btn.pack(side="left", padx=15, ipady=8)

        # Preview Card (Right Column)
        preview_card = tk.Frame(main_layout, bg=self.COLORS["bg_surface"], padx=25, pady=25,
                                highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1, width=420)
        preview_card.pack(side="right", fill="both", expand=False)
        preview_card.pack_propagate(False)

        tk.Label(preview_card, text="PDF REPORT HEADER PREVIEW", font=("Segoe UI", 11, "bold"), 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w", pady=(0, 20))

        # Preview Box Container matching PDF box layout
        self.preview_box = tk.Frame(preview_card, bg="white", highlightbackground="#888888", highlightthickness=1, padx=15, pady=15)
        self.preview_box.pack(fill="x", pady=10)

        self.lbl_prev_line1 = tk.Label(self.preview_box, text="", font=("Segoe UI", 12, "bold"), fg="black", bg="white")
        self.lbl_prev_line1.pack()

        self.lbl_prev_line2 = tk.Label(self.preview_box, text="", font=("Segoe UI", 9, "bold"), fg="#333333", bg="white")
        self.lbl_prev_line2.pack(pady=2)

        self.lbl_prev_line3 = tk.Label(self.preview_box, text="", font=("Segoe UI", 9), fg="#555555", bg="white")
        self.lbl_prev_line3.pack()

        tk.Label(preview_card, text="* This is a live preview of how the header box will appear at the top of exported PDF documents.", 
                 font=("Segoe UI", 8, "italic"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"], wraplength=350, justify="left").pack(anchor="w", pady=(20, 0))

        self.update_company_preview()

    def update_company_preview(self):
        if hasattr(self, 'lbl_prev_line1'):
            l1 = self.comp_line1_entry.get().strip() or "TempTrek Environmental Monitoring System"
            l2 = self.comp_line2_entry.get().strip() or "Environmental Monitoring Department"
            l3 = self.comp_line3_entry.get().strip() or "Temperature and Humidity Report"
            self.lbl_prev_line1.config(text=l1)
            self.lbl_prev_line2.config(text=l2)
            self.lbl_prev_line3.config(text=l3)
        if hasattr(self, 'lbl_logo_status'):
            custom_logo = os.path.join(DATA_PATH, "logo.png")
            if os.path.exists(custom_logo):
                self.lbl_logo_status.config(text="✓ Custom logo active", fg=self.COLORS["success"])
            else:
                self.lbl_logo_status.config(text="Default logo active", fg=self.COLORS["text_dim"])

    @storage_operation
    def upload_company_logo_action(self):
        filepath = filedialog.askopenfilename(
            title="Select Right Header Logo Image for PDF Reports",
            filetypes=[
                ("Image Files", "*.png *.jpg *.jpeg *.bmp *.ico"),
                ("PNG Images", "*.png"),
                ("All Files", "*.*")
            ]
        )
        if not filepath:
            return

        try:
            target_png = os.path.join(DATA_PATH, "logo.png")
            config_dir = os.path.join(DATA_PATH, "config")
            os.makedirs(config_dir, exist_ok=True)
            config_png = os.path.join(config_dir, "logo.png")

            try:
                img = Image.open(filepath)
                img.save(target_png, "PNG")
                img.save(config_png, "PNG")
            except Exception:
                shutil.copy(filepath, target_png)
                shutil.copy(filepath, config_png)

            self.log_audit("LOGO_UPLOAD", "CONFIG", f"Uploaded new PDF report header logo '{os.path.basename(filepath)}'")
            messagebox.showinfo("Success", "Report logo updated successfully!\n\nIt will now appear on all exported PDF report headers.")
            self.update_company_preview()
        except Exception as e:
            messagebox.showerror("Upload Error", f"Failed to save logo image: {e}")

    def save_company_details_action(self):
        line1 = self.comp_line1_entry.get().strip()
        line2 = self.comp_line2_entry.get().strip()
        line3 = self.comp_line3_entry.get().strip()

        if not line1:
            line1 = "TempTrek Environmental Monitoring System"
        if not line2:
            line2 = "Environmental Monitoring Department"
        if not line3:
            line3 = "Temperature and Humidity Report"

        self.company_details["company_name"] = line1
        self.company_details["department_name"] = line2
        self.company_details["report_title"] = line3

        try:
            save_company_details(self.company_details)
            self.log_audit("UPDATE_COMPANY_DETAILS", "CONFIG", f"Updated company details: '{line1}' | '{line2}' | '{line3}'")
            self.update_company_preview()
            messagebox.showinfo("Success", "Company header details saved successfully.\n\nAll future PDF exports will use these updated titles.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save company details: {e}")

    def reset_company_details_action(self):
        if messagebox.askyesno("Confirm Reset", "Reset company details back to default values?"):
            def_l1 = "TempTrek Environmental Monitoring System"
            def_l2 = "Environmental Monitoring Department"
            def_l3 = "Temperature and Humidity Report"
            
            self.comp_line1_entry.delete(0, tk.END)
            self.comp_line1_entry.insert(0, def_l1)
            self.comp_line2_entry.delete(0, tk.END)
            self.comp_line2_entry.insert(0, def_l2)
            self.comp_line3_entry.delete(0, tk.END)
            self.comp_line3_entry.insert(0, def_l3)

            self.company_details["company_name"] = def_l1
            self.company_details["department_name"] = def_l2
            self.company_details["report_title"] = def_l3

            save_company_details(self.company_details)
            self.log_audit("RESET_COMPANY_DETAILS", "CONFIG", "Reset company header details to defaults")
            self.update_company_preview()
            messagebox.showinfo("Success", "Company details reset to defaults.")

    def refresh_audit_log_view(self):
        if not hasattr(self, 'audit_tree') or not self.audit_tree.winfo_exists():
            return
        
        for item in self.audit_tree.get_children():
            self.audit_tree.delete(item)

        cat_filter = self.audit_cat_cb.get().upper() if hasattr(self, 'audit_cat_cb') else "ALL"
        search_query = self.audit_search_entry.get().strip().lower() if hasattr(self, 'audit_search_entry') else ""

        logs_to_display = []
        live_dir = os.path.join(DATA_PATH, "live")
        if os.path.exists(live_dir):
            audit_files = sorted([f for f in os.listdir(live_dir) if f.startswith("Audit_Logs_") and f.endswith(".csv")], reverse=True)
            for fname in audit_files:
                fn = os.path.join(live_dir, fname)
                try:
                    with open(fn, "r", encoding='utf-8', errors='replace') as f:
                        reader = csv.reader((line.replace('\0', '') for line in f))
                        header = next(reader, None)
                        previous_hash = ""
                        for row in reader:
                            if len(row) >= 10:
                                values = row[:9]
                                expected_hash = _audit_hash(previous_hash, values)
                                if row[9] != expected_hash:
                                    values[7] = "TAMPER WARNING"
                                previous_hash = row[9]
                                logs_to_display.append(tuple(values))
                            elif len(row) >= 5:
                                logs_to_display.append((row[0], row[1], "LEGACY", row[2], row[3], "", "", "UNKNOWN", row[4]))
                except Exception:
                    pass

        # Also merge in-memory log entries for entries not yet on disk or fallback
        for entry in list(self.global_audit_log):
            normalized = tuple(entry[:9]) if len(entry) >= 9 else (entry[0], entry[1], "LEGACY", entry[2], entry[3], "", "", "UNKNOWN", entry[4])
            if normalized not in logs_to_display:
                logs_to_display.append(normalized)

        # Sort descending by timestamp
        logs_to_display.sort(key=lambda x: x[0], reverse=True)

        for entry in logs_to_display:
            ts, user, workstation, action, category, previous, updated, status, details = entry[:9]
            if cat_filter != "ALL" and category.upper() != cat_filter:
                continue
            if search_query:
                combined = " ".join(str(value) for value in entry[:9]).lower()
                if search_query not in combined:
                    continue
            self.audit_tree.insert("", "end", values=(ts, user, workstation, action, category, previous, updated, status, details))

    def export_audit_log_to_pdf(self):
        if not HAS_REPORTLAB:
            messagebox.showerror("Error", "ReportLab library is missing. Cannot export PDF.")
            return

        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_file = filedialog.asksaveasfilename(
            defaultextension=".pdf", 
            initialfile=f"Equipment_Audit_Trail_Equipment_Wise_{date_str}.pdf",
            filetypes=[("PDF Files (*.pdf)", "*.pdf"), ("All Files", "*.*")]
        )
        if not target_file:
            return
            
        try:
            page_size = landscape((595.2756, 841.8898))  # A4 landscape
            c = canvas.Canvas(target_file, pagesize=page_size)
            w, h = page_size
            printed_at = datetime.now()
            
            # Fetch table rows from UI tree
            tree_items = self.audit_tree.get_children()
            log_records = []
            for item in tree_items:
                row = self.audit_tree.item(item)['values']
                if len(row) >= 9:
                    detail_text = (f"Status={row[7]}; Device={row[2]}; Previous={row[5]}; "
                                   f"Updated={row[6]}; Details={row[8]}")
                    log_records.append((str(row[0]), str(row[1]), str(row[3]), str(row[4]), detail_text))

            rows_per_page = 20
            total_pages = max(1, (len(log_records) + rows_per_page - 1) // rows_per_page)

            def box(x, y, bw, bh, fill=None, stroke=0.45):
                if fill is not None:
                    c.setFillColorRGB(*fill)
                    c.rect(x, y, bw, bh, fill=1, stroke=0)
                c.setStrokeColorRGB(stroke, stroke, stroke)
                c.setLineWidth(0.5)
                c.rect(x, y, bw, bh, fill=0, stroke=1)

            def text(x, y, value, bold=False, size=8, centered=False):
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
                value = str(value)
                if centered:
                    c.drawCentredString(x, y, value)
                else:
                    c.drawString(x, y, value)

            cat_filter = self.audit_cat_cb.get() if hasattr(self, 'audit_cat_cb') else "ALL"
            search_filter = self.audit_search_entry.get().strip() if hasattr(self, 'audit_search_entry') else ""
            if not search_filter:
                search_filter = "None"

            col_x = [25, 65, 185, 275, 395, 505]
            col_w = [40, 120, 90, 120, 110, 311]
            headers = ["S.No.", "Timestamp", "User", "Action", "Category", "Details"]

            def draw_page_decorations(page_no):
                left, right = 25, w - 25
                top = h - 12
                # Outer Title Box (66pt high)
                box(left, top - 66, right - left, 66)
                box(left, top - 66, 158, 66)
                box(right - 158, top - 66, 158, 66)
                
                comp_info = load_company_details()
                text(w / 2, top - 22, comp_info.get("company_name", "TempTrek Environmental Monitoring System"), True, 13, True)
                text(w / 2, top - 38, comp_info.get("department_name", "Environmental Monitoring Department"), True, 9, True)
                text(w / 2, top - 53, "EQUIPMENT AUDIT TRAIL (EQUIPMENT WISE)", True, 9, True)
                
                # Left side icon/logo (Temp Trek image format)
                left_logo_path = None
                for candidate in [resource_path("temptrek_logo.png"), resource_path("logo.png"), resource_path("left_logo.png"), resource_path("icon.png")]:
                    if os.path.exists(candidate):
                        left_logo_path = candidate
                        break
                left_image_drawn = False
                if left_logo_path:
                    try:
                        c.drawImage(left_logo_path, left + 10, top - 58, width=138, height=50, mask='auto', preserveAspectRatio=True)
                        left_image_drawn = True
                    except Exception:
                        pass
                if not left_image_drawn:
                    start_x = left + 40
                    c.setFillColorRGB(0.0, 0.36, 0.72)
                    c.setFont("Helvetica-Bold", 15)
                    c.drawString(start_x, top - 38, "Temp")
                    c.setFillColorRGB(0.11, 0.12, 0.13)
                    c.setFont("Helvetica", 15)
                    c.drawString(start_x + 41, top - 38, "Trek")

                # Right side logo option
                right_logo_path = None
                for candidate in [os.path.join(DATA_PATH, "logo.png"), os.path.join(DATA_PATH, "config", "logo.png"), resource_path("logo.png"), resource_path("company_logo.png")]:
                    if os.path.exists(candidate):
                        right_logo_path = candidate
                        break
                right_image_drawn = False
                if right_logo_path:
                    try:
                        c.drawImage(right_logo_path, right - 148, top - 57, width=48, height=48, mask='auto', preserveAspectRatio=True)
                        text(right - 92, top - 38, "AUDIT TRAIL", True, 10, False)
                        right_image_drawn = True
                    except Exception:
                        pass
                if not right_image_drawn:
                    text(right - 79, top - 38, "AUDIT TRAIL", True, 12, True)

                # Metadata Box
                meta_top = top - 74
                label_w, half_w = 142, (right - left) / 2
                meta_rows = [
                    ("Department", "Environmental Monitoring", "Report Type", "Equipment Audit Trail (Equipment Wise)"),
                    ("Category Filter", cat_filter, "Search Query", search_filter),
                    ("Total Records", str(len(log_records)), "Generated On", printed_at.strftime("%d/%m/%Y %H:%M:%S")),
                    ("Generated By", self.current_user, "System Version", "TempTrek 2.0")
                ]
                row_h = 14
                for i, (l1, v1, l2, v2) in enumerate(meta_rows):
                    yy = meta_top - ((i + 1) * row_h)
                    box(left, yy, right - left, row_h)
                    box(left, yy, label_w, row_h)
                    box(left + half_w, yy, 95, row_h)
                    text(left + 3, yy + 4, l1, True, 8)
                    text(left + label_w + 3, yy + 4, str(v1)[:45], False, 8)
                    text(left + half_w + 3, yy + 4, l2, True, 8)
                    text(left + half_w + 98, yy + 4, str(v2)[:45], False, 8)

                # Footer
                c.setStrokeColorRGB(0.35, 0.35, 0.35)
                c.line(left, 47, right, 47)
                text(left + 2, 20, "TempTrek 2.0 - Equipment Audit Trail", False, 8)
                text(w / 2, 20, f"Printed By - {self.current_user} On {printed_at.strftime('%d/%m/%Y %H:%M:%S')}", False, 8, True)
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica", 8)
                c.drawRightString(right - 2, 20, f"Page {page_no} of {total_pages}")

                # Table Column Headers
                y_hdr = meta_top - (4 * row_h) - 15
                for x, cw, label in zip(col_x, col_w, headers):
                    box(x, y_hdr - 16, cw, 16, (0.83, 0.83, 0.83))
                    text(x + 3, y_hdr - 11, label, True, 8)
                
                return y_hdr - 16

            current_page = 1
            y_curr = draw_page_decorations(current_page)
            row_height = 15

            for idx, rec in enumerate(log_records):
                if y_curr - row_height < 55:
                    c.showPage()
                    current_page += 1
                    y_curr = draw_page_decorations(current_page)

                sno = str(idx + 1)
                ts, user, action, cat, details = rec
                row_vals = [sno, ts, user, action, cat, details]
                max_lens = [5, 22, 16, 20, 18, 60]

                for x, cw, val, ml in zip(col_x, col_w, row_vals, max_lens):
                    box(x, y_curr - row_height, cw, row_height)
                    text(x + 3, y_curr - row_height + 4, str(val)[:ml], False, 8)

                y_curr -= row_height

            c.save()
            self.log_audit("AUDIT_LOG_EXPORT", "EXPORT", f"Exported equipment audit trail PDF report to '{os.path.basename(target_file)}'")
            messagebox.showinfo("Success", f"Equipment Audit Trail PDF exported successfully!\n\nFile: {target_file}")
            webbrowser.open(target_file)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export equipment audit trail PDF: {e}")

    def export_audit_log_to_csv(self):
        date_str = datetime.now().strftime("%Y-%m-%d")
        target_file = filedialog.asksaveasfilename(
            defaultextension=".csv", 
            initialfile=f"Audit_Report_{date_str}.csv",
            filetypes=[("CSV Files", "*.csv")]
        )
        if not target_file:
            return
            
        try:
            with open(target_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "Username", "Workstation", "Action", "Category",
                                 "Previous Value", "Updated Value", "Status", "Details"])
                for item in self.audit_tree.get_children():
                    row = self.audit_tree.item(item)['values']
                    writer.writerow(row)
            self.log_audit("AUDIT_LOG_EXPORT", "EXPORT", f"Exported audit log report to '{os.path.basename(target_file)}'")
            messagebox.showinfo("Success", f"Audit log exported to {target_file}")
            webbrowser.open(target_file)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export audit log: {e}")

# -------------------------
# MAIN
# -------------------------
def main():
    root = tk.Tk()
    def report_error(kind, value, traceback):
        messagebox.showerror("Operation failed", str(value), parent=root)
    root.report_callback_exception = report_error
    try:
        if STARTUP_STORAGE_ERROR is not None:
            raise RuntimeError(f"Restore recovery failed. Original folders have been retained: {STARTUP_STORAGE_ERROR}")
        app = ModbusApp(root)
    except Exception as exc:
        messagebox.showerror("TempTrek could not start", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()

if __name__ == "__main__":
    main()
