"""Site Builder — create and configure a new site (shift pattern, positions,
enabled features) entirely through the app, so standing up a new client or
contract is an admin task rather than a development task.

Everything here is Director/Admin only, enforced server-side. A newly
created site behaves identically to any existing one everywhere else in the
app — the same site_shifts/site_positions/site_features tables that scheduling,
check-in, invoicing, and chat already read from generically.
"""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_director
from ..models import (Operator, ShiftPatternTemplate, Site, SiteFeature,
                      SiteFeatureKey, SitePosition, SitePositionAssignment,
                      SiteShift)
from ..schemas import (ApplyShiftPatternRequest, SiteBuilderSiteOut,
                       SiteCreate, SiteFeatureOut, SiteFeaturePatch,
                       SitePositionAssignRequest, SitePositionCreate,
                       SitePositionOut, SiteShiftOut, SiteShiftPatch,
                       ShiftPatternTemplateOut)
from .availability import _build_site_shift_out

router = APIRouter(prefix="/admin/sites", tags=["site-builder"])

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _get_site_or_404(db: Session, slug: str) -> Site:
    site = db.query(Site).filter(Site.slug == slug).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.post("", response_model=SiteBuilderSiteOut, status_code=201)
def create_site(
    body: SiteCreate,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    slug = body.slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400,
                           detail="Slug must be lowercase letters, numbers, and hyphens only")
    if db.query(Site).filter(Site.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"A site with slug '{slug}' already exists")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    site = Site(
        name=body.name.strip(), slug=slug, site_type=body.site_type,
        starts_on=body.starts_on, ends_on=body.ends_on,
        description=body.description, color=body.color,
        slot_count=max(body.slot_count, 1),
    )
    db.add(site)
    db.flush()

    # Every site is immediately usable without extra setup calls: a default
    # position and a full feature row-set (sos off by default — it's the one
    # feature that's opt-in, matching the only site that has ever used it).
    db.add(SitePosition(site_id=site.id, name="Security Operator",
                        is_default_position=True, sort_order=0))
    for key in SiteFeatureKey:
        db.add(SiteFeature(site_id=site.id, feature_key=key,
                           enabled=(key != SiteFeatureKey.sos)))

    db.commit()
    db.refresh(site)
    return site


@router.get("/{slug}", response_model=SiteBuilderSiteOut)
def get_site(
    slug: str,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    return _get_site_or_404(db, slug)


# ── Shift pattern ────────────────────────────────────────────────────────────

@router.post("/{slug}/shift-pattern", response_model=list[SiteShiftOut], status_code=201)
def apply_shift_pattern(
    slug: str,
    body: ApplyShiftPatternRequest,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    """Applies a template's shifts (or a fully custom list) to a site. Adds
    to whatever shifts already exist rather than replacing them — a director
    building up a pattern in steps, or adding a second template's shifts on
    top of the first, shouldn't lose earlier work."""
    site = _get_site_or_404(db, slug)

    if body.template_id is not None:
        template = db.get(ShiftPatternTemplate, body.template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        source = template.default_shifts
        template_id = template.id
    elif body.shifts is not None:
        source = [s.model_dump() for s in body.shifts]
        template_id = None
    else:
        raise HTTPException(status_code=400, detail="Provide either template_id or shifts")

    existing_count = db.query(SiteShift).filter(SiteShift.site_id == site.id).count()
    created = []
    for i, s in enumerate(source):
        name = str(s.get("name", "")).strip()
        if not name:
            continue
        ss = SiteShift(
            site_id=site.id, shift_name=name,
            start_time=s.get("start_time") or None,
            end_time=s.get("end_time") or None,
            sort_order=existing_count + i,
            slot_count=s.get("default_slot_count") or s.get("slot_count"),
            based_on_template_id=template_id,
        )
        db.add(ss)
        created.append(ss)

    db.commit()
    for ss in created:
        db.refresh(ss)
    return [_build_site_shift_out(ss) for ss in created]


@router.patch("/{slug}/shift-pattern/{pattern_id}", response_model=SiteShiftOut)
def patch_shift_pattern(
    slug: str,
    pattern_id: uuid.UUID,
    body: SiteShiftPatch,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _get_site_or_404(db, slug)
    ss = db.query(SiteShift).filter(SiteShift.id == pattern_id, SiteShift.site_id == site.id).first()
    if not ss:
        raise HTTPException(status_code=404, detail="Shift not found on this site")

    from .availability import patch_site_shift as _patch
    return _patch(pattern_id, body, current, db)


@router.delete("/{slug}/shift-pattern/{pattern_id}", status_code=204)
def delete_shift_pattern(
    slug: str,
    pattern_id: uuid.UUID,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _get_site_or_404(db, slug)
    ss = db.query(SiteShift).filter(SiteShift.id == pattern_id, SiteShift.site_id == site.id).first()
    if not ss:
        raise HTTPException(status_code=404, detail="Shift not found on this site")

    from .availability import delete_site_shift as _delete
    _delete(pattern_id, current, db)


# ── Positions ────────────────────────────────────────────────────────────────

@router.get("/{slug}/positions", response_model=list[SitePositionOut])
def list_positions(
    slug: str,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _get_site_or_404(db, slug)
    return (
        db.query(SitePosition)
        .filter(SitePosition.site_id == site.id)
        .order_by(SitePosition.sort_order)
        .all()
    )


@router.post("/{slug}/positions", response_model=SitePositionOut, status_code=201)
def create_position(
    slug: str,
    body: SitePositionCreate,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _get_site_or_404(db, slug)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Position name cannot be empty")

    if body.is_default_position:
        # Exactly one default per site — demote whatever held it before.
        db.query(SitePosition).filter(
            SitePosition.site_id == site.id, SitePosition.is_default_position.is_(True)
        ).update({"is_default_position": False})

    pos = SitePosition(site_id=site.id, name=name,
                       is_default_position=body.is_default_position,
                       sort_order=body.sort_order)
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


@router.patch("/{slug}/positions/{position_id}/assign", response_model=dict)
def assign_position(
    slug: str,
    position_id: uuid.UUID,
    body: SitePositionAssignRequest,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _get_site_or_404(db, slug)
    position = db.query(SitePosition).filter(
        SitePosition.id == position_id, SitePosition.site_id == site.id
    ).first()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found on this site")
    shift_pattern = db.query(SiteShift).filter(
        SiteShift.id == body.shift_pattern_id, SiteShift.site_id == site.id
    ).first()
    if not shift_pattern:
        raise HTTPException(status_code=404, detail="Shift not found on this site")

    existing = db.query(SitePositionAssignment).filter(
        SitePositionAssignment.shift_pattern_id == body.shift_pattern_id,
        SitePositionAssignment.slot_index == body.slot_index,
    ).first()
    if existing:
        existing.position_id = position.id
    else:
        db.add(SitePositionAssignment(
            shift_pattern_id=body.shift_pattern_id,
            slot_index=body.slot_index, position_id=position.id,
        ))
    db.commit()
    return {"ok": True}


# ── Features ─────────────────────────────────────────────────────────────────

@router.get("/{slug}/features", response_model=list[SiteFeatureOut])
def get_features(
    slug: str,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _get_site_or_404(db, slug)
    return (
        db.query(SiteFeature)
        .filter(SiteFeature.site_id == site.id)
        .all()
    )


@router.patch("/{slug}/features/{feature_key}", response_model=SiteFeatureOut)
def patch_feature(
    slug: str,
    feature_key: SiteFeatureKey,
    body: SiteFeaturePatch,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _get_site_or_404(db, slug)
    row = db.query(SiteFeature).filter(
        SiteFeature.site_id == site.id, SiteFeature.feature_key == feature_key
    ).first()
    if not row:
        row = SiteFeature(site_id=site.id, feature_key=feature_key, enabled=body.enabled)
        db.add(row)
    else:
        row.enabled = body.enabled
    db.commit()
    db.refresh(row)
    return row


# ── Template library (mounted at /admin, not /admin/sites) ──────────────────

templates_router = APIRouter(prefix="/admin", tags=["site-builder"])


@templates_router.get("/shift-pattern-templates", response_model=list[ShiftPatternTemplateOut])
def list_shift_pattern_templates(
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    return db.query(ShiftPatternTemplate).all()
