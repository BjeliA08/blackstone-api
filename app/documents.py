"""Generated document storage — Site Records PDF exports.

Same private-Cloudinary-storage discipline as face photos: never a public
bucket, delivery only via a short-lived signed URL minted per request.
Unlike photos, these are never re-derived — once generated, a PDF is
retained byte-for-byte so a legal or insurance document always matches
exactly what was true when it was produced.
"""
import time
import uuid

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from fastapi import HTTPException

from .config import settings

FOLDER = "record-exports"


def _configured() -> bool:
    return bool(settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY
                and settings.CLOUDINARY_API_SECRET)


def _configure() -> None:
    if not _configured():
        raise HTTPException(status_code=503, detail="Document storage is not configured on this server.")
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def upload_pdf(site_slug: str, raw: bytes) -> str:
    _configure()
    public_id = f"{FOLDER}/{site_slug}-{uuid.uuid4().hex[:12]}"
    try:
        result = cloudinary.uploader.upload(
            raw,
            public_id=public_id,
            type="private",
            resource_type="raw",
            overwrite=True,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Document upload failed: {e}")
    return result.get("public_id", public_id)


def signed_pdf_url(file_key: str) -> str:
    _configure()
    expires_at = int(time.time()) + settings.RECORD_EXPORT_URL_TTL_SECONDS
    try:
        url, _ = cloudinary.utils.private_download_url(
            file_key, "pdf", resource_type="raw", type="private", expires_at=expires_at,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not sign document URL: {e}")
    return url


ATTACHMENT_FOLDER = "chat-attachments"


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"


def upload_attachment(owner_id: str, filename: str, raw: bytes) -> str:
    """A file attached to a chat message — currently invoice uploads, kept
    generic. Cloudinary needs the real extension at signing time to build a
    working URL, so it's recovered from the stored filename, not the key."""
    _configure()
    public_id = f"{ATTACHMENT_FOLDER}/{owner_id}-{uuid.uuid4().hex[:12]}"
    try:
        result = cloudinary.uploader.upload(
            raw, public_id=public_id, type="private", resource_type="raw", overwrite=True,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Attachment upload failed: {e}")
    return result.get("public_id", public_id)


def signed_attachment_url(file_key: str, filename: str) -> str:
    _configure()
    expires_at = int(time.time()) + settings.RECORD_EXPORT_URL_TTL_SECONDS
    try:
        url, _ = cloudinary.utils.private_download_url(
            file_key, _ext_of(filename), resource_type="raw", type="private", expires_at=expires_at,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not sign attachment URL: {e}")
    return url
