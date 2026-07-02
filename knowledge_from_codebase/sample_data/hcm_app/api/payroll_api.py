"""
Payroll API handlers — REST endpoints for payroll processing.

Routes:
    POST   /payroll/runs           — Create a new payroll run
    POST   /payroll/runs/{id}/process — Process a draft payroll run
    POST   /payroll/runs/{id}/approve — Approve a processed run
    GET    /payroll/runs/{id}      — Get payroll run details
    GET    /payroll/stubs/{employee_id} — Get pay stubs for an employee
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict

logger = logging.getLogger(__name__)


def handle_create_run(event: Dict[str, Any], payroll_service: Any) -> Dict[str, Any]:
    """Handle POST /payroll/runs — create a new payroll run.

    Validates pay period dates and creates a draft run ready for processing.
    """
    body = json.loads(event.get("body", "{}"))

    required = ["pay_period_start", "pay_period_end", "pay_date"]
    missing = [f for f in required if f not in body]
    if missing:
        return _error_response(400, f"Missing required fields: {', '.join(missing)}")

    try:
        run = payroll_service.create_payroll_run(
            pay_period_start=date.fromisoformat(body["pay_period_start"]),
            pay_period_end=date.fromisoformat(body["pay_period_end"]),
            pay_date=date.fromisoformat(body["pay_date"]),
        )
        return _success_response(201, {"run_id": run.run_id, "status": run.status})
    except ValueError as e:
        return _error_response(400, str(e))


def handle_process_run(event: Dict[str, Any], payroll_service: Any) -> Dict[str, Any]:
    """Handle POST /payroll/runs/{id}/process — process payroll.

    Calculates gross pay, taxes, deductions, and net pay for all
    eligible employees in the pay period.
    """
    run_id = event.get("pathParameters", {}).get("id")

    try:
        run = payroll_service.process_payroll(run_id)
        return _success_response(200, {
            "run_id": run.run_id,
            "status": run.status,
            "employees_processed": len(run.stubs),
            "total_gross": run.total_gross,
            "total_net": run.total_net,
            "total_taxes": run.total_taxes,
        })
    except ValueError as e:
        return _error_response(400, str(e))


def handle_approve_run(event: Dict[str, Any], payroll_service: Any) -> Dict[str, Any]:
    """Handle POST /payroll/runs/{id}/approve — approve payroll for payment.

    Only payroll admins can approve. Triggers ACH payment initiation.
    """
    run_id = event.get("pathParameters", {}).get("id")
    body = json.loads(event.get("body", "{}"))
    approver_id = body.get("approver_id")

    if not approver_id:
        return _error_response(400, "approver_id is required")

    try:
        run = payroll_service.approve_payroll(run_id, approver_id)
        return _success_response(200, {"run_id": run.run_id, "status": run.status})
    except ValueError as e:
        return _error_response(400, str(e))


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
