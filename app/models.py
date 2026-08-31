import uuid
import enum
from datetime import datetime
from sqlalchemy import (Boolean, Column, Date, DateTime, Enum as SAEnum,
                        ForeignKey, Integer, JSON, Numeric, String, Text, Time,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy import func
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship
from .database import Base


class OperatorRole(str, enum.Enum):
    operator = "operator"
    director = "director"
    admin = "admin"


class ShiftStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"


class CheckInStatus(str, enum.Enum):
    pending = "pending"
    checked_in = "checked_in"
    checked_out = "checked_out"


class AvailabilityStatus(str, enum.Enum):
    draft = "draft"
    open = "open"
    closed = "closed"


class CoverageType(str, enum.Enum):
    full = "full"
    partial_fallback = "partial_fallback"


class OnboardingStatus(str, enum.Enum):
    invited = "invited"
    profile_pending = "profile_pending"
    active = "active"
    deactivated = "deactivated"


class LicenceStatus(str, enum.Enum):
    valid = "valid"
    expiring_soon = "expiring_soon"
    expired = "expired"
    missing = "missing"


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    approved = "approved"
    paid = "paid"


class OperationStatus(str, enum.Enum):
    planning = "planning"
    confirmed = "confirmed"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class SiteType(str, enum.Enum):
    permanent = "permanent"
    temporary = "temporary"


class SiteStatus(str, enum.Enum):
    active = "active"
    upcoming = "upcoming"
    ended = "ended"
    archived = "archived"


class ChatChannelType(str, enum.Enum):
    site = "site"
    site_leads = "site_leads"
    directors = "directors"
    admin = "admin"
    operation = "operation"
    direct = "direct"


class Operator(Base):
    __tablename__ = "operators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False, default="")
    phone_number = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=True)
    setup_code = Column(String, nullable=True)
    discord_id = Column(String, nullable=True)
    role = Column(SAEnum(OperatorRole), nullable=False, default=OperatorRole.operator)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    security_licence_number = Column(String, nullable=True)
    security_licence_expiry = Column(Date, nullable=True)
    photo_key = Column(String, nullable=True)
    onboarding_status = Column(SAEnum(OnboardingStatus), nullable=False,
                               default=OnboardingStatus.invited)
    invited_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)
    activated_at = Column(DateTime, nullable=True)

    # Contractor invoicing — visible only to Admin, Director, and the operator
    # themselves, same discipline as the licence fields above.
    pay_rate = Column(Numeric(10, 2), nullable=True)
    gst_number = Column(String, nullable=True)
    gst_registered = Column(Boolean, nullable=False, default=False)

    @hybrid_property
    def full_name(self) -> str:
        """There are no nicknames or display names anywhere — an operator is
        always their real first and last name."""
        return f"{self.first_name} {self.last_name}".strip()

    @full_name.expression
    def full_name(cls):
        # Keeps order_by/filter on full_name working in SQL.
        return func.trim(func.concat(cls.first_name, " ", func.coalesce(cls.last_name, "")))

    @property
    def profile_complete(self) -> bool:
        return bool(self.photo_key and self.security_licence_number
                    and self.security_licence_expiry)

    assignments = relationship("Assignment", back_populates="operator")
    check_ins = relationship("CheckIn", back_populates="operator")
    operator_roles = relationship("OperatorRoleAssignment", back_populates="operator",
                                  foreign_keys="OperatorRoleAssignment.operator_id")
    site_accesses = relationship("SiteAccess", back_populates="operator",
                                 foreign_keys="SiteAccess.operator_id")


class Site(Base):
    __tablename__ = "sites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    slot_count = Column(Integer, nullable=False)
    color = Column(String, nullable=False)
    active = Column(Boolean, nullable=False, default=True)

    # What the client is billed per hour at this site — Admin/Director only.
    bill_rate = Column(Numeric(10, 2), nullable=True)

    site_type = Column(SAEnum(SiteType), nullable=False, default=SiteType.permanent)
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)
    description = Column(String, nullable=True)
    # Only ever written as 'archived'; every other value is derived on read.
    status = Column(SAEnum(SiteStatus), nullable=False, default=SiteStatus.active)

    shifts = relationship("Shift", back_populates="site")

    def effective_status(self, today) -> "SiteStatus":
        """Derived from the dates so a site can never sit stale, except for
        archived, which is a deliberate Admin decision."""
        if self.status == SiteStatus.archived:
            return SiteStatus.archived
        if self.starts_on and today < self.starts_on:
            return SiteStatus.upcoming
        if self.ends_on and today > self.ends_on:
            return SiteStatus.ended
        return SiteStatus.active


class Shift(Base):
    __tablename__ = "shifts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    date = Column(Date, nullable=False)
    shift_name = Column(String, nullable=False)
    status = Column(SAEnum(ShiftStatus), nullable=False, default=ShiftStatus.draft)
    created_at = Column(DateTime, default=datetime.utcnow)

    site = relationship("Site", back_populates="shifts")
    assignments = relationship("Assignment", back_populates="shift",
                               cascade="all, delete-orphan", order_by="Assignment.slot_index")


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False)
    slot_index = Column(Integer, nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)
    position = Column(String, nullable=True)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    accepted = Column(Boolean, nullable=False, default=False)

    shift = relationship("Shift", back_populates="assignments")
    operator = relationship("Operator", back_populates="assignments")
    check_in = relationship("CheckIn", back_populates="assignment", uselist=False)


class CheckIn(Base):
    __tablename__ = "check_ins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id = Column(UUID(as_uuid=True), ForeignKey("assignments.id"), nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=False)
    scheduled_start = Column(Time, nullable=False)
    scheduled_end = Column(Time, nullable=False)
    actual_check_in = Column(DateTime, nullable=True)
    actual_check_out = Column(DateTime, nullable=True)
    status = Column(SAEnum(CheckInStatus), nullable=False, default=CheckInStatus.pending)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    assignment = relationship("Assignment", back_populates="check_in")
    operator = relationship("Operator", back_populates="check_ins")


class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False)

    operator_roles = relationship("OperatorRoleAssignment", back_populates="role")


class OperatorRoleAssignment(Base):
    __tablename__ = "operator_roles"
    __table_args__ = (UniqueConstraint("operator_id", "role_id", name="uq_operator_role"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)

    operator = relationship("Operator", back_populates="operator_roles", foreign_keys=[operator_id])
    role = relationship("Role", back_populates="operator_roles")


class SiteAccess(Base):
    __tablename__ = "site_access"
    __table_args__ = (UniqueConstraint("operator_id", "site_id", name="uq_operator_site"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    granted_at = Column(DateTime, default=datetime.utcnow)
    granted_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)

    operator = relationship("Operator", back_populates="site_accesses", foreign_keys=[operator_id])
    site = relationship("Site")


class AvailabilityPeriod(Base):
    __tablename__ = "availability_periods"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    opens_at = Column(DateTime, nullable=False)
    closes_at = Column(DateTime, nullable=False)
    status = Column(SAEnum(AvailabilityStatus), nullable=False, default=AvailabilityStatus.draft)
    created_at = Column(DateTime, default=datetime.utcnow)

    submissions = relationship("AvailabilitySubmission", back_populates="period",
                               cascade="all, delete-orphan")


class AvailabilitySubmission(Base):
    __tablename__ = "availability_submissions"
    __table_args__ = (UniqueConstraint("operator_id", "period_id", name="uq_operator_period"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    period_id = Column(UUID(as_uuid=True), ForeignKey("availability_periods.id", ondelete="CASCADE"), nullable=False)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    operator = relationship("Operator")
    period = relationship("AvailabilityPeriod", back_populates="submissions")
    entries = relationship("AvailabilityEntry", back_populates="submission",
                           cascade="all, delete-orphan")


class SiteShift(Base):
    """Director-editable shift names per site — sites do not all run the same shifts."""
    __tablename__ = "site_shifts"
    __table_args__ = (UniqueConstraint("site_id", "shift_name", name="uq_site_shift_name"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    shift_name = Column(String, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Per-weekday override of how many posts this shift needs, keyed "0" (Monday)
    # through "6" (Sunday) per Python's date.weekday(). Null means every night
    # needs the site's flat slot_count — most sites never touch this.
    weekday_posts = Column(JSON, nullable=True)

    site = relationship("Site")

    def posts_required_on(self, weekday: int, default: int) -> int:
        """`default` is the site's flat slot_count, passed in rather than read
        off self.site so callers already holding the Site avoid a lazy load."""
        if self.weekday_posts:
            override = self.weekday_posts.get(str(weekday))
            if override is not None:
                return int(override)
        return default


class Invoice(Base):
    """One operator's claim for one month. Contractor invoicing only — no
    tax remittance, no payroll, nothing beyond invoice status."""
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("operator_id", "period_month", "period_year",
                                       name="uq_operator_period_invoice"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    period_month = Column(Integer, nullable=False)
    period_year = Column(Integer, nullable=False)
    status = Column(SAEnum(InvoiceStatus), nullable=False, default=InvoiceStatus.draft)

    submitted_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)
    paid_at = Column(DateTime, nullable=True)
    marked_paid_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)

    total_hours = Column(Numeric(10, 2), nullable=False, default=0)
    total_amount = Column(Numeric(10, 2), nullable=False, default=0)
    gst_amount = Column(Numeric(10, 2), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    operator = relationship("Operator", foreign_keys=[operator_id])
    line_items = relationship("InvoiceLineItem", back_populates="invoice",
                              cascade="all, delete-orphan", order_by="InvoiceLineItem.date")


class InvoiceLineItem(Base):
    """One worked shift on an invoice. `rate` is a snapshot of the operator's
    pay_rate at generation time — a later rate change never touches it."""
    __tablename__ = "invoice_line_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    date = Column(Date, nullable=False)
    shift_name = Column(String, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    hours = Column(Numeric(10, 2), nullable=False)
    rate = Column(Numeric(10, 2), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)

    invoice = relationship("Invoice", back_populates="line_items")
    site = relationship("Site")


class Division(Base):
    """A separate line of business alongside site-based operations — e.g.
    Valor Collective, close protection. Sits beside sites, not inside them."""
    __tablename__ = "divisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)


class DivisionOperator(Base):
    """The membership grant. Separate from site_access on purpose — being
    staffed on a site never implies CP qualification, and vice versa."""
    __tablename__ = "division_operators"
    __table_args__ = (UniqueConstraint("operator_id", "division_id", name="uq_operator_division"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    division_id = Column(UUID(as_uuid=True), ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False)
    cp_qualifications = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)

    operator = relationship("Operator", foreign_keys=[operator_id])
    division = relationship("Division")


class Operation(Base):
    """A close-protection engagement. `threat_notes` is Valor Director/Admin only —
    never serialized to an assigned operator regardless of their role."""
    __tablename__ = "operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    division_id = Column(UUID(as_uuid=True), ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False)
    client_name = Column(String, nullable=False)
    operation_name = Column(String, nullable=False)
    status = Column(SAEnum(OperationStatus), nullable=False, default=OperationStatus.planning)
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=True)
    location = Column(Text, nullable=False)
    brief = Column(Text, nullable=False)
    threat_notes = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    division = relationship("Division")
    roles = relationship("OperationRole", back_populates="operation",
                         cascade="all, delete-orphan")
    venues = relationship("OperationVenue", back_populates="operation",
                          cascade="all, delete-orphan", order_by="OperationVenue.sort_order")
    vehicles = relationship("OperationVehicle", back_populates="operation",
                            cascade="all, delete-orphan", order_by="OperationVehicle.sort_order")
    chat_channel = relationship("ChatChannel", back_populates="operation", uselist=False)


class OperationRole(Base):
    __tablename__ = "operation_roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    role_name = Column(String, nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)
    confirmed = Column(Boolean, nullable=False, default=False)

    operation = relationship("Operation", back_populates="roles")
    operator = relationship("Operator")


class OperationVenue(Base):
    """A location tied to an operation — venue, hotel, staging point, whatever
    the detail needs mapped. lat/lng are optional; without them the venue is
    still listed, just not pinned on a map."""
    __tablename__ = "operation_venues"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    address = Column(Text, nullable=True)
    lat = Column(Numeric(9, 6), nullable=True)
    lng = Column(Numeric(9, 6), nullable=True)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    operation = relationship("Operation", back_populates="venues")


class OperationVehicle(Base):
    __tablename__ = "operation_vehicles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    vehicle_type = Column(String, nullable=False)
    plate = Column(String, nullable=True)
    assigned_operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    operation = relationship("Operation", back_populates="vehicles")
    assigned_operator = relationship("Operator")


class EmergencyCode(Base):
    """Division-wide reference, not per-operation — a code means the same
    thing on every engagement. Managed by Valor Director/Admin, readable by
    anyone with division access."""
    __tablename__ = "emergency_codes"
    __table_args__ = (UniqueConstraint("division_id", "code", name="uq_division_emergency_code"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    division_id = Column(UUID(as_uuid=True), ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False)
    code = Column(String, nullable=False)
    meaning = Column(String, nullable=False)
    response = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)

    division = relationship("Division")


class RecordExport(Base):
    """A generated Site Records PDF. Exports are immutable once created —
    the underlying data can drift (an hour corrected, an invoice
    reclassified) so a legal or insurance document must reflect exactly
    what was true when it was generated, never regenerated in place."""
    __tablename__ = "record_exports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    generated_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    sections_included = Column(ARRAY(String), nullable=False)
    file_key = Column(String, nullable=False)
    purpose = Column(Text, nullable=True)
    include_rates = Column(Boolean, nullable=False, default=False)

    site = relationship("Site")
    generated_by_operator = relationship("Operator")


class ClientProfile(Base):
    """Entirely separate from the internal operator profile — no real name,
    licence number, or pay rate ever lives here. The operator fully controls
    publish/unpublish; nothing is client-facing until `visible` is True."""
    __tablename__ = "client_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"),
                         unique=True, nullable=False)
    headline = Column(String, nullable=True)
    bio = Column(Text, nullable=True)
    skills = Column(ARRAY(String), nullable=True)
    years_experience = Column(Integer, nullable=True)
    photo_key = Column(String, nullable=True)
    visible = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    operator = relationship("Operator")


class AvailabilityEntry(Base):
    __tablename__ = "availability_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("availability_submissions.id", ondelete="CASCADE"), nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    shift_name = Column(String, nullable=False)
    available = Column(Boolean, nullable=False, default=False)
    earliest_start = Column(Time, nullable=True)
    latest_end = Column(Time, nullable=True)
    note = Column(String, nullable=True)
    coverage_type = Column(SAEnum(CoverageType), nullable=False, default=CoverageType.full)

    submission = relationship("AvailabilitySubmission", back_populates="entries")
    site = relationship("Site")


class ChatChannel(Base):
    __tablename__ = "chat_channels"
    __table_args__ = (
        UniqueConstraint("dm_operator_a_id", "dm_operator_b_id", name="uq_dm_pair"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    channel_type = Column(SAEnum(ChatChannelType), nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=True)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=True)
    # Only set for channel_type == direct — always stored with a < b (as
    # strings) so a given pair of operators maps to exactly one row,
    # regardless of who started the conversation.
    dm_operator_a_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=True)
    dm_operator_b_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    site = relationship("Site")
    operation = relationship("Operation", back_populates="chat_channel")
    messages = relationship("ChatMessage", back_populates="channel",
                            cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # A file attached to this message — currently only used for invoice
    # uploads into the Directors channel, but kept generic (not "invoice_*")
    # since any future "post a file into chat" feature is the same shape.
    attachment_key = Column(String, nullable=True)
    attachment_filename = Column(String, nullable=True)
    attachment_site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=True)
    attachment_period_month = Column(Integer, nullable=True)
    attachment_period_year = Column(Integer, nullable=True)

    channel = relationship("ChatChannel", back_populates="messages")
    operator = relationship("Operator")
    attachment_site = relationship("Site")


class ChatRead(Base):
    __tablename__ = "chat_reads"
    __table_args__ = (UniqueConstraint("channel_id", "operator_id", name="uq_chat_read"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    last_read_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SiteReportCategory(str, enum.Enum):
    narcan_administration = "narcan_administration"
    incident_report = "incident_report"
    ejection = "ejection"
    eps_call = "eps_call"
    ems_call = "ems_call"


class SiteReport(Base):
    """An operational report filed by whoever is on shift — Narcan use,
    incidents, ejections, and calls to police/EMS. Every category shares the
    same core fields (when/who/what happened); category-specific details
    (dose count, unit numbers, etc.) live in `details` rather than as columns
    per category, since the set of categories is expected to grow."""
    __tablename__ = "site_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    category = Column(SAEnum(SiteReportCategory), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    submitted_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=False)
    narrative = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    site = relationship("Site")
    submitted_by_operator = relationship("Operator", foreign_keys=[submitted_by])


class InviteCode(Base):
    """Signup is impossible without one of these. Codes can pre-assign a role
    and site access so a new hire arrives already configured."""
    __tablename__ = "invite_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String, unique=True, nullable=False, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    max_uses = Column(Integer, nullable=False, default=1)
    use_count = Column(Integer, nullable=False, default=0)
    revoked = Column(Boolean, nullable=False, default=False)
    intended_role = Column(String, nullable=True)
    intended_site_access = Column(ARRAY(String), nullable=True)

    creator = relationship("Operator", foreign_keys=[created_by])

    def usable_at(self, now: datetime) -> bool:
        if self.revoked:
            return False
        if self.expires_at is not None and now > self.expires_at:
            return False
        return self.use_count < self.max_uses
