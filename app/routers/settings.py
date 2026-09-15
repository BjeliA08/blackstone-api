from fastapi import APIRouter, Depends, File, Request, UploadFile
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


def _icons_for(row: AppSettings, request: Request) -> dict[str, str]:
    if row.app_icon_key:
        return icon_urls(row.app_icon_key)
    # The frontend is served from a different origin than this API, so these
    # need to be absolute — a browser resolving a relative path would look
    # for it on its own origin instead. Railway terminates TLS at the proxy
    # and forwards to this process over plain HTTP, so request.base_url's
    # scheme is wrong unless the forwarded-proto header is honored.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    base = f"{proto}://{host}"
    return {str(size): f"{base}{DEFAULT_ICON_BASE}/{size}.png" for size in ICON_SIZES}


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
def get_app_settings(request: Request, db: Session = Depends(get_db)):
    row = _get_or_create(db)
    return AppSettingsOut(
        app_name=row.app_name,
        theme_color=row.theme_color,
        background_color=row.background_color,
        icons=_icons_for(row, request),
    )


@router.patch("/admin/app-settings", response_model=AppSettingsOut)
def patch_app_settings(
    body: AppSettingsPatch,
    request: Request,
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
        icons=_icons_for(row, request),
    )


@router.post("/admin/app-settings/icon", response_model=AppSettingsOut)
async def upload_app_icon(
    request: Request,
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
        icons=_icons_for(row, request),
    )


@router.get("/manifest.json")
def get_manifest(request: Request, db: Session = Depends(get_db)):
    row = _get_or_create(db)
    icons = _icons_for(row, request)
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
