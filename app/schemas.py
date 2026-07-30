from __future__ import annotations
import uuid
from datetime import date, datetime, time
from typing import Optional
from pydantic import BaseModel, ConfigDict
from .models import CheckInStatus, OperatorRole, ShiftStatus


# ── Auth ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    phone_number: str
    password: str


class SetPasswordRequest(BaseModel):
    phone_number: str
    setup_code: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# ── Operator ──────────────────────────────────────────────────────────────────

class OperatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    phone_number: str
    discord_id: Optional[str]
    role: OperatorRole
    active: bool
    created_at: datetime


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
