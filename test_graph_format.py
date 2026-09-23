import unittest
from graph_format import graph_precision


class GraphFormatTests(unittest.TestCase):
    def test_precision_matches_channel_and_shared_axis(self):
        devices = [{'ip': 'host', 'port': 502, 'decimals': {'1': '1', '2': '2', '3': '0'}}]
        for reg, expected in ((1, '25.4'), (2, '25.40'), (3, '25')):
            precision = graph_precision(devices, f'host:502:1:{reg}')
            self.assertEqual(f'{25.4:.{precision}f}', expected)
        self.assertEqual(graph_precision(devices, ['host:502:1:1', 'host:502:1:2']), 2)
        self.assertEqual(graph_precision(devices, 'host:502:1:4', 4), 4)
