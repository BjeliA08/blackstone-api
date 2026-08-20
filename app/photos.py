"""Face photo handling.

Face photos are biometric-adjacent personal data, so:

* they are uploaded to Cloudinary as `type=private` — never a public bucket,
  and never reachable from a guessable URL
* EXIF is stripped on ingest before the bytes leave this process, because
  phone cameras embed GPS coordinates in photos
* delivery is a short-lived signed URL minted per request, only after the
  caller's right to see that operator's photo has been checked
"""
import io
import time
import uuid
from typing import Optional

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from fastapi import HTTPException
from PIL import Image

from .config import settings

MAX_UPLOAD_BYTES = 8 * 1024 * 1024          # 8 MB off the wire
MAX_DIMENSION = 1024                         # a face shot needs no more
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}
FOLDER = "operator-photos"


def _configured() -> bool:
    return bool(settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY
                and settings.CLOUDINARY_API_SECRET)


def _configure() -> None:
    if not _configured():
        raise HTTPException(
            status_code=503,
            detail="Photo storage is not configured on this server.",
        )
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def sanitize_image(raw: bytes, content_type: Optional[str]) -> bytes:
    """Validate, normalise and strip metadata.

    Re-encoding through Pillow without copying the EXIF block is what actually
    removes location data — there is no metadata carried into the output.
    """
    if content_type and content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400,
                            detail="Upload a JPEG, PNG or WebP image.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400,
                            detail="That image is too large — keep it under 8 MB.")

    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()                      # cheap structural check
        img = Image.open(io.BytesIO(raw))  # verify() exhausts the file object
    except Exception:
        raise HTTPException(status_code=400, detail="That file is not a readable image.")

    # Honour the EXIF orientation flag before discarding EXIF, otherwise
    # phone photos come out sideways.
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)  # no exif= argument
    return out.getvalue()


def upload_photo(operator_id: uuid.UUID, raw: bytes, content_type: Optional[str]) -> str:
    """Store the sanitised image privately and return its storage key."""
    clean = sanitize_image(raw, content_type)
    _configure()
    public_id = f"{FOLDER}/{operator_id}-{uuid.uuid4().hex[:10]}"
    result = cloudinary.uploader.upload(
        clean,
        public_id=public_id,
        type="private",          # never publicly addressable
        resource_type="image",
        overwrite=True,
        invalidate=True,
        image_metadata=False,
    )
    return result.get("public_id", public_id)


def signed_url(photo_key: str) -> str:
    """A URL that stops working shortly after it is handed out."""
    _configure()
    expires_at = int(time.time()) + settings.PHOTO_URL_TTL_SECONDS
    url, _ = cloudinary.utils.private_download_url(
        photo_key, "jpg", resource_type="image", type="private",
        expires_at=expires_at,
    )
    return url


def purge_photo(photo_key: str) -> None:
    """Permanent removal, for a departed operator whose data should actually
    go rather than sit indefinitely."""
    if not photo_key or not _configured():
        return
    _configure()
    try:
        cloudinary.uploader.destroy(photo_key, type="private", resource_type="image",
                                    invalidate=True)
    except Exception:
        # The database record is cleared regardless; a stranded blob is
        # better than a half-purged operator.
        pass
