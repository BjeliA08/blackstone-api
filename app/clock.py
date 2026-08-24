"""Time, in the two senses this system needs them.

Shift times like 0700-1500 are Edmonton wall-clock. Stored timestamps are
absolute instants in UTC. Mixing the two is why the hub reported the Evening
shift at 10:47 in the morning: the server compared a UTC clock against local
shift windows.

Rule of thumb:
  * comparing against a shift time, or asking "what day is it" -> local_*
  * recording when something happened                          -> utc_now_naive
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config import settings


def company_tz() -> ZoneInfo:
    """Handles the MST/MDT switch on its own."""
    return ZoneInfo(settings.COMPANY_TIMEZONE)


def local_now() -> datetime:
    """Timezone-aware now, where the sites actually are."""
    return datetime.now(company_tz())


def local_now_naive() -> datetime:
    """Local wall-clock as a naive datetime, for comparing against shift times."""
    return local_now().replace(tzinfo=None)


def local_today() -> date:
    """The operational day. A shift at 23:00 Edmonton is still today, even
    though it is already tomorrow in UTC."""
    return local_now().date()


def local_minutes_now() -> int:
    """Minutes since local midnight — the unit shift windows are measured in."""
    n = local_now()
    return n.hour * 60 + n.minute


def utc_now_naive() -> datetime:
    """For recording when something happened. Instants stay in UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
