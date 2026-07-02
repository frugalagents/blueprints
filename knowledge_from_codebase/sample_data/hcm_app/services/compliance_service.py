"""
Compliance and reporting service — labor law compliance checks,
EEO reporting, ACA tracking, I-9 verification, and audit logging.

Ensures the organization meets federal and state regulatory requirements
for employment practices.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from models.employee import Employee, EmploymentStatus, EmploymentType

logger = logging.getLogger(__name__)

# ACA thresholds
ACA_FULL_TIME_HOURS_WEEKLY = 30
ACA_FULL_TIME_HOURS_MONTHLY = 130
ACA_LARGE_EMPLOYER_THRESHOLD = 50

# I-9 deadlines
I9_SECTION1_DEADLINE_DAYS = 1  # First day of employment
I9_SECTION2_DEADLINE_DAYS = 3  # Within 3 business days of start

# EEO-1 categories
EEO_CATEGORIES = [
    "Executive/Senior Officials",
    "First/Mid-Level Officials",
    "Professionals",
    "Technicians",
    "Sales Workers",
    "Administrative Support",
    "Craft Workers",
    "Operatives",
    "Laborers",
    "Service Workers",
]


class ComplianceService:
    """Monitors and enforces employment compliance requirements."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # ACA (Affordable Care Act) Compliance
    # ------------------------------------------------------------------

    def check_aca_compliance(self) -> Dict[str, Any]:
        """Check ACA compliance for the organization.

        Business rules:
        - Applicable Large Employers (50+ FTEs) must offer affordable
          minimum essential coverage to full-time employees.
        - Full-time = 30+ hours/week or 130+ hours/month.
        - Variable-hour employees are measured over a lookback period.
        - Non-compliance triggers IRS penalties (Section 4980H).
        """
        employees = self.db.find_all_employees()
        fte_count = self._calculate_fte_count(employees)

        is_ale = fte_count >= ACA_LARGE_EMPLOYER_THRESHOLD

        violations = []
        if is_ale:
            for emp in employees:
                if emp.status != EmploymentStatus.ACTIVE:
                    continue
                if self._is_aca_full_time(emp):
                    has_coverage = self.db.has_medical_coverage(emp.employee_id)
                    if not has_coverage:
                        violations.append({
                            "employee_id": emp.employee_id,
                            "name": emp.full_name,
                            "issue": "Full-time employee without medical coverage offer",
                            "risk": "Section 4980H(a) penalty exposure",
                        })

        result = {
            "is_applicable_large_employer": is_ale,
            "fte_count": fte_count,
            "total_employees": len(employees),
            "violations": violations,
            "compliant": len(violations) == 0,
            "checked_at": datetime.utcnow().isoformat(),
        }

        if violations:
            logger.warning("ACA compliance issues found: %d violations", len(violations))
        else:
            logger.info("ACA compliance check passed (FTE count: %d)", fte_count)

        return result

    def _calculate_fte_count(self, employees: List[Employee]) -> int:
        """Calculate Full-Time Equivalent count for ACA purposes.

        Full-time employees count as 1 FTE each.
        Part-time hours are aggregated and divided by 120 to get FTE equivalents.
        """
        full_time_count = 0
        part_time_hours = 0.0

        for emp in employees:
            if emp.status not in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE):
                continue
            if emp.employment_type == EmploymentType.FULL_TIME:
                full_time_count += 1
            elif emp.employment_type == EmploymentType.PART_TIME:
                monthly_hours = self.db.get_average_monthly_hours(emp.employee_id)
                part_time_hours += monthly_hours

        part_time_fte = int(part_time_hours / 120)
        return full_time_count + part_time_fte

    def _is_aca_full_time(self, employee: Employee) -> bool:
        """Determine if an employee is ACA full-time (30+ hours/week)."""
        if employee.employment_type == EmploymentType.FULL_TIME:
            return True
        avg_weekly = self.db.get_average_weekly_hours(employee.employee_id)
        return avg_weekly >= ACA_FULL_TIME_HOURS_WEEKLY

    # ------------------------------------------------------------------
    # I-9 Employment Verification
    # ------------------------------------------------------------------

    def check_i9_compliance(self) -> Dict[str, Any]:
        """Audit I-9 form completion for all employees.

        Business rules:
        - Section 1 must be completed by end of first day of employment.
        - Section 2 must be completed within 3 business days of start.
        - Reverification required when work authorization expires.
        - Missing or late I-9s expose the employer to fines ($252-$2,507 per form).
        """
        employees = self.db.find_all_employees()
        issues = []

        for emp in employees:
            if emp.status == EmploymentStatus.TERMINATED:
                continue

            i9 = self.db.get_i9_record(emp.employee_id)

            if i9 is None:
                days_since_hire = (date.today() - emp.hire_date).days
                if days_since_hire > I9_SECTION2_DEADLINE_DAYS:
                    issues.append({
                        "employee_id": emp.employee_id,
                        "name": emp.full_name,
                        "issue": "Missing I-9 form",
                        "days_overdue": days_since_hire - I9_SECTION2_DEADLINE_DAYS,
                        "severity": "critical",
                    })
                continue

            # Check Section 1 completion
            if not i9.get("section1_complete"):
                issues.append({
                    "employee_id": emp.employee_id,
                    "name": emp.full_name,
                    "issue": "I-9 Section 1 incomplete",
                    "severity": "high",
                })

            # Check Section 2 completion
            if not i9.get("section2_complete"):
                issues.append({
                    "employee_id": emp.employee_id,
                    "name": emp.full_name,
                    "issue": "I-9 Section 2 incomplete",
                    "severity": "high",
                })

            # Check work authorization expiration
            auth_expiry = i9.get("work_auth_expiry")
            if auth_expiry:
                expiry_date = date.fromisoformat(auth_expiry) if isinstance(auth_expiry, str) else auth_expiry
                days_until_expiry = (expiry_date - date.today()).days
                if days_until_expiry < 0:
                    issues.append({
                        "employee_id": emp.employee_id,
                        "name": emp.full_name,
                        "issue": "Work authorization expired",
                        "expired_days_ago": abs(days_until_expiry),
                        "severity": "critical",
                    })
                elif days_until_expiry <= 90:
                    issues.append({
                        "employee_id": emp.employee_id,
                        "name": emp.full_name,
                        "issue": f"Work authorization expiring in {days_until_expiry} days",
                        "severity": "warning",
                    })

        result = {
            "total_checked": len(employees),
            "issues": issues,
            "critical_count": sum(1 for i in issues if i.get("severity") == "critical"),
            "compliant": len(issues) == 0,
            "checked_at": datetime.utcnow().isoformat(),
        }

        if issues:
            logger.warning("I-9 compliance issues: %d total, %d critical", len(issues), result["critical_count"])

        return result

    # ------------------------------------------------------------------
    # EEO-1 Reporting
    # ------------------------------------------------------------------

    def generate_eeo1_report(self, report_year: int) -> Dict[str, Any]:
        """Generate EEO-1 Component 1 workforce demographics report.

        Business rules:
        - Required for employers with 100+ employees.
        - Reports workforce by job category, race/ethnicity, and gender.
        - Must be filed annually with the EEOC.
        """
        employees = self.db.find_employees_for_eeo(report_year)

        # Build the matrix: job_category × demographics
        matrix: Dict[str, Dict[str, int]] = {}
        for category in EEO_CATEGORIES:
            matrix[category] = {}

        for emp in employees:
            category = self._map_to_eeo_category(emp)
            demo_key = f"{emp.get('race', 'Not Specified')}_{emp.get('gender', 'Not Specified')}"
            if category not in matrix:
                matrix[category] = {}
            matrix[category][demo_key] = matrix[category].get(demo_key, 0) + 1

        total_employees = len(employees)

        report = {
            "report_year": report_year,
            "total_employees": total_employees,
            "filing_required": total_employees >= 100,
            "categories": matrix,
            "generated_at": datetime.utcnow().isoformat(),
        }

        logger.info("EEO-1 report generated for %d: %d employees", report_year, total_employees)
        return report

    def _map_to_eeo_category(self, employee: Dict[str, Any]) -> str:
        """Map a job title to an EEO-1 job category."""
        title = (employee.get("job_title") or "").lower()

        if any(kw in title for kw in ("vp", "director", "chief", "president", "ceo", "cfo", "cto")):
            return "Executive/Senior Officials"
        if any(kw in title for kw in ("manager", "supervisor", "lead", "head")):
            return "First/Mid-Level Officials"
        if any(kw in title for kw in ("engineer", "developer", "analyst", "scientist", "architect")):
            return "Professionals"
        if any(kw in title for kw in ("technician", "specialist", "support")):
            return "Technicians"
        if any(kw in title for kw in ("sales", "account")):
            return "Sales Workers"
        if any(kw in title for kw in ("admin", "assistant", "coordinator", "clerk")):
            return "Administrative Support"
        return "Professionals"  # default

    # ------------------------------------------------------------------
    # Overtime Compliance (FLSA)
    # ------------------------------------------------------------------

    def check_overtime_compliance(self, pay_period_start: date, pay_period_end: date) -> Dict[str, Any]:
        """Check FLSA overtime compliance for a pay period.

        Business rules:
        - Non-exempt employees must be paid 1.5x for hours over 40/week.
        - Misclassification of exempt status is a violation.
        - State laws may have stricter overtime rules (e.g., CA daily overtime).
        """
        employees = self.db.find_active_employees()
        violations = []

        for emp in employees:
            position = self.db.get_position_by_title(emp.job_title, emp.department_id)
            if position is None:
                continue

            hours = self.db.get_hours_worked(emp.employee_id, pay_period_start, pay_period_end)
            weekly_hours = hours  # simplified

            if not position.is_exempt and weekly_hours > 40:
                overtime_hours = weekly_hours - 40
                was_paid_overtime = self.db.was_overtime_paid(
                    emp.employee_id, pay_period_start, pay_period_end
                )
                if not was_paid_overtime:
                    violations.append({
                        "employee_id": emp.employee_id,
                        "name": emp.full_name,
                        "hours_worked": weekly_hours,
                        "overtime_hours": overtime_hours,
                        "issue": "Overtime hours worked but not compensated at 1.5x rate",
                    })

            # Check for potential misclassification
            if position.is_exempt and emp.base_salary < 35_568:
                violations.append({
                    "employee_id": emp.employee_id,
                    "name": emp.full_name,
                    "issue": (
                        f"Exempt classification may be invalid: salary ${emp.base_salary:,.2f} "
                        f"is below FLSA minimum threshold of $35,568"
                    ),
                })

        return {
            "period": f"{pay_period_start} to {pay_period_end}",
            "employees_checked": len(employees),
            "violations": violations,
            "compliant": len(violations) == 0,
        }

    # ------------------------------------------------------------------
    # Audit Trail
    # ------------------------------------------------------------------

    def log_audit_event(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        actor_id: str,
        details: Dict[str, Any],
    ) -> None:
        """Record an audit event for compliance tracking.

        All sensitive HR actions (hire, terminate, salary change, etc.)
        must be logged with who did what, when, and why.
        """
        event = {
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "actor_id": actor_id,
            "details": details,
            "timestamp": datetime.utcnow().isoformat(),
            "ip_address": self.db.get_current_ip(),
        }
        self.db.save_audit_event(event)
        logger.info(
            "Audit: %s on %s/%s by %s",
            event_type, entity_type, entity_id, actor_id,
        )
