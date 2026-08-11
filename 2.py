import time
import base64
import struct
import threading
import json
import os
import csv
import webbrowser
from datetime import datetime, timedelta
from collections import deque
import random
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

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
    """Encodes string data with XOR obfuscation and Base64."""
    key = get_hwid()
    # Simple XOR byte-wise obfuscation
    xor_data = "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(data_str))
    return base64.b64encode(xor_data.encode()).decode()

def deobfuscate_data(encoded_str):
    """Decodes data encoded by obfuscate_data. Tries both stable and legacy keys."""
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

# Ensure directories exist
os.makedirs(os.path.join(DATA_PATH, "config"), exist_ok=True)
os.makedirs(os.path.join(DATA_PATH, "live"), exist_ok=True)
os.makedirs(os.path.join(DATA_PATH, "reports"), exist_ok=True)

CONFIG_FILE = os.path.join(DATA_PATH, "config", "devices.json")
EMAIL_CONFIG_FILE = os.path.join(DATA_PATH, "config", "email_settings.json")
USERS_FILE = os.path.join(DATA_PATH, "config", "users.json")
TREND_CONFIG_FILE = os.path.join(DATA_PATH, "config", "trend_settings.json")

def migrate_config_files():
    """Scan config folder and obfuscate any plain-text JSON files."""
    files = [CONFIG_FILE, EMAIL_CONFIG_FILE, USERS_FILE, TREND_CONFIG_FILE]
    for filepath in files:
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    content = f.read().strip()
                if not content: continue
                
                # Check if already obfuscated
                if deobfuscate_data(content) is None:
                    # Likely plain text JSON, try parsing to confirm
                    try:
                        data = json.loads(content)
                        # Re-save as obfuscated
                        with open(filepath, "w") as f:
                            f.write(obfuscate_data(json.dumps(data, indent=4)))
                    except json.JSONDecodeError:
                        pass # Not JSON, skip
            except:
                pass

# Run migration on startup
migrate_config_files()

# -------------------------
# JSON STORAGE
# -------------------------

def load_devices():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r") as f:
            content = f.read().strip()
            # Try deobfuscating first
            decoded = deobfuscate_data(content)
            if decoded:
                return json.loads(decoded)
            else:
                # If not obfuscated, assume plain JSON and return
                return json.loads(content)
    except:
        return []

def save_devices(devices):
    with open(CONFIG_FILE, "w") as f:
        data_str = json.dumps(devices, indent=4)
        f.write(obfuscate_data(data_str))

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
    if not os.path.exists(EMAIL_CONFIG_FILE):
        return default_settings
            
    try:
        with open(EMAIL_CONFIG_FILE, "r") as f:
            content = f.read().strip()
            decoded = deobfuscate_data(content)
            if decoded:
                data = json.loads(decoded)
                # Ensure new keys exist in loaded data
                for k, v in default_settings.items():
                    if k not in data:
                        data[k] = v
                return data
            else:
                return json.loads(content)
    except:
        return default_settings

def save_email_settings(settings):
    with open(EMAIL_CONFIG_FILE, "w") as f:
        data_str = json.dumps(settings, indent=4)
        f.write(obfuscate_data(data_str))

def load_users():
    # Define default admin in case of failure or first run
    default_h = hashlib.sha256("admin".encode()).hexdigest()
    default_admin = {
        "admin": {
            "password": default_h,
            "permissions": ["live_data", "add_device", "live_log", "email_config", "email_smtp_config", "export_data", "data_trends", "user_manager", "audit_log"],
            "is_admin": True
        }
    }

    if not os.path.exists(USERS_FILE):
        save_users(default_admin)
        return default_admin
            
    try:
        with open(USERS_FILE, "r") as f:
            content = f.read().strip()
            decoded = deobfuscate_data(content)
            if decoded:
                users = json.loads(decoded)
            else:
                users = json.loads(content)
            
            # Ensure "admin" exists and has full permissions
            if "admin" not in users:
                users["admin"] = default_admin["admin"]
            
            all_perms = ["live_data", "add_device", "live_log", "email_config", "email_smtp_config", "export_data", "data_trends", "user_manager", "audit_log"]
            for p in all_perms:
                if p not in users["admin"]["permissions"]:
                    users["admin"]["permissions"].append(p)
            
            return users
    except:
        # If file is corrupted or HWID changed beyond recovery, return default admin
        return default_admin

def save_users(users):
    with open(USERS_FILE, "w") as f:
        data_str = json.dumps(users, indent=4)
        f.write(obfuscate_data(data_str))

def load_trend_settings():
    if not os.path.exists(TREND_CONFIG_FILE):
        return {"low_limit": "", "upper_limit": "", "visible_condition": "Both Limits", "decimal_places": "3"}
    try:
        with open(TREND_CONFIG_FILE, "r") as f:
            content = f.read().strip()
            decoded = deobfuscate_data(content)
            if decoded:
                data = json.loads(decoded)
            else:
                data = json.loads(content)
            
            if "decimal_places" not in data: data["decimal_places"] = "3"
            return data
    except:
        return {"low_limit": "", "upper_limit": "", "visible_condition": "Both Limits", "decimal_places": "3"}

def save_trend_settings(settings):
    with open(TREND_CONFIG_FILE, "w") as f:
        data_str = json.dumps(settings, indent=4)
        f.write(obfuscate_data(data_str))

# -------------------------
# CSV LOGGING
# -------------------------
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

def write_csv(ip, port, slave_id, reg, value):
    filename = csv_filename(ip, port, slave_id)
    for attempt in range(3):
        exists = os.path.exists(filename)
        try:
            with open(filename, "a", newline="") as f:
                writer = csv.writer(f)
                if not exists:
                    writer.writerow(["Timestamp", "IP", "Port", "SlaveID", "Register", "Value"])
                writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ip, port, slave_id, reg, value])
            break
        except Exception:
            time.sleep(0.1)

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
            break
        except Exception:
            time.sleep(0.1)

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
            break
        except Exception:
            time.sleep(0.1)

def write_audit_log_csv(username, action, category, details):
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = os.path.join(DATA_PATH, "live", f"Audit_Logs_{date_str}.csv")
    for attempt in range(3):
        exists = os.path.exists(filename)
        try:
            with open(filename, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not exists:
                    writer.writerow(["Timestamp", "Username", "Action", "Category", "Details"])
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                    username, 
                    action, 
                    category, 
                    details
                ])
            break
        except Exception:
            time.sleep(0.1)

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

                        if reg_mode == "FC03":
                            result = client.read_holding_registers(address=reg, count=count_to_read, **kwargs)
                        elif reg_mode == "FC01":
                            result = client.read_coils(address=reg, count=count_to_read, **kwargs)
                        elif reg_mode == "FC02":
                            result = client.read_discrete_inputs(address=reg, count=count_to_read, **kwargs)
                        else:
                            result = client.read_input_registers(address=reg, count=count_to_read, **kwargs)

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
                                    if cond == ">" and value > thresh: triggered = True
                                    elif cond == "<" and value < thresh: triggered = True
                                    elif cond == "==" and value == thresh: triggered = True
                                    
                                    # Process Alert State Logic (Alert vs Normal)
                                    app.check_alert_logic(alert, value, triggered)
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
                            if cond == ">" and value > thresh: triggered = True
                            elif cond == "<" and value < thresh: triggered = True
                            elif cond == "==" and value == thresh: triggered = True
                            app.check_alert_logic(alert, value, triggered)
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
class ModbusApp:
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
        self.root.configure(bg=self.COLORS["bg_dark"])
        
        # High DPI Support for Windows
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass

        # Load Icon
        try:
            self.icon_img = tk.PhotoImage(file=resource_path("icon.png"))
            self.root.iconphoto(False, self.icon_img)
        except Exception as e:
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

        # Communication Locks (per IP:Port) to prevent collisions
        self.comms_lock = threading.Lock()
        self.ip_locks = {}
        self.shared_clients = {} # (host, port) -> ModbusTcpClient
        
        # User Authentication & Audit Log
        self.users = load_users()
        self.current_user = None
        self.global_audit_log = deque(maxlen=2000)

        # Live Real-time Analytics
        self.data_history = {}  # { lid: [val1, val2, ...] }
        self.graph_canvas = None
        self.chat_box = None
        self.selected_graph_lid = None
        
        # Trend Settings
        self.trend_settings = load_trend_settings()

        # Force encode all config files on startup (Migration)
        save_devices(self.devices)
        save_email_settings(self.email_settings)
        save_users(self.users)
        save_trend_settings(self.trend_settings)

        # Start Flow
        self.edit_index = None
        self.active_view = "live_data"
        self.check_initial_license()
        self.periodic_refresher()

    def log_audit(self, action, category, details, user=None):
        username = user if user else (getattr(self, 'current_user', None) or 'SYSTEM')
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_audit_log_csv(username, action, category, details)
        entry = (ts, username, action, category, details)
        self.global_audit_log.append(entry)
        if hasattr(self, 'audit_tree') and self.audit_tree and self.audit_tree.winfo_exists():
            self.root.after(0, lambda: self._append_audit_live(entry))

    def _append_audit_live(self, entry):
        try:
            if hasattr(self, 'audit_tree') and self.audit_tree and self.audit_tree.winfo_exists():
                self.audit_tree.insert("", 0, values=entry)
        except Exception:
            pass

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
        self.u_entry.insert(0, "ADMIN")
        self.u_entry.pack(fill="x", pady=(5, 20), ipady=8)

        tk.Label(card, text="PASSCODE", font=self.FONTS["body"], 
                 bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(anchor="w")
        self.pass_entry = tk.Entry(card, show="*", font=self.FONTS["body"], bg=self.COLORS["bg_dark"], 
                                  fg=self.COLORS["text_main"], relief="flat", insertbackground="black", bd=0)
        self.pass_entry.pack(fill="x", pady=(5, 30), ipady=8)
        self.pass_entry.focus()
        
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
        h = hashlib.sha256(password.encode()).hexdigest()
        
        if username in self.users:
            if self.users[username]["password"] == h:
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

        self.nav_container = tk.Frame(self.sidebar, bg=self.COLORS["bg_surface"], pady=10)
        self.nav_container.pack(fill="both", expand=True)

        self.nav_btns = {}
        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        
        if "live_data" in user_perms:
            self.create_nav_item("live_data", "DASHBOARD")
        if "add_device" in user_perms:
            self.create_nav_item("add_device", "Device Manager")
        if "live_log" in user_perms:
            self.create_nav_item("live_log", "Live Log")
        if "email_config" in user_perms:
            self.create_nav_item("email_config", "Alarm Configuration")
        if "export_data" in user_perms:
            self.create_nav_item("export_data", "Data Export")
        if "data_trends" in user_perms:
            self.create_nav_item("data_trends", "Historic Trends")
        if "user_manager" in user_perms:
            self.create_nav_item("user_manager", "System Users")
        if "audit_log" in user_perms:
            self.create_nav_item("audit_log", "Audit Log")

        footer = tk.Frame(self.sidebar, bg=self.COLORS["bg_surface"], pady=20)
        footer.pack(side="bottom", fill="x")
        
        status_frame = tk.Frame(footer, bg=self.COLORS["bg_surface"])
        status_frame.pack(side="left", padx=20)
        
        status_dot = tk.Label(status_frame, text="●", fg=self.COLORS["success"], bg=self.COLORS["bg_surface"])
        status_dot.pack(side="left")
        tk.Label(status_frame, text=self.current_user.upper(), font=("Segoe UI", 8, "bold"), 
                 fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(5, 0))

        tk.Button(footer, text="LOGOUT", font=("Segoe UI", 8, "bold"), fg=self.COLORS["danger"],
                  bg=self.COLORS["bg_surface"], relief="flat", cursor="hand2",
                  command=self.show_login).pack(side="right", padx=20)

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

    def create_nav_item(self, view_id, text):
        btn = tk.Button(self.nav_container, text=text, font=("Segoe UI", 10), 
                        fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"], 
                        relief="flat", anchor="w", padx=30, pady=12, cursor="hand2",
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

    def navigate(self, view_name):
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
            "email_config": "ALARM CONFIGURATION",
            "export_data": "DATA EXPORT",
            "data_trends": "HISTORIC TREND ANALYTICS",
            "user_manager": "USER MANAGEMENT",
            "email_alerts": "ALARM HISTORY",
            "email_logs_view": "EMAIL SEND LOGS",
            "audit_log": "USER AUDIT LOG"
        }
        self.view_title.config(text=titles.get(view_name, ""))

        for widget in self.content_frame.winfo_children():
            widget.destroy()

        if view_name == "live_data":
            self.show_live_data()
        elif view_name == "add_device":
            self.show_add_device()
        elif view_name == "live_log":
            self.show_live_log()
        elif view_name == "email_config":
            self.show_email_config()
        elif view_name == "export_data":
            self.show_export_data()
        elif view_name == "data_trends":
            self.show_data_trends()
        elif view_name == "email_alerts":
            self.show_email_alerts_log()
        elif view_name == "email_logs_view":
            self.show_email_logs_history()
        elif view_name == "user_manager":
            self.show_user_manager()
        elif view_name == "audit_log":
            self.show_audit_log()

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
        # LIVE ANALYTICS SIDEBAR (Graph & Chat)
        # -------------------------
        analytics_sidebar = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"], width=380, 
                                    highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        analytics_sidebar.pack(side="right", fill="y", padx=(15, 0))
        analytics_sidebar.pack_propagate(False)

        # 1. LIVE GRAPH SECTION
        graph_frame = tk.Frame(analytics_sidebar, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        graph_frame.pack(fill="x", pady=(0, 15))
        
        tk.Label(graph_frame, text="LIVE TREND ANALYTICS", font=("Segoe UI", 9, "bold"), 
                 fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(pady=10)
        
        self.graph_canvas = tk.Canvas(graph_frame, bg=self.COLORS["bg_dark"], height=220, highlightthickness=0)
        self.graph_canvas.pack(fill="x", padx=15, pady=(0, 10))
        
        self.graph_title = tk.Label(graph_frame, text="SELECT A CARD TO VIEW TREND", font=("Segoe UI", 7, "bold"), 
                                   fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"])
        self.graph_title.pack(pady=(0, 10))

        # 2. LIVE CHAT / ACTIVITY SECTION
        chat_frame = tk.Frame(analytics_sidebar, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        chat_frame.pack(fill="both", expand=True)

        tk.Label(chat_frame, text="SYSTEM ACTIVITY LOG", font=("Segoe UI", 8, "bold"), 
                 fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"]).pack(pady=10)

        chat_container = tk.Frame(chat_frame, bg=self.COLORS["bg_dark"])
        chat_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.chat_box = tk.Text(chat_container, bg=self.COLORS["bg_dark"], fg=self.COLORS["text_main"],
                                font=self.FONTS["code"], relief="flat", wrap="word", state="disabled")
        self.chat_box.pack(side="left", fill="both", expand=True)
        
        chat_sb = ttk.Scrollbar(chat_container, orient="vertical", command=self.chat_box.yview, style="Vertical.TScrollbar")
        self.chat_box.config(yscrollcommand=chat_sb.set)
        chat_sb.pack(side="right", fill="y")

        # Initial Chat Load
        self.update_chat_from_history()

        # Scrollable Area (Main Grid)
        container = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        container.pack(side="left", fill="both", expand=True)

        canvas = tk.Canvas(container, bg=self.COLORS["bg_dark"], highlightthickness=0)
        v_sb = ttk.Scrollbar(container, orient="vertical", style="Vertical.TScrollbar", command=canvas.yview)
        
        self.grid_frame = tk.Frame(canvas, bg=self.COLORS["bg_dark"])
        
        # Window configuration
        def _configure_window(event):
            canvas.itemconfig(canvas_window, width=event.width)
        
        canvas_window = canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        canvas.bind("<Configure>", _configure_window)
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
            r_addr = lid.split(":")[-1]
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
            c.drawString(40, h-50, "TempTrek Environmental Monitoring System")
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
                    
                    r_addr = lid.split(":")[-1]
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
                    # Check if all values in this row are zero
                    all_zero = True
                    row_vals_only = []
                    for r_addr in target_regs:
                        v = pivoted[ts].get(r_addr, "---")
                        row_vals_only.append(v)
                        if v != "---":
                            try:
                                if float(v) != 0: all_zero = False
                            except: pass
                    
                    if all_zero and any(v != "---" for v in row_vals_only):
                        continue

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
                            try:
                                if dev.get('types', {}).get(str(r_addr)) == "BOOLEAN":
                                    label_0 = dev.get('ops', {}).get(str(r_addr), "OFF")
                                    label_1 = dev.get('factors', {}).get(str(r_addr), "ON")
                                    v = label_0 if int(float(v)) == 0 else label_1
                                else:
                                    prec = int(dev.get('decimals', {}).get(str(r_addr), 3))
                                    v = f"{float(v):.{prec}f}"
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
        target_regs = {lid.split(":")[-1]: lid for lid in lids}
        
        # We assume all selected lids are from the same device (based on how UI works)
        # Get device info from first lid
        first_lid = lids[0].split(":")
        ip, port, slave = first_lid[0], first_lid[1], first_lid[2]
        
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
                            r_addr = str(row[4])
                            if r_addr in target_regs:
                                try:
                                    row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                                    if f_dt <= row_dt <= t_dt:
                                        ts_key = row[0]
                                        if interval_sec > 0:
                                            # Round to interval
                                            rounded_ts = int(row_dt.timestamp() // interval_sec * interval_sec)
                                            ts_key = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                                        
                                        val = float(row[5])
                                        if ts_key not in pivoted_data: pivoted_data[ts_key] = {}
                                        # Use latest value in interval
                                        pivoted_data[ts_key][r_addr] = val
                                except: pass
            except: continue

        # Limit Graph Data to last 300 points
        sorted_ts = sorted(pivoted_data.keys())
        self.trend_timestamps = sorted_ts[-300:] 
        
        for lid in lids:
            r_addr = lid.split(":")[-1]
            self.data_history[lid] = [pivoted_data[ts].get(r_addr, 0.0) for ts in self.trend_timestamps]

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
            reg_addr = lid.split(":")[-1]
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
            reg_addr = lid.split(":")[-1]
            display_name = getattr(self, 'trend_addr_to_name', {}).get(reg_addr, f"REG {reg_addr}")
            self.bar_chart_canvas.create_rectangle(w-padding_r+30, legend_y, w-padding_r+45, legend_y+15, fill=color, outline="")
            self.bar_chart_canvas.create_text(w-padding_r+55, legend_y+7, text=display_name.upper(), anchor="w", fill=self.COLORS["text_main"], font=("Segoe UI", 9, "bold"))
            legend_y += 25

    def show_user_manager(self):
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True, padx=30, pady=20)

        left = tk.Frame(layout, bg=self.COLORS["bg_surface"], padx=20, pady=20,
                       highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        left.pack(side="left", fill="y", anchor="n")

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
        
        tk.Label(left, text="PERMISSIONS", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w", pady=(15, 5))
        
        self.perm_vars = {}
        perms = [
            ("live_data", "Live Dashboard"),
            ("add_device", "Device Manager"),
            ("live_log", "Live Log"),
            ("email_config", "Email Alerts"),
            ("email_smtp_config", "Gmail Configuration"),
            ("export_data", "Data Export"),
            ("data_trends", "Historic Trends"),
            ("user_manager", "User Manager"),
            ("audit_log", "Audit Log")
        ]
        
        for pid, pname in perms:
            var = tk.BooleanVar(value=True if pid != "user_manager" else False)
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

        self.user_tree = ttk.Treeview(table_frame, columns=("user", "perms", "type"), show="headings", style="Treeview")
        self.user_tree.heading("user", text="USERNAME")
        self.user_tree.heading("perms", text="PERMISSIONS")
        self.user_tree.heading("type", text="ACCOUNT TYPE")
        
        self.user_tree.column("user", width=150)
        self.user_tree.column("perms", width=400)
        self.user_tree.column("type", width=100)
        
        self.user_tree.pack(side="left", fill="both", expand=True)

        self.user_tree.bind("<<TreeviewSelect>>", self._on_user_select)

        # Remove button
        tk.Button(right, text="REMOVE SELECTED USER", bg=self.COLORS["danger"], fg="white", 
                  font=self.FONTS["button"], relief="flat", padx=20, pady=10, command=self.remove_user_submit).pack(anchor="e", pady=10)

        self.refresh_user_list()

    def refresh_user_list(self):
        for i in self.user_tree.get_children():
            self.user_tree.delete(i)
        
        for uname, udata in self.users.items():
            perms = ", ".join(udata.get("permissions", []))
            utype = "ADMIN" if udata.get("is_admin") else "OPERATOR"
            self.user_tree.insert("", "end", values=(uname.upper(), perms, utype))

    def _on_user_select(self, event):
        selected = self.user_tree.selection()
        if not selected: return
        
        uname = self.user_tree.item(selected[0])['values'][0].lower()
        if uname in self.users:
            udata = self.users[uname]
            self.u_manager_name.delete(0, tk.END)
            self.u_manager_name.insert(0, uname)
            
            # Clear password for security, let them type new one if needed
            self.u_manager_pass.delete(0, tk.END)
            
            # Sync permissions
            up = udata.get("permissions", [])
            for pid, var in self.perm_vars.items():
                var.set(True if pid in up else False)

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

        selected_perms = [pid for pid, var in self.perm_vars.items() if var.get()]
        
        # Maintain admin status if updating
        is_admin = self.users[uname].get("is_admin", False) if is_update else (uname == "admin")
        
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
            diff_str = "; ".join(changes) if changes else "No permission/passcode changes"
            audit_msg = f"Updated user '{uname.upper()}': {diff_str}"
            act = "USER_UPDATE"
        else:
            audit_msg = f"Created user '{uname.upper()}' (Permissions: [{', '.join(selected_perms)}])"
            act = "USER_CREATE"

        self.users[uname] = {
            "password": h,
            "permissions": selected_perms,
            "is_admin": is_admin
        }
        
        save_users(self.users)
        self.log_audit(act, "USER_MGMT", audit_msg)
        self.refresh_user_list()
        self.u_manager_name.delete(0, tk.END)
        self.u_manager_pass.delete(0, tk.END)
        
        msg = f"User {uname} updated successfully" if is_update else f"User {uname} created successfully"
        messagebox.showinfo("Success", msg)

    def remove_user_submit(self):
        selected = self.user_tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select a user to remove")
            return
        
        uname = self.user_tree.item(selected[0])['values'][0].lower()
        if uname == "admin":
            messagebox.showerror("Error", "Cannot remove primary admin")
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

        cols = 4
        for i, reg in enumerate(dev['registers']):
            r_frame = tk.Frame(cards, bg=self.COLORS["bg_surface"], width=200, height=130,
                               highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
            r_frame.grid(row=i//cols, column=i%cols, padx=5, pady=5)
            r_frame.pack_propagate(False)

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
                     fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(pady=(15, 0))
            
            val_id = f"{ip}:{port}:{slave_id}:{reg}"
            cached_val = self.last_values.get(val_id, "---")
            
            val_container = tk.Frame(r_frame, bg=self.COLORS["bg_surface"])
            val_container.pack(expand=True)
            
            lbl = tk.Label(val_container, text=cached_val, font=("Segoe UI", 22, "bold"), 
                           fg=self.COLORS["accent"], bg=self.COLORS["bg_surface"])
            lbl.pack(side="left")
            
            if unit and unit != "na" and dtype != "BOOLEAN":
                tk.Label(val_container, text=unit, font=("Segoe UI", 12, "bold"), 
                         fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(side="left", padx=(5, 0), pady=(8, 0))
            
            self.live_labels[val_id] = lbl
            
            # Graph Selection Support
            def _select_graph(e, lid=val_id, nm=name):
                self.selected_graph_lid = lid
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

    # -------------------------
    # VIEW: ADD DEVICE
    # -------------------------
    def show_add_device(self):
        self.edit_index = None
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True)

        left = tk.Frame(layout, bg=self.COLORS["bg_surface"], padx=30, pady=20,
                       highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        left.pack(side="left", fill="y", anchor="n")

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

        self.entry_device_name = fld("DEVICE ALIAS / NAME")

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
        map_frame = tk.Frame(left, bg=self.COLORS["bg_surface"])
        map_frame.pack(fill="x", pady=5)
        self.map_frame = map_frame
        
        self.entry_reg_addr = fld("ADDRESS", map_frame)
        self.entry_reg_addr.master.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        # Name Selection
        f_name = tk.Frame(map_frame, bg=self.COLORS["bg_surface"])
        f_name.pack(side="left", fill="x", expand=True)
        tk.Label(f_name, text="NAME", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_name = ttk.Combobox(f_name, values=self.DEFAULT_REGISTER_NAMES, style="TCombobox", font=self.FONTS["body"])
        self.entry_reg_name.pack(fill="x", pady=(2, 0))
        tk.Frame(f_name, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Unit Selection
        f_unit = tk.Frame(map_frame, bg=self.COLORS["bg_surface"])
        f_unit.pack(side="left", fill="x", expand=True, padx=2)
        tk.Label(f_unit, text="UNIT", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_unit = ttk.Combobox(f_unit, values=self.COMMON_UNITS, style="TCombobox", width=6, font=self.FONTS["body"])
        self.entry_reg_unit.pack(fill="x", pady=(2, 0))
        tk.Frame(f_unit, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Type Selection
        f_type = tk.Frame(map_frame, bg=self.COLORS["bg_surface"])
        f_type.pack(side="left", fill="x", expand=True, padx=(2, 0))
        tk.Label(f_type, text="TYPE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_type = ttk.Combobox(f_type, values=self.DATA_TYPES, style="TCombobox", width=12, font=self.FONTS["body"])
        self.entry_reg_type.set("FLOAT32")
        self.entry_reg_type.pack(fill="x", pady=(2, 0))
        tk.Frame(f_type, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Mode Selection
        f_mode = tk.Frame(map_frame, bg=self.COLORS["bg_surface"])
        f_mode.pack(side="left", fill="x", expand=True, padx=(5, 0))
        tk.Label(f_mode, text="MODE", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_mode = ttk.Combobox(f_mode, values=["Output Coil", "Input Coil", "Holding Register", "Input Register"], style="TCombobox", width=14, font=self.FONTS["body"])
        self.entry_reg_mode.set("Input Register")
        self.entry_reg_mode.pack(fill="x", pady=(2, 0))
        tk.Frame(f_mode, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

        # Resolution Selection
        # Resolution / Scaling Section
        f_math = tk.Frame(map_frame, bg=self.COLORS["bg_surface"])
        f_math.pack(side="left", fill="x", expand=True, padx=(5, 0))
        tk.Label(f_math, text="MATH", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        
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
        f_prec = tk.Frame(map_frame, bg=self.COLORS["bg_surface"])
        f_prec.pack(side="left", fill="x", expand=True, padx=(5, 0))
        tk.Label(f_prec, text="DECIMALS", font=("Segoe UI", 8, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_surface"]).pack(anchor="w")
        self.entry_reg_prec = ttk.Combobox(f_prec, values=["0", "1", "2", "3", "4"], style="TCombobox", width=4, font=self.FONTS["body"])
        self.entry_reg_prec.set("3")
        self.entry_reg_prec.pack(fill="x", pady=(2, 0))
        tk.Frame(f_prec, bg=self.COLORS["bg_highlight"], height=1).pack(fill="x")

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
        self.mini_tree.heading("type", text="TYPE")
        self.mini_tree.heading("mode", text="MODE")
        self.mini_tree.heading("math", text="MATH (OP/FACTOR)")
        self.mini_tree.heading("prec", text="DEC")
        
        self.mini_tree.column("addr", width=60)
        self.mini_tree.column("name", width=120)
        self.mini_tree.column("unit", width=60)
        self.mini_tree.column("type", width=80)
        self.mini_tree.column("mode", width=60)
        self.mini_tree.column("math", width=100)
        self.mini_tree.column("prec", width=40)
        self.mini_tree.pack(side="top", fill="both", expand=True)
        
        v_sb = ttk.Scrollbar(mini_cnt, orient="vertical", command=self.mini_tree.yview, style="Vertical.TScrollbar")
        h_sb = ttk.Scrollbar(mini_cnt, orient="horizontal", command=self.mini_tree.xview, style="Horizontal.TScrollbar")
        self.mini_tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        
        v_sb.place(relx=1, rely=0, relheight=1, anchor="ne")
        h_sb.pack(side="bottom", fill="x")
        
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
        self.node_tree.heading("addr", text="DEVICE")
        self.node_tree.heading("slave", text="SLAVE")
        self.node_tree.heading("reg_name", text="REGISTER NAME")
        self.node_tree.heading("reg_addr", text="ADDRESS")
        self.node_tree.heading("reg_unit", text="UNIT")
        self.node_tree.heading("reg_type", text="TYPE")
        self.node_tree.heading("math", text="MATH")
        self.node_tree.heading("mode", text="MODE")
        self.node_tree.heading("prec", text="DEC")
        self.node_tree.heading("status", text="STATE")
        
        self.node_tree.column("id", width=40, anchor="center")
        self.node_tree.column("addr", width=140, anchor="w")
        self.node_tree.column("slave", width=50, anchor="center")
        self.node_tree.column("reg_name", width=160, anchor="w")
        self.node_tree.column("reg_addr", width=80, anchor="center")
        self.node_tree.column("reg_unit", width=60, anchor="center")
        self.node_tree.column("reg_type", width=80, anchor="center")
        self.node_tree.column("math", width=80, anchor="center")
        self.node_tree.column("mode", width=60, anchor="center")
        self.node_tree.column("prec", width=40, anchor="center")
        self.node_tree.column("status", width=100, anchor="center")
        
        self.node_tree.pack(side="top", fill="both", expand=True)
        
        # Custom tags for row coloring and status
        self.node_tree.tag_configure("even", background=self.COLORS["bg_surface"])
        self.node_tree.tag_configure("odd", background=self.COLORS["bg_dark"])
        self.node_tree.tag_configure("online", foreground=self.COLORS["accent"])
        self.node_tree.tag_configure("offline", foreground=self.COLORS["text_dim"])

        v_sb = ttk.Scrollbar(tree_cnt, orient="vertical", command=self.node_tree.yview, style="Vertical.TScrollbar")
        self.node_tree.configure(yscrollcommand=v_sb.set)
        v_sb.place(relx=1, rely=0, relheight=1, anchor="ne")

        acts = tk.Frame(right, bg=self.COLORS["bg_dark"], pady=20)
        acts.pack(side="bottom", fill="x", padx=30)
        
        tk.Button(acts, text="🗑 DELETE NODE", bg=self.COLORS["danger"], fg="white", 
                  font=self.FONTS["button"], relief="flat", padx=25, pady=10, 
                  cursor="hand2", command=self.remove_device_submit).pack(side="right", padx=5)
        
        tk.Button(acts, text="📝 EDIT NODE", bg=self.COLORS["bg_highlight"], fg=self.COLORS["accent"], 
                  font=self.FONTS["button"], relief="flat", padx=25, pady=10, 
                  cursor="hand2", command=self.load_edit_device).pack(side="right", padx=5)

        self.refresh_device_list()

    # -------------------------
    # VIEW: EMAIL CONFIG
    # -------------------------
    def show_email_config(self):
        layout = tk.Frame(self.content_frame, bg=self.COLORS["bg_dark"])
        layout.pack(fill="both", expand=True)

        user_perms = self.users.get(self.current_user, {}).get("permissions", [])
        
        # Only admin or users with specific permission can see SMTP Configuration
        if self.current_user == "admin" or "email_smtp_config" in user_perms:
            # Left Panel: Server Settings
            left = tk.Frame(layout, bg=self.COLORS["bg_surface"], padx=30, pady=20,
                           highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
            left.pack(side="left", fill="y", anchor="n")

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
            self.recipient_email, self.f_recip = fld("RECIPIENT EMAIL", initial=s.get('recipient_email', ''))
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
        self.rules_tree.heading("cond", text="COND")
        self.rules_tree.heading("thresh", text="PROCESS VALUE")
        self.rules_tree.heading("interval", text="INTERVAL")
        self.rules_tree.heading("msg", text="TEMPLATE")
        
        self.rules_tree.column("ip", width=140)
        self.rules_tree.column("reg", width=140)
        self.rules_tree.column("cond", width=60)
        self.rules_tree.column("thresh", width=100)
        self.rules_tree.column("interval", width=80)
        self.rules_tree.column("msg", width=250)
        self.rules_tree.pack(side="top", fill="both", expand=True)

        self.rules_tree.bind("<<TreeviewSelect>>", self.on_rule_select)

        self.refresh_rules_list()

    def save_email_settings_ui(self):
        old_settings = dict(self.email_settings)
        stype = self.server_type_cb.get()
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
        self.log_audit("EMAIL_CONFIG_SAVE", "ALARM", f"Updated Email Settings ({stype}): {diff_str}")
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
                for unverified in [False, True]:
                    if unverified:
                        ssl_ctx = ssl._create_unverified_context()
                    else:
                        try:
                            ssl_ctx = ssl.create_default_context()
                        except Exception:
                            ssl_ctx = ssl._create_unverified_context()
                    try:
                        if target_port == 465:
                            srv = smtplib.SMTP_SSL(target_host, target_port, timeout=15, context=ssl_ctx)
                        else:
                            srv = smtplib.SMTP(target_host, target_port, timeout=15)
                            if srv.has_extn('STARTTLS') or target_port == 587:
                                srv.starttls(context=ssl_ctx)
                        srv.login(user, pwd)
                        return srv, target_port, None
                    except (ssl.SSLError, ssl.SSLCertVerificationError) as ssl_err:
                        if not unverified:
                            continue
                        return None, target_port, ssl_err
                    except Exception as err:
                        return None, target_port, err
                return None, target_port, Exception("Connection failed")

            try:
                if stype in ["Gmail", "SMTP"]:
                    # Support multiple recipients (comma or semicolon)
                    raw_to = self.recipient_email.get().strip()
                    if not raw_to:
                        self.root.after(0, lambda: messagebox.showwarning("Incomplete", "Please specify at least one recipient email address."))
                        return
                    # Replace semicolon with comma then split
                    recipients = [r.strip() for r in raw_to.replace(";", ",").split(",") if r.strip()]
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
                    alt_p = 587 if port == 465 else (465 if port == 587 else 587)
                    if (host, alt_p) not in candidate_targets:
                        candidate_targets.append((host, alt_p))
                    
                    # Add domain-specific candidate hosts for custom domains
                    if domain and '.' in domain:
                        cpanel_host = f"mail.{domain}"
                        if (cpanel_host, 465) not in candidate_targets:
                            candidate_targets.append((cpanel_host, 465))
                        if (cpanel_host, 587) not in candidate_targets:
                            candidate_targets.append((cpanel_host, 587))
                        if ("smtp.office365.com", 587) not in candidate_targets:
                            candidate_targets.append(("smtp.office365.com", 587))
                        if ("smtpout.secureserver.net", 465) not in candidate_targets:
                            candidate_targets.append(("smtpout.secureserver.net", 465))
                        if ("smtpout.secureserver.net", 587) not in candidate_targets:
                            candidate_targets.append(("smtpout.secureserver.net", 587))

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
                        if isinstance(last_err, smtplib.SMTPServerDisconnected):
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
                self.root.after(0, lambda: messagebox.showerror("Failure", f"Test failed: {str(e)}"))

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

    def check_alert_logic(self, alert, current_value, is_triggered):
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
            recipients = [r.strip() for r in raw_to.replace(";", ",").split(",") if r.strip()]
            
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
                for unverified in [False, True]:
                    if unverified:
                        ssl_ctx = ssl._create_unverified_context()
                    else:
                        try:
                            ssl_ctx = ssl.create_default_context()
                        except Exception:
                            ssl_ctx = ssl._create_unverified_context()
                    try:
                        if target_port == 465:
                            srv = smtplib.SMTP_SSL(target_host, target_port, timeout=15, context=ssl_ctx)
                        else:
                            srv = smtplib.SMTP(target_host, target_port, timeout=15)
                            if srv.has_extn('STARTTLS') or target_port == 587:
                                srv.starttls(context=ssl_ctx)
                        srv.login(sender_usr, sender_pwd)
                        return srv, target_port, None
                    except (ssl.SSLError, ssl.SSLCertVerificationError) as ssl_err:
                        if not unverified:
                            continue
                        return None, target_port, ssl_err
                    except Exception as err:
                        return None, target_port, err
                return None, target_port, Exception("Connection failed")

            domain = sender_usr.split('@')[-1] if '@' in sender_usr else ''
            candidate_targets = [(smtp_server, smtp_port)]
            alt_p = 587 if smtp_port == 465 else (465 if smtp_port == 587 else 587)
            if (smtp_server, alt_p) not in candidate_targets:
                candidate_targets.append((smtp_server, alt_p))
            
            if domain and '.' in domain:
                cpanel_host = f"mail.{domain}"
                if (cpanel_host, 465) not in candidate_targets: candidate_targets.append((cpanel_host, 465))
                if (cpanel_host, 587) not in candidate_targets: candidate_targets.append((cpanel_host, 587))
                if ("smtp.office365.com", 587) not in candidate_targets: candidate_targets.append(("smtp.office365.com", 587))

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
            # Live Update UI
            ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.root.after(0, lambda: self._append_email_log_live([
                ts_now, dev_alias, only_reg_name, raw_to, "SENT"
            ]))
            
            self.root.after(0, lambda: self.log(f"SUCCESS: Alert email sent at {datetime.now().strftime('%H:%M:%S')} for {full_name}"))
        except Exception as e:
            self.root.after(0, lambda: self.log(f"FAILURE: Email send failed: {e}"))

    def _connect_smtp(self, sender_usr, sender_pwd, smtp_server, smtp_port):
        def try_smtp_connect(target_host, target_port):
            for unverified in [False, True]:
                if unverified:
                    ssl_ctx = ssl._create_unverified_context()
                else:
                    try:
                        ssl_ctx = ssl.create_default_context()
                    except Exception:
                        ssl_ctx = ssl._create_unverified_context()
                try:
                    if target_port == 465:
                        srv = smtplib.SMTP_SSL(target_host, target_port, timeout=15, context=ssl_ctx)
                    else:
                        srv = smtplib.SMTP(target_host, target_port, timeout=15)
                        if srv.has_extn('STARTTLS') or target_port == 587:
                            srv.starttls(context=ssl_ctx)
                    srv.login(sender_usr, sender_pwd)
                    return srv, target_port, None
                except (ssl.SSLError, ssl.SSLCertVerificationError) as ssl_err:
                    if not unverified:
                        continue
                    return None, target_port, ssl_err
                except Exception as err:
                    return None, target_port, err
            return None, target_port, Exception("Connection failed")

        domain = sender_usr.split('@')[-1] if '@' in sender_usr else ''
        candidate_targets = [(smtp_server, smtp_port)]
        alt_p = 587 if smtp_port == 465 else (465 if smtp_port == 587 else 587)
        if (smtp_server, alt_p) not in candidate_targets:
            candidate_targets.append((smtp_server, alt_p))
        
        if domain and '.' in domain:
            cpanel_host = f"mail.{domain}"
            if (cpanel_host, 465) not in candidate_targets: candidate_targets.append((cpanel_host, 465))
            if (cpanel_host, 587) not in candidate_targets: candidate_targets.append((cpanel_host, 587))
            if ("smtp.office365.com", 587) not in candidate_targets: candidate_targets.append(("smtp.office365.com", 587))

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

    def send_scheduled_pdf_email(self, interval_str, interval_sec):
        s = self.email_settings
        raw_to = s.get('recipient_email', '')
        recipients = [r.strip() for r in raw_to.replace(";", ",").split(",") if r.strip()]
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

                device_list = list(self.devices)
                if not device_list:
                    c.setFillColorRGB(0.0, 0.36, 0.72); c.setFont("Helvetica-Bold", 18)
                    c.drawString(40, h-50, "TempTrek Environmental Monitoring System")
                    c.setFont("Helvetica", 9); c.setFillColorRGB(0.5, 0.5, 0.5)
                    c.drawString(40, h-70, f"SCHEDULED REPORT ({interval_str.upper()}) | GENERATED: {to_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                    c.drawString(40, h-85, f"RANGE: {from_dt.strftime('%Y-%m-%d %H:%M')} TO {to_dt.strftime('%Y-%m-%d %H:%M')}")
                    c.line(40, h-95, w-40, h-95)
                    c.drawString(40, h-130, "No configured devices found.")
                else:
                    for dev_idx, dev in enumerate(device_list):
                        if dev_idx > 0:
                            c.showPage()

                        ip, port, slave = dev['ip'], str(dev['port']), str(dev.get('slave_id', 1))
                        d_alias = dev.get('device_name', ip)
                        registers = [str(r) for r in dev.get('registers', [])]
                        reg_names = [dev.get('names', {}).get(r, f"Reg {r}") for r in registers]
                        num_params = len(reg_names)

                        col_spacing = (w - 200) / (num_params if num_params > 0 else 1)
                        f_size = 9 if num_params <= 6 else (8 if num_params <= 10 else 7)

                        # Header
                        c.setFillColorRGB(0.0, 0.36, 0.72); c.setFont("Helvetica-Bold", 18)
                        c.drawString(40, h-50, "TempTrek Environmental Monitoring System")
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
                                        if ts_str not in pivoted: pivoted[ts_str] = {}
                                        pivoted[ts_str][r_addr] = val
                            except: pass

                        # Build series data for Graph Preview
                        all_series = []
                        sorted_ts = sorted(pivoted.keys())
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
                                    try:
                                        if dev.get('types', {}).get(str(r_addr)) == "BOOLEAN":
                                            label_0 = dev.get('ops', {}).get(str(r_addr), "OFF")
                                            label_1 = dev.get('factors', {}).get(str(r_addr), "ON")
                                            v = label_0 if int(float(v)) == 0 else label_1
                                        else:
                                            prec = int(dev.get('decimals', {}).get(str(r_addr), 3))
                                            v = f"{float(v):.{prec}f}"
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
            msg['Subject'] = f"PERIODIC DATA REPORT ({interval_str}): {base_sub}"

            body = (f"TempTrek Environmental Monitoring System\n"
                    f"=========================================\n"
                    f"SCHEDULED PDF DATA REPORT ({interval_str.upper()})\n"
                    f"=========================================\n"
                    f"Report Time Window: {from_dt.strftime('%Y-%m-%d %H:%M:%S')} to {to_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Generated At: {to_dt.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
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
                for r in recipients:
                    try:
                        msg.replace_header('To', r)
                    except Exception:
                        msg['To'] = r
                    try:
                        server.sendmail(sender_usr, [r], msg.as_string())
                        write_email_log_csv("System Periodic Report", f"{interval_str} PDF Report", r, "SUCCESS")
                        ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.root.after(0, lambda r_tgt=r: self._append_email_log_live([ts_now, "System Periodic Report", f"{interval_str} PDF Report", r_tgt, "SUCCESS"]))
                    except Exception as send_err:
                        write_email_log_csv("System Periodic Report", f"{interval_str} PDF Report", r, f"FAIL: {send_err}")
                try: server.quit()
                except: pass

                self.log_audit("SCHEDULED_REPORT_EMAIL", "EXPORT", f"Sent scheduled {interval_str} PDF report email to [{', '.join(recipients)}]")
                self.root.after(0, lambda: self.log(f"SCHEDULED PDF EMAIL SENT ({interval_str}) to {', '.join(recipients)}"))
            else:
                for r in recipients:
                    write_email_log_csv("System Periodic Report", f"{interval_str} PDF Report", r, f"FAIL: {err}")
                self.root.after(0, lambda: self.log(f"SCHEDULED PDF EMAIL FAILED: {err}"))
        except Exception as ex:
            self.root.after(0, lambda: self.log(f"Scheduled PDF email dispatch failed: {ex}"))

    # -------------------------
    # VIEW: DATA EXPORT
    # -------------------------
    def show_export_data(self):
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

        tk.Label(card, text="Excel files are generated as CSV. PDF reports use high-resolution formatting.", 
                 font=("Segoe UI", 7), bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"]).pack(pady=15)

    def perform_export(self, fmt="excel"):
        idx = self.export_node.current()
        if idx < 0: 
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
            from_dt = get_dt(self.export_from_selectors)
            to_dt = get_dt(self.export_to_selectors)
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
        sorted_regs = sorted([int(r) for r in dev_names.keys()])
        reg_headers = [dev_names.get(str(r), f"Reg {r}") for r in sorted_regs]
        
        # Select Export Folder
        export_dir = filedialog.askdirectory(title="Select Folder to Save Data", initialdir=DATA_PATH)
        if not export_dir: return

        if fmt == "excel":
            target_name = f"Export_{clean_alias}_{datetime.now().strftime('%H%M%S')}.csv"
            target_file = os.path.join(export_dir, target_name)
            
            # Pivot the reading data by timestamp
            pivoted_data = {} # {ts_string: {reg_name: val}}
            
            # Interval Logic
            interval_str = self.export_interval_cb.get()
            interval_map = {"1 Min": 60, "5 Min": 300, "15 Min": 900, "30 Min": 1800, "1 Hour": 3600}
            interval_sec = interval_map.get(interval_str, 0)

            try:
                for source_file in all_files:
                    with open(source_file, 'r', encoding='utf-8', errors='replace') as f_in:
                        reader = csv.reader((line.replace('\0', '') for line in f_in))
                        next(reader, None) 
                        for row in reader:
                            try:
                                row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                                if from_dt <= row_dt <= to_dt:
                                    ts = row[0]
                                    if interval_sec > 0:
                                        rounded_ts = int(row_dt.timestamp() // interval_sec * interval_sec)
                                        ts = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                                    
                                    reg_addr = str(row[4])
                                    reg_name = dev_names.get(reg_addr, f"Reg {reg_addr}")
                                    if ts not in pivoted_data: pivoted_data[ts] = {}
                                    if dev.get('types', {}).get(reg_addr) == "BOOLEAN":
                                        label_0 = dev.get('ops', {}).get(reg_addr, "OFF")
                                        label_1 = dev.get('factors', {}).get(reg_addr, "ON")
                                        try:
                                            pivoted_data[ts][reg_name] = label_0 if int(float(row[5])) == 0 else label_1
                                        except:
                                            pivoted_data[ts][reg_name] = row[5]
                                    else:
                                        try:
                                            prec = int(dev_decimals.get(reg_addr, 3))
                                            pivoted_data[ts][reg_name] = f"{float(row[5]):.{prec}f}"
                                        except:
                                            pivoted_data[ts][reg_name] = row[5]
                            except: continue

                if pivoted_data:
                    with open(target_file, 'w', newline='') as f_out:
                        writer = csv.writer(f_out)
                        header = ["Timestamp", "Device Name"] + reg_headers
                        writer.writerow(header)
                        
                        # Sort by timestamp
                        for ts in sorted(pivoted_data.keys()):
                            row_vals = [ts, dev_alias]
                            for rname in reg_headers:
                                row_vals.append(pivoted_data[ts].get(rname, ""))
                            writer.writerow(row_vals)
                    
                    messagebox.showinfo("Success", f"Data pivoted and exported to {target_file}")
                    self.log_audit("DATA_EXPORT", "EXPORT", f"Exported CSV data for device '{dev_alias}' ({len(pivoted_data)} records)")
                    webbrowser.open(target_file)
                else:
                    messagebox.showwarning("No Data", "No records found for the range.")
            except Exception as e: messagebox.showerror("Error", f"Excel Pivot Export failed: {e}")

        elif fmt == "pdf":
            if not HAS_REPORTLAB:
                messagebox.showerror("Error", "Feature unavailable: reportlab library not found.")
                return
            
            target_name = f"Report_{clean_alias}_{datetime.now().strftime('%H%M%S')}.pdf"
            target_file = os.path.join(export_dir, target_name)
            
            # Pivot the reading data for PDF
            pivoted_data = {} # {ts_string: {reg_name: val}}
            reg_names_only = [dev_names.get(str(r), f"Reg {r}") for r in sorted_regs]
            
            # Interval Logic
            interval_str = self.export_interval_cb.get()
            interval_map = {"1 Min": 60, "5 Min": 300, "15 Min": 900, "30 Min": 1800, "1 Hour": 3600}
            interval_sec = interval_map.get(interval_str, 0)

            try:
                for source_file in all_files:
                    with open(source_file, 'r', encoding='utf-8', errors='replace') as f_in:
                        reader = csv.reader((line.replace('\0', '') for line in f_in))
                        next(reader, None) 
                        for row in reader:
                            try:
                                row_dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                                if from_dt <= row_dt <= to_dt:
                                    ts = row[0]
                                    if interval_sec > 0:
                                        rounded_ts = int(row_dt.timestamp() // interval_sec * interval_sec)
                                        ts = datetime.fromtimestamp(rounded_ts).strftime("%Y-%m-%d %H:%M:%S")
                                    
                                    reg_addr = str(row[4])
                                    reg_name = dev_names.get(reg_addr, f"Reg {reg_addr}")
                                    if ts not in pivoted_data: pivoted_data[ts] = {}
                                    if dev.get('types', {}).get(reg_addr) == "BOOLEAN":
                                        label_0 = dev.get('ops', {}).get(reg_addr, "OFF")
                                        label_1 = dev.get('factors', {}).get(reg_addr, "ON")
                                        try:
                                            pivoted_data[ts][reg_name] = label_0 if int(float(row[5])) == 0 else label_1
                                        except:
                                            pivoted_data[ts][reg_name] = row[5]
                                    else:
                                        try:
                                            prec = int(dev_decimals.get(reg_addr, 3))
                                            pivoted_data[ts][reg_name] = f"{float(row[5]):.{prec}f}"
                                        except:
                                            pivoted_data[ts][reg_name] = row[5]
                            except: continue

                if not pivoted_data:
                    messagebox.showwarning("No Data", "No records found for the range.")
                    return

                # Generate Optimized PDF
                c = canvas.Canvas(target_file, pagesize=letter)
                w, h = letter
                
                def draw_report_header(canvas_obj, page_num):
                    # Background
                    canvas_obj.setFillColorRGB(1, 1, 1); canvas_obj.rect(0, 0, w, h, fill=1)
                    
                    # Title
                    canvas_obj.setFillColorRGB(0.01, 0.35, 0.73); canvas_obj.setFont("Helvetica-Bold", 18)
                    canvas_obj.drawString(40, h-50, "TempTrek Environmental Monitoring System")
                    
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
                self.log_audit("DATA_EXPORT", "EXPORT", f"Exported PDF report for device '{dev_alias}' ({len(sorted_ts)} records)")
                webbrowser.open(target_file)
            except Exception as e: messagebox.showerror("Error", f"PDF Export failed: {e}")

    def add_reg_to_mini(self):
        addr = self.entry_reg_addr.get().strip()
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
        
        if not addr: return
        try:
            int(addr)
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
        except: messagebox.showerror("Error", "Address must be integer")

    def on_mini_select(self, event):
        sel = self.mini_tree.selection()
        if not sel:
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
        addr = vals[0] if len(vals) > 0 else ""
        name = vals[1] if len(vals) > 1 else ""
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
        
        self.entry_reg_addr.delete(0, tk.END)
        self.entry_reg_addr.insert(0, addr)
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
                addr, name, unit, dtype, reg_mode_ui, math_str, dec_prec = vals[0], vals[1], vals[2], vals[3], str(vals[4]), str(vals[5]), str(vals[6])
                rl.append(int(addr))
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
        tree.heading("reg", text="ADDR")
        tree.heading("val", text="READING")
        tree.heading("cond", text="COND")
        tree.heading("thresh", text="THRESH")
        
        tree.column("time", width=140, anchor="center")
        tree.column("device", width=140, anchor="w")
        tree.column("field", width=160, anchor="w")
        tree.column("reg", width=60, anchor="center")
        tree.column("val", width=80, anchor="center")
        tree.column("cond", width=60, anchor="center")
        tree.column("thresh", width=80, anchor="center")
        
        v_sb = ttk.Scrollbar(cnt, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=v_sb.set)
        v_sb.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
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
        tree.heading("device", text="DEVICE")
        tree.heading("field", text="FIELD")
        tree.heading("recipient", text="RECIPIENT")
        tree.heading("status", text="STATUS")
        
        tree.column("time", width=140, anchor="center")
        tree.column("device", width=140, anchor="w")
        tree.column("field", width=140, anchor="w")
        tree.column("recipient", width=180, anchor="w")
        tree.column("status", width=80, anchor="center")
        
        v_sb = ttk.Scrollbar(cnt, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=v_sb.set)
        v_sb.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
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
                c.drawString(40, h-45, "TempTrek Environmental Monitoring System")
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
        self.log_tree.heading("node", text="NODE")
        self.log_tree.heading("name", text="FIELD NAME")
        self.log_tree.heading("addr", text="ADDR")
        self.log_tree.heading("val", text="DATA READING")
        self.log_tree.heading("unit", text="UNIT")
        self.log_tree.heading("dtype", text="TYPE")
        self.log_tree.heading("dt", text="TIMESTAMP")
        
        self.log_tree.column("sno", width=60, anchor="center")
        self.log_tree.column("device", width=140, anchor="w")
        self.log_tree.column("node", width=140, anchor="w")
        self.log_tree.column("name", width=180, anchor="w")
        self.log_tree.column("addr", width=60, anchor="center")
        self.log_tree.column("val", width=100, anchor="center")
        self.log_tree.column("unit", width=60, anchor="center")
        self.log_tree.column("dtype", width=80, anchor="center")
        self.log_tree.column("dt", width=120, anchor="center")
        
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
            self.check_scheduled_pdf_email()
        except Exception:
            pass
        
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
                            if not nm:
                                try:
                                    idx = d['registers'].index(int(rn))
                                    if idx < len(self.DEFAULT_REGISTER_NAMES):
                                        nm = self.DEFAULT_REGISTER_NAMES[idx]
                                        if not unit: unit = self.DEFAULT_REGISTER_UNITS[idx]
                                        dtype = self.DEFAULT_REGISTER_TYPES[idx]
                                except: pass
                            break
                    
                    # Update live dash
                    lid = f"{ip}:{port}:{slave}:{rn}"
                    if lid in self.live_labels:
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
        self.audit_cat_cb = ttk.Combobox(bar, values=["ALL", "AUTHENTICATION", "DEVICE", "ALARM", "USER_MGMT", "EXPORT"], width=15, style="TCombobox", state="readonly")
        self.audit_cat_cb.set("ALL")
        self.audit_cat_cb.pack(side="left", padx=5)

        tk.Label(bar, text="SEARCH:", font=("Segoe UI", 9, "bold"), fg=self.COLORS["text_dim"], bg=self.COLORS["bg_dark"]).pack(side="left", padx=(15, 5))
        self.audit_search_entry = tk.Entry(bar, bg=self.COLORS["bg_surface"], fg=self.COLORS["text_main"], font=self.FONTS["body"], relief="flat", width=20, insertbackground="black")
        self.audit_search_entry.pack(side="left", padx=5, ipady=4)

        tk.Button(bar, text="🔍 FILTER", bg=self.COLORS["accent"], fg="black", font=self.FONTS["button"], relief="flat", padx=10, command=self.refresh_audit_log_view).pack(side="left", padx=5)
        tk.Button(bar, text="↺ RESET", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], font=self.FONTS["button"], relief="flat", padx=10, command=lambda: (self.audit_cat_cb.set("ALL"), self.audit_search_entry.delete(0, tk.END), self.refresh_audit_log_view())).pack(side="left", padx=5)

        tk.Button(bar, text="📁 FOLDER", bg=self.COLORS["bg_surface"], fg=self.COLORS["text_dim"], font=self.FONTS["button"], relief="flat", padx=12, command=self.open_logs_folder).pack(side="right", padx=5)
        tk.Button(bar, text="💾 EXPORT CSV", bg=self.COLORS["success"], fg="white", font=self.FONTS["button"], relief="flat", padx=12, command=self.export_audit_log_to_csv).pack(side="right", padx=5)

        cnt = tk.Frame(self.content_frame, bg=self.COLORS["bg_surface"], highlightbackground=self.COLORS["bg_highlight"], highlightthickness=1)
        cnt.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        cols = ("timestamp", "user", "action", "category", "details")
        self.audit_tree = ttk.Treeview(cnt, columns=cols, show="headings", style="Treeview")

        self.audit_tree.heading("timestamp", text="TIMESTAMP")
        self.audit_tree.heading("user", text="USER")
        self.audit_tree.heading("action", text="ACTION")
        self.audit_tree.heading("category", text="CATEGORY")
        self.audit_tree.heading("details", text="DETAILS")

        self.audit_tree.column("timestamp", width=140, anchor="center")
        self.audit_tree.column("user", width=110, anchor="w")
        self.audit_tree.column("action", width=130, anchor="w")
        self.audit_tree.column("category", width=130, anchor="w")
        self.audit_tree.column("details", width=420, anchor="w")

        v_sb = ttk.Scrollbar(cnt, orient="vertical", command=self.audit_tree.yview)
        self.audit_tree.configure(yscrollcommand=v_sb.set)
        v_sb.pack(side="right", fill="y")
        self.audit_tree.pack(fill="both", expand=True)

        self.refresh_audit_log_view()

    def refresh_audit_log_view(self):
        if not hasattr(self, 'audit_tree') or not self.audit_tree.winfo_exists():
            return
        
        for item in self.audit_tree.get_children():
            self.audit_tree.delete(item)

        cat_filter = self.audit_cat_cb.get().upper() if hasattr(self, 'audit_cat_cb') else "ALL"
        search_query = self.audit_search_entry.get().strip().lower() if hasattr(self, 'audit_search_entry') else ""

        logs_to_display = []
        date_str = datetime.now().strftime("%Y-%m-%d")
        fn = os.path.join(DATA_PATH, "live", f"Audit_Logs_{date_str}.csv")
        if os.path.exists(fn):
            try:
                with open(fn, "r", encoding='utf-8', errors='replace') as f:
                    reader = csv.reader((line.replace('\0', '') for line in f))
                    header = next(reader, None)
                    for row in reader:
                        if len(row) >= 5:
                            logs_to_display.append(tuple(row[:5]))
            except Exception:
                pass

        # Also merge in-memory log entries for entries not yet on disk or fallback
        for entry in list(self.global_audit_log):
            if entry not in logs_to_display:
                logs_to_display.append(entry)

        # Sort descending by timestamp
        logs_to_display.sort(key=lambda x: x[0], reverse=True)

        for entry in logs_to_display:
            ts, user, action, category, details = entry[0], entry[1], entry[2], entry[3], entry[4]
            if cat_filter != "ALL" and category.upper() != cat_filter:
                continue
            if search_query:
                combined = f"{ts} {user} {action} {category} {details}".lower()
                if search_query not in combined:
                    continue
            self.audit_tree.insert("", "end", values=(ts, user, action, category, details))

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
                writer.writerow(["Timestamp", "Username", "Action", "Category", "Details"])
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
    app = ModbusApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
