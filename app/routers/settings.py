from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..icon_processing import ICON_SIZES, icon_urls, upload_icon
from ..models import AppSettings

router = APIRouter(tags=["app-settings"])

DEFAULT_ICON_BASE = "/static/default_icon"
SHORT_NAME_MAX = 12


def _get_or_create(db: Session) -> AppSettings:
    row = db.query(AppSettings).first()
    if not row:
        row = AppSettings()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _icons_for(row: AppSettings) -> dict[str, str]:
    if row.app_icon_key:
        return icon_urls(row.app_icon_key)
    return {str(size): f"{DEFAULT_ICON_BASE}/{size}.png" for size in ICON_SIZES}


class AppSettingsOut(BaseModel):
    app_name: str
    theme_color: str
    background_color: str
    icons: dict[str, str]


class AppSettingsPatch(BaseModel):
    app_name: str | None = None
    theme_color: str | None = None
    background_color: str | None = None


@router.get("/app-settings", response_model=AppSettingsOut)
def get_app_settings(db: Session = Depends(get_db)):
    row = _get_or_create(db)
    return AppSettingsOut(
        app_name=row.app_name,
        theme_color=row.theme_color,
        background_color=row.background_color,
        icons=_icons_for(row),
    )


@router.patch("/admin/app-settings", response_model=AppSettingsOut)
def patch_app_settings(
    body: AppSettingsPatch,
    db: Session = Depends(get_db),
    current=Depends(require_admin),
):
    row = _get_or_create(db)
    if body.app_name is not None:
        row.app_name = body.app_name
    if body.theme_color is not None:
        row.theme_color = body.theme_color
    if body.background_color is not None:
        row.background_color = body.background_color
    db.commit()
    db.refresh(row)
    return AppSettingsOut(
        app_name=row.app_name,
        theme_color=row.theme_color,
        background_color=row.background_color,
        icons=_icons_for(row),
    )


@router.post("/admin/app-settings/icon", response_model=AppSettingsOut)
async def upload_app_icon(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current=Depends(require_admin),
):
    raw = await file.read()
    key = upload_icon(raw, file.content_type)

    row = _get_or_create(db)
    row.app_icon_key = key
    db.commit()
    db.refresh(row)
    return AppSettingsOut(
        app_name=row.app_name,
        theme_color=row.theme_color,
        background_color=row.background_color,
        icons=_icons_for(row),
    )


@router.get("/manifest.json")
def get_manifest(db: Session = Depends(get_db)):
    row = _get_or_create(db)
    icons = _icons_for(row)
    name = row.app_name
    short_name = name if len(name) <= SHORT_NAME_MAX else name[:SHORT_NAME_MAX]

    manifest = {
        "name": name,
        "short_name": short_name,
        # The frontend uses HashRouter — "/select" alone would hit the
        # server's plain index route and boot at the root hash, not the
        # site-selection hub. The hash form is what actually lands there.
        "start_url": "/#/select",
        "display": "standalone",
        "background_color": row.background_color,
        "theme_color": row.theme_color,
        "orientation": "portrait",
        "icons": [
            {"src": url, "sizes": f"{size}x{size}", "type": "image/png"}
            for size, url in ((int(s), u) for s, u in icons.items())
        ],
    }
    return JSONResponse(content=manifest, media_type="application/manifest+json")
