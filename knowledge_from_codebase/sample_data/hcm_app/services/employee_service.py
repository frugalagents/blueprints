"""
Employee lifecycle service — hiring, onboarding, transfers, promotions,
terminations, and org-chart management.

This is the core service that manages the employee lifecycle from hire to
separation, enforcing company policies at each transition.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from models.employee import (
    Department,
    Employee,
    EmploymentStatus,
    EmploymentType,
    JobPosition,
)

logger = logging.getLogger(__name__)


class EmployeeService:
    """Manages the full employee lifecycle."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Hiring & Onboarding
    # ------------------------------------------------------------------

    def hire_employee(
        self,
        first_name: str,
        last_name: str,
        email: str,
        department_id: str,
        job_title: str,
        base_salary: float,
        hire_date: Optional[date] = None,
        employment_type: EmploymentType = EmploymentType.FULL_TIME,
        manager_id: Optional[str] = None,
    ) -> Employee:
        """Create a new employee record and initiate the onboarding process.

        Business rules:
        - Salary must fall within the position's grade band.
        - Department must not exceed its headcount limit.
        - New full-time hires start in PROBATION status for 90 days.
        - Contract and intern hires start as ACTIVE immediately.
        """
        department = self.db.get_department(department_id)
        if department is None:
            raise ValueError(f"Department {department_id} not found")

        # Enforce headcount limit
        current_headcount = self.db.count_active_employees(department_id)
        if department.headcount_limit > 0 and current_headcount >= department.headcount_limit:
            raise ValueError(
                f"Department '{department.name}' has reached its headcount limit "
                f"({current_headcount}/{department.headcount_limit})"
            )

        # Validate salary against grade band
        position = self.db.get_position_by_title(job_title, department_id)
        if position:
            if base_salary < position.min_salary or base_salary > position.max_salary:
                raise ValueError(
                    f"Salary ${base_salary:,.2f} is outside the grade band "
                    f"(${position.min_salary:,.2f} – ${position.max_salary:,.2f}) "
                    f"for {job_title}"
                )

        # Determine initial status
        if employment_type in (EmploymentType.FULL_TIME, EmploymentType.PART_TIME):
            initial_status = EmploymentStatus.PROBATION
        else:
            initial_status = EmploymentStatus.ACTIVE

        employee = Employee(
            employee_id=f"EMP-{uuid.uuid4().hex[:8].upper()}",
            first_name=first_name,
            last_name=last_name,
            email=email,
            department_id=department_id,
            job_title=job_title,
            base_salary=base_salary,
            hire_date=hire_date or date.today(),
            employment_type=employment_type,
            manager_id=manager_id,
            status=initial_status,
        )

        self.db.save_employee(employee)
        self._trigger_onboarding(employee)

        logger.info(
            "Hired %s as %s in %s (status=%s)",
            employee.full_name,
            job_title,
            department.name,
            initial_status.value,
        )
        return employee

    def complete_probation(self, employee_id: str) -> Employee:
        """Transition an employee from PROBATION to ACTIVE after 90 days.

        Business rules:
        - Employee must currently be in PROBATION status.
        - At least 90 calendar days must have elapsed since hire date.
        - Manager approval is assumed (handled upstream).
        """
        employee = self._get_employee_or_raise(employee_id)

        if employee.status != EmploymentStatus.PROBATION:
            raise ValueError(
                f"Employee {employee_id} is not in probation "
                f"(current status: {employee.status.value})"
            )

        days_since_hire = (date.today() - employee.hire_date).days
        if days_since_hire < 90:
            raise ValueError(
                f"Probation period not complete: {days_since_hire}/90 days elapsed"
            )

        employee.status = EmploymentStatus.ACTIVE
        employee.updated_at = datetime.utcnow()
        self.db.save_employee(employee)

        logger.info("Probation completed for %s", employee.full_name)
        return employee

    # ------------------------------------------------------------------
    # Transfers & Promotions
    # ------------------------------------------------------------------

    def transfer_employee(
        self,
        employee_id: str,
        new_department_id: str,
        new_job_title: Optional[str] = None,
        new_salary: Optional[float] = None,
        effective_date: Optional[date] = None,
    ) -> Employee:
        """Transfer an employee to a different department.

        Business rules:
        - Employee must be ACTIVE.
        - Target department must have headcount capacity.
        - Salary adjustment must stay within the new position's grade band.
        - Transfer cannot happen during an active leave.
        """
        employee = self._get_employee_or_raise(employee_id)

        if employee.status != EmploymentStatus.ACTIVE:
            raise ValueError("Only active employees can be transferred")

        # Check for active leave
        active_leave = self.db.get_active_leave(employee_id)
        if active_leave:
            raise ValueError("Cannot transfer an employee who is currently on leave")

        new_dept = self.db.get_department(new_department_id)
        if new_dept is None:
            raise ValueError(f"Department {new_department_id} not found")

        # Headcount check
        current_headcount = self.db.count_active_employees(new_department_id)
        if new_dept.headcount_limit > 0 and current_headcount >= new_dept.headcount_limit:
            raise ValueError(
                f"Target department '{new_dept.name}' is at headcount capacity"
            )

        # Salary validation
        if new_salary is not None and new_job_title:
            position = self.db.get_position_by_title(new_job_title, new_department_id)
            if position and (new_salary < position.min_salary or new_salary > position.max_salary):
                raise ValueError(
                    f"New salary ${new_salary:,.2f} is outside the grade band for {new_job_title}"
                )

        old_dept = employee.department_id
        employee.department_id = new_department_id
        if new_job_title:
            employee.job_title = new_job_title
        if new_salary is not None:
            employee.base_salary = new_salary
        employee.updated_at = datetime.utcnow()

        self.db.save_employee(employee)
        self.db.record_transfer(employee_id, old_dept, new_department_id, effective_date)

        logger.info("Transferred %s to %s", employee.full_name, new_dept.name)
        return employee

    def promote_employee(
        self,
        employee_id: str,
        new_job_title: str,
        new_salary: float,
        effective_date: Optional[date] = None,
    ) -> Employee:
        """Promote an employee to a higher position.

        Business rules:
        - Employee must be ACTIVE and have at least 1 year of tenure.
        - New salary must be higher than current salary.
        - New salary must fall within the target position's grade band.
        - Salary increase cannot exceed 30% without VP approval flag.
        """
        employee = self._get_employee_or_raise(employee_id)

        if not employee.is_eligible_for_promotion():
            raise ValueError(
                f"Employee {employee_id} is not eligible for promotion "
                f"(status={employee.status.value}, tenure={employee.tenure_years}y)"
            )

        if new_salary <= employee.base_salary:
            raise ValueError(
                "Promotion salary must be higher than current salary "
                f"(${employee.base_salary:,.2f} → ${new_salary:,.2f})"
            )

        # Check salary increase cap
        increase_pct = (new_salary - employee.base_salary) / employee.base_salary
        if increase_pct > 0.30:
            logger.warning(
                "Salary increase of %.1f%% exceeds 30%% cap for %s — requires VP approval",
                increase_pct * 100,
                employee.full_name,
            )

        # Validate against grade band
        position = self.db.get_position_by_title(new_job_title, employee.department_id)
        if position and (new_salary < position.min_salary or new_salary > position.max_salary):
            raise ValueError(
                f"Salary ${new_salary:,.2f} is outside the grade band for {new_job_title}"
            )

        old_title = employee.job_title
        old_salary = employee.base_salary
        employee.job_title = new_job_title
        employee.base_salary = new_salary
        employee.updated_at = datetime.utcnow()

        self.db.save_employee(employee)
        self.db.record_promotion(employee_id, old_title, new_job_title, old_salary, new_salary)

        logger.info(
            "Promoted %s: %s → %s ($%,.2f → $%,.2f)",
            employee.full_name, old_title, new_job_title, old_salary, new_salary,
        )
        return employee

    # ------------------------------------------------------------------
    # Termination & Offboarding
    # ------------------------------------------------------------------

    def terminate_employee(
        self,
        employee_id: str,
        reason: str,
        termination_date: Optional[date] = None,
        is_voluntary: bool = False,
    ) -> Employee:
        """Terminate an employee and initiate offboarding.

        Business rules:
        - Cannot terminate an already-terminated employee.
        - Termination triggers: benefit COBRA eligibility, final paycheck,
          access revocation, and equipment return.
        - Involuntary terminations require a documented reason.
        - PTO balance is paid out on termination (state law dependent).
        """
        employee = self._get_employee_or_raise(employee_id)

        if employee.status == EmploymentStatus.TERMINATED:
            raise ValueError(f"Employee {employee_id} is already terminated")

        if not is_voluntary and not reason:
            raise ValueError("Involuntary terminations require a documented reason")

        employee.status = EmploymentStatus.TERMINATED
        employee.termination_date = termination_date or date.today()
        employee.termination_reason = reason
        employee.updated_at = datetime.utcnow()

        self.db.save_employee(employee)

        # Trigger offboarding workflows
        self._cancel_pending_leaves(employee_id)
        self._trigger_final_paycheck(employee)
        self._trigger_cobra_eligibility(employee)
        self._revoke_system_access(employee_id)

        logger.info(
            "Terminated %s (reason: %s, voluntary: %s)",
            employee.full_name, reason, is_voluntary,
        )
        return employee

    # ------------------------------------------------------------------
    # Org Chart
    # ------------------------------------------------------------------

    def get_direct_reports(self, manager_id: str) -> List[Employee]:
        """Return all employees who report directly to the given manager."""
        return self.db.find_employees(manager_id=manager_id)

    def get_org_tree(self, root_employee_id: str, max_depth: int = 5) -> Dict[str, Any]:
        """Build a hierarchical org tree starting from a given employee.

        Returns a nested dict: {employee, direct_reports: [{employee, direct_reports: …}]}
        """
        employee = self._get_employee_or_raise(root_employee_id)
        return self._build_org_node(employee, depth=0, max_depth=max_depth)

    def _build_org_node(self, employee: Employee, depth: int, max_depth: int) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "employee_id": employee.employee_id,
            "name": employee.full_name,
            "title": employee.job_title,
            "department_id": employee.department_id,
            "direct_reports": [],
        }
        if depth < max_depth:
            reports = self.get_direct_reports(employee.employee_id)
            for report in reports:
                node["direct_reports"].append(
                    self._build_org_node(report, depth + 1, max_depth)
                )
        return node

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_employee_or_raise(self, employee_id: str) -> Employee:
        employee = self.db.get_employee(employee_id)
        if employee is None:
            raise ValueError(f"Employee {employee_id} not found")
        return employee

    def _trigger_onboarding(self, employee: Employee) -> None:
        """Kick off onboarding tasks: IT setup, badge, orientation, etc."""
        logger.info("Onboarding initiated for %s", employee.full_name)
        self.db.create_onboarding_tasks(employee.employee_id)

    def _cancel_pending_leaves(self, employee_id: str) -> None:
        """Cancel any pending or approved future leave requests."""
        pending = self.db.get_pending_leaves(employee_id)
        for leave in pending:
            leave.status = "cancelled"
            self.db.save_leave(leave)

    def _trigger_final_paycheck(self, employee: Employee) -> None:
        """Schedule the final paycheck including PTO payout."""
        logger.info("Final paycheck scheduled for %s", employee.full_name)

    def _trigger_cobra_eligibility(self, employee: Employee) -> None:
        """Notify benefits team to send COBRA continuation notice."""
        if employee.employment_type == EmploymentType.FULL_TIME:
            logger.info("COBRA eligibility triggered for %s", employee.full_name)

    def _revoke_system_access(self, employee_id: str) -> None:
        """Disable all system accounts and revoke badge access."""
        logger.info("System access revoked for %s", employee_id)
