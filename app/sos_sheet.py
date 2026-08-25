"""Best-effort read of the SOS Registry Google Sheet for the Incidents & SOS
section of Site Records.

SOS/incident data lives in a Google Sheet, not Postgres — this reads it the
same way the desktop app does, via a service account, but server-side so a
PDF export can include it. If the service account isn't configured (it's
optional — see config.py), or the sheet's shape doesn't match what we expect,
this section is simply empty rather than failing the whole export. Site
Records is meant to be trustworthy about what it *does* show; it isn't
required to show everything that could theoretically exist.
"""
import logging
from datetime import date
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

# Header name -> canonical field, matched case-insensitively by substring so
# small naming drift in the sheet doesn't break this entirely.
_FIELD_MATCHERS = {
    "date": ["date"],
    "site": ["site"],
    "type": ["type", "incident"],
    "operator": ["operator", "name", "submitted by"],
    "summary": ["summary", "description", "notes", "details"],
}


def _configured() -> bool:
    return bool(settings.GOOGLE_SERVICE_ACCOUNT_EMAIL and settings.GOOGLE_PRIVATE_KEY
                and settings.SOS_SHEET_ID)


def _match_column(headers: list[str], keys: list[str]) -> Optional[int]:
    for i, h in enumerate(headers):
        low = h.strip().lower()
        if any(k in low for k in keys):
            return i
    return None


def fetch_sos_entries(site_name: str, start: date, end: date) -> list[dict]:
    """Rows from the SOS Registry sheet for this site within the period.
    Never raises — an empty list means either nothing matched or the
    integration isn't configured/reachable."""
    if not _configured():
        return []

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        private_key = settings.GOOGLE_PRIVATE_KEY.replace("\\n", "\n")
        creds = Credentials.from_service_account_info(
            {
                "type": "service_account",
                "client_email": settings.GOOGLE_SERVICE_ACCOUNT_EMAIL,
                "private_key": private_key,
                "token_uri": "https://oauth2.googleapis.com/token",
            },
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(settings.SOS_SHEET_ID).worksheet("SOS Registry")
        rows = sheet.get_values("A1:Z2000")
        if not rows:
            return []

        headers = rows[0]
        col = {
            field: _match_column(headers, keys)
            for field, keys in _FIELD_MATCHERS.items()
        }
        if col["date"] is None:
            return []  # Can't filter by period without a date column.

        out: list[dict] = []
        for row in rows[1:]:
            raw_date = row[col["date"]] if col["date"] < len(row) else ""
            if not raw_date:
                continue
            try:
                entry_date = date.fromisoformat(raw_date[:10])
            except ValueError:
                continue
            if not (start <= entry_date <= end):
                continue

            site_val = row[col["site"]] if col["site"] is not None and col["site"] < len(row) else ""
            if site_val and site_name.lower() not in site_val.lower():
                continue

            def cell(field: str) -> str:
                idx = col[field]
                return row[idx] if idx is not None and idx < len(row) else ""

            out.append({
                "date": entry_date.isoformat(),
                "incident_type": cell("type") or "SOS entry",
                "operator_name": cell("operator") or None,
                "summary": cell("summary") or "",
            })
        return out
    except Exception:
        logger.warning("SOS sheet read failed; Incidents & SOS section will be empty", exc_info=True)
        return []
