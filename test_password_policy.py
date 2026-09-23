import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from password_policy import password_expired, password_expiry


class PasswordExpiryTests(unittest.TestCase):
    def test_expired_password_accepts_six_and_rejects_five_characters(self):
        source = ast.parse(Path(__file__).with_name('2.py').read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == 'renew_expired_password')
        dialogs = Mock()
        dialogs.askstring.side_effect = ['short', 'sixsix', 'sixsix']
        save = Mock()
        messages = Mock()
        scope = {'simpledialog': dialogs, 'messagebox': messages, 'hash_password': lambda value: 'hash:' + value,
                 'password_changed_at': lambda: 'now', 'save_users': save}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'renewal-test', 'exec'), scope)
        app = SimpleNamespace(root=None, users={'alice': {'password': 'old'}}, log_audit=Mock())
        self.assertTrue(scope['renew_expired_password'](app, 'alice', 'old-password'))
        messages.showerror.assert_called_once()
        save.assert_called_once()
        self.assertEqual(app.users['alice']['password'], 'hash:sixsix')

    def test_selected_lifetimes_and_exact_boundary(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for days in (1, 30, 60, 90):
            user = {'password_changed_at': start.isoformat(), 'password_expiry_days': days}
            end = start + timedelta(days=days)
            self.assertEqual(password_expiry(user), end)
            self.assertFalse(password_expired(user, end - timedelta(seconds=1)))
            self.assertTrue(password_expired(user, end))

    def test_invalid_metadata_requires_change(self):
        for user in ({}, {'password_changed_at': 'bad'},
                     {'password_changed_at': '2026-01-01'},
                     {'password_changed_at': '2026-01-01T00:00:00+00:00', 'password_expiry_days': 0}):
            self.assertTrue(password_expired(user))

    def test_future_change_date_requires_change(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(password_expired({'password_changed_at': (now + timedelta(days=1)).isoformat()}, now))

    def test_expired_login_cannot_bypass_password_change(self):
        source = ast.parse(Path(__file__).with_name('2.py').read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == 'check_password')
        scope = {'verify_password': lambda *args: True, 'password_expired': lambda user: True,
                 'tk': SimpleNamespace(END='end')}
        from date_display import display_dates
        scope['display_dates'] = display_dates
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'login-test', 'exec'), scope)
        for renewed in (False, True):
            app = SimpleNamespace(u_entry=Mock(), pass_entry=Mock(), users={'alice': {'password': 'pbkdf2_sha256$test'}},
                                  renew_expired_password=Mock(return_value=renewed), log_audit=Mock(),
                                  build_main_ui=Mock(), lbl_error=Mock(), current_user=None)
            app.u_entry.get.return_value = 'alice'
            app.pass_entry.get.return_value = 'old-password'
            scope['check_password'](app)
            self.assertEqual(app.build_main_ui.call_count, int(renewed))
            self.assertEqual(app.current_user, 'alice' if renewed else None)


if __name__ == '__main__':
    unittest.main()
