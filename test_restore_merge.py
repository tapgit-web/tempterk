import csv
import io
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backup_engine import BackupMixin
from restore_merge import merge_restore, merge_history


class MergeTests(unittest.TestCase):
    def test_restore_resumes_only_previously_active_devices_in_same_mode(self):
        live = {'ip': 'live', 'port': 502}
        simulation = {'ip': 'simulation', 'port': 502}
        stopped = {'ip': 'stopped', 'port': 502}
        app = SimpleNamespace(devices=[live, simulation, stopped], start_device=Mock(),
                              start_simulation_device=Mock(), refresh_backup_view=Mock())
        with patch('backup_engine.messagebox.showinfo'):
            BackupMixin._finish_restore(app, {'live:502:1': 'live', 'simulation:502:1': 'simulation'}, 'safety')
        app.start_device.assert_called_once_with(live)
        app.start_simulation_device.assert_called_once_with(simulation)
        app.refresh_backup_view.assert_called_once()

    def test_merge_preserves_current_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'current'
            staged = Path(directory) / 'staged'
            header = 'Timestamp,IP,Port,SlaveID,Register,Value\n'
            current = '2026-09-22,host,502,1,5,25\n'
            older = '2026-09-21,host,502,1,5,24\n'
            conflict = '2026-09-22,host,502,1,5,99\n'
            for base in (root, staged):
                for folder in ('config', 'live', 'reports'):
                    (base / folder).mkdir(parents=True)
            (root / 'live/data.csv').write_text(header + current)
            (staged / 'live/data.csv').write_text(header + older + current + conflict)
            (root / 'config/users.json').write_text('current accounts')
            (staged / 'config/users.json').write_text('old accounts')
            (root / 'reports/current.txt').write_text('keep')
            (staged / 'live/Audit_Logs_old.csv').write_text('audit chain')
            merge_restore(root, staged, {})
            rows = list(csv.reader(io.StringIO((staged / 'live/data.csv').read_text())))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[-1][-1], '25')
            self.assertEqual((staged / 'config/users.json').read_text(), 'current accounts')
            self.assertEqual((staged / 'reports/current.txt').read_text(), 'keep')
            self.assertFalse((staged / 'live/Audit_Logs_old.csv').exists())
            self.assertTrue(list((staged / 'reports/restored_backups').rglob('Audit_Logs_old.csv')))
            merge_restore(root, staged, {})
            self.assertEqual(list(csv.reader(io.StringIO((staged / 'live/data.csv').read_text()))), rows)
            self.assertEqual((root / 'live/data.csv').read_text(), header + current)

    def test_history_union_keeps_current_conflicts(self):
        self.assertEqual(merge_history({'x': {'ranges': [1, 2], 'name': 'current'}},
                                      {'x': {'ranges': [2, 3], 'name': 'old'}, 'y': 1}),
                         {'x': {'ranges': [1, 2, 3], 'name': 'current'}, 'y': 1})
