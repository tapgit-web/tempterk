from date_display import display_dates
"""Encrypted backups and transactional restoration for the desktop application."""
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import threading
import zipfile
from datetime import datetime
from tkinter import messagebox, simpledialog, filedialog
from restore_merge import merge_restore

from storage_security import (
    BACKUP_MAGIC, STORAGE_LOCK, STORAGE_FROZEN, STORAGE_ERRORS, atomic_write,
    encrypt_backup, decrypt_backup, require_windows_admin, protect_admin_path,
    replace_folders, recover_restore, commit_restore,
)

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024


def validate_archive(payload):
    """Validate before extraction, including duplicate/Windows-special paths."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) > 100_000 or sum(m.file_size for m in members) > MAX_EXTRACT_BYTES:
            raise ValueError("Backup exceeds the supported extraction size or file count.")
        seen = set()
        for member in members:
            name = member.orig_filename
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith(("/", "\\")) or "\\" in name or
                    any(p in (".", "..") or ":" in p or p.rstrip(" .") != p or
                        p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}
                        for p in parts) or
                    (parts[0] not in ("config", "live", "reports") and name != "manifest.json") or
                    stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError(f"Unsafe or unsupported backup path: {name}")
            normalized = name.rstrip("/").casefold()
            if normalized in seen:
                raise ValueError("Backup contains duplicate paths.")
            seen.add(normalized)
        if archive.testzip():
            raise ValueError("Backup ZIP integrity check failed.")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("application") != "TempTrek" or manifest.get("format_version") not in (1, 2):
            raise ValueError("Unsupported TempTrek backup format.")
        if "config/users.json" not in seen:
            raise ValueError("Backup has no user database.")
        return manifest


class BackupMixin:
    def _require_backup_access(self):
        require_windows_admin()
        if "backup_restore" not in self.users.get(self.current_user, {}).get("permissions", []):
            raise PermissionError("This application user cannot manage backups or restore data.")

    def browse_restore_file(self):
        self._require_backup_access()
        path = filedialog.askopenfilename(title="Select a backup", filetypes=[("TempTrek backups", "*.ttbackup *.zip")])
        if path:
            item = self.backup_tree.insert("", "end", values=display_dates(("IMPORTED", os.path.basename(path), "", "", "VERIFY ON RESTORE", "", path)))
            self.backup_tree.selection_set(item)

    def export_selected_backup(self):
        temporary_path = None
        try:
            self._require_backup_access()
            if self.restore_in_progress or self.restart_required:
                messagebox.showwarning("Export Backup", display_dates("Finish restoring and restart TempTrek before exporting a backup."))
                return
            selected = self.backup_tree.selection()
            if len(selected) != 1:
                messagebox.showwarning("Export Backup", display_dates("Please select one backup to export."))
                return
            source = self.backup_tree.set(selected[0], "location")
            if not source or not os.path.isfile(source):
                messagebox.showerror("Export Backup", display_dates("The selected backup file no longer exists."))
                return
            if not self._authenticate_current_user("Export Backup Authorization"):
                return
            extension = os.path.splitext(source)[1].lower()
            target = filedialog.asksaveasfilename(
                title="Export Selected Backup", initialfile=os.path.basename(source),
                defaultextension=extension,
                filetypes=[("TempTrek backup", "*.ttbackup") if extension == ".ttbackup"
                           else ("ZIP backup", "*.zip")])
            if not target:
                return
            with self.backup_lock:
                if (os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target))
                        or (os.path.exists(target) and os.path.samefile(source, target))):
                    messagebox.showwarning("Export Backup", display_dates("Choose a different location or filename for the exported copy."))
                    return
                # Publish only a complete copy, leaving the source and its encryption intact.
                with tempfile.NamedTemporaryFile(dir=os.path.dirname(os.path.abspath(target)),
                                                 prefix=".backup-export-", delete=False) as output:
                    temporary_path = output.name
                    with open(source, "rb") as original:
                        shutil.copyfileobj(original, output)
                os.replace(temporary_path, target)
                temporary_path = None
            self._backup_event("BACKUP_EXPORT", f"Exported backup '{os.path.basename(source)}' to '{target}'")
            messagebox.showinfo("Export Complete", display_dates(f"Backup exported successfully:\n{target}"))
        except Exception as exc:
            self._backup_event("BACKUP_EXPORT", str(exc), status="FAILED")
            messagebox.showerror("Export Backup Failed", display_dates(str(exc)))
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)

    def _ensure_backup_password(self):
        self._require_backup_access()
        if self.backup_settings.get("backup_password"):
            return True
        password = simpledialog.askstring(
            "Backup encryption", "Create a backup password (at least 6 characters). Keep it safely: restoring on another computer requires it.",
            show="*", parent=self.root)
        if password is None:
            return False
        if len(password) < 6:
            raise ValueError("Use a backup password of at least 6 characters.")
        if password != simpledialog.askstring("Confirm backup password", "Re-enter the backup password:", show="*", parent=self.root):
            raise ValueError("Backup passwords did not match.")
        self.backup_settings["backup_password"] = password
        self._backup_runtime()["save_backup_settings"](self.backup_settings)
        return True

    def _backup_event(self, action, details, status="SUCCESS"):
        try:
            self.log_audit(action, "RESTORE" if action.startswith("RESTORE") else "BACKUP", details, status=status)
        except Exception as exc:
            STORAGE_ERRORS.put(f"Audit event {action} could not be saved: {exc}")

    def create_backup(self, trigger="MANUAL", silent=False, retain=True):
        runtime = self._backup_runtime()
        if self.restart_required or (self.restore_in_progress and trigger != "PRE_RESTORE"):
            return None
        if not self.backup_lock.acquire(blocking=False):
            return None
        self.backup_in_progress = True
        target = None
        try:
            require_windows_admin()
            password = self.backup_settings.get("backup_password", "")
            if len(password) < 6:
                raise ValueError("Set a backup encryption password using CREATE BACKUP NOW or SAVE SETTINGS first.")
            storage = self._backup_storage_path()
            with STORAGE_LOCK:
                if STORAGE_FROZEN.is_set():
                    raise RuntimeError("Restart TempTrek before making a backup.")
                now = datetime.now()
                self.backup_settings.update(last_attempt=now.strftime("%Y-%m-%d %H:%M:%S"), last_status="RUNNING", last_error="")
                runtime["save_backup_settings"](self.backup_settings)
                self.log_audit("BACKUP_STARTED", "BACKUP", f"{trigger} backup started")
                counts = self._count_backup_content()
                manifest = {"application": "TempTrek", "format_version": 2,
                            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"), "created_by": self.current_user or "SYSTEM",
                            "trigger": trigger, "record_count": counts[0], "configuration_items": counts[1],
                            "audit_record_count": counts[2], "includes": ["config", "live", "reports"]}
                output = io.BytesIO()
                source_bytes = 0
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", json.dumps(manifest))
                    for folder in ("config", "live", "reports"):
                        source = Path(runtime["DATA_PATH"]) / folder
                        archive.writestr(folder + "/", b"")
                        for file in sorted(source.rglob("*")):
                            if file.is_symlink() or (hasattr(file, "is_junction") and file.is_junction()):
                                raise ValueError(f"Backup source contains a link: {file.name}")
                            if not file.is_file():
                                continue
                            source_bytes += file.stat().st_size
                            if source_bytes > MAX_EXTRACT_BYTES:
                                raise ValueError("Backup source exceeds the supported 2 GiB size.")
                            name = file.relative_to(runtime["DATA_PATH"]).as_posix()
                            if folder == "config" and file.suffix.lower() == ".json":
                                text = file.read_text(encoding="utf-8")
                                decoded = runtime["deobfuscate_data"](text)
                                value = json.loads(decoded if decoded is not None else text)
                                if file.name == "backup_settings.json":
                                    value.pop("backup_password", None)
                                archive.writestr(name, json.dumps(value))
                            else:
                                archive.write(file, name)
                            if output.tell() > MAX_ARCHIVE_BYTES:
                                raise ValueError("Compressed backup exceeds the supported 512 MiB size.")
                payload = output.getvalue()
                validate_archive(payload)
                filename = f"TempTrek_Backup_{now.strftime('%Y%m%d_%H%M%S_%f')}_{trigger}.ttbackup"
                target = os.path.join(storage, filename)
                atomic_write(target, encrypt_backup(payload, password))
                protect_admin_path(target)
                completed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.backup_settings.update(last_success=completed, last_status="SUCCESS", last_error="")
                runtime["save_backup_settings"](self.backup_settings)
                self._record_backup_history({"type": trigger, "file_name": filename, "date_time": completed,
                    "size": os.path.getsize(target), "status": "SUCCESS", "location": target,
                    "record_count": counts[0], "configuration_items": counts[1], "audit_record_count": counts[2], "error": ""})
                self.log_audit("BACKUP_SUCCESS", "BACKUP", f"{trigger} backup created: {filename}")
                if retain:
                    self._enforce_backup_retention(storage, protected=(target,))
            if not silent:
                self.root.after(0, lambda: messagebox.showinfo("Backup Complete", display_dates(f"Encrypted backup created:\n{target}")))
            return target
        except Exception as exc:
            self.backup_settings.update(last_status="FAILED", last_error=str(exc))
            try:
                runtime["save_backup_settings"](self.backup_settings)
                self._record_backup_history({"type": trigger, "file_name": os.path.basename(target) if target else "",
                    "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "size": 0, "status": "FAILED",
                    "location": target or "", "error": str(exc)})
            except Exception as reporting_error:
                STORAGE_ERRORS.put(str(reporting_error))
            self._backup_event("BACKUP_FAILED", str(exc), "FAILED")
            if not silent:
                self.root.after(0, lambda error=str(exc): messagebox.showerror("Backup Failed", display_dates(error)))
            return None
        finally:
            self.backup_in_progress = False
            self.backup_lock.release()
            self.root.after(0, self.refresh_backup_view)

    def check_automatic_backup(self):
        if (not self.backup_settings.get("enabled", True) or self.backup_in_progress or
                self.restore_in_progress or self.restart_required):
            return
        try:
            require_windows_admin()
        except PermissionError:
            self.backup_settings["last_status"] = "ADMIN REQUIRED"
            return
        if not self.backup_settings.get("backup_password"):
            self.backup_settings["last_status"] = "PASSWORD SETUP REQUIRED"
            return
        try:
            previous = datetime.strptime(self.backup_settings.get("last_attempt", ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            previous = datetime.min
        if (datetime.now() - previous).total_seconds() >= self._backup_interval_seconds():
            self.backup_settings["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            threading.Thread(target=self.create_backup, args=("AUTOMATIC", True), daemon=True).start()

    def _read_backup(self, path, password=None):
        with open(path, "rb") as stream:
            payload = stream.read(MAX_ARCHIVE_BYTES + 1024)
            if len(payload) > MAX_ARCHIVE_BYTES + 128:
                raise ValueError("Selected backup exceeds the supported size.")
        if payload.startswith(BACKUP_MAGIC):
            payload = decrypt_backup(payload, password or self.backup_settings.get("backup_password", ""))
        return payload, validate_archive(payload)

    def _load_backup_manifest(self, path):
        return self._read_backup(path)[1]

    def start_manual_backup(self):
        if self.restore_in_progress or self.restart_required or self.backup_in_progress:
            messagebox.showwarning("Busy", display_dates("A backup/restore is running, or restart is required."))
            return
        if not self._authenticate_current_user("Manual Backup Authorization") or not self._ensure_backup_password():
            return
        threading.Thread(target=self.create_backup, args=("MANUAL", False), daemon=True).start()

    def restore_selected_backup(self):
        self._require_backup_access()
        if self.restore_in_progress or self.backup_in_progress or self.restart_required:
            raise RuntimeError("A backup/restore is running, or restart is required.")
        selected = self.backup_tree.selection()
        if not selected:
            messagebox.showwarning("Select Backup", display_dates("Select a backup to restore."))
            return
        values = self.backup_tree.item(selected[0], "values")
        path = str(values[6])
        if not os.path.isfile(path):
            raise FileNotFoundError("The selected backup file is not available.")
        if not self._authenticate_current_user("Restore Authorization"):
            return
        try:
            payload, manifest = self._read_backup(path)
        except ValueError:
            password = simpledialog.askstring("Backup password", "Enter the password used to create this backup:", show="*", parent=self.root)
            if password is None:
                return
            payload, manifest = self._read_backup(path, password)
        if not self._ensure_backup_password():
            return
        if not messagebox.askyesno("Confirm Restore", display_dates(f"Merge {os.path.basename(path)}?\nCreated: {manifest.get('created_at')}\nRecords: {manifest.get('record_count', 0)}\n\nCurrent data will be kept and missing backup records added. Current settings win conflicts; backup copies are preserved in reports/restored_backups. A safety backup is required first. Monitoring pauses during the merge and resumes automatically after success; the application stays open.")):
            return
        self.restore_in_progress = True  # Set on UI thread before worker creation.
        threading.Thread(target=self._restore_backup_worker, args=(path, payload, manifest), daemon=True).start()

    def _restore_backup_worker(self, backup_path, payload, manifest):
        runtime = self._backup_runtime()
        data_root = runtime["DATA_PATH"]
        safety = None
        stopped = False
        staged = None
        completed = False
        active_devices = {}
        acquired = self.backup_lock.acquire(blocking=False)
        try:
            if not acquired:
                raise RuntimeError("Another backup is running.")
            # Pin the selected archive in memory; retention cannot invalidate it.
            validate_archive(payload)
            staged = tempfile.mkdtemp(prefix=".restore-stage-", dir=data_root)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                archive.extractall(staged)
            for name in ("config", "live", "reports"):
                os.makedirs(os.path.join(staged, name), exist_ok=True)
            # Decode and validate every configuration before stopping or replacing.
            for file in (Path(staged) / "config").rglob("*.json"):
                text = file.read_text(encoding="utf-8")
                decoded = text if manifest["format_version"] == 2 else runtime["deobfuscate_data"](text)
                value = json.loads(decoded if decoded is not None else text)
                expected = list if file.name in ("devices.json", "backup_history.json") else dict
                if not isinstance(value, expected):
                    raise ValueError(f"Invalid configuration: {file.name}")
                if file.name == "users.json":
                    if not value or not any(isinstance(u, dict) and u.get("is_superadmin") for u in value.values()):
                        raise ValueError("Backup has no valid Super Admin.")
                    if any(not isinstance(u, dict) or not isinstance(u.get("password"), str) or not isinstance(u.get("permissions"), list) for u in value.values()):
                        raise ValueError("Backup contains invalid accounts.")
                # Local backup location/password stay local to this installation.
                if file.name == "backup_settings.json":
                    value = dict(self.backup_settings)
                atomic_write(file, runtime["obfuscate_data"](json.dumps(value)))
            active_devices = {
                key: getattr(self, "device_modes", {}).get(key, "live")
                for key, worker in self.device_threads.items()
                if worker.is_alive() and not (key in self.stop_events and self.stop_events[key].is_set())
            }
            for event in self.stop_events.values():
                event.set()
            stopped = True
            for worker in list(self.device_threads.values()):
                worker.join(timeout=15)
                if worker.is_alive():
                    raise RuntimeError("A monitoring worker did not stop; no folders were replaced. Restart TempTrek.")
            for client in list(self.shared_clients.values()):
                client.close()
            self.shared_clients.clear()
            with STORAGE_LOCK:
                safety = self.create_backup("PRE_RESTORE", silent=True, retain=False)
                if not safety:
                    raise RuntimeError("Required safety backup failed. No folders were replaced.")
                merge_restore(data_root, staged, runtime)
                journal, old = replace_folders(data_root, staged)
                try:
                    self._reload_restored_configuration()
                    self._record_backup_history({"type": "RESTORE", "file_name": os.path.basename(backup_path),
                        "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "size": len(payload),
                        "status": "SUCCESS", "location": backup_path, "pre_restore_backup": safety,
                        "record_count": manifest.get("record_count", 0), "configuration_items": manifest.get("configuration_items", 0),
                        "audit_record_count": manifest.get("audit_record_count", 0), "error": ""})
                    self.log_audit("RESTORE_SUCCESS", "RESTORE", f"Merged {os.path.basename(backup_path)} with current data; safety backup: {safety}")
                    commit_restore(journal, old)
                except Exception:
                    recover_restore(data_root)
                    self._reload_restored_configuration()
                    raise
                self.restart_required = False
                completed = True
        except Exception as exc:
            self.restart_required = stopped
            self._backup_event("RESTORE_FAILED", str(exc), "FAILED")
            try:
                self._record_backup_history({"type": "RESTORE", "file_name": os.path.basename(backup_path),
                    "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "status": "FAILED",
                    "location": backup_path, "pre_restore_backup": safety or "", "error": str(exc)})
            except Exception as error:
                STORAGE_ERRORS.put(str(error))
            if stopped:
                STORAGE_FROZEN.set()
            self.root.after(0, lambda error=str(exc): messagebox.showerror("Restore Failed", display_dates(error + ("\nRestart TempTrek before continuing." if stopped else ""))))
        finally:
            if staged:
                shutil.rmtree(staged, ignore_errors=True)
            if acquired:
                self.backup_lock.release()
            self.restore_in_progress = False
            if completed:
                self.root.after(0, lambda: self._finish_restore(active_devices, safety))
            else:
                self.root.after(0, self.refresh_backup_view)

    def _finish_restore(self, active_devices, safety):
        """Resume previous workers on the UI thread, after the transaction ends."""
        failures = []
        for dev in self.devices:
            key = f"{dev['ip']}:{dev['port']}:{dev.get('slave_id', 1)}"
            if key not in active_devices:
                continue
            try:
                if active_devices[key] == "simulation":
                    self.start_simulation_device(dev)
                else:
                    self.start_device(dev)
            except Exception as exc:
                failures.append(f"{key}: {exc}")
        self.refresh_backup_view()
        if failures:
            messagebox.showwarning("Restore Complete", display_dates("Data merged, but some devices could not resume:\n" + "\n".join(failures)))
        else:
            messagebox.showinfo("Restore Complete", display_dates(f"Backup merged with current data. Previously running devices have been restarted.\nSafety backup: {safety}\n\nThe application is ready to use; no restart is needed."))
