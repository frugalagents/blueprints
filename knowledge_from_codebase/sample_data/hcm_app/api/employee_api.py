"""
Employee API handlers — REST endpoints for employee lifecycle operations.

Routes:
    POST   /employees              — Hire a new employee
    GET    /employees/{id}         — Get employee details
    PUT    /employees/{id}         — Update employee info
    POST   /employees/{id}/promote — Promote an employee
    POST   /employees/{id}/transfer — Transfer to another department
    POST   /employees/{id}/terminate — Terminate employment
    GET    /employees/{id}/org-tree — Get org chart subtree
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def handle_hire(event: Dict[str, Any], employee_service: Any) -> Dict[str, Any]:
    """Handle POST /employees — create a new hire.

    Validates the request body, delegates to EmployeeService.hire_employee,
    and returns the created employee record with a 201 status.
    """
    body = json.loads(event.get("body", "{}"))

    required_fields = ["first_name", "last_name", "email", "department_id", "job_title", "base_salary"]
    missing = [f for f in required_fields if f not in body]
    if missing:
        return _error_response(400, f"Missing required fields: {', '.join(missing)}")

    try:
        employee = employee_service.hire_employee(
            first_name=body["first_name"],
            last_name=body["last_name"],
            email=body["email"],
            department_id=body["department_id"],
            job_title=body["job_title"],
            base_salary=float(body["base_salary"]),
            manager_id=body.get("manager_id"),
        )
        return _success_response(201, {"employee_id": employee.employee_id, "status": "hired"})
    except ValueError as e:
        return _error_response(400, str(e))
    except Exception as e:
        logger.exception("Error hiring employee")
        return _error_response(500, "Internal server error")


def handle_get_employee(event: Dict[str, Any], employee_service: Any) -> Dict[str, Any]:
    """Handle GET /employees/{id} — retrieve employee details."""
    employee_id = event.get("pathParameters", {}).get("id")
    if not employee_id:
        return _error_response(400, "Employee ID is required")

    employee = employee_service.db.get_employee(employee_id)
    if employee is None:
        return _error_response(404, f"Employee {employee_id} not found")

    return _success_response(200, employee.__dict__)


def handle_promote(event: Dict[str, Any], employee_service: Any) -> Dict[str, Any]:
    """Handle POST /employees/{id}/promote — promote an employee.

    Validates promotion eligibility, salary constraints, and grade band
    compliance before executing the promotion.
    """
    employee_id = event.get("pathParameters", {}).get("id")
    body = json.loads(event.get("body", "{}"))

    if not body.get("new_job_title") or not body.get("new_salary"):
        return _error_response(400, "new_job_title and new_salary are required")

    try:
        employee = employee_service.promote_employee(
            employee_id=employee_id,
            new_job_title=body["new_job_title"],
            new_salary=float(body["new_salary"]),
        )
        return _success_response(200, {"employee_id": employee.employee_id, "status": "promoted"})
    except ValueError as e:
        return _error_response(400, str(e))


def handle_transfer(event: Dict[str, Any], employee_service: Any) -> Dict[str, Any]:
    """Handle POST /employees/{id}/transfer — transfer to a new department."""
    employee_id = event.get("pathParameters", {}).get("id")
    body = json.loads(event.get("body", "{}"))

    if not body.get("new_department_id"):
        return _error_response(400, "new_department_id is required")

    try:
        employee = employee_service.transfer_employee(
            employee_id=employee_id,
            new_department_id=body["new_department_id"],
            new_job_title=body.get("new_job_title"),
            new_salary=body.get("new_salary"),
        )
        return _success_response(200, {"employee_id": employee.employee_id, "status": "transferred"})
    except ValueError as e:
        return _error_response(400, str(e))


def handle_terminate(event: Dict[str, Any], employee_service: Any) -> Dict[str, Any]:
    """Handle POST /employees/{id}/terminate — terminate employment.

    Triggers the full offboarding workflow: benefit termination, COBRA,
    final paycheck, and access revocation.
    """
    employee_id = event.get("pathParameters", {}).get("id")
    body = json.loads(event.get("body", "{}"))

    if not body.get("reason"):
        return _error_response(400, "Termination reason is required")

    try:
        employee = employee_service.terminate_employee(
            employee_id=employee_id,
            reason=body["reason"],
            is_voluntary=body.get("is_voluntary", False),
        )
        return _success_response(200, {"employee_id": employee.employee_id, "status": "terminated"})
    except ValueError as e:
        return _error_response(400, str(e))


def handle_org_tree(event: Dict[str, Any], employee_service: Any) -> Dict[str, Any]:
    """Handle GET /employees/{id}/org-tree — get organizational hierarchy."""
    employee_id = event.get("pathParameters", {}).get("id")
    max_depth = int(event.get("queryStringParameters", {}).get("depth", 3))

    try:
        tree = employee_service.get_org_tree(employee_id, max_depth=max_depth)
        return _success_response(200, tree)
    except ValueError as e:
        return _error_response(404, str(e))


# ------------------------------------------------------------------
# Response helpers
# ------------------------------------------------------------------

def _success_response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _error_response(status_code: int, message: str) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }
