"""Exercise monitoring transitions without launching Tk or connecting to a PLC."""
import ast
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest


def monitoring_class():
    tree = ast.parse(Path(__file__).with_name('2.py').read_text(encoding='utf-8-sig'))
    names = {'monitoring_status', 'refresh_monitoring_indicators',
             '_wait_for_device_restart', 'stop_device', 'stop_all_devices'}
    methods = [node for cls in tree.body if isinstance(cls, ast.ClassDef)
               for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'display_dates': lambda value: value}
    module = ast.Module(body=methods, type_ignores=[])
    exec(compile(module, 'monitoring_methods', 'exec'), namespace)
    return type('Monitoring', (), {name: namespace[name] for name in names})


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.app = monitoring_class()()
        self.dev = {'ip': 'localhost', 'port': 502, 'slave_id': 1}
        self.key = 'localhost:502:1'
        self.alive = True
        self.app.device_threads = {self.key: SimpleNamespace(is_alive=lambda: self.alive)}
        self.stop = threading.Event()
        self.app.stop_events = {self.key: self.stop}
        self.app.refresh_events = {self.key: threading.Event()}
        self.app.live_labels = {}
        self.app.COLORS = {'danger': 'red', 'success': 'green', 'text_dim': 'gray'}
        self.app.restore_in_progress = self.app.restart_required = False
        self.callbacks = []
        self.app.root = SimpleNamespace(after=lambda delay, callback: self.callbacks.append(callback))
        self.app.refresh_view_if_active = lambda: None

    def test_status_tracks_worker_shutdown(self):
        self.assertIn('RUNNING', self.app.monitoring_status(self.key)[0])
        self.app.stop_device(self.dev)
        self.assertTrue(self.app.refresh_events[self.key].is_set())
        self.assertEqual(self.app.monitoring_status(self.key)[0], 'STOPPING...')
        self.alive = False
        self.assertIn('STOPPED', self.app.monitoring_status(self.key)[0])

    def test_quick_restart_waits_and_is_queued_once(self):
        self.stop.set()
        starts = []
        for _ in range(2):
            self.assertTrue(self.app._wait_for_device_restart(self.dev, starts.append))
        self.assertEqual(len(self.callbacks), 1)
        self.callbacks.pop(0)()
        self.assertEqual(starts, [])
        self.alive = False
        self.callbacks.pop(0)()
        self.assertEqual(starts, [self.dev])

    def test_stop_all_cancels_pending_restart(self):
        self.stop.set()
        starts = []
        self.app._wait_for_device_restart(self.dev, starts.append)
        self.app.stop_all_devices()
        self.alive = False
        self.callbacks.pop(0)()
        self.assertEqual(starts, [])

    def test_header_and_card_use_same_status(self):
        states = []
        label = SimpleNamespace(winfo_exists=lambda: True, config=lambda **kw: states.append(kw),
                                monitoring_device_key=self.key)
        self.app.live_labels = {self.key + ':status': label,
                                self.key + ':1:monitoring': label}
        self.stop.set()
        self.app.refresh_monitoring_indicators()
        self.assertEqual([state['fg'] for state in states], ['red', 'red'])
        self.assertEqual(states[1]['text'], 'STOPPING...')

    def test_named_registers_with_colons_find_running_device(self):
        states = []
        for register in ('MKT:31', 'MKRH:131'):
            self.app.live_labels[self.key + ':' + register + ':monitoring'] = SimpleNamespace(
                winfo_exists=lambda: True, config=lambda **kw: states.append(kw),
                monitoring_device_key=self.key)
        self.app.refresh_monitoring_indicators()
        self.assertEqual(len(states), 2)
        self.assertTrue(all('RUNNING' in state['text'] for state in states))
        self.assertTrue(all(state['fg'] == 'green' for state in states))
        self.alive = False
        states.clear()
        self.app.refresh_monitoring_indicators()
        self.assertTrue(all('STOPPED' in state['text'] for state in states))


if __name__ == '__main__':
    unittest.main()
