from __future__ import annotations

from ..schemas import WorkOrderReadinessResult


def _present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def check_work_order_readiness(context: dict) -> WorkOrderReadinessResult:
    """Validate deterministic Work Order source/routing metadata.

    This readiness check does not authorize work, assign a person, or close a
    Work Order. It is used by the Agent to make sure the database facts are
    internally consistent before/after Node performs business mutations.
    """
    blocking: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    route_type = str(context.get("routeType") or "").strip().upper()
    review_decision = str(
        context.get("maintenanceReviewDecision") or ""
    ).strip().upper()
    work_order_status = str(
        context.get("workOrderStatus") or "PENDING_ASSIGNMENT"
    ).strip().upper()

    if not context.get("reportAssessmentExists"):
        blocking.append("Completed Facility Issue assessment is missing.")

    if not context.get("maintenanceReviewCompleted"):
        blocking.append("Completed Maintenance Review is missing.")

    if route_type not in {"INTERNAL", "PROCUREMENT"}:
        blocking.append("Work Order route type must be INTERNAL or PROCUREMENT.")
    elif review_decision and review_decision != route_type:
        blocking.append(
            "Work Order route type does not match the completed Maintenance Review decision."
        )

    if not context.get("maintenanceRequestReady"):
        blocking.append(
            "Maintenance Request is not in a reviewed/routed state that can support a Work Order."
        )

    procurement_outcome_exists = bool(
        context.get("procurementOutcomeExists")
    )

    if route_type == "INTERNAL":
        if procurement_outcome_exists:
            blocking.append(
                "INTERNAL Work Order must not reference a Procurement Outcome."
            )
        if _present(context.get("executionType")) and str(
            context.get("executionType")
        ).upper() != "INTERNAL":
            blocking.append(
                "INTERNAL Work Order must use execution type INTERNAL."
            )

    if route_type == "PROCUREMENT":
        if context.get("procurementHandoffStatus") != "COMPLETED":
            blocking.append("Procurement handoff is not completed.")
        if not procurement_outcome_exists:
            blocking.append("Procurement Outcome is missing.")
        if not _present(context.get("procurementReferenceNo")):
            warnings.append(
                "Procurement reference number is not recorded. The database allows it to be empty, but the Maintenance Office should confirm whether the current Procurement process requires one."
            )

    if not _present(context.get("executionType")):
        blocking.append("Execution type is missing.")

    if not _present(context.get("requiredService")):
        blocking.append("Required service is missing.")

    request_safety = context.get("requestSafetyRequirements")
    work_order_safety = context.get("workOrderSafetyRequirements")
    if _present(request_safety) and not _present(work_order_safety):
        blocking.append(
            "Maintenance Request safety requirements were not preserved in the Work Order."
        )

    assignment_present = (
        _present(context.get("assignedPartyName"))
        and (
            _present(context.get("responsibleLeadUserId"))
            or _present(context.get("responsibleLeadName"))
        )
    )

    if work_order_status == "PENDING_ASSIGNMENT":
        if not assignment_present:
            info.append(
                "Work Order is valid for PENDING_ASSIGNMENT; assignment is the next Maintenance Office action."
            )
        else:
            info.append(
                "Assignment details are prefilled, but the Work Order remains PENDING_ASSIGNMENT until Maintenance staff formally dispatch it."
            )
    elif not assignment_present:
        blocking.append(
            "Assigned execution party and responsible lead are required after PENDING_ASSIGNMENT."
        )
    elif not _present(context.get("assignedAt")):
        blocking.append(
            "AssignedAt is required after the Work Order leaves PENDING_ASSIGNMENT."
        )

    if assignment_present and not (
        _present(context.get("responsibleLeadContact"))
        or _present(context.get("responsibleLeadEmail"))
    ):
        warnings.append(
            "Responsible lead contact/email is not recorded. This is allowed by the schema but may reduce dispatch communication quality."
        )

    if not _present(context.get("plannedStartAt")):
        warnings.append("Planned start is not recorded yet.")

    if not _present(context.get("deadline")):
        warnings.append("Work Order deadline is not recorded yet.")

    if not blocking:
        info.append(
            "Work Order source, Maintenance Review, and route-specific metadata are consistent."
        )

    info.append(
        "Readiness is deterministic assistance only; the Agent does not choose the human routing decision, assign workers, or close the Work Order."
    )

    return WorkOrderReadinessResult(
        ready=not blocking,
        blocking_errors=blocking,
        warnings=warnings,
        information=info,
    )
