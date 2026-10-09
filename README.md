# SEEFIX Agent Service

SEEFIX is a local/on-premises AI-assisted maintenance prioritization and workflow-assistance service. This revision targets the redesigned 2026-10-08 PostgreSQL `dbo` contract in which the Maintenance Department reviews the prioritized queue and routes each actionable maintenance request either directly to internal maintenance or to Procurement.

## Core rule

**AI assesses, prioritizes, retrieves bounded knowledge, drafts, compares, warns, and monitors. Human-authorized roles still make routing, assignment, Procurement, and final completion decisions.**

The Agent does not decide `INTERNAL` versus `PROCUREMENT`, does not assign workers, does not select contractors/providers, and does not close Work Orders.

## Architecture

```text
Reporter / Maintenance / Procurement interfaces
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

## Revised workflow

```text
Report
  -> AI inspection
  -> deterministic policy + priority
  -> PENDING_REVIEW / Maintenance Review queue
  -> Maintenance Request DRAFT
  -> human Maintenance Review
       -> INTERNAL
            -> Work Order PENDING_ASSIGNMENT
            -> human assignment
            -> execution
       -> PROCUREMENT
            -> Procurement handoff
            -> existing UC Procurement process
            -> Procurement Outcome
            -> Work Order PENDING_ASSIGNMENT
            -> human dispatch/assignment
            -> execution
  -> completion evidence
  -> AI completion assistance
  -> Maintenance Supervisor complete/rework
```

## Implemented Agent responsibilities

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
-> persist ReportAssessmentHistory attempt
-> create/update Maintenance Request DRAFT
-> OutboxEvents / Reporter assessment notification
```

The database automatically advances a successfully assessed `SUBMITTED` Report into `PENDING_REVIEW`, where it appears in the Maintenance Department priority/triage queue.

### Maintenance Request drafting

The Agent prepares technical content only:

- required service
- required capability
- scope of work
- safety requirements
- preliminary skills
- preliminary materials
- preliminary labor/manpower/duration estimates

It intentionally does **not** choose `INTERNAL` versus `PROCUREMENT` and does not choose a worker, maintenance team, contractor, bidder, vendor, or provider.

### Procurement assistance

Procurement assistance is available only after a completed human Maintenance Review with decision `PROCUREMENT`.

The Agent can:

- prepare the Procurement package preview
- freeze bounded technical facts for Node to submit
- draft a response to an OPEN Procurement clarification
- monitor deterministic delay/exception conditions

SEEFIX does not implement bidding, canvassing, quotation comparison, provider ranking, contractor selection, award decisions, or Procurement approval.

### Route-aware Work Order assistance

Two Work Order sources are supported:

```text
INTERNAL:
Maintenance Review -> Work Order preview

PROCUREMENT:
Maintenance Review -> Procurement -> Outcome -> Work Order preview
```

Both begin as `PENDING_ASSIGNMENT`. There is no second Work Order confirmation gate in the redesigned database.

The Agent performs deterministic source/readiness checks and estimate-vs-plan variance checks. Human Maintenance staff perform assignment/dispatch through the Node API.

### Completion assistance

A separate `WorkOrders.CompletionAgentStatus` queue retrieves the original Report image and completion images, performs before/after visual assistance, compares planned/actual metadata, and persists the structured completion assessment.

The Agent never sets `WorkOrders.Status = COMPLETED`. `needs_maintenance_supervisor_review` is deterministically forced to `true` because final completion/rework remains a Maintenance Supervisor responsibility.

## Database contract

Target the redesigned SEEFIX database schema for 2026-10-08.

Major Agent-facing objects include:

- `dbo.Reports`
- `dbo.ReportImages`
- `dbo.ReportAssessmentHistory`
- `dbo.MaintenanceRequests`
- `dbo.MaintenanceReviews`
- `dbo.ProcurementHandoffs`
- `dbo.ProcurementClarifications`
- `dbo.ProcurementOutcomes`
- `dbo.WorkOrders`
- `dbo.WorkOrderMaterials`
- `dbo.WorkOrderImages`
- `dbo.WorkflowActionItems`
- `dbo.Notifications`
- `dbo.OutboxEvents`

Queue functions remain:

- `dbo.ClaimReportForAgent`
- `dbo.ClaimNextPendingReport`
- `dbo.RequeueStaleAgentReports`
- `dbo.ClaimNextPendingCompletionWorkOrder`
- `dbo.RequeueStaleCompletionAgentWorkOrders`

Important views include:

- `dbo.v_ReportPriorityLive`
- `dbo.v_MaintenanceReviewQueue`
- `dbo.v_MaintenanceActionCenter`
- `dbo.v_ProcurementInbox`
- `dbo.v_WorkerInbox`
- `dbo.v_CompletedWorkOrderKnowledge`
- `dbo.v_ReportTimeline`

## Protected endpoints

Send `X-SEEFIX-AGENT-KEY` when API-key protection is enabled.

```text
GET  /health

POST /api/reports/{report_id}/process
GET  /api/reports/{report_id}/status
POST /api/reports/{report_id}/maintenance-request/generate

POST /api/maintenance-requests/{maintenance_request_id}/procurement-package/preview
POST /api/procurement/{handoff_id}/clarifications/{clarification_id}/draft

POST /api/maintenance-reviews/{maintenance_review_id}/work-order/preview
POST /api/procurement-outcomes/{procurement_outcome_id}/work-order/preview
POST /api/work-orders/{work_order_id}/review

POST /api/work-orders/{work_order_id}/completion/process
GET  /api/work-orders/{work_order_id}/completion/status

POST /api/monitor/run
```

There are intentionally no Agent endpoints for:

- completing a Maintenance Review
- deciding INTERNAL versus PROCUREMENT
- assigning a worker
- approving Procurement
- selecting a provider
- starting a Work Order
- final completion/rework authorization

Those are business-authority operations owned by the Node API and authenticated human roles.

## Deterministic priority

```text
Low      = 25
Medium   = 50
High     = 75
Critical = 100

recurrence threshold reached   = +15
verification threshold reached = +10
age > 3 days                   = +10
age > 7 days                   = +20
public access exposure         = +10
```

There is no hard total cap. Scoring uses policy-normalized urgency. `Reports.PriorityScore` is the assessment-time audit snapshot; the Maintenance Department queue should use `dbo.v_ReportPriorityLive` for live age-aware ranking.

## Human-role terminology

The redesigned database uses:

```text
REPORTER
MAINTENANCE_STAFF
MAINTENANCE_SUPERVISOR
PROCUREMENT
WORKER
ADMIN
```

The old `PPO_STAFF`, `PPO_HEAD`, and `STAFF` role names are no longer part of the revised database contract.

## Image security

Remote Report/Work Order images require HTTPS, an approved host, no credentials in the URL, approved redirect destinations, supported image MIME type, configured size limits, and Pillow validation. Default approved host: `res.cloudinary.com`.

## Validation

At minimum run:

```cmd
python -m compileall app
```

Then run the project's current unit/integration tests after updating any test assertions that still reference the old PPO role names, `PENDING_CONFIRMATION`, `CONFIRMED`, or `needs_ppo_review`.
