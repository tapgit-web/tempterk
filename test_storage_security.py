import ast
import base64
import csv
from datetime import datetime
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import sys
import types
import unittest
from unittest.mock import Mock, patch
import zipfile
import storage_security as security
import backup_engine as engine

SOURCE = ast.parse(Path(__file__).with_name("2.py").read_text(encoding="utf-8"))


def source_function(name, namespace):
    namespace.setdefault("DPAPI_PREFIX", security.DPAPI_PREFIX)
    namespace.setdefault("COMPANY_CONFIG_FILE", "nonexistent-company-reference.json")
    namespace.setdefault("Path", Path)
    namespace.setdefault("recover_legacy_config", security.recover_legacy_config)
    node = next(n for n in ast.walk(SOURCE) if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "2.py", "exec"), namespace)
    return namespace[name]


def archive_payload(extra=None, version=2):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"application": "TempTrek", "format_version": version}))
        z.writestr("config/users.json", json.dumps({"superadmin": {"password": "x", "permissions": [], "is_superadmin": True}}))
        z.writestr("config/devices.json", "[]")
        z.writestr("live/readings.csv", "old")
        if extra:
            info = zipfile.ZipInfo("placeholder")
            info.filename = extra[0]  # Preserve raw path even on Windows.
            z.writestr(info, extra[1])
    return output.getvalue()


class StorageTests(unittest.TestCase):
    def setUp(self):
        security.STORAGE_FROZEN.clear()
        self.temp = tempfile.TemporaryDirectory(prefix="temptrek-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        security.STORAGE_FROZEN.clear()
        while not security.STORAGE_ERRORS.empty():
            security.STORAGE_ERRORS.get_nowait()
        security.remove_tree(self.root)
        self.temp.cleanup()

    def test_password_salts_and_legacy_compatibility(self):
        one = security.hash_password("long-test-password")
        self.assertNotEqual(one, security.hash_password("long-test-password"))
        self.assertTrue(security.verify_password("long-test-password", one))
        self.assertFalse(security.verify_password("wrong", one))
        self.assertTrue(security.verify_password("legacy", hashlib.sha256(b"legacy").hexdigest()))
        self.assertFalse(security.verify_password("x", "pbkdf2_sha256$999999999$00$00"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_dpapi_roundtrip_and_damage(self):
        encoded = security.protect_text('sensitive password \u2603')
        self.assertNotIn("sensitive", encoded)
        self.assertEqual(security.unprotect_text(encoded), 'sensitive password \u2603')
        with self.assertRaises(Exception):
            security.unprotect_text(encoded[:-12] + "AAAAAAAAAAAA")

    def test_atomic_failure_preserves_original(self):
        target = self.root / "settings.json"
        target.write_text("original")
        with patch.object(security.os, "replace", side_effect=PermissionError("injected")), self.assertRaises(PermissionError):
            security.atomic_write(target, "replacement")
        self.assertEqual(target.read_text(), "original")
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_authenticated_portable_backup(self):
        payload = archive_payload()
        encrypted = security.encrypt_backup(payload, "portable-test-password")
        self.assertNotIn(b"superadmin", encrypted)
        self.assertEqual(security.decrypt_backup(encrypted, "portable-test-password"), payload)
        with self.assertRaises(ValueError):
            security.decrypt_backup(encrypted, "wrong-password")
        with self.assertRaises(ValueError):
            security.decrypt_backup(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), "portable-test-password")

    def _folders(self):
        staged = self.root / ".restore-stage-test"
        for name in ("config", "live", "reports"):
            (self.root / name).mkdir()
            (self.root / name / "data").write_text("original")
            (staged / name).mkdir(parents=True)
            (staged / name / "data").write_text("restored")
        return staged

    def test_restore_failure_rolls_back_all_folders(self):
        staged = self._folders()
        original_replace = os.replace
        def fail(source, target):
            if Path(source) == staged / "live":
                raise OSError("injected disk failure")
            return original_replace(source, target)
        with patch.object(security.os, "replace", side_effect=fail), self.assertRaises(OSError):
            security.replace_folders(self.root, staged)
        for name in ("config", "live", "reports"):
            self.assertEqual((self.root / name / "data").read_text(), "original")
        self.assertFalse((self.root / ".restore-journal.json").exists())

    def test_restart_recovers_uncommitted_transaction(self):
        security.replace_folders(self.root, self._folders())
        os.chmod(self.root / "live" / "data", stat.S_IREAD)
        security.recover_restore(self.root)
        for name in ("config", "live", "reports"):
            self.assertEqual((self.root / name / "data").read_text(), "original")

    def test_committed_restore_survives_restart(self):
        journal, rollback = security.replace_folders(self.root, self._folders())
        security.commit_restore(journal, rollback)
        security.recover_restore(self.root)
        self.assertEqual((self.root / "config" / "data").read_text(), "restored")

    def test_archive_rejects_unsafe_windows_paths(self):
        for path in ("../outside", "config/../../outside", "config/x:stream", "config/CON.txt", "config\\x", "config/x."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                engine.validate_archive(archive_payload((path, "x")))

    def test_archive_rejects_duplicate_paths(self):
        with self.assertRaises(ValueError):
            engine.validate_archive(archive_payload(("config/USERS.JSON", "{}")))

    def test_smtp_does_not_login_after_tls_failure(self):
        server = Mock()
        server.starttls.side_effect = security.ssl.SSLCertVerificationError("bad certificate")
        with patch.object(security.smtplib, "SMTP", return_value=server), self.assertRaises(security.ssl.SSLCertVerificationError):
            security.connect_smtp("configured.example", 587, "user", "password")
        server.login.assert_not_called()
        server.close.assert_called_once()

    def test_smtp_requires_starttls_and_verified_context(self):
        server = Mock()
        with patch.object(security.smtplib, "SMTP", return_value=server):
            security.connect_smtp("configured.example", 25, "user", "password")
        context = server.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, security.ssl.CERT_REQUIRED)
        server.login.assert_called_once_with("user", "password")

    def test_smtp_adds_public_ca_bundle_to_system_context(self):
        context = Mock()
        with patch.object(security.ssl, "create_default_context", return_value=context), \
                patch.object(security.smtplib, "SMTP_SSL") as smtp:
            security.connect_smtp("configured.example", 465, "user", "password")
        context.load_verify_locations.assert_called_once_with(cafile=security.certifi.where())
        self.assertIs(smtp.call_args.kwargs["context"], context)

    def test_corrupt_settings_are_not_defaulted_or_overwritten(self):
        target = self.root / "devices.json"
        target.write_text("broken JSON")
        load = source_function("load_secure_json", {"os": os, "json": json, "deobfuscate_data": lambda text: None})
        with self.assertRaises(RuntimeError):
            load(str(target), [])
        self.assertEqual(target.read_text(), "broken JSON")

    def test_company_settings_recover_from_another_legacy_machine(self):
        expected = {"company_name": "The Automation People", "department_name": "Environmental Monitoring", "report_title": "Temperature"}
        plain = json.dumps(expected, indent=4)
        key = "ABCDEF0123456789"
        encoded = base64.b64encode("".join(chr(ord(c) ^ ord(key[i % 16])) for i, c in enumerate(plain)).encode()).decode()
        self.assertEqual(security.recover_legacy_company_details(encoded), expected)

    def test_company_recovery_rejects_corruption_and_dpapi(self):
        for text in ("broken JSON", security.DPAPI_PREFIX + "broken", base64.b64encode(b"random data").decode()):
            with self.subTest(text=text), self.assertRaises(Exception):
                security.recover_legacy_company_details(text)

    def test_company_loader_preserves_original_before_migration(self):
        target = self.root / "company_details.json"
        expected = {"company_name": "Existing company", "department_name": "Existing department", "report_title": "Existing title"}
        plain, key = json.dumps(expected, indent=4), "1234567890ABCDEF"
        encoded = base64.b64encode("".join(chr(ord(c) ^ ord(key[i % 16])) for i, c in enumerate(plain)).encode())
        target.write_bytes(encoded)
        namespace = {"COMPANY_CONFIG_FILE": str(target), "load_secure_json": Mock(side_effect=RuntimeError("legacy")),
                     "Path": Path, "os": os, "atomic_write": security.atomic_write,
                     "recover_legacy_company_details": security.recover_legacy_company_details}
        result = source_function("load_company_details", namespace)()
        self.assertEqual(result, expected)
        self.assertEqual(target.read_bytes(), encoded)
        self.assertEqual(Path(str(target) + ".legacy-original").read_bytes(), encoded)

    def test_approval_recovery_preserves_history_and_original_bytes(self):
        key = "1234567890ABCDEF"
        def encode(value):
            return base64.b64encode("".join(chr(ord(c) ^ ord(key[i % 16])) for i, c in enumerate(json.dumps(value, indent=4))).encode()).decode()
        company = self.root / "company_details.json"
        company.write_text(encode({"company_name": "Example", "department_name": "QA", "report_title": "Readings"}))
        approvals = self.root / "approvals.json"
        expected = {"device_2026-09-08": {"ranges": [{"from": "2026-09-08 10:00:00", "to": "2026-09-08 11:00:00", "approved_by": "existing-user"}]}}
        approvals.write_text(encode(expected))
        original = approvals.read_bytes()
        load = source_function("load_secure_json", {"os": os, "json": json, "COMPANY_CONFIG_FILE": str(company),
            "deobfuscate_data": lambda value: None, "atomic_write": security.atomic_write})
        self.assertEqual(load(str(approvals), {}), expected)
        self.assertEqual(approvals.read_bytes(), original)
        self.assertEqual(Path(str(approvals) + ".legacy-original").read_bytes(), original)

    def test_missing_user_database_does_not_reset_existing_install(self):
        (self.root / "devices.json").write_text("[]")
        namespace = {"os": os, "DATA_PATH": str(self.root), "USERS_FILE": str(self.root / "users.json"),
                     "CONFIG_FILE": str(self.root / "devices.json"), "EMAIL_CONFIG_FILE": "unused", "SMS_CONFIG_FILE": "unused"}
        with self.assertRaisesRegex(RuntimeError, "automatic account reset is disabled"):
            source_function("load_users", namespace)()
        self.assertFalse((self.root / "users.json").exists())

    def test_retention_preserves_pinned_archives_and_unrelated_zips(self):
        selected = self.root / "TempTrek_Backup_selected.zip"
        safety = self.root / "TempTrek_Backup_safety.ttbackup"
        unrelated = self.root / "personal.zip"
        for file in (selected, safety, unrelated):
            file.write_bytes(b"archive")
        retain = source_function("_enforce_backup_retention", {"os": os, "stat": stat})
        retain(Mock(backup_settings={"max_history": 1, "max_storage_mb": 1}), str(self.root), protected=(str(selected), str(safety)))
        self.assertTrue(all(f.exists() for f in (selected, safety, unrelated)))

    def test_csv_and_audit_write_failures_raise_and_report(self):
        for name in ("write_csv", "write_audit_log_csv"):
            namespace = {"storage_operation": security.storage_operation, "os": os, "stat": stat, "csv": csv,
                         "datetime": datetime, "DATA_PATH": str(self.root), "time": Mock(), "math": __import__('math'),
                         "csv_filename": lambda *args: str(self.root / "missing" / "readings.csv"),
                         "platform": Mock(), "AUDIT_WRITE_LOCK": threading.Lock(), "_audit_hash": lambda *a: "hash"}
            write = source_function(name, namespace)
            with self.subTest(name=name), self.assertRaises(OSError):
                write("ip", 502, 1, 1, 2) if name == "write_csv" else write("user", "action", "DATA", "details")
            self.assertFalse(security.STORAGE_ERRORS.empty())

    def test_frozen_storage_prevents_writes(self):
        security.STORAGE_FROZEN.set()
        with self.assertRaises(RuntimeError):
            security.atomic_write(self.root / "file", "must not write")
        self.assertFalse((self.root / "file").exists())

    def _app(self):
        root = self.root
        for name in ("config", "live", "reports", "storage"):
            (root / name).mkdir()
        def save(name, value):
            security.atomic_write(root / "config" / name, security.protect_text(json.dumps(value)))
        def load(name):
            return json.loads(security.unprotect_text((root / "config" / name).read_text()))
        class App(engine.BackupMixin):
            def __init__(self):
                self.root = Mock()
                self.backup_lock = threading.RLock()
                self.restore_in_progress = self.restart_required = self.backup_in_progress = False
                self.current_user = "superadmin"
                self.backup_settings = {"backup_password": "test-backup-password", "max_history": 1, "max_storage_mb": 2}
                self.backup_history = []
                self.stop_events = {}
                self.device_threads = {}
                self.shared_clients = {}
            def _backup_runtime(self):
                return {"DATA_PATH": str(root), "save_backup_settings": lambda value: save("backup_settings.json", value),
                        "deobfuscate_data": security.unprotect_text, "obfuscate_data": security.protect_text}
            def _backup_storage_path(self):
                return str(root / "storage")
            def _count_backup_content(self):
                return 1, 3, 0
            def log_audit(self, *args, **kwargs):
                pass
            def _record_backup_history(self, entry):
                self.backup_history.append(entry)
                save("backup_history.json", self.backup_history)
            def refresh_backup_view(self):
                pass
            def _reload_restored_configuration(self):
                self.backup_settings = load("backup_settings.json")
                self.backup_history = load("backup_history.json")
        App._enforce_backup_retention = source_function("_enforce_backup_retention", {"os": os, "stat": stat})
        app = App()
        save("users.json", {"superadmin": {"password": security.hash_password("test-user-password"), "permissions": [], "is_superadmin": True}})
        save("devices.json", [])
        save("backup_settings.json", app.backup_settings)
        save("backup_history.json", [])
        (root / "live" / "readings.csv").write_text("snapshot")
        return app

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
    def test_full_backup_restore_preserves_selected_and_safety_with_limit_one(self):
        app = self._app()
        with patch.object(engine, "require_windows_admin"), patch.object(engine, "protect_admin_path"):
            backup = app.create_backup(silent=True)
            self.assertIsNotNone(backup)
            payload, manifest = app._read_backup(backup)
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                settings = json.loads(z.read("config/backup_settings.json"))
                self.assertNotIn("backup_password", settings)
                self.assertIsInstance(json.loads(z.read("config/users.json")), dict)
            (self.root / "live" / "readings.csv").write_text("current data")
            app.restore_in_progress = True
            app._restore_backup_worker(backup, payload, manifest)
            self.assertEqual((self.root / "live" / "readings.csv").read_text(), "snapshot")
            self.assertTrue(app.restart_required)
            self.assertTrue(Path(backup).exists())
            self.assertEqual(len(list((self.root / "storage").glob("*.ttbackup"))), 2)
            self.assertTrue(security.STORAGE_FROZEN.is_set())

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
    def test_safety_backup_failure_does_not_replace_data(self):
        app = self._app()
        payload = archive_payload()
        app.create_backup = Mock(return_value=None)
        app.restore_in_progress = True
        app._restore_backup_worker("selected.zip", payload, engine.validate_archive(payload))
        self.assertEqual((self.root / "live" / "readings.csv").read_text(), "snapshot")
        self.assertFalse((self.root / ".restore-journal.json").exists())

    def test_busy_backup_lock_prevents_second_operation(self):
        app = self._app()
        ready, release = threading.Event(), threading.Event()
        def lock():
            with app.backup_lock:
                ready.set()
                release.wait(5)
        worker = threading.Thread(target=lock)
        worker.start()
        try:
            self.assertTrue(ready.wait(2))
            self.assertIsNone(app.create_backup(silent=True))
        finally:
            release.set()
            worker.join(2)

    @unittest.skipUnless(os.name == "nt", "Windows ACL descriptor")
    def test_admin_acl_descriptor_contains_only_admin_and_system(self):
        import subprocess
        with patch.object(security, "require_windows_admin"), patch.object(security.subprocess, "run") as run:
            security.protect_admin_path(str(self.root), is_directory=True)
        args, kwargs = run.call_args
        command = list(args[0])
        command[-1] = command[-1].replace("Set-Acl -LiteralPath $target -AclObject $acl", "$acl.GetSecurityDescriptorSddlForm('All')")
        result = subprocess.run(command, **kwargs)
        self.assertIn(";;;BA)", result.stdout)
        self.assertIn(";;;SY)", result.stdout)
        self.assertNotIn(";;;BU)", result.stdout)
        self.assertNotIn(";;;WD)", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows first-run startup")
    def test_first_run_and_corrupt_account_startup(self):
        import tkinter as tk
        from tkinter import simpledialog
        modules = {}
        for name in ("pymodbus", "pymodbus.client", "pymodbus.client.sync", "requests"):
            modules[name] = types.ModuleType(name)
            modules[name].__path__ = []
        modules["pymodbus"].__version__ = "3.0.0"
        modules["pymodbus.client.sync"].ModbusTcpClient = Mock()
        spec = importlib.util.spec_from_file_location("temptrek_startup_test", Path(__file__).with_name("2.py"))
        module = importlib.util.module_from_spec(spec)
        window = tk.Tk()
        window.withdraw()
        try:
            with patch.dict(sys.modules, modules), patch.object(sys, "frozen", True, create=True), patch.dict(os.environ, {"APPDATA": str(self.root)}):
                spec.loader.exec_module(module)
                with patch.object(module.ModbusApp, "check_initial_license"), patch.object(module.ModbusApp, "periodic_refresher"), patch.object(simpledialog, "askstring", return_value="new-install-password"):
                    app = module.ModbusApp(window)
                    self.assertTrue(security.verify_password("new-install-password", app.users["superadmin"]["password"]))
                    users = Path(module.USERS_FILE)
                    self.assertTrue(users.read_text().startswith(security.DPAPI_PREFIX))
                    users.write_text("corrupted")
                    with self.assertRaises(RuntimeError):
                        module.ModbusApp(window)
                    self.assertEqual(users.read_text(), "corrupted")
        finally:
            window.destroy()


if __name__ == "__main__":
    unittest.main()
