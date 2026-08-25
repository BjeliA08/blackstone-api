from __future__ import annotations
import uuid
from datetime import date, datetime, time
from typing import Optional
from pydantic import BaseModel, ConfigDict
from .models import (AvailabilityStatus, ChatChannelType, CheckInStatus,
                     CoverageType, LicenceStatus, OnboardingStatus,
                     OperatorRole, ShiftStatus, SiteStatus, SiteType)


# ── Auth ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    phone_number: str
    password: str
    # Keeps this device signed in until the trust window runs out.
    remember_device: bool = False


class SetPasswordRequest(BaseModel):
    phone_number: str
    setup_code: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    # Only issued for a remembered device; absent otherwise.
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    device_trust_expires_at: Optional[datetime] = None


class RefreshRequest(BaseModel):
    refresh_token: str


# ── Operator ──────────────────────────────────────────────────────────────────

class OperatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    full_name: str
    phone_number: str
    discord_id: Optional[str]
    role: OperatorRole
    active: bool
    created_at: datetime
    is_admin: bool = False
    onboarding_status: OnboardingStatus = OnboardingStatus.active
    profile_complete: bool = False
    has_photo: bool = False
    # Compliance data — present only for Admin, Directors, and the operator
    # viewing their own profile. Absent entirely otherwise.
    security_licence_number: Optional[str] = None
    security_licence_expiry: Optional[date] = None
    licence_status: Optional[LicenceStatus] = None


class OperatorCreate(BaseModel):
    full_name: str
    phone_number: str
    discord_id: Optional[str] = None
    role: OperatorRole = OperatorRole.operator
    active: bool = True


class OperatorPatch(BaseModel):
    full_name: Optional[str] = None
    role: Optional[OperatorRole] = None
    active: Optional[bool] = None
    discord_id: Optional[str] = None


# ── Site ─────────────────────────────────────────────────────────────────────

class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    slot_count: int
    color: str
    active: bool


# ── Assignment ────────────────────────────────────────────────────────────────

class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slot_index: int
    operator_id: Optional[uuid.UUID]
    operator_name: Optional[str] = None
    position: Optional[str]
    start_time: Optional[time]
    end_time: Optional[time]
    accepted: bool
    is_open: bool = False

    @classmethod
    def from_orm_with_name(cls, a) -> "AssignmentOut":
        return cls(
            id=a.id,
            slot_index=a.slot_index,
            operator_id=a.operator_id,
            operator_name=a.operator.full_name if a.operator else None,
            position=a.position,
            start_time=a.start_time,
            end_time=a.end_time,
            accepted=a.accepted,
            is_open=a.operator_id is None,
        )


# ── Shift ─────────────────────────────────────────────────────────────────────

class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    site_name: Optional[str] = None
    date: date
    shift_name: str
    status: ShiftStatus
    assignments: list[AssignmentOut] = []


# ── Check-in ──────────────────────────────────────────────────────────────────

class CheckInOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assignment_id: uuid.UUID
    operator_id: uuid.UUID
    scheduled_start: time
    scheduled_end: time
    actual_check_in: Optional[datetime]
    actual_check_out: Optional[datetime]
    status: CheckInStatus
    notes: Optional[str]
    created_at: datetime


# ── Hours ─────────────────────────────────────────────────────────────────────

class HoursSummary(BaseModel):
    operator_id: uuid.UUID
    operator_name: str
    month: int
    year: int
    total_hours: float
    shift_count: int


# ── Today check-in status ─────────────────────────────────────────────────────

class CheckInStatusRow(BaseModel):
    assignment_id: uuid.UUID
    operator_name: str
    site_name: str
    shift_name: str
    scheduled_start: time
    scheduled_end: time
    actual_check_in: Optional[datetime]
    actual_check_out: Optional[datetime]
    status: CheckInStatus
    missed_check_in: bool
    missed_check_out: bool


# ── Roles / Admin ─────────────────────────────────────────────────────────────

class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str


class SiteAccessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: Optional[str] = None
    granted_at: datetime


class OperatorRoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    role_id: uuid.UUID
    role_name: Optional[str] = None
    assigned_at: datetime


class OperatorWithRoles(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    full_name: str
    phone_number: str
    discord_id: Optional[str]
    active: bool
    roles: list[OperatorRoleOut] = []
    site_accesses: list[SiteAccessOut] = []


class AssignRoleRequest(BaseModel):
    role_name: str


class GrantSiteRequest(BaseModel):
    site_slug: str


# ── Availability ──────────────────────────────────────────────────────────────

class AvailabilityPeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    month: int
    year: int
    opens_at: datetime
    closes_at: datetime
    status: AvailabilityStatus
    created_at: datetime


class AvailabilityPeriodCreate(BaseModel):
    month: int
    year: int
    opens_at: datetime
    closes_at: datetime
    status: AvailabilityStatus = AvailabilityStatus.draft


class AvailabilityPeriodPatch(BaseModel):
    opens_at: Optional[datetime] = None
    closes_at: Optional[datetime] = None
    status: Optional[AvailabilityStatus] = None


class SiteShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    site_id: uuid.UUID
    site_slug: Optional[str] = None
    site_name: Optional[str] = None
    shift_name: str
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    sort_order: int
    active: bool
    # Keyed "0" (Monday) through "6" (Sunday). Null means every night needs
    # the site's flat slot_count.
    weekday_posts: Optional[dict[str, int]] = None


class SiteShiftCreate(BaseModel):
    site_slug: str
    shift_name: str
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    sort_order: int = 0
    weekday_posts: Optional[dict[str, int]] = None


class SiteShiftPatch(BaseModel):
    shift_name: Optional[str] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    clear_times: bool = False
    sort_order: Optional[int] = None
    active: Optional[bool] = None
    # Pass {} to clear back to the flat slot_count for every night.
    weekday_posts: Optional[dict[str, int]] = None


class AvailabilityEntryIn(BaseModel):
    site_id: uuid.UUID
    date: date
    shift_name: str
    available: bool
    earliest_start: Optional[time] = None
    latest_end: Optional[time] = None
    note: Optional[str] = None


class AvailabilityEntryOut(AvailabilityEntryIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    # Derived server-side from the shift window; clients cannot set it.
    coverage_type: CoverageType = CoverageType.full


class AvailabilitySubmissionIn(BaseModel):
    entries: list[AvailabilityEntryIn]


class AvailabilitySubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    operator_id: uuid.UUID
    period_id: uuid.UUID
    submitted_at: datetime
    updated_at: datetime
    entries: list[AvailabilityEntryOut] = []


class AvailabilitySubmissionWithOperator(AvailabilitySubmissionOut):
    operator_name: str


class MissingOperatorOut(BaseModel):
    operator_id: uuid.UUID
    operator_name: str


class AvailabilitySummaryOperator(BaseModel):
    operator_id: uuid.UUID
    operator_name: str
    earliest_start: Optional[time] = None
    latest_end: Optional[time] = None
    note: Optional[str] = None
    coverage_type: CoverageType = CoverageType.full


class ApproveDraftResult(BaseModel):
    approved: int


class GenerateScheduleRequest(BaseModel):
    respect_site_access: bool = True
    replace_existing_drafts: bool = True


class UnfilledSlotOut(BaseModel):
    date: date
    site_slug: str
    shift_name: str
    slot_index: int
    reason: str


class PartialFillOut(BaseModel):
    date: date
    site_slug: str
    shift_name: str
    operator_name: str
    covered_start: time
    covered_end: time
    remainder_start: time
    remainder_end: time
    description: str


class GenerationResultOut(BaseModel):
    period_id: uuid.UUID
    shifts_created: int
    slots_total: int
    slots_filled: int
    slots_open: int
    partial_fills: list[PartialFillOut] = []
    unfilled: list[UnfilledSlotOut] = []
    hours_by_operator: dict[str, float] = {}
    warnings: list[str] = []


class AvailabilitySummaryCell(BaseModel):
    date: date
    site_id: uuid.UUID
    site_slug: Optional[str] = None
    shift_name: str
    available_operators: list[AvailabilitySummaryOperator] = []
    # Split out so a director never reads a fallback offer as real coverage.
    full_count: int = 0
    partial_count: int = 0


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    channel_type: ChatChannelType
    site_slug: Optional[str] = None
    unread_count: int = 0
    last_message_at: Optional[datetime] = None


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    channel_id: uuid.UUID
    operator_id: uuid.UUID
    operator_name: str
    body: str
    created_at: datetime


class ChatMessageCreate(BaseModel):
    body: str


class ChatReadResult(BaseModel):
    channel_id: uuid.UUID
    last_read_at: datetime


# ── Signup & invite codes ─────────────────────────────────────────────────────

class ValidateCodeRequest(BaseModel):
    code: str


class ValidateCodeResult(BaseModel):
    valid: bool
    # Deliberately no reason field — a caller must not learn whether a code
    # was wrong, expired, revoked or used up.


class SignupRequest(BaseModel):
    code: str
    first_name: str
    last_name: str
    phone_number: str
    password: str


class ProfilePatch(BaseModel):
    security_licence_number: Optional[str] = None
    security_licence_expiry: Optional[date] = None


class InviteCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    created_by_name: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    max_uses: int
    use_count: int
    uses_remaining: int
    revoked: bool
    status: str          # active | used_up | expired | revoked
    intended_role: Optional[str] = None
    intended_site_access: Optional[list[str]] = None


class InviteCodeCreate(BaseModel):
    max_uses: int = 1
    expires_in_days: Optional[int] = 14
    intended_role: Optional[str] = None
    intended_site_access: Optional[list[str]] = None


class PhotoUrlOut(BaseModel):
    url: str
    expires_in: int


# ── Site selection hub ────────────────────────────────────────────────────────

class SiteSummary(BaseModel):
    """What the selector needs to show where attention is required."""
    on_post: int = 0            # operators currently checked in / assigned right now
    posts_required: int = 0     # slots the current shift should have filled
    open_contracts: int = 0     # unfilled slots from now onwards
    urgent_contracts: int = 0   # unfilled slots starting within 24h
    unread_messages: int = 0
    current_shift_name: Optional[str] = None


class SiteCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    color: str
    site_type: SiteType
    status: SiteStatus
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    days_remaining: Optional[int] = None
    description: Optional[str] = None
    slot_count: int
    summary: SiteSummary = SiteSummary()


# ── Personal operations ───────────────────────────────────────────────────────

class NextShiftOut(BaseModel):
    assignment_id: uuid.UUID
    site_slug: str
    site_name: str
    site_color: str
    date: date
    shift_name: str
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    position: Optional[str] = None
    accepted: bool
    hours_until: Optional[float] = None


class OutstandingAction(BaseModel):
    """Something the operator has to do. Severity drives the indicator."""
    kind: str                 # availability | licence | unaccepted_shift
    severity: str             # critical | caution
    title: str
    detail: str
    route: Optional[str] = None


class ActivityRowOut(BaseModel):
    kind: str                 # claimed | assigned
    date: date
    site_slug: str
    site_name: str
    site_color: str
    shift_name: str
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    accepted: bool


class HistoryRowOut(BaseModel):
    check_in_id: uuid.UUID
    date: date
    site_name: str
    site_color: str
    shift_name: str
    scheduled_start: time
    scheduled_end: time
    actual_check_in: Optional[datetime] = None
    actual_check_out: Optional[datetime] = None
    status: CheckInStatus
    notes: Optional[str] = None
    # Minutes relative to the scheduled start; negative means early.
    start_delta_minutes: Optional[int] = None


class MeOverviewOut(BaseModel):
    operator_id: uuid.UUID
    full_name: str
    has_photo: bool
    next_shift: Optional[NextShiftOut] = None
    month_hours: float = 0.0
    month_shift_count: int = 0
    hours_threshold: int = 160
    outstanding: list[OutstandingAction] = []
    recent_activity: list[ActivityRowOut] = []
