# SEEFIX Agent Service

SEEFIX is a local/on-premises AI-assisted maintenance operations Agent. This implementation follows the final 2026-09-16 architecture and canonical PostgreSQL `dbo` contract.

## Core rule

**AI prepares, analyzes, normalizes by explicit policy, retrieves bounded knowledge, drafts, compares, warns, and monitors. Human-authorized roles still make the decisions that create maintenance responsibility or close work.**

PPO Staff performs **Verify & Request Maintenance**. PPO Head handles Procurement clarification, confirms the final Work Order, and confirms completion or requires rework. Procurement bidding/canvassing/quotation comparison/provider selection remain outside SEEFIX.

## Architecture

```text
Reporter / PPO / Procurement UI
            |
            v
      Node.js / Express
            |
     PostgreSQL + Cloudinary
            ^
            |
  targeted reads / writes
            |
      Python / FastAPI
       SEEFIX Agent
            |
       local Ollama
 qwen3-vl:2b-instruct-q4_K_M
```

One Agent service contains specialized workflows; it is not an autonomous multi-agent supervisor network.

## Implemented workflows

### Initial Report Agent

```text
claim Report
-> download/validate primary image
-> Qwen inspection
-> deterministic category/urgency policy
-> targeted category/skills/material/history knowledge
-> FinalCategory-aware recurrence
-> verification/age context
-> duplicate candidates
-> deterministic priority
-> persist canonical Report fields
-> create/update Maintenance Request DRAFT
-> OutboxEvents
```

### Procurement assistance

The Agent can prepare an authorized Maintenance Request package preview, bounded snapshot/email text, draft a response to an OPEN Procurement clarification, and create deterministic delay/exception action items. It does not implement bidding, provider ranking, contractor selection, or Procurement approval.

### Work Order assistance

The Agent can build a final Work Order preview from a Procurement Outcome, run deterministic readiness checks, and calculate estimate-vs-plan warnings. It does not confirm or start the Work Order.

### Completion assistance

A separate `WorkOrders.CompletionAgentStatus` queue retrieves the original Report image and completion images, runs preliminary before/after visual assistance, compares planned/actual metadata, and persists completion assessment fields. It never sets `WorkOrders.Status = COMPLETED`.

## Database

Use the canonical `SEEFIX_dbo_full_schema_20260916.sql` schema. No new database changes are required by this implementation.

If your current database predates that canonical schema, migrate/rebuild it first. This Agent expects the canonical 33-table design and the canonical functions/views, including:

- `dbo.ClaimReportForAgent`
- `dbo.ClaimNextPendingReport`
- `dbo.RequeueStaleAgentReports`
- `dbo.ClaimNextPendingCompletionWorkOrder`
- `dbo.RequeueStaleCompletionAgentWorkOrders`
- `dbo.v_ReportPriorityLive`
- `dbo.v_CompletedWorkOrderKnowledge`
- `dbo.v_ProcurementInbox`
- `dbo.v_PpoActionCenter`

Read-only object verification:

```cmd
python scripts\verify_database_contract.py
```

## Local setup (Windows)

```cmd
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` with your PostgreSQL connection and a newly generated Agent API secret:

```cmd
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Make sure Ollama is running locally and the configured model is available. Then:

```cmd
run_windows.bat
```

or:

```cmd
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Keep Ollama bound to localhost. Expose only FastAPI if remote Node access is required.

## Protected endpoints

Send `X-SEEFIX-AGENT-KEY` when API-key protection is enabled.

```text
GET  /health
POST /api/reports/{report_id}/process
GET  /api/reports/{report_id}/status
POST /api/reports/{report_id}/maintenance-request/generate
POST /api/maintenance-requests/{maintenance_request_id}/procurement-package/preview
POST /api/procurement/{handoff_id}/clarifications/{clarification_id}/draft
POST /api/procurement-outcomes/{procurement_outcome_id}/work-order/preview
POST /api/work-orders/{work_order_id}/review
POST /api/work-orders/{work_order_id}/completion/process
GET  /api/work-orders/{work_order_id}/completion/status
POST /api/monitor/run
```

There are intentionally no Agent endpoints for PPO verification/authorization, Procurement approval, provider selection, Work Order confirmation/start, or final completion.

## Deterministic priority

```text
Low      = 25
Medium   = 50
High     = 75
Critical = 100
recurrence threshold reached = +15
verification threshold reached = +10
age > 3 days = +10
age > 7 days = +20
public access exposure = +10
```

There is no hard total cap. Scoring uses policy-normalized urgency. `Reports.PriorityScore` is the assessment-time snapshot; dashboards can use `dbo.v_ReportPriorityLive` for live age-aware ranking.

## Image security

Remote Report/Work Order images require HTTPS, an approved host, no credentials in the URL, approved redirect destinations, supported image MIME type, configured size limits, and Pillow validation. Default approved host: `res.cloudinary.com`.

## Tests

```cmd
python -m compileall app tests
python -m unittest discover -s tests -v
```

The unit suite covers category/scope contracts, urgency policy, priority boundaries, URL security, readiness/variance, fallback Maintenance Request generation, and static architecture guards. Database integration should be run only against a known SEEFIX test database; do not reset an unknown populated database.

## Environment/security

The distributed project intentionally contains `.env.example`, not `.env`. Do not commit secrets. Any password/API secret previously included in an exported project file should be rotated before external deployment or sharing.

See `docs/IMPLEMENTATION_STATUS.md` for the implementation/validation summary.
