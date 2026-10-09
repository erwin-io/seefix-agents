from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse

from .agent import FacilityInspectionAgent
from .config import settings
from .database import Database
from .maintenance_request.generator import MaintenanceRequestGenerator
from .maintenance_request.workflow import MaintenanceRequestWorkflow
from .procurement.clarification import ProcurementClarificationService
from .procurement.monitoring import WorkflowMonitor
from .repositories.maintenance_requests import MaintenanceRequestRepository
from .repositories.procurement import ProcurementRepository
from .repositories.reports import ReportRepository
from .repositories.work_orders import WorkOrderRepository
from .work_order.completion import CompletionAssessmentService
from .workers.completion_worker import CompletionWorker
from .workers.report_worker import ReportWorker


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="SEEFIX Agent operation failed. Check service logs for the bounded diagnostic.",
    )


# ---------------------------------------------------------------------------
# Core services
# ---------------------------------------------------------------------------

database = Database(
    database_url=settings.database_url,
    sslmode=settings.database_sslmode,
    sslrootcert=settings.database_sslrootcert,
    connect_timeout_seconds=settings.database_connect_timeout_seconds,
    application_name=settings.database_application_name,
)
agent = FacilityInspectionAgent(settings)
report_repository = ReportRepository(
    database=database,
    worker_name=settings.agent_worker_name,
    recurrence_threshold=settings.recurrence_threshold,
    recurrence_lookback_days=settings.recurrence_lookback_days,
    verification_threshold=settings.verification_threshold,
    duplicate_lookback_days=settings.duplicate_lookback_days,
)
maintenance_repository = MaintenanceRequestRepository(database)
maintenance_generator = MaintenanceRequestGenerator(agent.provider)
maintenance_workflow = MaintenanceRequestWorkflow(
    generator=maintenance_generator,
    repository=maintenance_repository,
)
procurement_repository = ProcurementRepository(
    database,
    expected_days=settings.procurement_expected_days,
    followup_hours=settings.procurement_followup_hours,
)
clarification_service = ProcurementClarificationService(
    repository=procurement_repository,
    provider=agent.provider,
)
work_order_repository = WorkOrderRepository(
    database,
    completion_worker_name=settings.completion_worker_name,
)
completion_service = CompletionAssessmentService(agent.provider)
report_worker = ReportWorker(
    settings=settings,
    repository=report_repository,
    agent=agent,
    maintenance_request_workflow=maintenance_workflow,
)
completion_worker = CompletionWorker(
    settings=settings,
    repository=work_order_repository,
    service=completion_service,
)
workflow_monitor = WorkflowMonitor(
    repository=procurement_repository,
    interval_seconds=settings.workflow_monitor_seconds,
    enabled=settings.workflow_monitor_enabled,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    report_worker.start()
    completion_worker.start()
    workflow_monitor.start()
    try:
        yield
    finally:
        workflow_monitor.stop()
        completion_worker.stop()
        report_worker.stop()


app = FastAPI(
    title="SEEFIX Agent Service",
    version="4.0.0",
    description=(
        "Local/on-premises SEEFIX Agent service for inspection, deterministic prioritization/knowledge, "
        "Maintenance Request drafting, Maintenance Review support, conditional Procurement assistance, "
        "route-aware Work Order assistance, and completion assistance."
    ),
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


def require_agent_api_key(
    x_seefix_agent_key: Annotated[str | None, Header(alias="X-SEEFIX-AGENT-KEY")] = None,
) -> None:
    if not settings.require_api_key:
        return
    if not hmac.compare_digest(x_seefix_agent_key or "", settings.agent_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized agent request.")


@app.get("/", include_in_schema=False)
def index():
    if INDEX_FILE.is_file():
        return FileResponse(INDEX_FILE)
    return {
        "service": "seefix-agents",
        "architecture": "one-agent-service-multiple-specialized-workflows",
        "status": "ok",
    }


@app.get("/health")
def health() -> dict:
    database_ok = database.health()
    ollama = agent.provider.ping()
    components = {
        "database": {"reachable": database_ok},
        "ollama": {
            "reachable": bool(ollama.get("reachable")),
            "modelAvailable": bool(ollama.get("model_available")),
            "model": agent.provider.model_id,
        },
        "reportWorker": {"running": report_worker.is_running},
        "completionWorker": {"running": completion_worker.is_running},
        "workflowMonitor": {
            "enabled": settings.workflow_monitor_enabled,
            "running": workflow_monitor.is_running,
        },
    }
    required_ok = (
        database_ok
        and bool(ollama.get("reachable"))
        and bool(ollama.get("model_available"))
        and report_worker.is_running
        and completion_worker.is_running
        and (not settings.workflow_monitor_enabled or workflow_monitor.is_running)
    )
    return {"status": "ok" if required_ok else "degraded", "components": components}


@app.post(
    "/api/reports/{report_id}/process",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_agent_api_key)],
)
def process_report(report_id: UUID) -> dict:
    try:
        current = report_repository.get_state(report_id)
        if current is None:
            raise LookupError("Report was not found.")
        agent_status = str(current["AgentStatus"])
        # A repeat HTTP trigger must never reopen CANCELLED or routed work.
        # PostgreSQL claim functions also enforce this under a row lock.
        if current["Status"] != "SUBMITTED":
            return {
                "accepted": False,
                "reportId": str(report_id),
                "reportNo": current["ReportNo"],
                "businessStatus": current["Status"],
                "agentStatus": agent_status,
                "message": "Report is no longer eligible for initial Agent processing.",
            }
        if agent_status == "COMPLETED":
            return {
                "accepted": False,
                "reportId": str(report_id),
                "reportNo": current["ReportNo"],
                "agentStatus": agent_status,
                "message": "Report assessment is already completed.",
            }
        if agent_status == "PROCESSING":
            return {
                "accepted": False,
                "reportId": str(report_id),
                "reportNo": current["ReportNo"],
                "agentStatus": agent_status,
                "message": "Report assessment is already processing.",
            }
        claimed = report_worker.submit(report_id)
        updated = report_repository.get_state(report_id) or current
        updated_status = str(updated["AgentStatus"])

        if claimed is None:
            # A background poller may have claimed the Report between the first
            # state read and this request. Return the actual durable state.
            return {
                "accepted": False,
                "reportId": str(report_id),
                "reportNo": updated["ReportNo"],
                "agentStatus": updated_status,
                "attemptCount": updated["AgentAttemptCount"],
                "message": (
                    "Report assessment is processing."
                    if updated_status == "PROCESSING"
                    else "Report was not claimed because it is no longer eligible or another worker owns it."
                ),
            }

        return {
            "accepted": True,
            "reportId": str(report_id),
            "reportNo": updated["ReportNo"],
            "agentStatus": updated_status,
            "attemptCount": updated["AgentAttemptCount"],
            "message": "Report claimed and queued for local Agent processing.",
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get(
    "/api/reports/{report_id}/status",
    dependencies=[Depends(require_agent_api_key)],
)
def report_agent_status(report_id: UUID) -> dict:
    try:
        current = report_repository.get_state(report_id)
        if current is None:
            raise LookupError("Report was not found.")
        return {
            "reportId": str(current["Id"]),
            "reportNo": current["ReportNo"],
            "businessStatus": current["Status"],
            "agentStatus": current["AgentStatus"],
            "attemptCount": current["AgentAttemptCount"],
            "agentStartedAt": current["AgentStartedAt"].isoformat() if current["AgentStartedAt"] else None,
            "agentCompletedAt": current["AgentCompletedAt"].isoformat() if current["AgentCompletedAt"] else None,
            "lastError": current["AgentLastError"],
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post(
    "/api/reports/{report_id}/maintenance-request/generate",
    dependencies=[Depends(require_agent_api_key)],
)
def generate_maintenance_request(report_id: UUID) -> dict:
    try:
        result = report_repository.get_completed_result(report_id)
        if result is None or result.assessment is None:
            raise ValueError("A completed Facility Issue assessment is required.")
        bundle = report_repository.get_bundle(report_id)
        context = report_repository.get_assessment_context(
            bundle=bundle,
            category=result.assessment.category,
        )
        saved = maintenance_workflow.generate_for_report(
            report_id=report_id,
            result=result,
            context=context,
        )
        return {
            "maintenanceRequestId": str(saved["Id"]),
            "requestNo": saved["RequestNo"],
            "status": saved["Status"],
            "updated": saved["updated"],
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post(
    "/api/maintenance-requests/{maintenance_request_id}/procurement-package/preview",
    dependencies=[Depends(require_agent_api_key)],
)
def procurement_package_preview(maintenance_request_id: UUID) -> dict:
    try:
        return procurement_repository.build_package_preview(maintenance_request_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post(
    "/api/procurement/{handoff_id}/clarifications/{clarification_id}/draft",
    dependencies=[Depends(require_agent_api_key)],
)
def procurement_clarification_draft(handoff_id: UUID, clarification_id: UUID) -> dict:
    try:
        return clarification_service.generate_draft(
            handoff_id=handoff_id,
            clarification_id=clarification_id,
        ).model_dump(mode="json")
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post(
    "/api/maintenance-reviews/{maintenance_review_id}/work-order/preview",
    dependencies=[Depends(require_agent_api_key)],
)
def internal_work_order_preview(maintenance_review_id: UUID) -> dict:
    """Preview the direct INTERNAL Work Order after human Maintenance Review."""
    try:
        return work_order_repository.build_draft_preview_for_review(
            maintenance_review_id
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post(
    "/api/procurement-outcomes/{procurement_outcome_id}/work-order/preview",
    dependencies=[Depends(require_agent_api_key)],
)
def procurement_work_order_preview(procurement_outcome_id: UUID) -> dict:
    """Preview the PROCUREMENT Work Order after the final outcome is recorded."""
    try:
        return work_order_repository.build_draft_preview_for_outcome(
            procurement_outcome_id
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post(
    "/api/work-orders/{work_order_id}/review",
    dependencies=[Depends(require_agent_api_key)],
)
def review_work_order(work_order_id: UUID) -> dict:
    try:
        return work_order_repository.review_work_order(work_order_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post(
    "/api/work-orders/{work_order_id}/completion/process",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_agent_api_key)],
)
def process_completion(work_order_id: UUID) -> dict:
    try:
        state = work_order_repository.request_completion_processing(work_order_id)
        completion_worker.wake()
        return {
            "accepted": state["CompletionAgentStatus"] in {"PENDING", "PROCESSING"},
            "workOrderId": str(state["Id"]),
            "workOrderNo": state["WorkOrderNo"],
            "workOrderStatus": state["Status"],
            "completionAgentStatus": state["CompletionAgentStatus"],
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get(
    "/api/work-orders/{work_order_id}/completion/status",
    dependencies=[Depends(require_agent_api_key)],
)
def completion_status(work_order_id: UUID) -> dict:
    try:
        state = work_order_repository.get_completion_status(work_order_id)
        if state is None:
            raise LookupError("Work Order was not found.")
        return {
            "workOrderId": str(state["Id"]),
            "workOrderNo": state["WorkOrderNo"],
            "workOrderStatus": state["Status"],
            "completionAgentStatus": state["CompletionAgentStatus"],
            "attemptCount": state["CompletionAgentAttemptCount"],
            "startedAt": state["CompletionAgentStartedAt"].isoformat() if state["CompletionAgentStartedAt"] else None,
            "completedAt": state["CompletionAgentCompletedAt"].isoformat() if state["CompletionAgentCompletedAt"] else None,
            "visualResult": state["CompletionVisualResult"],
            "lastError": state["CompletionAgentLastError"],
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post(
    "/api/monitor/run",
    dependencies=[Depends(require_agent_api_key)],
)
def run_monitor_now() -> dict:
    try:
        return {"createdActionItems": workflow_monitor.run_once()}
    except Exception as exc:
        raise _http_error(exc) from exc
