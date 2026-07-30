import hashlib
import time
from fastapi import APIRouter, Depends
from ..config import settings
from ..deps import require_admin
from ..models import Operator

router = APIRouter(prefix="/admin/sos", tags=["sos"])


@router.post("/upload-signature")
def upload_signature(_: Operator = Depends(require_admin)):
    """
    Returns a Cloudinary signed upload signature so the desktop app can upload
    directly to Cloudinary without exposing the API secret client-side.
    """
    timestamp = int(time.time())
    folder = "sos_registry"
    upload_preset = "ml_default"

    # Parameters must be sorted alphabetically for signature
    params_str = f"folder={folder}&timestamp={timestamp}&upload_preset={upload_preset}"
    signature = hashlib.sha1(
        f"{params_str}{settings.CLOUDINARY_API_SECRET}".encode()
    ).hexdigest()

    return {
        "timestamp": timestamp,
        "signature": signature,
        "folder": folder,
        "upload_preset": upload_preset,
        "api_key": settings.CLOUDINARY_API_KEY,
        "cloud_name": settings.CLOUDINARY_CLOUD_NAME,
    }
