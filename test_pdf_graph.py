"""Generate real PDFs from the shared View/Approved Data report renderer."""
import ast
from datetime import datetime, timedelta
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import landscape
from pypdf import PdfReader
from date_display import display_dates
from graph_format import channel_precision


class PdfGraphTests(unittest.TestCase):
    def render(self, folder, channel_count=2, records=12, approved=False):
        tree = ast.parse(Path(__file__).with_name('2.py').read_text(encoding='utf-8-sig'))
        method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef)
                      for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_generate_view_data_log_pdf')
        method.decorator_list = []  # Test the renderer independently of storage locking.
        circles = []

        class RecordingCanvas(canvas.Canvas):
            def circle(self, x, y, radius, **kwargs):
                circles.append((self.getPageNumber(), x, y, radius))
                return super().circle(x, y, radius, **kwargs)

        context = dict(canvas=SimpleNamespace(Canvas=RecordingCanvas), landscape=landscape,
                       datetime=datetime, timedelta=timedelta, math=math, os=os,
                       DATA_PATH=folder, resource_path=lambda name: os.path.join(folder, name),
                       load_approvals=lambda: {}, load_company_details=lambda: {},
                       display_dates=display_dates, channel_precision=channel_precision)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'pdf_renderer', 'exec'), context)
        start = datetime(2026, 9, 23, 10)
        names = [f'Channel {i + 1}' for i in range(channel_count)]
        regs = [str(i + 1) for i in range(channel_count)]
        data = {(start + timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S'):
                {name: f'{30 + j * 15 + i / 10:.1f}' for j, name in enumerate(names)}
                for i in range(records)}
        target = os.path.join(folder, 'sample.pdf')
        context[method.name](SimpleNamespace(current_user='TEST'), target,
                             {'ip': 'sample', 'port': 502}, 'SAMPLE PLC', start,
                             start + timedelta(minutes=records), data, regs, names,
                             {reg: 'C' if i == 0 else '%RH' for i, reg in enumerate(regs)},
                             force_approved=approved)
        reader = PdfReader(target)
        graph_text = reader.pages[1].extract_text()
        points = [p for p in circles if p[0] == 2 and p[3] in (1, 3)]
        total_pages = 2 + (records + 13) // 14
        self.assertEqual(len(reader.pages), total_pages)
        self.assertEqual(len(points), records * channel_count)
        self.assertEqual(len(set(p[1] for p in points)), records)
        self.assertTrue(all(85 <= p[1] <= 787 and 100 <= p[2] <= 380 for p in points))
        for name in names:
            self.assertIn(name, graph_text)
        self.assertIn(f'Page 2 of {total_pages}', graph_text)
        self.assertEqual('DRAFT MODE' in graph_text, not approved)
        if records > 10:
            self.assertIn('30.0', graph_text)
            self.assertIn(f'{30 + (records - 1) / 10:.1f}', graph_text)
            self.assertIn(f'All {records} records plotted', graph_text)
        return points

    def test_both_exports_share_combined_graph(self):
        for approved in (False, True):
            with self.subTest(approved=approved), tempfile.TemporaryDirectory() as folder:
                points = self.render(folder, approved=approved)
                self.assertGreater(points[12][2], points[0][2])

    def test_ten_channels_fit_one_graph_page(self):
        with tempfile.TemporaryDirectory() as folder:
            self.render(folder, channel_count=10)

    def test_long_range_keeps_every_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            self.render(folder, channel_count=10, records=120)

    def test_single_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            self.render(folder, channel_count=1, records=1)


if __name__ == '__main__':
    unittest.main()
