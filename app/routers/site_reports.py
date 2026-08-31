"""Operational report filing — Narcan administration, incidents, ejections,
and EPS/EMS calls.

Shelter-only for now (the only site that needs it); the guard is a single
slug check below rather than a site attribute, so extending this to other
sites later is a one-line change, not a migration.

Any operator with SiteAccess to the site can file a report — this happens
during or right after a shift, by whoever was there, not after the fact by a
director. Directors/Admin can view everything for the site. A report can be
deleted by whoever submitted it (fixing a mis-filed entry) or by Director/
Admin — nobody else, since these are meant to stand as a record.
"""
import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..deps import get_current_operator, require_director
from ..models import Operator, Site, SiteAccess, SiteReport, SiteReportCategory
from ..schemas import (EjectionReportDetails, EMSCallReportDetails,
                       EPSCallReportDetails, IncidentReportDetails,
                       NarcanReportDetails, SiteReportCreate, SiteReportOut)

router = APIRouter(prefix="/site/{slug}/reports", tags=["site-reports"])

# Sites this feature is enabled on. A slug check rather than a Site column,
# since "which sites report which things" is an operational decision, not
# schema — flip it here when Shelter isn't the only one anymore.
ENABLED_SITE_SLUGS = {"shelter"}

DETAIL_SCHEMAS = {
    SiteReportCategory.narcan_administration: NarcanReportDetails,
    SiteReportCategory.incident_report: IncidentReportDetails,
    SiteReportCategory.ejection: EjectionReportDetails,
    SiteReportCategory.eps_call: EPSCallReportDetails,
    SiteReportCategory.ems_call: EMSCallReportDetails,
}


def _get_enabled_site(db: Session, slug: str) -> Site:
    if slug not in ENABLED_SITE_SLUGS:
        raise HTTPException(status_code=404, detail="Site reports are not enabled for this site")
    site = db.query(Site).filter(Site.slug == slug).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


def _require_site_access(db: Session, operator: Operator, site: Site) -> None:
    from ..identity import role_names
    if "admin" in role_names(db, operator):
        return
    has_access = (
        db.query(SiteAccess)
        .filter(SiteAccess.operator_id == operator.id, SiteAccess.site_id == site.id)
        .first()
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="You do not have access to this site")


def _out(report: SiteReport) -> SiteReportOut:
    op = report.submitted_by_operator
    name = f"{op.first_name} {op.last_name}".strip() if op else "Unknown"
    return SiteReportOut(
        id=report.id,
        site_id=report.site_id,
        category=report.category,
        occurred_at=report.occurred_at,
        narrative=report.narrative,
        details=report.details,
        submitted_by=report.submitted_by,
        submitted_by_name=name,
        created_at=report.created_at,
    )


@router.post("", response_model=SiteReportOut)
def create_report(
    slug: str,
    body: SiteReportCreate,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    site = _get_enabled_site(db, slug)
    _require_site_access(db, current, site)

    schema = DETAIL_SCHEMAS[body.category]
    try:
        validated_details = schema(**body.details).model_dump()
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid details for {body.category.value}: {e}")

    report = SiteReport(
        site_id=site.id,
        category=body.category,
        occurred_at=body.occurred_at,
        narrative=body.narrative,
        details=validated_details,
        submitted_by=current.id,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _out(report)


@router.get("", response_model=list[SiteReportOut])
def list_reports(
    slug: str,
    category: Optional[SiteReportCategory] = None,
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = _get_enabled_site(db, slug)

    q = (
        db.query(SiteReport)
        .options(joinedload(SiteReport.submitted_by_operator))
        .filter(SiteReport.site_id == site.id)
    )
    if category:
        q = q.filter(SiteReport.category == category)
    if date_from:
        q = q.filter(SiteReport.occurred_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(SiteReport.occurred_at <= datetime.combine(date_to, datetime.max.time()))

    reports = q.order_by(SiteReport.occurred_at.desc()).all()
    return [_out(r) for r in reports]


@router.delete("/{report_id}", status_code=204)
def delete_report(
    slug: str,
    report_id: uuid.UUID,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    from ..identity import role_names

    site = _get_enabled_site(db, slug)
    report = (
        db.query(SiteReport)
        .filter(SiteReport.id == report_id, SiteReport.site_id == site.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    is_submitter = report.submitted_by == current.id
    is_director_or_admin = bool(role_names(db, current) & {"admin", "director"})
    if not is_submitter and not is_director_or_admin:
        raise HTTPException(status_code=403, detail="Only the submitter or a Director/Admin can delete this report")

    db.delete(report)
    db.commit()
