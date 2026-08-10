import os
import base64
import hashlib
import uuid
import shutil
import subprocess

def get_hwid():
    try:
        out = subprocess.check_output('vol c:', shell=True).decode()
        serial = out.split()[-1].replace('-', '')
        return hashlib.sha256(serial.encode()).hexdigest()[:16].upper()
    except:
        mac = uuid.getnode()
        return hashlib.sha256(str(mac).encode()).hexdigest()[:16].upper()

def deobfuscate_data(encoded_str):
    try:
        raw_data = base64.b64decode(encoded_str).decode()
        cur_key = get_hwid()
        decoded = "".join(chr(ord(c) ^ ord(cur_key[i % len(cur_key)])) for i, c in enumerate(raw_data))
        if decoded.strip().startswith(("{", "[")): return decoded
        mac_key = hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()[:16].upper()
        decoded_mac = "".join(chr(ord(c) ^ ord(mac_key[i % len(mac_key)])) for i, c in enumerate(raw_data))
        if decoded_mac.strip().startswith(("{", "[")): return decoded_mac
        return None
    except: return None

base_dir = os.path.dirname(os.path.abspath(__file__))
src_config = os.path.join(base_dir, "config")
src_live = os.path.join(base_dir, "live")
src_reports = os.path.join(base_dir, "reports")

export_dir = os.path.join(base_dir, "export_bundle")
exp_config = os.path.join(export_dir, "config")

os.makedirs(export_dir, exist_ok=True)
os.makedirs(exp_config, exist_ok=True)

if os.path.exists(src_config):
    for file in os.listdir(src_config):
        if file.endswith(".json"):
            with open(os.path.join(src_config, file), "r") as f:
                content = f.read().strip()
            decoded = deobfuscate_data(content)
            if decoded:
                with open(os.path.join(exp_config, file), "w") as f:
                    f.write(decoded)
            else:
                with open(os.path.join(exp_config, file), "w") as f:
                    f.write(content)

# Copy live and reports
if os.path.exists(src_live): shutil.copytree(src_live, os.path.join(export_dir, "live"), dirs_exist_ok=True)
if os.path.exists(src_reports): shutil.copytree(src_reports, os.path.join(export_dir, "reports"), dirs_exist_ok=True)

print("Data unlocked and exported to export_bundle/")
