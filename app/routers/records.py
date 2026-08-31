"""Site Records — Director/Admin only, no exceptions.

Unifies operational, incident and compliance data already captured
elsewhere into one searchable archive and accumulating case file per site,
with PDF export for legal, insurance or client purposes. Exports are
immutable once generated — see models.RecordExport.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..deps import require_director, require_site_feature
from ..documents import signed_pdf_url, upload_pdf
from ..identity import role_names
from ..models import Operator, RecordExport, Site, SiteFeatureKey
from ..pdf_export import build_pdf
from ..schemas import RecordExportOut, RecordExportRequest, SiteRecordsOut
from ..site_records import build_site_records

router = APIRouter(prefix="/director/sites", tags=["records"])

ALL_SECTIONS = ["shifts", "check_ins", "invoices", "incidents", "compliance"]


def _site_or_404(db: Session, slug: str) -> Site:
    site = db.query(Site).filter(Site.slug == slug).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


def _build_export_out(exp: RecordExport, with_url: bool = False) -> RecordExportOut:
    return RecordExportOut(
        id=exp.id, site_id=exp.site_id, generated_by=exp.generated_by,
        generated_by_name=exp.generated_by_operator.full_name if exp.generated_by_operator else None,
        generated_at=exp.generated_at, period_start=exp.period_start, period_end=exp.period_end,
        sections_included=exp.sections_included, purpose=exp.purpose, include_rates=exp.include_rates,
        download_url=signed_pdf_url(exp.file_key) if with_url else None,
    )


@router.get("/{slug}/records", response_model=SiteRecordsOut)
def get_site_records(
    slug: str,
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    types: Optional[str] = Query(None, description="Comma-separated: shifts,check_ins,invoices,incidents,compliance"),
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _site_or_404(db, slug)
    include_rates = "admin" in role_names(db, current)

    records = build_site_records(db, site, from_, to, include_rates=include_rates)

    if types:
        wanted = {t.strip() for t in types.split(",") if t.strip()}
        if "shifts" not in wanted: records.shifts = []
        if "check_ins" not in wanted: records.check_ins = []
        if "invoices" not in wanted: records.invoices = []
        if "incidents" not in wanted: records.incidents = []
        if "compliance" not in wanted: records.compliance = []

    return records


@router.get("/{slug}/records/search", response_model=SiteRecordsOut)
def search_site_records(
    slug: str,
    q: str = Query(..., min_length=1),
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = Query(None),
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    """A simple substring search across the unified record set — operator
    name, shift name, incident type, invoice status, whatever mentions the
    term. Defaults to the trailing year if no range is given, since search
    implies you don't already know where to look."""
    site = _site_or_404(db, slug)
    include_rates = "admin" in role_names(db, current)
    start = from_ or date(date.today().year - 1, date.today().month, 1)
    end = to or date.today()

    records = build_site_records(db, site, start, end, include_rates=include_rates)
    needle = q.strip().lower()

    records.shifts = [s for s in records.shifts if needle in s.shift_name.lower()
                      or any(needle in (a.operator_name or "").lower() for a in s.assignments)
                      or needle in s.date.isoformat()]
    records.check_ins = [c for c in records.check_ins if needle in c.operator_name.lower()
                         or needle in c.shift_name.lower() or needle in c.status.value.lower()
                         or needle in c.date.isoformat()]
    records.invoices = [i for i in records.invoices if needle in i.operator_name.lower()
                        or needle in i.status.value.lower()]
    records.incidents = [i for i in records.incidents if needle in i.incident_type.lower()
                         or needle in i.summary.lower() or needle in (i.operator_name or "").lower()
                         or needle in i.date.isoformat()]
    records.compliance = [c for c in records.compliance if needle in c.operator_name.lower()
                          or needle in c.licence_status.value.lower()]
    return records


@router.post("/{slug}/records/export", response_model=RecordExportOut, status_code=201)
def export_site_records(
    slug: str,
    body: RecordExportRequest,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _site_or_404(db, slug)
    require_site_feature(db, site, SiteFeatureKey.records_export)

    sections = [s for s in body.sections if s in ALL_SECTIONS]
    if not sections:
        raise HTTPException(status_code=400, detail="Choose at least one section to export")
    if body.period_end < body.period_start:
        raise HTTPException(status_code=400, detail="Period end must be on or after period start")

    # A Director can only produce a client-safe export; only Admin can choose
    # to include rate/financial detail, regardless of what the request asks for.
    include_rates = body.include_rates and "admin" in role_names(db, current)

    records = build_site_records(db, site, body.period_start, body.period_end, include_rates=include_rates)
    effective_request = body.model_copy(update={"sections": sections, "include_rates": include_rates})
    pdf_bytes = build_pdf(records, effective_request, current.full_name)
    file_key = upload_pdf(site.slug, pdf_bytes)

    export = RecordExport(
        site_id=site.id, generated_by=current.id, period_start=body.period_start,
        period_end=body.period_end, sections_included=sections, file_key=file_key,
        purpose=body.purpose, include_rates=include_rates,
    )
    db.add(export)
    db.commit()
    db.refresh(export)
    return _build_export_out(export, with_url=True)


@router.get("/{slug}/records/exports", response_model=list[RecordExportOut])
def list_site_record_exports(
    slug: str,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _site_or_404(db, slug)
    rows = (
        db.query(RecordExport)
        .options(joinedload(RecordExport.generated_by_operator))
        .filter(RecordExport.site_id == site.id)
        .order_by(RecordExport.generated_at.desc())
        .all()
    )
    return [_build_export_out(e, with_url=True) for e in rows]
