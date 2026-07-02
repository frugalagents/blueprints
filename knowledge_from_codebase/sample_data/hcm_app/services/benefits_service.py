"""
Benefits administration service — enrollment, eligibility, open enrollment
windows, life event changes, and COBRA continuation.

Enforces eligibility rules, enrollment windows, and contribution limits
per IRS regulations and company policy.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from models.employee import Employee, EmploymentStatus, EmploymentType
from models.benefits import (
    BenefitEnrollment,
    BenefitPlan,
    BenefitType,
    EnrollmentStatus,
)

logger = logging.getLogger(__name__)

# IRS contribution limits (2024)
HSA_INDIVIDUAL_LIMIT = 4_150
HSA_FAMILY_LIMIT = 8_300
FSA_LIMIT = 3_200
RETIREMENT_401K_LIMIT = 23_000
RETIREMENT_401K_CATCHUP = 7_500  # age 50+
RETIREMENT_401K_CATCHUP_AGE = 50

# Company policy
EMPLOYER_401K_MATCH_PCT = 0.50  # 50% match
EMPLOYER_401K_MATCH_CAP = 0.06  # up to 6% of salary
NEW_HIRE_ENROLLMENT_WINDOW_DAYS = 30
OPEN_ENROLLMENT_MONTH = 11  # November


class BenefitsService:
    """Manages benefit plan enrollment and administration."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    def enroll_employee(
        self,
        employee_id: str,
        plan_id: str,
        coverage_level: str = "employee_only",
        dependent_count: int = 0,
        annual_contribution: Optional[float] = None,
    ) -> BenefitEnrollment:
        """Enroll an employee in a benefit plan.

        Business rules:
        - Employee must be eligible for benefits (full-time, past probation).
        - Must be within an enrollment window (new hire, open enrollment, or life event).
        - Cannot enroll in the same plan type twice.
        - Contribution amounts must not exceed IRS limits.
        - Dependent count must not exceed plan maximum.
        """
        employee = self.db.get_employee(employee_id)
        if employee is None:
            raise ValueError(f"Employee {employee_id} not found")

        if not employee.is_eligible_for_benefits():
            raise ValueError(
                f"Employee is not eligible for benefits "
                f"(type={employee.employment_type.value}, status={employee.status.value})"
            )

        plan = self.db.get_benefit_plan(plan_id)
        if plan is None:
            raise ValueError(f"Benefit plan {plan_id} not found")

        if not plan.is_active:
            raise ValueError(f"Plan '{plan.name}' is not currently active")

        # Check enrollment window
        if not self._is_in_enrollment_window(employee):
            raise ValueError(
                "Not within an enrollment window. Enrollment is allowed during: "
                "new hire window (30 days), open enrollment (November), or qualifying life event."
            )

        # Check for duplicate enrollment in same benefit type
        existing = self.db.get_active_enrollment_by_type(employee_id, plan.benefit_type)
        if existing:
            raise ValueError(
                f"Already enrolled in a {plan.benefit_type.value} plan: {existing.plan_id}"
            )

        # Validate dependent count
        if dependent_count > 0 and not plan.dependents_allowed:
            raise ValueError(f"Plan '{plan.name}' does not allow dependents")
        if dependent_count > plan.max_dependents:
            raise ValueError(
                f"Dependent count {dependent_count} exceeds plan maximum of {plan.max_dependents}"
            )

        # Calculate contributions
        employee_contribution = plan.employee_cost_monthly
        employer_contribution = plan.employer_cost_monthly

        # For 401k, calculate based on salary percentage
        if plan.benefit_type == BenefitType.RETIREMENT_401K and annual_contribution is not None:
            self._validate_401k_contribution(employee, annual_contribution)
            employee_contribution = annual_contribution / 12
            employer_contribution = self._calculate_401k_match(employee, annual_contribution) / 12

        # For HSA/FSA, validate against IRS limits
        if plan.benefit_type == BenefitType.HSA and annual_contribution is not None:
            limit = HSA_FAMILY_LIMIT if coverage_level != "employee_only" else HSA_INDIVIDUAL_LIMIT
            if annual_contribution > limit:
                raise ValueError(
                    f"HSA contribution ${annual_contribution:,.2f} exceeds "
                    f"IRS limit of ${limit:,.2f}"
                )
            employee_contribution = annual_contribution / 12

        if plan.benefit_type == BenefitType.FSA and annual_contribution is not None:
            if annual_contribution > FSA_LIMIT:
                raise ValueError(
                    f"FSA contribution ${annual_contribution:,.2f} exceeds "
                    f"IRS limit of ${FSA_LIMIT:,.2f}"
                )
            employee_contribution = annual_contribution / 12

        enrollment = BenefitEnrollment(
            enrollment_id=f"BE-{uuid.uuid4().hex[:8].upper()}",
            employee_id=employee_id,
            plan_id=plan_id,
            status=EnrollmentStatus.ACTIVE,
            coverage_level=coverage_level,
            effective_date=self._calculate_effective_date(employee),
            dependent_count=dependent_count,
            employee_contribution=employee_contribution,
            employer_contribution=employer_contribution,
        )

        self.db.save_enrollment(enrollment)
        logger.info(
            "Enrolled %s in %s (coverage=%s, employee=$%,.2f/mo)",
            employee_id, plan.name, coverage_level, employee_contribution,
        )
        return enrollment

    def waive_benefit(self, employee_id: str, benefit_type: BenefitType) -> None:
        """Waive a benefit type during enrollment window.

        Business rules:
        - Medical coverage waiver requires proof of alternative coverage.
        - Waiver is recorded for compliance tracking.
        """
        if benefit_type == BenefitType.MEDICAL:
            has_alt = self.db.has_alternative_coverage(employee_id)
            if not has_alt:
                raise ValueError(
                    "Medical coverage waiver requires proof of alternative coverage"
                )

        existing = self.db.get_active_enrollment_by_type(employee_id, benefit_type)
        if existing:
            existing.status = EnrollmentStatus.WAIVED
            self.db.save_enrollment(existing)

        logger.info("Employee %s waived %s coverage", employee_id, benefit_type.value)

    def terminate_benefits(self, employee_id: str, termination_date: date) -> List[BenefitEnrollment]:
        """Terminate all active benefit enrollments upon employee separation.

        Business rules:
        - Benefits continue through the end of the termination month.
        - COBRA eligibility is triggered for medical, dental, and vision.
        - 401k is rolled over or distributed per employee election.
        """
        enrollments = self.db.get_active_enrollments(employee_id)
        cobra_eligible = []

        # Benefits end at end of termination month
        benefit_end = date(
            termination_date.year,
            termination_date.month + 1 if termination_date.month < 12 else 1,
            1,
        ) - timedelta(days=1)

        for enrollment in enrollments:
            enrollment.status = EnrollmentStatus.TERMINATED
            enrollment.termination_date = benefit_end
            self.db.save_enrollment(enrollment)

            plan = self.db.get_benefit_plan(enrollment.plan_id)
            if plan and plan.benefit_type in (BenefitType.MEDICAL, BenefitType.DENTAL, BenefitType.VISION):
                cobra_eligible.append(enrollment)

        if cobra_eligible:
            self._initiate_cobra(employee_id, cobra_eligible, benefit_end)

        logger.info(
            "Terminated %d benefits for %s (COBRA eligible: %d)",
            len(enrollments), employee_id, len(cobra_eligible),
        )
        return enrollments

    # ------------------------------------------------------------------
    # 401k
    # ------------------------------------------------------------------

    def _validate_401k_contribution(self, employee: Employee, annual_amount: float) -> None:
        """Validate 401k contribution against IRS limits.

        Employees age 50+ get an additional catch-up contribution allowance.
        """
        limit = RETIREMENT_401K_LIMIT
        if employee.date_of_birth:
            age = (date.today() - employee.date_of_birth).days / 365.25
            if age >= RETIREMENT_401K_CATCHUP_AGE:
                limit += RETIREMENT_401K_CATCHUP

        if annual_amount > limit:
            raise ValueError(
                f"401k contribution ${annual_amount:,.2f} exceeds "
                f"IRS limit of ${limit:,.2f}"
            )

        if annual_amount > employee.base_salary:
            raise ValueError("401k contribution cannot exceed annual salary")

    def _calculate_401k_match(self, employee: Employee, annual_contribution: float) -> float:
        """Calculate employer 401k match.

        Company matches 50% of employee contributions up to 6% of salary.
        """
        max_matchable = employee.base_salary * EMPLOYER_401K_MATCH_CAP
        matchable_amount = min(annual_contribution, max_matchable)
        return round(matchable_amount * EMPLOYER_401K_MATCH_PCT, 2)

    # ------------------------------------------------------------------
    # Enrollment Windows
    # ------------------------------------------------------------------

    def _is_in_enrollment_window(self, employee: Employee) -> bool:
        """Check if the employee is within a valid enrollment window."""
        today = date.today()

        # New hire window: 30 days from hire date
        if (today - employee.hire_date).days <= NEW_HIRE_ENROLLMENT_WINDOW_DAYS:
            return True

        # Open enrollment: November
        if today.month == OPEN_ENROLLMENT_MONTH:
            return True

        # Qualifying life event (marriage, birth, etc.)
        has_life_event = self.db.has_qualifying_life_event(employee.employee_id)
        if has_life_event:
            return True

        return False

    def _calculate_effective_date(self, employee: Employee) -> date:
        """Determine when coverage becomes effective.

        New hires: first of the month following 30 days from hire.
        Open enrollment: January 1 of the next year.
        Life event: first of the month following the event.
        """
        today = date.today()

        # New hire
        if (today - employee.hire_date).days <= NEW_HIRE_ENROLLMENT_WINDOW_DAYS:
            target = employee.hire_date + timedelta(days=30)
            return date(target.year, target.month + 1 if target.month < 12 else 1, 1)

        # Open enrollment
        if today.month == OPEN_ENROLLMENT_MONTH:
            return date(today.year + 1, 1, 1)

        # Life event — first of next month
        if today.month < 12:
            return date(today.year, today.month + 1, 1)
        return date(today.year + 1, 1, 1)

    # ------------------------------------------------------------------
    # COBRA
    # ------------------------------------------------------------------

    def _initiate_cobra(
        self,
        employee_id: str,
        enrollments: List[BenefitEnrollment],
        benefit_end: date,
    ) -> None:
        """Initiate COBRA continuation coverage process.

        COBRA allows terminated employees to continue group health coverage
        for up to 18 months at full cost (employee + employer share) plus
        a 2% administrative fee.
        """
        for enrollment in enrollments:
            plan = self.db.get_benefit_plan(enrollment.plan_id)
            if plan is None:
                continue

            cobra_monthly_cost = (
                enrollment.employee_contribution + enrollment.employer_contribution
            ) * 1.02  # 2% admin fee

            cobra_enrollment = BenefitEnrollment(
                enrollment_id=f"COBRA-{uuid.uuid4().hex[:8].upper()}",
                employee_id=employee_id,
                plan_id=enrollment.plan_id,
                status=EnrollmentStatus.COBRA,
                coverage_level=enrollment.coverage_level,
                effective_date=benefit_end + timedelta(days=1),
                dependent_count=enrollment.dependent_count,
                employee_contribution=cobra_monthly_cost,
                employer_contribution=0.0,
            )
            self.db.save_enrollment(cobra_enrollment)

        logger.info(
            "COBRA initiated for %s: %d plans eligible",
            employee_id, len(enrollments),
        )
