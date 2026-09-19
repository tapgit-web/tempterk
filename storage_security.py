"""Storage primitives shared by TempTrek and its regression tests."""
import base64
import ctypes
import functools
import hashlib
import hmac
import json
import os
import queue
import secrets
import shutil
import stat
import ssl
import smtplib
import certifi
import subprocess
import tempfile
import threading
from pathlib import Path

STORAGE_LOCK = threading.RLock()
STORAGE_FROZEN = threading.Event()
STORAGE_ERRORS = queue.Queue()
DPAPI_PREFIX = "TTDPAPI1:"
BACKUP_MAGIC = b"TTBACKUP2\0"


def storage_operation(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        # Never make the UI wait for a worker which may be posting a UI callback.
        acquired = STORAGE_LOCK.acquire(blocking=threading.current_thread() is not threading.main_thread())
        if not acquired:
            raise RuntimeError("Storage is busy with another operation. Please try again.")
        try:
            if STORAGE_FROZEN.is_set():
                raise RuntimeError("Restart TempTrek after restoration before writing data.")
            return function(*args, **kwargs)
        except Exception as exc:
            STORAGE_ERRORS.put(f"{function.__name__}: {exc}")
            raise
        finally:
            STORAGE_LOCK.release()
    return wrapped


def hash_password(password):
    salt = secrets.token_bytes(16)
    rounds = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        if encoded.startswith("pbkdf2_sha256$"):
            _, rounds, salt, digest = encoded.split("$")
            rounds = int(rounds)
            if not 100_000 <= rounds <= 2_000_000:
                return False
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), rounds).hex()
        else:
            # Read-only compatibility; successful login upgrades this hash.
            if len(encoded) != 64:
                return False
            actual = hashlib.sha256(password.encode()).hexdigest()
            digest = encoded
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError, AttributeError):
        return False


def _dpapi(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Secure configuration storage requires Windows DPAPI.")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel.LocalFree(result.pbData)


def protect_text(text):
    return DPAPI_PREFIX + base64.b64encode(_dpapi(text.encode("utf-8"))).decode("ascii")


def unprotect_text(text):
    return _dpapi(base64.b64decode(text[len(DPAPI_PREFIX):], validate=True), decrypt=True).decode("utf-8")


def recover_legacy_company_details(text):
    """Recover only the old, non-secret company-header schema.

    Its fixed JSON prefix identifies the repeating hardware-derived XOR key.
    This is deliberately not a fallback for accounts or other settings, and
    never applies to DPAPI ciphertext or substitutes defaults for damaged data.
    """
    if text.startswith(DPAPI_PREFIX):
        raise ValueError("Windows-encrypted settings require their original Windows account.")
    raw = base64.b64decode(text.strip(), validate=True).decode("utf-8")
    prefix = '{\n    "company_name"'
    if len(raw) < 16:
        raise ValueError("Incomplete legacy company settings.")
    key = "".join(chr(ord(raw[i]) ^ ord(prefix[i])) for i in range(16))
    if any(c not in "0123456789ABCDEF" for c in key):
        raise ValueError("Unrecognized legacy company encoding.")
    decoded = "".join(chr(ord(c) ^ ord(key[i % 16])) for i, c in enumerate(raw))
    value = json.loads(decoded)
    if (not isinstance(value, dict) or set(value) != {"company_name", "department_name", "report_title"}
            or any(not isinstance(v, str) or len(v) > 4096 for v in value.values())):
        raise ValueError("Invalid legacy company settings schema.")
    return value


def recover_legacy_config(text, company_reference):
    """Decode settings with the key established by a validated legacy header."""
    if text.startswith(DPAPI_PREFIX):
        raise ValueError("Windows-encrypted settings cannot use legacy recovery.")
    recover_legacy_company_details(company_reference)
    reference = base64.b64decode(company_reference.strip(), validate=True).decode("utf-8")
    prefix = '{\n    "company_name"'
    key = "".join(chr(ord(reference[i]) ^ ord(prefix[i])) for i in range(16))
    raw = base64.b64decode(text.strip(), validate=True).decode("utf-8")
    return json.loads("".join(chr(ord(c) ^ ord(key[i % 16])) for i, c in enumerate(raw)))


@storage_operation
def atomic_write(path, data):
    """Flush a unique sibling file before replacing the original."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".temptrek-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data if isinstance(data, bytes) else data.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def encrypt_backup(data, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(password) < 6:
        raise ValueError("Use a backup password of at least 6 characters.")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    header = BACKUP_MAGIC + salt + nonce
    return header + AESGCM(key).encrypt(nonce, data, header)


def decrypt_backup(data, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    offset = len(BACKUP_MAGIC)
    if not data.startswith(BACKUP_MAGIC) or len(data) < offset + 44:
        raise ValueError("Invalid encrypted backup.")
    salt, nonce = data[offset:offset + 16], data[offset + 16:offset + 28]
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    try:
        return AESGCM(key).decrypt(nonce, data[offset + 28:], data[:offset + 28])
    except InvalidTag as exc:
        raise ValueError("Incorrect backup password or damaged backup.") from exc


def require_windows_admin():
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError("Backup management requires Windows administrator rights. Run TempTrek as administrator.")


def secure_backup_root():
    return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "TempTrekBackups")


def protect_admin_path(path, is_directory=False):
    """Replace the entire DACL and owner; never silently ignore ACL failures."""
    require_windows_admin()
    script = r'''
$ErrorActionPreference = 'Stop'
$target = $env:TEMPTREK_ACL_TARGET
$directory = $env:TEMPTREK_ACL_DIRECTORY -eq '1'
$acl = if ($directory) { New-Object System.Security.AccessControl.DirectorySecurity } else { New-Object System.Security.AccessControl.FileSecurity }
$acl.SetAccessRuleProtection($true, $false)
$admin = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
$system = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
$acl.SetOwner($admin)
$inherit = if ($directory) { [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' } else { [System.Security.AccessControl.InheritanceFlags]::None }
foreach ($sid in @($admin, $system)) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', $inherit, 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $target -AclObject $acl
'''
    env = dict(os.environ, TEMPTREK_ACL_TARGET=os.path.abspath(path),
               TEMPTREK_ACL_DIRECTORY="1" if is_directory else "0")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   env=env, check=True, capture_output=True, text=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def connect_smtp(host, port, username, password):
    context = ssl.create_default_context()
    # Supplement Windows/system trust without replacing private corporate CAs.
    context.load_verify_locations(cafile=certifi.where())
    server = None
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            server.starttls(context=context)  # Required: no plaintext fallback.
            server.ehlo()
        server.login(username, password)
        return server
    except Exception:
        if server is not None:
            server.close()
        raise


def replace_folders(data_root, staged_root):
    """Rename originals aside, install staged folders, and roll back on error.

    Staging and rollback must be on the data volume. A journal also allows the
    next process to roll back an interrupted transaction before loading config.
    """
    root = Path(data_root).resolve()
    staged = Path(staged_root).resolve()
    if staged.parent != root:
        raise ValueError("Restore staging must be directly inside the data directory.")
    rollback = Path(tempfile.mkdtemp(prefix=".restore-old-", dir=root))
    journal = root / ".restore-journal.json"
    entries = [{"name": name, "existed": (root / name).exists()}
               for name in ("config", "live", "reports")]
    atomic_write(journal, json.dumps({"rollback": rollback.name, "entries": entries}))
    try:
        for entry in entries:
            name = entry["name"]
            if entry["existed"]:
                os.replace(root / name, rollback / name)
            os.replace(staged / name, root / name)
    except Exception:
        recover_restore(root)
        raise
    return journal, rollback


def remove_tree(path):
    def writable_retry(function, target, error):
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        function(target)
    shutil.rmtree(path, onerror=writable_retry)


def recover_restore(data_root):
    root = Path(data_root).resolve()
    journal = root / ".restore-journal.json"
    if not journal.exists():
        return
    data = json.loads(journal.read_text(encoding="utf-8"))
    rollback = root / data["rollback"]
    if rollback.parent != root or not rollback.name.startswith(".restore-old-") or rollback.is_symlink():
        raise ValueError("Invalid restore recovery journal; manual recovery required.")
    for entry in reversed(data["entries"]):
        name = entry["name"]
        if name not in ("config", "live", "reports"):
            raise ValueError("Invalid recovery folder.")
        original, target = rollback / name, root / name
        if original.exists():
            if target.exists():
                remove_tree(target)
            os.replace(original, target)
        elif not entry["existed"] and target.exists():
            remove_tree(target)
    journal.unlink()
    remove_tree(rollback)


def commit_restore(journal, rollback):
    # Removing the journal is the commit point. Old copies may be cleaned later.
    journal.unlink()
    try:
        remove_tree(rollback)
    except OSError as exc:
        STORAGE_ERRORS.put(f"Restoration succeeded; old folder cleanup failed: {exc}")
