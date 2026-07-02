"""
Payroll models — pay runs, stubs, tax withholdings, and deductions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import List, Optional


class PayFrequency(Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    SEMIMONTHLY = "semimonthly"
    MONTHLY = "monthly"


class DeductionType(Enum):
    HEALTH_INSURANCE = "health_insurance"
    DENTAL = "dental"
    VISION = "vision"
    RETIREMENT_401K = "retirement_401k"
    HSA = "hsa"
    FSA = "fsa"
    LIFE_INSURANCE = "life_insurance"
    GARNISHMENT = "garnishment"
    UNION_DUES = "union_dues"


@dataclass
class TaxWithholding:
    """Federal and state tax withholding for a pay period."""

    federal_income_tax: float = 0.0
    state_income_tax: float = 0.0
    social_security: float = 0.0
    medicare: float = 0.0
    additional_medicare: float = 0.0
    state_disability: float = 0.0
    local_tax: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.federal_income_tax
            + self.state_income_tax
            + self.social_security
            + self.medicare
            + self.additional_medicare
            + self.state_disability
            + self.local_tax
        )


@dataclass
class Deduction:
    """A single pre-tax or post-tax deduction from a paycheck."""

    deduction_type: DeductionType
    amount: float
    is_pretax: bool = True
    employer_match: float = 0.0


@dataclass
class PayStub:
    """A single employee's pay stub for one pay period."""

    stub_id: str
    employee_id: str
    pay_period_start: date
    pay_period_end: date
    gross_pay: float
    taxes: TaxWithholding = field(default_factory=TaxWithholding)
    deductions: List[Deduction] = field(default_factory=list)
    overtime_hours: float = 0.0
    overtime_pay: float = 0.0
    bonus: float = 0.0
    commission: float = 0.0
    reimbursements: float = 0.0

    @property
    def total_deductions(self) -> float:
        return sum(d.amount for d in self.deductions)

    @property
    def net_pay(self) -> float:
        return self.gross_pay + self.overtime_pay + self.bonus + self.commission + self.reimbursements - self.taxes.total - self.total_deductions


@dataclass
class PayrollRun:
    """A payroll processing run covering all employees for a pay period."""

    run_id: str
    pay_period_start: date
    pay_period_end: date
    pay_date: date
    frequency: PayFrequency = PayFrequency.BIWEEKLY
    status: str = "draft"
    stubs: List[PayStub] = field(default_factory=list)
    total_gross: float = 0.0
    total_net: float = 0.0
    total_taxes: float = 0.0
    total_employer_taxes: float = 0.0
    processed_at: Optional[datetime] = None
    approved_by: Optional[str] = None
