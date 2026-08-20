import uuid
import enum
from datetime import datetime
from sqlalchemy import (Boolean, Column, Date, DateTime, Enum as SAEnum,
                        ForeignKey, Integer, String, Text, Time, UniqueConstraint)
from sqlalchemy.dialects.postgresql import UUID
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


class ChatChannelType(str, enum.Enum):
    site = "site"
    site_leads = "site_leads"
    directors = "directors"
    admin = "admin"


class Operator(Base):
    __tablename__ = "operators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String, nullable=False)
    phone_number = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=True)
    setup_code = Column(String, nullable=True)
    discord_id = Column(String, nullable=True)
    role = Column(SAEnum(OperatorRole), nullable=False, default=OperatorRole.operator)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

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

    shifts = relationship("Shift", back_populates="site")


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

    site = relationship("Site")


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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    channel_type = Column(SAEnum(ChatChannelType), nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    site = relationship("Site")
    messages = relationship("ChatMessage", back_populates="channel",
                            cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    channel = relationship("ChatChannel", back_populates="messages")
    operator = relationship("Operator")


class ChatRead(Base):
    __tablename__ = "chat_reads"
    __table_args__ = (UniqueConstraint("channel_id", "operator_id", name="uq_chat_read"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    last_read_at = Column(DateTime, default=datetime.utcnow, nullable=False)
