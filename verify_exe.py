"""Smoke-test a packaged EXE with an isolated Windows application-data folder."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    executable = Path(__file__).resolve().parent / "dist" / "TempTrek Environmental Monitoring System.exe"
    with tempfile.TemporaryDirectory(prefix="temptrek-exe-check-") as scratch:
        process = subprocess.Popen([str(executable)], env=dict(os.environ, APPDATA=scratch),
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
            user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            deadline = time.monotonic() + 35
            titles = []
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"EXE exited before startup: {process.returncode}")
                result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    f"Get-CimInstance Win32_Process -Filter 'ParentProcessId = {process.pid}' | Select-Object -ExpandProperty ProcessId"],
                    capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                pids = {process.pid} | {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}
                titles = []
                @callback_type
                def visit(window, param):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
                    if pid.value in pids:
                        buffer = ctypes.create_unicode_buffer(512)
                        user32.GetWindowTextW(window, buffer, len(buffer))
                        if buffer.value:
                            titles.append(buffer.value)
                    return True
                user32.EnumWindows(visit, 0)
                if "First-time setup" in titles:
                    print("PASS: packaged EXE reached first-time administrator setup with isolated data.")
                    return
                if any("could not start" in title or "Failed to execute" in title for title in titles):
                    raise RuntimeError(f"Packaged startup failed: {titles}")
                time.sleep(0.5)
            raise RuntimeError(f"Startup timed out; observed windows: {titles}")
        finally:
            # Only stop the process tree this check created; no other app instance.
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
