"""SOS length -> end-date calculation and manager-approval gating.

Ported field-for-field from the Discord bot's `calculate_sos_end_date` /
`sos_requires_manager` (Discord Bot/main.py) so both systems agree on the
same rules — adapted to run off the `SOSLength` enum instead of free text,
since the API only ever receives one of those fixed values (the bot itself
is left untouched and keeps parsing free text from its own sheet).
"""
import calendar
from datetime import date, timedelta

from .models import SOSLength

SOS_MANAGER_REQUIRED_LENGTHS = {SOSLength.months_6, SOSLength.year_1, SOSLength.indefinite}


def add_months(start_date: date, months: int) -> date:
    """Add a number of months to a date without external libraries."""
    month_index = start_date.month - 1 + months
    year = start_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def calculate_sos_end_date(length: SOSLength, start_date: date) -> date | None:
    """Returns the calculated end date, or None for indefinite."""
    if length == SOSLength.days_7:
        return start_date + timedelta(days=7)
    if length == SOSLength.days_14:
        return start_date + timedelta(days=14)
    if length == SOSLength.days_30:
        return start_date + timedelta(days=30)
    if length == SOSLength.months_3:
        return add_months(start_date, 3)
    if length == SOSLength.months_6:
        return add_months(start_date, 6)
    if length == SOSLength.year_1:
        return add_months(start_date, 12)
    return None  # indefinite


def sos_requires_manager(length: SOSLength) -> bool:
    return length in SOS_MANAGER_REQUIRED_LENGTHS
