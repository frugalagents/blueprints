"""
Leave management models — types, requests, balances, and accrual tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class LeaveType(Enum):
    PTO = "pto"
    SICK = "sick"
    PARENTAL = "parental"
    BEREAVEMENT = "bereavement"
    JURY_DUTY = "jury_duty"
    MILITARY = "military"
    FMLA = "fmla"
    UNPAID = "unpaid"


class LeaveStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class LeaveRequest:
    """A single leave request submitted by an employee."""

    request_id: str
    employee_id: str
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: str = ""
    status: LeaveStatus = LeaveStatus.PENDING
    approver_id: Optional[str] = None
    approved_at: Optional[datetime] = None
    denial_reason: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def business_days(self) -> int:
        """Calculate the number of business days in the leave period."""
        count = 0
        current = self.start_date
        from datetime import timedelta
        while current <= self.end_date:
            if current.weekday() < 5:  # Monday=0 through Friday=4
                count += 1
            current += timedelta(days=1)
        return count


@dataclass
class LeaveBalance:
    """Tracks an employee's leave balance for a specific leave type and year."""

    employee_id: str
    leave_type: LeaveType
    year: int
    total_days: float
    used_days: float = 0.0
    pending_days: float = 0.0
    carried_over: float = 0.0

    @property
    def available_days(self) -> float:
        return self.total_days + self.carried_over - self.used_days - self.pending_days
