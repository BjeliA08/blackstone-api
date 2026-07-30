import uuid
import enum
from datetime import datetime
from sqlalchemy import (Boolean, Column, Date, DateTime, Enum as SAEnum,
                        ForeignKey, Integer, String, Text, Time)
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
