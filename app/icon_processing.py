"""PWA icon handling: validation, center-crop-to-square, and generating the
full size set required by manifest.json / apple-touch-icon.

Unlike face photos (app/photos.py) or chat attachments (app/documents.py),
these need to be fetchable by the browser/OS without auth, so they're
uploaded to Cloudinary with public (`type="upload"`) delivery — the first
public delivery mode in this codebase. "Unguessable" comes from a random
public-id prefix, not from access control.
"""
import io
import uuid
from typing import Optional

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from fastapi import HTTPException
from PIL import Image

from .config import settings

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MIN_DIMENSION = 512
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
FOLDER = "app-icon"
ICON_SIZES = [72, 96, 128, 144, 152, 180, 192, 384, 512]


def _configured() -> bool:
    return bool(settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY
                and settings.CLOUDINARY_API_SECRET)


def _configure() -> None:
    if not _configured():
        raise HTTPException(
            status_code=503,
            detail="Icon storage is not configured on this server.",
        )
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def _load_and_square(raw: bytes, content_type: Optional[str]) -> Image.Image:
    if content_type and content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Upload a JPEG, PNG or WebP image.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="That image is too large — keep it under 8 MB.")

    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
    except Exception:
        raise HTTPException(status_code=400, detail="That file is not a readable image.")

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    w, h = img.size
    if min(w, h) < MIN_DIMENSION:
        raise HTTPException(
            status_code=400,
            detail=f"Icon must be at least {MIN_DIMENSION}x{MIN_DIMENSION} — got {w}x{h}.",
        )

    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

    return img


def generate_size_set(img: Image.Image) -> dict[int, bytes]:
    out = {}
    for size in ICON_SIZES:
        resized = img.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG", optimize=True)
        out[size] = buf.getvalue()
    return out


def upload_icon(raw: bytes, content_type: Optional[str]) -> str:
    """Validate, square, generate every required size, upload each publicly.
    Returns the random key to store as `app_icon_key`."""
    img = _load_and_square(raw, content_type)
    sizes = generate_size_set(img)

    _configure()
    key = uuid.uuid4().hex
    for size, png_bytes in sizes.items():
        cloudinary.uploader.upload(
            png_bytes,
            public_id=f"{FOLDER}/{key}-{size}",
            type="upload",          # public delivery — required for OS/browser fetch
            resource_type="image",
            overwrite=True,
            invalidate=True,
        )
    return key


def icon_urls(key: str) -> dict[str, str]:
    """Public URLs for an already-uploaded icon key, one per required size."""
    _configure()
    urls = {}
    for size in ICON_SIZES:
        url, _ = cloudinary.utils.cloudinary_url(
            f"{FOLDER}/{key}-{size}", format="png", resource_type="image", type="upload",
        )
        urls[str(size)] = url
    return urls
