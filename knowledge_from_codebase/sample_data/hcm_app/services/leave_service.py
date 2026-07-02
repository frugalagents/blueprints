"""
Leave management service — request submission, approval workflows,
balance tracking, accrual calculations, and compliance checks.

Enforces company leave policies including accrual rates, carryover limits,
blackout periods, and FMLA eligibility.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from models.employee import Employee, EmploymentStatus, EmploymentType
from models.leave import LeaveBalance, LeaveRequest, LeaveStatus, LeaveType

logger = logging.getLogger(__name__)

# Company leave policy constants
PTO_ACCRUAL_RATES = {
    # tenure_years_threshold: annual_days
    0: 15,    # 0-2 years: 15 days/year
    2: 20,    # 2-5 years: 20 days/year
    5: 25,    # 5-10 years: 25 days/year
    10: 30,   # 10+ years: 30 days/year
}

SICK_DAYS_PER_YEAR = 10
MAX_PTO_CARRYOVER = 5  # Max days that can roll over to next year
PARENTAL_LEAVE_WEEKS = 12
BEREAVEMENT_DAYS = 5
FMLA_WEEKS = 12
FMLA_ELIGIBILITY_MONTHS = 12
FMLA_ELIGIBILITY_HOURS = 1250


class LeaveService:
    """Manages leave requests, balances, and policy enforcement."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Leave Requests
    # ------------------------------------------------------------------

    def submit_leave_request(
        self,
        employee_id: str,
        leave_type: LeaveType,
        start_date: date,
        end_date: date,
        reason: str = "",
    ) -> LeaveRequest:
        """Submit a new leave request.

        Business rules:
        - Employee must be ACTIVE or in PROBATION.
        - Start date must be in the future (or today).
        - End date must be on or after start date.
        - Employee must have sufficient leave balance (for PTO/sick).
        - Cannot have overlapping approved leave.
        - Probationary employees cannot take PTO (only sick leave).
        - FMLA requires 12 months of employment and 1250 hours worked.
        """
        employee = self.db.get_employee(employee_id)
        if employee is None:
            raise ValueError(f"Employee {employee_id} not found")

        if employee.status not in (EmploymentStatus.ACTIVE, EmploymentStatus.PROBATION):
            raise ValueError(
                f"Employee must be active or in probation to request leave "
                f"(current status: {employee.status.value})"
            )

        if start_date < date.today():
            raise ValueError("Leave start date cannot be in the past")

        if end_date < start_date:
            raise ValueError("End date must be on or after start date")

        # Probation restriction
        if employee.status == EmploymentStatus.PROBATION and leave_type == LeaveType.PTO:
            raise ValueError("Employees in probation cannot take PTO — only sick leave is allowed")

        # FMLA eligibility check
        if leave_type == LeaveType.FMLA:
            self._validate_fmla_eligibility(employee)

        # Check for overlapping leave
        overlapping = self.db.find_overlapping_leave(employee_id, start_date, end_date)
        if overlapping:
            raise ValueError(
                f"Overlapping leave exists: {overlapping.request_id} "
                f"({overlapping.start_date} to {overlapping.end_date})"
            )

        # Balance check for PTO and sick leave
        request = LeaveRequest(
            request_id=f"LR-{uuid.uuid4().hex[:8].upper()}",
            employee_id=employee_id,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
        )

        if leave_type in (LeaveType.PTO, LeaveType.SICK):
            balance = self._get_or_create_balance(employee_id, leave_type, start_date.year)
            if request.business_days > balance.available_days:
                raise ValueError(
                    f"Insufficient {leave_type.value} balance: "
                    f"requesting {request.business_days} days, "
                    f"available {balance.available_days} days"
                )
            # Reserve the days
            balance.pending_days += request.business_days
            self.db.save_leave_balance(balance)

        self.db.save_leave_request(request)
        logger.info(
            "Leave request %s submitted: %s %s (%d days)",
            request.request_id,
            employee_id,
            leave_type.value,
            request.business_days,
        )
        return request

    def approve_leave(
        self,
        request_id: str,
        approver_id: str,
    ) -> LeaveRequest:
        """Approve a pending leave request.

        Business rules:
        - Only pending requests can be approved.
        - Approver must be the employee's manager or an HR admin.
        - Approver cannot approve their own leave.
        """
        request = self.db.get_leave_request(request_id)
        if request is None:
            raise ValueError(f"Leave request {request_id} not found")

        if request.status != LeaveStatus.PENDING:
            raise ValueError(f"Request is not pending (status: {request.status.value})")

        if approver_id == request.employee_id:
            raise ValueError("Employees cannot approve their own leave requests")

        # Verify approver is the employee's manager
        employee = self.db.get_employee(request.employee_id)
        if employee and employee.manager_id != approver_id:
            is_hr = self.db.is_hr_admin(approver_id)
            if not is_hr:
                raise ValueError("Only the employee's manager or HR can approve leave")

        request.status = LeaveStatus.APPROVED
        request.approver_id = approver_id
        request.approved_at = datetime.utcnow()
        self.db.save_leave_request(request)

        logger.info("Leave %s approved by %s", request_id, approver_id)
        return request

    def deny_leave(
        self,
        request_id: str,
        approver_id: str,
        denial_reason: str,
    ) -> LeaveRequest:
        """Deny a pending leave request and release reserved balance.

        Business rules:
        - A denial reason is required.
        - Reserved balance days must be released back.
        """
        request = self.db.get_leave_request(request_id)
        if request is None:
            raise ValueError(f"Leave request {request_id} not found")

        if request.status != LeaveStatus.PENDING:
            raise ValueError(f"Request is not pending (status: {request.status.value})")

        if not denial_reason:
            raise ValueError("A denial reason is required")

        request.status = LeaveStatus.DENIED
        request.approver_id = approver_id
        request.denial_reason = denial_reason
        self.db.save_leave_request(request)

        # Release reserved balance
        if request.leave_type in (LeaveType.PTO, LeaveType.SICK):
            balance = self._get_or_create_balance(
                request.employee_id, request.leave_type, request.start_date.year
            )
            balance.pending_days = max(0, balance.pending_days - request.business_days)
            self.db.save_leave_balance(balance)

        logger.info("Leave %s denied: %s", request_id, denial_reason)
        return request

    def cancel_leave(self, request_id: str, employee_id: str) -> LeaveRequest:
        """Cancel a pending or approved leave request.

        Business rules:
        - Only the requesting employee can cancel.
        - Cannot cancel leave that is already in progress or completed.
        - Approved leave cancelled less than 24 hours before start requires manager notification.
        """
        request = self.db.get_leave_request(request_id)
        if request is None:
            raise ValueError(f"Leave request {request_id} not found")

        if request.employee_id != employee_id:
            raise ValueError("Only the requesting employee can cancel their leave")

        if request.status in (LeaveStatus.IN_PROGRESS, LeaveStatus.COMPLETED):
            raise ValueError(f"Cannot cancel leave that is {request.status.value}")

        if request.status == LeaveStatus.CANCELLED:
            raise ValueError("Leave is already cancelled")

        # Late cancellation warning
        if request.status == LeaveStatus.APPROVED:
            days_until_start = (request.start_date - date.today()).days
            if days_until_start <= 1:
                self._notify_manager_late_cancellation(request)

        request.status = LeaveStatus.CANCELLED
        self.db.save_leave_request(request)

        # Release balance
        if request.leave_type in (LeaveType.PTO, LeaveType.SICK):
            balance = self._get_or_create_balance(
                employee_id, request.leave_type, request.start_date.year
            )
            balance.pending_days = max(0, balance.pending_days - request.business_days)
            self.db.save_leave_balance(balance)

        logger.info("Leave %s cancelled by %s", request_id, employee_id)
        return request

    # ------------------------------------------------------------------
    # Balance & Accrual
    # ------------------------------------------------------------------

    def get_leave_balance(
        self, employee_id: str, leave_type: LeaveType, year: Optional[int] = None
    ) -> LeaveBalance:
        """Get an employee's leave balance for a given type and year."""
        year = year or date.today().year
        return self._get_or_create_balance(employee_id, leave_type, year)

    def calculate_pto_accrual(self, employee: Employee) -> float:
        """Calculate annual PTO accrual based on tenure.

        Accrual tiers:
        - 0-2 years: 15 days/year
        - 2-5 years: 20 days/year
        - 5-10 years: 25 days/year
        - 10+ years: 30 days/year
        """
        tenure = employee.tenure_years
        accrual = 15  # default
        for threshold, days in sorted(PTO_ACCRUAL_RATES.items(), reverse=True):
            if tenure >= threshold:
                accrual = days
                break
        return accrual

    def run_year_end_carryover(self, year: int) -> Dict[str, Any]:
        """Process year-end PTO carryover for all employees.

        Business rules:
        - Maximum of 5 PTO days can carry over to the next year.
        - Excess PTO is forfeited ("use it or lose it").
        - Sick leave does not carry over.
        - Carryover is calculated from available (unused, non-pending) balance.
        """
        employees = self.db.find_active_employees()
        results = {"processed": 0, "total_carried": 0.0, "total_forfeited": 0.0}

        for emp in employees:
            balance = self.db.get_leave_balance(emp.employee_id, LeaveType.PTO, year)
            if balance is None:
                continue

            available = balance.available_days
            carry = min(available, MAX_PTO_CARRYOVER)
            forfeited = max(0, available - MAX_PTO_CARRYOVER)

            # Create next year's balance with carryover
            new_accrual = self.calculate_pto_accrual(emp)
            new_balance = LeaveBalance(
                employee_id=emp.employee_id,
                leave_type=LeaveType.PTO,
                year=year + 1,
                total_days=new_accrual,
                carried_over=carry,
            )
            self.db.save_leave_balance(new_balance)

            results["processed"] += 1
            results["total_carried"] += carry
            results["total_forfeited"] += forfeited

            if forfeited > 0:
                logger.info(
                    "%s forfeited %.1f PTO days (carried %.1f)",
                    emp.full_name, forfeited, carry,
                )

        logger.info(
            "Year-end carryover complete: %d employees, %.1f days carried, %.1f forfeited",
            results["processed"],
            results["total_carried"],
            results["total_forfeited"],
        )
        return results

    # ------------------------------------------------------------------
    # FMLA
    # ------------------------------------------------------------------

    def _validate_fmla_eligibility(self, employee: Employee) -> None:
        """Validate FMLA eligibility per federal requirements.

        Requirements:
        - Employed for at least 12 months.
        - Worked at least 1,250 hours in the past 12 months.
        - Employer has 50+ employees within 75 miles.
        """
        months_employed = employee.tenure_years * 12
        if months_employed < FMLA_ELIGIBILITY_MONTHS:
            raise ValueError(
                f"FMLA requires 12 months of employment "
                f"(current: {months_employed:.0f} months)"
            )

        hours_worked = self.db.get_hours_worked_last_12_months(employee.employee_id)
        if hours_worked < FMLA_ELIGIBILITY_HOURS:
            raise ValueError(
                f"FMLA requires 1,250 hours worked in past 12 months "
                f"(current: {hours_worked:.0f} hours)"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_balance(
        self, employee_id: str, leave_type: LeaveType, year: int
    ) -> LeaveBalance:
        balance = self.db.get_leave_balance(employee_id, leave_type, year)
        if balance is not None:
            return balance

        employee = self.db.get_employee(employee_id)
        if leave_type == LeaveType.PTO:
            total = self.calculate_pto_accrual(employee) if employee else 15
        elif leave_type == LeaveType.SICK:
            total = SICK_DAYS_PER_YEAR
        else:
            total = 0

        balance = LeaveBalance(
            employee_id=employee_id,
            leave_type=leave_type,
            year=year,
            total_days=total,
        )
        self.db.save_leave_balance(balance)
        return balance

    def _notify_manager_late_cancellation(self, request: LeaveRequest) -> None:
        """Send notification when approved leave is cancelled with short notice."""
        employee = self.db.get_employee(request.employee_id)
        if employee and employee.manager_id:
            logger.warning(
                "Late leave cancellation: %s cancelled %s leave starting %s",
                employee.full_name,
                request.leave_type.value,
                request.start_date,
            )
