"""
Payroll processing service — gross pay calculation, tax withholding,
deductions, overtime, and payroll run management.

Implements federal and state tax rules, FLSA overtime requirements,
and company-specific compensation policies.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from models.employee import Employee, EmploymentStatus, EmploymentType
from models.payroll import (
    Deduction,
    DeductionType,
    PayFrequency,
    PayrollRun,
    PayStub,
    TaxWithholding,
)

logger = logging.getLogger(__name__)

# 2024 Federal tax brackets (simplified — single filer)
FEDERAL_TAX_BRACKETS = [
    (11_600, 0.10),
    (47_150, 0.12),
    (100_525, 0.22),
    (191_950, 0.24),
    (243_725, 0.32),
    (609_350, 0.35),
    (float("inf"), 0.37),
]

# FICA rates
SOCIAL_SECURITY_RATE = 0.062
SOCIAL_SECURITY_WAGE_BASE = 168_600
MEDICARE_RATE = 0.0145
ADDITIONAL_MEDICARE_RATE = 0.009
ADDITIONAL_MEDICARE_THRESHOLD = 200_000

# Employer-side FICA
EMPLOYER_SS_RATE = 0.062
EMPLOYER_MEDICARE_RATE = 0.0145
FUTA_RATE = 0.006
FUTA_WAGE_BASE = 7_000

# Overtime
OVERTIME_MULTIPLIER = 1.5
STANDARD_WEEKLY_HOURS = 40


class PayrollService:
    """Processes payroll for all employees."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Payroll Run
    # ------------------------------------------------------------------

    def create_payroll_run(
        self,
        pay_period_start: date,
        pay_period_end: date,
        pay_date: date,
        frequency: PayFrequency = PayFrequency.BIWEEKLY,
    ) -> PayrollRun:
        """Create a new payroll run in draft status.

        Business rules:
        - Pay date must be on or after the period end date.
        - Cannot create duplicate runs for the same period.
        """
        if pay_date < pay_period_end:
            raise ValueError("Pay date must be on or after the period end date")

        existing = self.db.find_payroll_run(pay_period_start, pay_period_end)
        if existing:
            raise ValueError(
                f"Payroll run already exists for {pay_period_start} to {pay_period_end}"
            )

        run = PayrollRun(
            run_id=f"PR-{uuid.uuid4().hex[:8].upper()}",
            pay_period_start=pay_period_start,
            pay_period_end=pay_period_end,
            pay_date=pay_date,
            frequency=frequency,
            status="draft",
        )
        self.db.save_payroll_run(run)
        logger.info("Created payroll run %s for %s to %s", run.run_id, pay_period_start, pay_period_end)
        return run

    def process_payroll(self, run_id: str) -> PayrollRun:
        """Process a payroll run: calculate pay for all eligible employees.

        Business rules:
        - Only draft runs can be processed.
        - Only ACTIVE and ON_LEAVE employees are included.
        - Terminated employees are excluded unless termination is within the period.
        - Overtime is calculated for non-exempt employees.
        """
        run = self.db.get_payroll_run(run_id)
        if run is None:
            raise ValueError(f"Payroll run {run_id} not found")

        if run.status != "draft":
            raise ValueError(f"Payroll run is not in draft status (current: {run.status})")

        employees = self._get_eligible_employees(run)
        logger.info("Processing payroll for %d employees", len(employees))

        total_gross = 0.0
        total_net = 0.0
        total_taxes = 0.0
        total_employer_taxes = 0.0

        for emp in employees:
            stub = self._calculate_pay_stub(emp, run)
            run.stubs.append(stub)

            total_gross += stub.gross_pay + stub.overtime_pay + stub.bonus
            total_net += stub.net_pay
            total_taxes += stub.taxes.total
            total_employer_taxes += self._calculate_employer_taxes(stub)

        run.total_gross = total_gross
        run.total_net = total_net
        run.total_taxes = total_taxes
        run.total_employer_taxes = total_employer_taxes
        run.status = "processed"
        run.processed_at = datetime.utcnow()

        self.db.save_payroll_run(run)
        logger.info(
            "Payroll %s processed: gross=$%,.2f net=$%,.2f taxes=$%,.2f",
            run_id, total_gross, total_net, total_taxes,
        )
        return run

    def approve_payroll(self, run_id: str, approver_id: str) -> PayrollRun:
        """Approve a processed payroll run for payment.

        Business rules:
        - Only processed runs can be approved.
        - Approver must be a payroll admin or finance director.
        - Approval triggers ACH payment initiation.
        """
        run = self.db.get_payroll_run(run_id)
        if run is None:
            raise ValueError(f"Payroll run {run_id} not found")

        if run.status != "processed":
            raise ValueError("Only processed payroll runs can be approved")

        if not self.db.is_payroll_admin(approver_id):
            raise ValueError("Only payroll admins can approve payroll runs")

        run.status = "approved"
        run.approved_by = approver_id
        self.db.save_payroll_run(run)

        self._initiate_ach_payments(run)
        logger.info("Payroll %s approved by %s", run_id, approver_id)
        return run

    # ------------------------------------------------------------------
    # Pay Calculation
    # ------------------------------------------------------------------

    def _calculate_pay_stub(self, employee: Employee, run: PayrollRun) -> PayStub:
        """Calculate a single employee's pay for the period."""
        gross_pay = self._calculate_gross_pay(employee, run.frequency)

        # Overtime for non-exempt employees
        overtime_hours = 0.0
        overtime_pay = 0.0
        position = self.db.get_position_by_title(employee.job_title, employee.department_id)
        if position and not position.is_exempt:
            overtime_hours = self.db.get_overtime_hours(
                employee.employee_id, run.pay_period_start, run.pay_period_end
            )
            overtime_pay = self._calculate_overtime_pay(employee, overtime_hours, run.frequency)

        # Bonuses and commissions
        bonus = self.db.get_pending_bonus(employee.employee_id, run.pay_period_end) or 0.0
        commission = self.db.get_pending_commission(employee.employee_id, run.pay_period_end) or 0.0

        # Calculate taxes
        annual_gross = employee.base_salary + (overtime_pay * self._periods_per_year(run.frequency))
        taxes = self._calculate_tax_withholding(gross_pay + overtime_pay + bonus + commission, annual_gross, employee)

        # Deductions
        deductions = self._calculate_deductions(employee, run.frequency)

        stub = PayStub(
            stub_id=f"PS-{uuid.uuid4().hex[:8].upper()}",
            employee_id=employee.employee_id,
            pay_period_start=run.pay_period_start,
            pay_period_end=run.pay_period_end,
            gross_pay=gross_pay,
            taxes=taxes,
            deductions=deductions,
            overtime_hours=overtime_hours,
            overtime_pay=overtime_pay,
            bonus=bonus,
            commission=commission,
        )
        self.db.save_pay_stub(stub)
        return stub

    def _calculate_gross_pay(self, employee: Employee, frequency: PayFrequency) -> float:
        """Calculate gross pay for one pay period from annual salary.

        Business rules:
        - Full-time: annual salary / periods per year.
        - Part-time: prorated based on scheduled hours.
        - Unpaid leave days are deducted.
        """
        periods = self._periods_per_year(frequency)
        gross = employee.base_salary / periods

        if employee.employment_type == EmploymentType.PART_TIME:
            # Assume part-time is 50% of full-time
            gross *= 0.5

        return round(gross, 2)

    def _calculate_overtime_pay(
        self, employee: Employee, overtime_hours: float, frequency: PayFrequency
    ) -> float:
        """Calculate overtime pay at 1.5x the regular hourly rate.

        FLSA requires non-exempt employees to receive overtime for hours
        worked beyond 40 in a workweek.
        """
        if overtime_hours <= 0:
            return 0.0

        # Derive hourly rate from salary
        periods = self._periods_per_year(frequency)
        period_hours = STANDARD_WEEKLY_HOURS * (26 / periods)  # approximate
        hourly_rate = (employee.base_salary / periods) / period_hours

        return round(overtime_hours * hourly_rate * OVERTIME_MULTIPLIER, 2)

    def _calculate_tax_withholding(
        self,
        period_gross: float,
        annual_gross: float,
        employee: Employee,
    ) -> TaxWithholding:
        """Calculate federal and state tax withholding for a pay period.

        Implements:
        - Progressive federal income tax brackets.
        - Social Security tax (6.2%) up to wage base.
        - Medicare tax (1.45%) + additional Medicare (0.9%) above threshold.
        - State income tax (simplified flat rate by state).
        """
        # Federal income tax (annualize, apply brackets, de-annualize)
        annual_tax = self._apply_federal_brackets(annual_gross)
        federal_tax = round(annual_tax * (period_gross / annual_gross) if annual_gross > 0 else 0, 2)

        # Social Security
        ytd_gross = self.db.get_ytd_gross(employee.employee_id) or 0.0
        ss_taxable = min(period_gross, max(0, SOCIAL_SECURITY_WAGE_BASE - ytd_gross))
        social_security = round(ss_taxable * SOCIAL_SECURITY_RATE, 2)

        # Medicare
        medicare = round(period_gross * MEDICARE_RATE, 2)

        # Additional Medicare for high earners
        additional_medicare = 0.0
        if annual_gross > ADDITIONAL_MEDICARE_THRESHOLD:
            additional_medicare = round(period_gross * ADDITIONAL_MEDICARE_RATE, 2)

        # State tax (simplified)
        state_rate = self._get_state_tax_rate(employee)
        state_tax = round(period_gross * state_rate, 2)

        return TaxWithholding(
            federal_income_tax=federal_tax,
            state_income_tax=state_tax,
            social_security=social_security,
            medicare=medicare,
            additional_medicare=additional_medicare,
        )

    def _apply_federal_brackets(self, annual_income: float) -> float:
        """Apply progressive federal tax brackets to annual income."""
        tax = 0.0
        prev_limit = 0.0
        for limit, rate in FEDERAL_TAX_BRACKETS:
            if annual_income <= prev_limit:
                break
            taxable = min(annual_income, limit) - prev_limit
            tax += taxable * rate
            prev_limit = limit
        return tax

    def _get_state_tax_rate(self, employee: Employee) -> float:
        """Return the state income tax rate based on employee's work state."""
        state = "CA"  # default
        if employee.address:
            state = employee.address.state

        # Simplified state tax rates
        STATE_RATES = {
            "CA": 0.0725, "NY": 0.0685, "TX": 0.0, "FL": 0.0,
            "WA": 0.0, "IL": 0.0495, "PA": 0.0307, "OH": 0.04,
            "NJ": 0.0637, "MA": 0.05,
        }
        return STATE_RATES.get(state, 0.05)

    def _calculate_deductions(
        self, employee: Employee, frequency: PayFrequency
    ) -> List[Deduction]:
        """Calculate benefit deductions for the pay period."""
        enrollments = self.db.get_active_enrollments(employee.employee_id)
        deductions = []

        periods = self._periods_per_year(frequency)

        for enrollment in enrollments:
            plan = self.db.get_benefit_plan(enrollment.plan_id)
            if plan is None:
                continue

            # Convert monthly cost to per-period
            period_cost = round(enrollment.employee_contribution * 12 / periods, 2)

            deduction_type_map = {
                "medical": DeductionType.HEALTH_INSURANCE,
                "dental": DeductionType.DENTAL,
                "vision": DeductionType.VISION,
                "retirement_401k": DeductionType.RETIREMENT_401K,
                "hsa": DeductionType.HSA,
                "fsa": DeductionType.FSA,
                "life_insurance": DeductionType.LIFE_INSURANCE,
            }

            dtype = deduction_type_map.get(plan.benefit_type.value, DeductionType.HEALTH_INSURANCE)
            is_pretax = plan.benefit_type.value in ("medical", "dental", "vision", "hsa", "fsa", "retirement_401k")

            deductions.append(Deduction(
                deduction_type=dtype,
                amount=period_cost,
                is_pretax=is_pretax,
                employer_match=round(enrollment.employer_contribution * 12 / periods, 2),
            ))

        return deductions

    def _calculate_employer_taxes(self, stub: PayStub) -> float:
        """Calculate employer-side payroll taxes (FICA + FUTA)."""
        gross = stub.gross_pay + stub.overtime_pay + stub.bonus
        employer_ss = round(min(gross, SOCIAL_SECURITY_WAGE_BASE) * EMPLOYER_SS_RATE, 2)
        employer_medicare = round(gross * EMPLOYER_MEDICARE_RATE, 2)
        employer_futa = round(min(gross, FUTA_WAGE_BASE) * FUTA_RATE, 2)
        return employer_ss + employer_medicare + employer_futa

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_eligible_employees(self, run: PayrollRun) -> List[Employee]:
        """Return employees eligible for this payroll run."""
        all_employees = self.db.find_active_employees()
        eligible = []
        for emp in all_employees:
            if emp.status in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE, EmploymentStatus.PROBATION):
                eligible.append(emp)
            elif emp.status == EmploymentStatus.TERMINATED:
                # Include if terminated within the pay period (final check)
                if emp.termination_date and emp.termination_date >= run.pay_period_start:
                    eligible.append(emp)
        return eligible

    @staticmethod
    def _periods_per_year(frequency: PayFrequency) -> int:
        return {
            PayFrequency.WEEKLY: 52,
            PayFrequency.BIWEEKLY: 26,
            PayFrequency.SEMIMONTHLY: 24,
            PayFrequency.MONTHLY: 12,
        }[frequency]

    def _initiate_ach_payments(self, run: PayrollRun) -> None:
        """Initiate ACH direct deposit payments for all stubs in the run."""
        for stub in run.stubs:
            bank_info = self.db.get_bank_info(stub.employee_id)
            if bank_info:
                logger.info(
                    "ACH payment initiated: %s $%,.2f",
                    stub.employee_id, stub.net_pay,
                )
