import unittest
from date_display import display_dates


class DateDisplayTests(unittest.TestCase):
    def test_dates_and_times(self):
        self.assertEqual(display_dates('2026-09-22 14:30:59'), '22-09-26 14:30:59')
        self.assertEqual(display_dates('22/09/2026 14:30'), '22-09-26 14:30')
        self.assertEqual(display_dates('From 2026-01-31 to 2026-02-01'), 'From 31-01-26 to 01-02-26')

    def test_rows_preserve_types_and_paths(self):
        self.assertEqual(display_dates(('2026-09-22', 3, 'logs_2026-09-22.csv')), ('22-09-26', 3, 'logs_2026-09-22.csv'))
        self.assertEqual(display_dates('2026-02-30'), '2026-02-30')
        self.assertEqual(display_dates('22-09-26'), '22-09-26')
