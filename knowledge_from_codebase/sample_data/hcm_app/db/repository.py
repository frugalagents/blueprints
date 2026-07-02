"""
Database repository — data access layer for the HCM platform.

Provides CRUD operations for employees, departments, leave, payroll,
benefits, and compliance records.  Uses DynamoDB as the backing store.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HCMRepository:
    """Unified data access layer for all HCM entities.

    Wraps DynamoDB tables and S3 storage behind a clean interface
    so business services don't depend on AWS SDK details.
    """

    def __init__(self, dynamodb_client: Any, s3_client: Any, config: Dict[str, str]) -> None:
        self.dynamodb = dynamodb_client
        self.s3 = s3_client
        self.employee_table = config.get("employee_table", "hcm-employees")
        self.leave_table = config.get("leave_table", "hcm-leave")
        self.payroll_table = config.get("payroll_table", "hcm-payroll")
        self.benefits_table = config.get("benefits_table", "hcm-benefits")
        self.audit_table = config.get("audit_table", "hcm-audit")
        self.bucket = config.get("s3_bucket", "hcm-data")

    # ------------------------------------------------------------------
    # Employee CRUD
    # ------------------------------------------------------------------

    def get_employee(self, employee_id: str) -> Optional[Any]:
        """Retrieve an employee by ID."""
        try:
            response = self.dynamodb.get_item(
                TableName=self.employee_table,
                Key={"employee_id": {"S": employee_id}},
            )
            item = response.get("Item")
            return self._deserialize_employee(item) if item else None
        except Exception as e:
            logger.error("Failed to get employee %s: %s", employee_id, e)
            return None

    def save_employee(self, employee: Any) -> None:
        """Create or update an employee record."""
        item = self._serialize_employee(employee)
        self.dynamodb.put_item(TableName=self.employee_table, Item=item)

    def find_active_employees(self) -> List[Any]:
        """Return all non-terminated employees."""
        response = self.dynamodb.scan(
            TableName=self.employee_table,
            FilterExpression="attribute_not_equal(#s, :terminated)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":terminated": {"S": "terminated"}},
        )
        return [self._deserialize_employee(item) for item in response.get("Items", [])]

    def find_employees(self, **filters: Any) -> List[Any]:
        """Find employees matching the given filters."""
        # Simplified scan with filters
        response = self.dynamodb.scan(TableName=self.employee_table)
        employees = [self._deserialize_employee(item) for item in response.get("Items", [])]

        for key, value in filters.items():
            employees = [e for e in employees if getattr(e, key, None) == value]

        return employees

    def count_active_employees(self, department_id: str) -> int:
        """Count active employees in a department."""
        employees = self.find_employees(department_id=department_id)
        return sum(1 for e in employees if e.status.value != "terminated")

    # ------------------------------------------------------------------
    # Department
    # ------------------------------------------------------------------

    def get_department(self, department_id: str) -> Optional[Any]:
        """Retrieve a department by ID."""
        try:
            response = self.dynamodb.get_item(
                TableName=self.employee_table,
                Key={"employee_id": {"S": f"DEPT#{department_id}"}},
            )
            item = response.get("Item")
            return self._deserialize_department(item) if item else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Leave
    # ------------------------------------------------------------------

    def save_leave_request(self, request: Any) -> None:
        """Save a leave request."""
        item = self._serialize_leave(request)
        self.dynamodb.put_item(TableName=self.leave_table, Item=item)

    def get_leave_request(self, request_id: str) -> Optional[Any]:
        """Retrieve a leave request by ID."""
        response = self.dynamodb.get_item(
            TableName=self.leave_table,
            Key={"request_id": {"S": request_id}},
        )
        item = response.get("Item")
        return self._deserialize_leave(item) if item else None

    def find_overlapping_leave(self, employee_id: str, start: date, end: date) -> Optional[Any]:
        """Check for overlapping approved or pending leave."""
        response = self.dynamodb.scan(
            TableName=self.leave_table,
            FilterExpression="employee_id = :eid AND #s IN (:pending, :approved)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":eid": {"S": employee_id},
                ":pending": {"S": "pending"},
                ":approved": {"S": "approved"},
            },
        )
        for item in response.get("Items", []):
            leave = self._deserialize_leave(item)
            if leave and leave.start_date <= end and leave.end_date >= start:
                return leave
        return None

    def get_leave_balance(self, employee_id: str, leave_type: Any, year: int) -> Optional[Any]:
        """Get leave balance for an employee, type, and year."""
        key = f"{employee_id}#{leave_type.value}#{year}"
        response = self.dynamodb.get_item(
            TableName=self.leave_table,
            Key={"request_id": {"S": f"BAL#{key}"}},
        )
        item = response.get("Item")
        return self._deserialize_balance(item) if item else None

    def save_leave_balance(self, balance: Any) -> None:
        """Save a leave balance record."""
        item = self._serialize_balance(balance)
        self.dynamodb.put_item(TableName=self.leave_table, Item=item)

    # ------------------------------------------------------------------
    # Payroll
    # ------------------------------------------------------------------

    def save_payroll_run(self, run: Any) -> None:
        """Save a payroll run."""
        item = self._serialize_payroll_run(run)
        self.dynamodb.put_item(TableName=self.payroll_table, Item=item)

    def get_payroll_run(self, run_id: str) -> Optional[Any]:
        """Retrieve a payroll run by ID."""
        response = self.dynamodb.get_item(
            TableName=self.payroll_table,
            Key={"run_id": {"S": run_id}},
        )
        item = response.get("Item")
        return self._deserialize_payroll_run(item) if item else None

    def save_pay_stub(self, stub: Any) -> None:
        """Save a pay stub."""
        self.dynamodb.put_item(
            TableName=self.payroll_table,
            Item={"run_id": {"S": f"STUB#{stub.stub_id}"}, "data": {"S": json.dumps(stub.__dict__, default=str)}},
        )

    def get_ytd_gross(self, employee_id: str) -> float:
        """Get year-to-date gross pay for an employee."""
        # Scan pay stubs for this employee in the current year
        response = self.dynamodb.scan(
            TableName=self.payroll_table,
            FilterExpression="begins_with(run_id, :prefix)",
            ExpressionAttributeValues={":prefix": {"S": "STUB#"}},
        )
        total = 0.0
        for item in response.get("Items", []):
            data = json.loads(item.get("data", {}).get("S", "{}"))
            if data.get("employee_id") == employee_id:
                total += data.get("gross_pay", 0)
        return total

    # ------------------------------------------------------------------
    # Benefits
    # ------------------------------------------------------------------

    def get_active_enrollments(self, employee_id: str) -> List[Any]:
        """Get all active benefit enrollments for an employee."""
        response = self.dynamodb.scan(
            TableName=self.benefits_table,
            FilterExpression="employee_id = :eid AND #s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":eid": {"S": employee_id},
                ":active": {"S": "active"},
            },
        )
        return [self._deserialize_enrollment(item) for item in response.get("Items", [])]

    def save_enrollment(self, enrollment: Any) -> None:
        """Save a benefit enrollment."""
        self.dynamodb.put_item(
            TableName=self.benefits_table,
            Item={"enrollment_id": {"S": enrollment.enrollment_id}, "data": {"S": json.dumps(enrollment.__dict__, default=str)}},
        )

    def get_benefit_plan(self, plan_id: str) -> Optional[Any]:
        """Retrieve a benefit plan by ID."""
        response = self.dynamodb.get_item(
            TableName=self.benefits_table,
            Key={"enrollment_id": {"S": f"PLAN#{plan_id}"}},
        )
        item = response.get("Item")
        if item and "data" in item:
            return json.loads(item["data"]["S"])
        return None

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def save_audit_event(self, event: Dict[str, Any]) -> None:
        """Persist an audit trail event."""
        self.dynamodb.put_item(
            TableName=self.audit_table,
            Item={
                "event_id": {"S": f"AUD-{datetime.utcnow().isoformat()}"},
                "data": {"S": json.dumps(event, default=str)},
            },
        )

    # ------------------------------------------------------------------
    # Serialization helpers (stubs — real impl would use proper marshalling)
    # ------------------------------------------------------------------

    def _serialize_employee(self, emp: Any) -> Dict[str, Any]:
        return {"employee_id": {"S": emp.employee_id}, "data": {"S": json.dumps(emp.__dict__, default=str)}}

    def _deserialize_employee(self, item: Dict[str, Any]) -> Any:
        if "data" in item:
            return json.loads(item["data"]["S"])
        return item

    def _serialize_leave(self, req: Any) -> Dict[str, Any]:
        return {"request_id": {"S": req.request_id}, "data": {"S": json.dumps(req.__dict__, default=str)}}

    def _deserialize_leave(self, item: Any) -> Any:
        if item and "data" in item:
            return json.loads(item["data"]["S"])
        return item

    def _serialize_balance(self, bal: Any) -> Dict[str, Any]:
        key = f"BAL#{bal.employee_id}#{bal.leave_type.value}#{bal.year}"
        return {"request_id": {"S": key}, "data": {"S": json.dumps(bal.__dict__, default=str)}}

    def _deserialize_balance(self, item: Any) -> Any:
        if item and "data" in item:
            return json.loads(item["data"]["S"])
        return item

    def _serialize_payroll_run(self, run: Any) -> Dict[str, Any]:
        return {"run_id": {"S": run.run_id}, "data": {"S": json.dumps(run.__dict__, default=str)}}

    def _deserialize_payroll_run(self, item: Any) -> Any:
        if item and "data" in item:
            return json.loads(item["data"]["S"])
        return item

    def _deserialize_department(self, item: Any) -> Any:
        if item and "data" in item:
            return json.loads(item["data"]["S"])
        return item

    def _deserialize_enrollment(self, item: Any) -> Any:
        if item and "data" in item:
            return json.loads(item["data"]["S"])
        return item

    # ------------------------------------------------------------------
    # Placeholder methods (called by services, would be real queries)
    # ------------------------------------------------------------------

    def get_position_by_title(self, title: str, department_id: str) -> Optional[Any]:
        return None

    def get_active_leave(self, employee_id: str) -> Optional[Any]:
        return None

    def record_transfer(self, emp_id: str, old_dept: str, new_dept: str, effective: Any) -> None:
        pass

    def record_promotion(self, emp_id: str, old_title: str, new_title: str, old_sal: float, new_sal: float) -> None:
        pass

    def create_onboarding_tasks(self, employee_id: str) -> None:
        pass

    def get_pending_leaves(self, employee_id: str) -> List[Any]:
        return []

    def save_leave(self, leave: Any) -> None:
        pass

    def is_hr_admin(self, user_id: str) -> bool:
        return False

    def get_hours_worked_last_12_months(self, employee_id: str) -> float:
        return 2080.0

    def find_payroll_run(self, start: date, end: date) -> Optional[Any]:
        return None

    def get_overtime_hours(self, emp_id: str, start: date, end: date) -> float:
        return 0.0

    def get_pending_bonus(self, emp_id: str, as_of: date) -> float:
        return 0.0

    def get_pending_commission(self, emp_id: str, as_of: date) -> float:
        return 0.0

    def is_payroll_admin(self, user_id: str) -> bool:
        return False

    def get_bank_info(self, employee_id: str) -> Optional[Dict[str, str]]:
        return None

    def get_active_enrollment_by_type(self, emp_id: str, benefit_type: Any) -> Optional[Any]:
        return None

    def has_alternative_coverage(self, employee_id: str) -> bool:
        return False

    def has_qualifying_life_event(self, employee_id: str) -> bool:
        return False

    def has_medical_coverage(self, employee_id: str) -> bool:
        return True

    def get_average_monthly_hours(self, employee_id: str) -> float:
        return 80.0

    def get_average_weekly_hours(self, employee_id: str) -> float:
        return 40.0

    def get_i9_record(self, employee_id: str) -> Optional[Dict[str, Any]]:
        return {"section1_complete": True, "section2_complete": True}

    def find_all_employees(self) -> List[Any]:
        return self.find_active_employees()

    def find_employees_for_eeo(self, year: int) -> List[Dict[str, Any]]:
        return []

    def find_employees_as_of(self, as_of: date) -> List[Any]:
        return self.find_active_employees()

    def find_terminations(self, start: date, end: date) -> List[Dict[str, Any]]:
        return []

    def get_average_headcount(self, start: date, end: date) -> int:
        return 100

    def get_hours_worked(self, emp_id: str, start: date, end: date) -> float:
        return 40.0

    def was_overtime_paid(self, emp_id: str, start: date, end: date) -> bool:
        return True

    def get_current_ip(self) -> str:
        return "0.0.0.0"
