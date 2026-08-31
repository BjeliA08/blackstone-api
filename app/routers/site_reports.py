"""Operational report filing — Narcan administration, incidents, ejections,
and EPS/EMS calls.

Which sites report which categories is an operational decision, not schema
— it's a plain dict below rather than a Site column or a migration. Shelter
gets everything (it's the only site that needs Narcan/EMS/EPS); Club101 and
Starhall only get Ejection and Incident Report, since those are the
categories that actually happen at a club/event venue.

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

DETAIL_SCHEMAS = {
    SiteReportCategory.narcan_administration: NarcanReportDetails,
    SiteReportCategory.incident_report: IncidentReportDetails,
    SiteReportCategory.ejection: EjectionReportDetails,
    SiteReportCategory.eps_call: EPSCallReportDetails,
    SiteReportCategory.ems_call: EMSCallReportDetails,
}

ALL_CATEGORIES = set(DETAIL_SCHEMAS)
CLUB_CATEGORIES = {SiteReportCategory.ejection, SiteReportCategory.incident_report}

# Sites this feature is enabled on, and which categories each one allows.
SITE_ALLOWED_CATEGORIES: dict[str, set] = {
    "shelter": ALL_CATEGORIES,
    "club101": CLUB_CATEGORIES,
    "starhall": CLUB_CATEGORIES,
}


def _get_enabled_site(db: Session, slug: str) -> Site:
    if slug not in SITE_ALLOWED_CATEGORIES:
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

    if body.category not in SITE_ALLOWED_CATEGORIES[slug]:
        raise HTTPException(status_code=400,
                           detail=f"{body.category.value} reports are not enabled for this site")

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
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Director/Admin see every report for the site. A plain operator only
    sees their own — enough to find and delete something they mis-filed,
    without opening up everyone else's reports to everyone on site."""
    from ..identity import role_names

    site = _get_enabled_site(db, slug)
    roles = role_names(db, current)
    is_director_or_admin = bool(roles & {"admin", "director"})
    if not is_director_or_admin:
        _require_site_access(db, current, site)

    q = (
        db.query(SiteReport)
        .options(joinedload(SiteReport.submitted_by_operator))
        .filter(SiteReport.site_id == site.id)
    )
    if not is_director_or_admin:
        q = q.filter(SiteReport.submitted_by == current.id)
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
