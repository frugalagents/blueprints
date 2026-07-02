"""
Benefits models — plans, enrollments, and eligibility tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import List, Optional


class BenefitType(Enum):
    MEDICAL = "medical"
    DENTAL = "dental"
    VISION = "vision"
    LIFE_INSURANCE = "life_insurance"
    DISABILITY_SHORT = "disability_short"
    DISABILITY_LONG = "disability_long"
    RETIREMENT_401K = "retirement_401k"
    HSA = "hsa"
    FSA = "fsa"
    TUITION_REIMBURSEMENT = "tuition_reimbursement"


class EnrollmentStatus(Enum):
    ACTIVE = "active"
    PENDING = "pending"
    WAIVED = "waived"
    TERMINATED = "terminated"
    COBRA = "cobra"


@dataclass
class BenefitPlan:
    """A benefit plan offered by the company."""

    plan_id: str
    name: str
    benefit_type: BenefitType
    plan_year: int
    employee_cost_monthly: float
    employer_cost_monthly: float
    coverage_details: str = ""
    is_active: bool = True
    requires_enrollment: bool = True
    dependents_allowed: bool = True
    max_dependents: int = 10


@dataclass
class BenefitEnrollment:
    """An employee's enrollment in a specific benefit plan."""

    enrollment_id: str
    employee_id: str
    plan_id: str
    status: EnrollmentStatus = EnrollmentStatus.PENDING
    coverage_level: str = "employee_only"
    effective_date: Optional[date] = None
    termination_date: Optional[date] = None
    dependent_count: int = 0
    employee_contribution: float = 0.0
    employer_contribution: float = 0.0
    enrolled_at: datetime = field(default_factory=datetime.utcnow)
