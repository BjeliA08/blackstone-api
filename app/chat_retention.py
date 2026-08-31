"""Chat message retention: every message in every channel is permanently
deleted 90 days after it was sent, no exceptions and no per-channel opt-out.

Runs as a periodic loop inside the same process (no separate worker/cron
service configured for this app) — a few hours of drift past exactly 90
days is fine for this purpose, so a coarse interval is deliberate.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from .database import SessionLocal
from .models import ChatMessage

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90
CHECK_INTERVAL_SECONDS = 6 * 60 * 60  # every 6 hours


def purge_old_messages() -> int:
    """Deletes messages older than the retention window. Returns the count
    deleted. Safe to call anytime — a no-op when nothing has aged out."""
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    db = SessionLocal()
    try:
        deleted = (
            db.query(ChatMessage)
            .filter(ChatMessage.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        return deleted
    finally:
        db.close()


async def retention_loop() -> None:
    while True:
        try:
            deleted = await asyncio.get_event_loop().run_in_executor(None, purge_old_messages)
            if deleted:
                logger.info(f"Chat retention: purged {deleted} message(s) older than {RETENTION_DAYS} days")
        except Exception:
            logger.exception("Chat retention pass failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
