from __future__ import annotations

from ..schemas import WorkOrderReadinessResult


def _present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def check_work_order_readiness(context: dict) -> WorkOrderReadinessResult:
    blocking: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    if not context.get("reportAssessmentExists"):
        blocking.append("Verified/effective Report assessment is missing.")
    if not context.get("maintenanceRequestAuthorized"):
        blocking.append("Maintenance Request is not authorized.")
    if context.get("procurementHandoffStatus") != "COMPLETED":
        blocking.append("Procurement handoff is not completed.")
    if not context.get("procurementOutcomeExists"):
        blocking.append("Procurement Outcome is missing.")
    if not _present(context.get("executionType")):
        blocking.append("Execution type is missing.")
    if not _present(context.get("assignedPartyName")):
        blocking.append("Assigned execution party is missing.")
    if not (
        _present(context.get("responsibleLeadUserId"))
        or _present(context.get("responsibleLeadName"))
    ):
        blocking.append("Responsible lead reference is missing.")
    if not (
        _present(context.get("responsibleLeadContact"))
        or _present(context.get("responsibleLeadEmail"))
    ):
        blocking.append("Responsible lead contact/email is missing.")
    if not _present(context.get("requiredService")):
        blocking.append("Required service is missing.")
    if not _present(context.get("plannedStartAt")):
        blocking.append("Planned start is missing.")
    if not _present(context.get("deadline")):
        blocking.append("Planned deadline is missing.")

    request_safety = context.get("requestSafetyRequirements")
    work_order_safety = context.get("workOrderSafetyRequirements")
    if _present(request_safety) and not _present(work_order_safety):
        blocking.append("Maintenance Request safety requirements were not preserved in the Work Order.")

    if not _present(context.get("procurementReferenceNo")):
        warnings.append(
            "Procurement reference number is not recorded. The canonical schema allows it to be empty, but PPO should confirm whether the current Procurement process requires one."
        )

    if not blocking:
        info.append("Required execution metadata is present for PPO Head confirmation review.")
    info.append("Readiness is deterministic assistance only; the Agent does not confirm the Work Order.")

    return WorkOrderReadinessResult(
        ready=not blocking,
        blocking_errors=blocking,
        warnings=warnings,
        information=info,
    )
