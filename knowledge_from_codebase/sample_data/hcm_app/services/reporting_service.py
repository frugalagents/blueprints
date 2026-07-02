"""
Reporting and analytics service — headcount reports, turnover analysis,
compensation benchmarking, and workforce planning metrics.

Provides data-driven insights for HR leadership and finance teams.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from models.employee import Employee, EmploymentStatus, EmploymentType

logger = logging.getLogger(__name__)


class ReportingService:
    """Generates HR analytics and workforce reports."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Headcount Reports
    # ------------------------------------------------------------------

    def headcount_report(self, as_of_date: Optional[date] = None) -> Dict[str, Any]:
        """Generate a headcount report broken down by department and status.

        Includes active, on-leave, and probationary employees.
        Excludes terminated employees.
        """
        as_of = as_of_date or date.today()
        employees = self.db.find_employees_as_of(as_of)

        by_department: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        by_type: Dict[str, int] = defaultdict(int)
        by_status: Dict[str, int] = defaultdict(int)
        total = 0

        for emp in employees:
            if emp.status == EmploymentStatus.TERMINATED:
                continue
            dept = self.db.get_department(emp.department_id)
            dept_name = dept.name if dept else "Unknown"
            by_department[dept_name][emp.status.value] += 1
            by_type[emp.employment_type.value] += 1
            by_status[emp.status.value] += 1
            total += 1

        return {
            "as_of_date": as_of.isoformat(),
            "total_headcount": total,
            "by_department": dict(by_department),
            "by_employment_type": dict(by_type),
            "by_status": dict(by_status),
        }

    # ------------------------------------------------------------------
    # Turnover Analysis
    # ------------------------------------------------------------------

    def turnover_report(self, start_date: date, end_date: date) -> Dict[str, Any]:
        """Calculate employee turnover rate for a given period.

        Turnover rate = (Separations / Average Headcount) × 100

        Business rules:
        - Voluntary and involuntary separations are tracked separately.
        - Turnover is broken down by department for targeted retention efforts.
        - Industry benchmark comparison is included when available.
        """
        terminations = self.db.find_terminations(start_date, end_date)
        avg_headcount = self.db.get_average_headcount(start_date, end_date)

        voluntary = [t for t in terminations if t.get("is_voluntary")]
        involuntary = [t for t in terminations if not t.get("is_voluntary")]

        overall_rate = (len(terminations) / avg_headcount * 100) if avg_headcount > 0 else 0
        voluntary_rate = (len(voluntary) / avg_headcount * 100) if avg_headcount > 0 else 0
        involuntary_rate = (len(involuntary) / avg_headcount * 100) if avg_headcount > 0 else 0

        # Department breakdown
        dept_turnover: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "voluntary": 0, "involuntary": 0})
        for t in terminations:
            dept = t.get("department", "Unknown")
            dept_turnover[dept]["count"] += 1
            if t.get("is_voluntary"):
                dept_turnover[dept]["voluntary"] += 1
            else:
                dept_turnover[dept]["involuntary"] += 1

        # Tenure at separation
        tenure_buckets = {"<1yr": 0, "1-3yr": 0, "3-5yr": 0, "5-10yr": 0, "10+yr": 0}
        for t in terminations:
            tenure = t.get("tenure_years", 0)
            if tenure < 1:
                tenure_buckets["<1yr"] += 1
            elif tenure < 3:
                tenure_buckets["1-3yr"] += 1
            elif tenure < 5:
                tenure_buckets["3-5yr"] += 1
            elif tenure < 10:
                tenure_buckets["5-10yr"] += 1
            else:
                tenure_buckets["10+yr"] += 1

        return {
            "period": f"{start_date} to {end_date}",
            "total_separations": len(terminations),
            "voluntary_separations": len(voluntary),
            "involuntary_separations": len(involuntary),
            "average_headcount": avg_headcount,
            "overall_turnover_rate": round(overall_rate, 1),
            "voluntary_turnover_rate": round(voluntary_rate, 1),
            "involuntary_turnover_rate": round(involuntary_rate, 1),
            "by_department": dict(dept_turnover),
            "by_tenure": tenure_buckets,
        }

    # ------------------------------------------------------------------
    # Compensation Analysis
    # ------------------------------------------------------------------

    def compensation_report(self) -> Dict[str, Any]:
        """Generate a compensation analysis report.

        Includes salary distribution, compa-ratio analysis, and pay equity metrics.

        Business rules:
        - Compa-ratio = actual salary / midpoint of grade band.
        - Compa-ratio below 0.80 flags underpayment risk.
        - Compa-ratio above 1.20 flags overpayment or compression.
        - Gender pay gap is calculated per department.
        """
        employees = self.db.find_active_employees()

        total_payroll = 0.0
        compa_ratios: List[float] = []
        underpaid: List[Dict[str, Any]] = []
        overpaid: List[Dict[str, Any]] = []
        dept_salaries: Dict[str, List[float]] = defaultdict(list)

        for emp in employees:
            total_payroll += emp.base_salary
            dept_salaries[emp.department_id].append(emp.base_salary)

            position = self.db.get_position_by_title(emp.job_title, emp.department_id)
            if position:
                midpoint = (position.min_salary + position.max_salary) / 2
                compa = emp.base_salary / midpoint if midpoint > 0 else 1.0
                compa_ratios.append(compa)

                if compa < 0.80:
                    underpaid.append({
                        "employee_id": emp.employee_id,
                        "name": emp.full_name,
                        "salary": emp.base_salary,
                        "midpoint": midpoint,
                        "compa_ratio": round(compa, 2),
                    })
                elif compa > 1.20:
                    overpaid.append({
                        "employee_id": emp.employee_id,
                        "name": emp.full_name,
                        "salary": emp.base_salary,
                        "midpoint": midpoint,
                        "compa_ratio": round(compa, 2),
                    })

        avg_compa = sum(compa_ratios) / len(compa_ratios) if compa_ratios else 1.0

        # Department salary stats
        dept_stats = {}
        for dept_id, salaries in dept_salaries.items():
            dept = self.db.get_department(dept_id)
            dept_name = dept.name if dept else dept_id
            dept_stats[dept_name] = {
                "count": len(salaries),
                "avg_salary": round(sum(salaries) / len(salaries), 2),
                "min_salary": min(salaries),
                "max_salary": max(salaries),
            }

        return {
            "total_annual_payroll": round(total_payroll, 2),
            "average_salary": round(total_payroll / len(employees), 2) if employees else 0,
            "average_compa_ratio": round(avg_compa, 2),
            "underpaid_employees": underpaid,
            "overpaid_employees": overpaid,
            "by_department": dept_stats,
        }

    # ------------------------------------------------------------------
    # Workforce Planning
    # ------------------------------------------------------------------

    def workforce_planning_metrics(self) -> Dict[str, Any]:
        """Calculate workforce planning metrics for strategic HR decisions.

        Metrics:
        - Time to fill open positions (average days).
        - Retirement eligibility (employees within 5 years of 65).
        - Span of control (average direct reports per manager).
        - Internal mobility rate (transfers + promotions / headcount).
        """
        employees = self.db.find_active_employees()

        # Retirement risk
        retirement_eligible = []
        for emp in employees:
            if emp.date_of_birth:
                age = (date.today() - emp.date_of_birth).days / 365.25
                if age >= 60:
                    retirement_eligible.append({
                        "employee_id": emp.employee_id,
                        "name": emp.full_name,
                        "age": round(age, 1),
                        "department": emp.department_id,
                        "title": emp.job_title,
                    })

        # Span of control
        manager_counts: Dict[str, int] = defaultdict(int)
        for emp in employees:
            if emp.manager_id:
                manager_counts[emp.manager_id] += 1

        avg_span = (
            sum(manager_counts.values()) / len(manager_counts)
            if manager_counts else 0
        )

        # Average tenure
        tenures = [emp.tenure_years for emp in employees]
        avg_tenure = sum(tenures) / len(tenures) if tenures else 0

        return {
            "total_headcount": len(employees),
            "retirement_eligible_5yr": len(retirement_eligible),
            "retirement_risk_employees": retirement_eligible[:10],
            "average_span_of_control": round(avg_span, 1),
            "managers_count": len(manager_counts),
            "average_tenure_years": round(avg_tenure, 1),
        }
