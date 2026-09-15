import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_operator, require_director
from ..models import (Operator, OperatorRole, OperatorRoleAssignment, Role,
                      Site, SOSEntry, SOSStatus)
from ..photos import purge_photo, signed_url, upload_photo
from ..schemas import SOSEntryCreate, SOSEntryOut, SOSEntryPatch
from ..sos_logic import calculate_sos_end_date, sos_requires_manager

router = APIRouter(tags=["sos"])

SOS_PHOTO_FOLDER = "sos-photos"


def _out(entry: SOSEntry) -> SOSEntryOut:
    return SOSEntryOut(
        id=entry.id,
        site_id=entry.site_id,
        site_name=entry.site.name if entry.site else None,
        name=entry.name,
        status=entry.status,
        trespassed=entry.trespassed,
        reason=entry.reason,
        length=entry.length,
        date_posted=entry.date_posted,
        calculated_end_date=entry.calculated_end_date,
        photo_url=signed_url(entry.photo_key) if entry.photo_key else None,
        notes=entry.notes,
        submitted_by=entry.submitted_by,
        submitted_by_name=entry.submitted_by_operator.full_name if entry.submitted_by_operator else None,
        approved_by=entry.approved_by,
        approved_by_name=entry.approved_by_operator.full_name if entry.approved_by_operator else None,
        reviewed_at=entry.reviewed_at,
        created_at=entry.created_at,
        requires_manager=sos_requires_manager(entry.length),
    )


@router.get("/sites/{slug}/sos", response_model=list[SOSEntryOut])
def list_site_sos(
    slug: str,
    status: SOSStatus | None = None,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    site = db.query(Site).filter(Site.slug == slug).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    q = db.query(SOSEntry).filter(SOSEntry.site_id == site.id)
    if status:
        q = q.filter(SOSEntry.status == status)
    entries = q.order_by(SOSEntry.created_at.desc()).all()
    return [_out(e) for e in entries]


@router.get("/sos/{sos_id}", response_model=SOSEntryOut)
def get_sos_entry(
    sos_id: uuid.UUID,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    entry = db.get(SOSEntry, sos_id)
    if not entry:
        raise HTTPException(status_code=404, detail="SOS entry not found")
    return _out(entry)


@router.post("/sos", response_model=SOSEntryOut, status_code=201)
def create_sos_entry(
    body: SOSEntryCreate,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    site = db.get(Site, body.site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    entry = SOSEntry(
        site_id=body.site_id,
        name=body.name,
        trespassed=body.trespassed,
        reason=body.reason,
        length=body.length,
        date_posted=body.date_posted,
        notes=body.notes,
        submitted_by=current.id,
        status=SOSStatus.pending,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _out(entry)


@router.patch("/sos/{sos_id}", response_model=SOSEntryOut)
def patch_sos_entry(
    sos_id: uuid.UUID,
    body: SOSEntryPatch,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    entry = db.get(SOSEntry, sos_id)
    if not entry:
        raise HTTPException(status_code=404, detail="SOS entry not found")

    is_director_or_admin = current.role in (OperatorRole.director, OperatorRole.admin) or (
        db.query(OperatorRoleAssignment)
        .join(Role)
        .filter(OperatorRoleAssignment.operator_id == current.id,
                Role.name.in_(["admin", "director"]))
        .first()
        is not None
    )
    is_pending_submitter = entry.submitted_by == current.id and entry.status == SOSStatus.pending
    if not (is_director_or_admin or is_pending_submitter):
        raise HTTPException(status_code=403, detail="Not allowed to edit this entry")

    if body.name is not None:
        entry.name = body.name
    if body.trespassed is not None:
        entry.trespassed = body.trespassed
    if body.reason is not None:
        entry.reason = body.reason
    if body.length is not None:
        entry.length = body.length
        if entry.status == SOSStatus.active:
            entry.calculated_end_date = calculate_sos_end_date(entry.length, entry.date_posted)
    if body.notes is not None:
        entry.notes = body.notes

    db.commit()
    db.refresh(entry)
    return _out(entry)


@router.patch("/sos/{sos_id}/approve", response_model=SOSEntryOut)
def approve_sos_entry(
    sos_id: uuid.UUID,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    entry = db.get(SOSEntry, sos_id)
    if not entry:
        raise HTTPException(status_code=404, detail="SOS entry not found")

    entry.status = SOSStatus.active
    entry.calculated_end_date = calculate_sos_end_date(entry.length, entry.date_posted)
    entry.approved_by = current.id
    entry.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)
    return _out(entry)


@router.patch("/sos/{sos_id}/reject", response_model=SOSEntryOut)
def reject_sos_entry(
    sos_id: uuid.UUID,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    entry = db.get(SOSEntry, sos_id)
    if not entry:
        raise HTTPException(status_code=404, detail="SOS entry not found")

    entry.status = SOSStatus.rejected
    entry.approved_by = current.id
    entry.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)
    return _out(entry)


@router.post("/sos/{sos_id}/photo", response_model=SOSEntryOut)
async def upload_sos_photo(
    sos_id: uuid.UUID,
    file: UploadFile = File(...),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    entry = db.get(SOSEntry, sos_id)
    if not entry:
        raise HTTPException(status_code=404, detail="SOS entry not found")

    raw = await file.read()
    old_key = entry.photo_key
    entry.photo_key = upload_photo(entry.id, raw, file.content_type, folder=SOS_PHOTO_FOLDER)
    db.commit()
    db.refresh(entry)

    if old_key:
        purge_photo(old_key)

    return _out(entry)
