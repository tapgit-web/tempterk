import subprocess
import hashlib
import uuid

def get_hwid():
    """Generates a stable unique ID for this specific PC using Disk Serial (C: drive)."""
    try:
        # Use Volume Serial of C: - very stable on Windows
        out = subprocess.check_output('vol c:', shell=True).decode()
        serial = out.split()[-1].replace('-', '')
        hwid = hashlib.sha256(serial.encode()).hexdigest()[:16].upper()
        method = "Volume Serial Number of C: drive"
    except Exception as e:
        # Fallback to MAC address if Volume Serial fails
        mac = uuid.getnode()
        hwid = hashlib.sha256(str(mac).encode()).hexdigest()[:16].upper()
        method = "MAC Address (Fallback)"
        
    return hwid, method

if __name__ == "__main__":
    hwid, method = get_hwid()
    print("=" * 50)
    print("           TEMPTREK HARDWARE ID (HWID) TOOL")
    print("=" * 50)
    print(f"HWID:   {hwid}")
    print(f"Method: {method}")
    print("=" * 50)
