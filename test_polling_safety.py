"""Polling failure regressions, without Tk, hardware, or application startup."""
import ast
import math
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
import unittest


class PollingSafetyTests(unittest.TestCase):
    def poll(self, response, devices=None):
        tree = ast.parse(Path(__file__).with_name('2.py').read_text(encoding='utf-8-sig'))
        worker = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                      and n.name == 'read_modbus_device')
        stop = threading.Event()
        messages, stale, writes, requests = [], [], [], []
        def read(**kwargs):
            requests.append(kwargs)
            return response
        client = SimpleNamespace(is_connected=lambda: True,
                                 read_input_registers=read, close=lambda: None)
        dev = {'ip': 'test', 'port': 502, 'slave_id': 1, 'types': {'0': 'UINT16'}}
        app = SimpleNamespace(
            comms_lock=threading.Lock(), shared_clients={('test', 502): client},
            ip_locks={}, devices=[dev] if devices is None else devices,
            root=SimpleNamespace(after=lambda delay, cb: cb()), log=messages.append,
            mark_read_error=stale.append, trend_settings={'decimal_places': 0},
            email_settings={}, sms_settings={})
        refresh = SimpleNamespace(wait=lambda timeout: stop.set(), clear=lambda: None)
        scope = dict(threading=threading, time=time, math=math, re=re,
                     PYMODBUS_V3=True, decode_uint16=lambda value: value,
                     write_csv=lambda *args: writes.append(args))
        exec(compile(ast.Module(body=[worker], type_ignores=[]), 'worker', 'exec'), scope)
        scope['read_modbus_device']('test', 502, ['0'], app, stop, refresh_event=refresh)
        return messages, stale, writes, requests

    def test_timeout_does_not_invent_zero(self):
        messages, stale, writes, _ = self.poll(None)
        self.assertEqual(stale, ['test:502:1:0'])
        self.assertFalse(any('Reg 0 \u2192' in m for m in messages))
        self.assertEqual(writes, [])

    def test_wrong_slave_is_rejected(self):
        response = SimpleNamespace(isError=lambda: False, slave_id=2, registers=[60])
        _, stale, writes, _ = self.poll(response)
        self.assertEqual(stale, ['test:502:1:0'])
        self.assertEqual(writes, [])

    def test_real_zero_is_preserved(self):
        response = SimpleNamespace(isError=lambda: False, slave_id=1, registers=[0])
        messages, stale, writes, _ = self.poll(response)
        self.assertTrue(any('Reg 0 \u2192 0' in m for m in messages))
        self.assertEqual(stale, [])
        self.assertEqual(writes[0][-1], '0')

    def test_missing_device_cannot_use_another_devices_settings(self):
        other = {'ip': 'other', 'port': 502, 'slave_id': 1}
        _, stale, writes, requests = self.poll(None, devices=[other])
        self.assertEqual(stale, ['test:502:1:0'])
        self.assertEqual(requests, [])
        self.assertEqual(writes, [])


if __name__ == '__main__':
    unittest.main()
