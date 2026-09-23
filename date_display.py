"""Date formatting at presentation boundaries; storage remains ISO formatted."""
import re
from datetime import datetime

_DATE = re.compile(r'(?<![\w/\\])(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})(?![\w/\\])')


def display_dates(value):
    if isinstance(value, (tuple, list)):
        return type(value)(display_dates(item) for item in value)
    if not isinstance(value, str):
        return value
    def convert(match):
        text = match.group()
        try:
            return datetime.strptime(text, '%Y-%m-%d' if '-' in text else '%d/%m/%Y').strftime('%d-%m-%y')
        except ValueError:
            return text
    return _DATE.sub(convert, value)
