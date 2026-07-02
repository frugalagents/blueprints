"""
Employee domain models — core data structures for the HCM platform.

Represents employees, departments, job positions, and employment history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class EmploymentStatus(Enum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    TERMINATED = "terminated"
    PROBATION = "probation"
    SUSPENDED = "suspended"


class EmploymentType(Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERN = "intern"
    TEMPORARY = "temporary"


@dataclass
class Address:
    street: str
    city: str
    state: str
    zip_code: str
    country: str = "US"


@dataclass
class EmergencyContact:
    name: str
    relationship: str
    phone: str
    email: Optional[str] = None


@dataclass
class Employee:
    """Core employee record in the HCM system."""

    employee_id: str
    first_name: str
    last_name: str
    email: str
    department_id: str
    job_title: str
    hire_date: date
    manager_id: Optional[str] = None
    status: EmploymentStatus = EmploymentStatus.ACTIVE
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    base_salary: float = 0.0
    address: Optional[Address] = None
    emergency_contact: Optional[EmergencyContact] = None
    date_of_birth: Optional[date] = None
    ssn_last_four: Optional[str] = None
    termination_date: Optional[date] = None
    termination_reason: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def tenure_years(self) -> float:
        """Calculate years of service from hire date."""
        end = self.termination_date or date.today()
        delta = end - self.hire_date
        return round(delta.days / 365.25, 1)

    def is_eligible_for_benefits(self) -> bool:
        """Employees must be full-time and past 90-day probation to get benefits."""
        if self.employment_type != EmploymentType.FULL_TIME:
            return False
        if self.status == EmploymentStatus.TERMINATED:
            return False
        return self.tenure_years >= (90 / 365.25)

    def is_eligible_for_promotion(self) -> bool:
        """Must be active, past probation, and have at least 1 year tenure."""
        if self.status != EmploymentStatus.ACTIVE:
            return False
        return self.tenure_years >= 1.0


@dataclass
class Department:
    department_id: str
    name: str
    cost_center: str
    head_employee_id: Optional[str] = None
    parent_department_id: Optional[str] = None
    budget: float = 0.0
    headcount_limit: int = 0


@dataclass
class JobPosition:
    position_id: str
    title: str
    department_id: str
    grade_level: int
    min_salary: float
    max_salary: float
    is_exempt: bool = True
    required_skills: List[str] = field(default_factory=list)
