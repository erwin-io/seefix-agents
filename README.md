# SEEFIX Agent Service

SEEFIX is a local/on-premises AI-assisted maintenance prioritization and workflow-assistance service. This revision targets the redesigned 2026-10-08 PostgreSQL `dbo` contract in which the Maintenance Department reviews the prioritized queue and routes each actionable maintenance request either directly to internal maintenance or to Procurement.

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Git | any | to clone the repo |
| Python | 3.12 (`py -3.12 --version`) | python.org installer on Windows, with the `py` launcher |
| Ollama | current | `winget install Ollama.Ollama` (Windows) or ollama.com |
| Vision model | `qwen3-vl:2b-instruct-q4_K_M` (~2 GB) | pulled in step 2; a GPU helps but isn't required |
| SEEFIX database | PostgreSQL / Neon | the **same** database as seefix-api; set it up first ([seefix-api README](https://github.com/seefixmanagement/seefix-api#setup-step-by-step)) |

## Setup (step by step, Windows)

1. **Clone and create the virtual environment**
   ```powershell
   git clone https://github.com/seefixmanagement/seefix-agents.git
   cd seefix-agents
   py -3.12 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
   On Linux/macOS: `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt`.
2. **Get the model** (Ollama must be running; the installer starts it):
   ```powershell
   ollama pull qwen3-vl:2b-instruct-q4_K_M
   ```
3. **Configure `.env`**
   ```powershell
   copy .env.example .env
   ```
   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the seefix-api database. For Neon: the pooled host with `?sslmode=require` |
   | `SEEFIX_DATABASE_SSLMODE` | `prefer` for local PostgreSQL, `require` for Neon |
   | `SEEFIX_AGENT_API_KEY` | a new random value (`python -c "import secrets; print(secrets.token_urlsafe(48))"`); seefix-api's `LOCAL_AGENT_SECRET` must be identical. Never reuse the sample value |
   | `SEEFIX_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` |

   The other settings have working defaults.
4. **Check the database contract** (read-only):
   ```powershell
   .venv\Scripts\python.exe scriptserify_database_contract.py
   ```
5. **Start**
   ```powershell
   run_windows.bat
   ```
   or `.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
6. **Check** `http://127.0.0.1:8000/health`: `"status":"ok"`, database and ollama `reachable:true`,
   `modelAvailable:true`. The Agent now claims pending reports and completions from the database every ~3 s.
7. **Connect seefix-api**: set its `LOCAL_AGENT_URL` (`http://127.0.0.1:8000` on the same machine) and
   `LOCAL_AGENT_SECRET`, then restart the API. Its `/health` should show `agent.reachable:true`.

For an always-on AI host (Windows services with NSSM, and a public HTTPS address with Tailscale Funnel so a hosted
API can reach the Agent), follow
[seefix-api docs/SYSTEM_SETUP.md § 4](https://github.com/seefixmanagement/seefix-api/blob/main/docs/SYSTEM_SETUP.md#4-ai-host-windows--machine).

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

A result or failure is written only by the attempt that still owns the claim (`CompletionAgentAttemptCount`) and only
while the Work Order is still `COMPLETION_SUBMITTED`. A stale or requeued attempt, or one that finishes after the
supervisor decided, is discarded.

### Role-scoped assistant (Web Admin V2)

`POST /api/assistant/respond` phrases a short answer from facts that seefix-api's own RBAC-checked tools returned.
The Agent has no tools, no database access and no actions on this path.

- **Request:** `role` (five internal roles only), `question`, and up to 40 `facts` (`id`, `kind`, `label`, `data` up to
  4 KB, optional in-app `navigation` path).
- **Response:** `status` (`ANSWERED`, `NO_FACTS` or `FALLBACK`), `answer`, `citedFactIds`, `navigation`,
  `fallbackReason`.
- **Checks on the model's output:**
  - Citations must be supplied fact ids.
  - Links come only from the API's facts.
  - URLs in the model's text are removed.
  - Any extra field (action, SQL, tool call) produces a deterministic `FALLBACK`.
- **The model is never required.** When it is down, busy or invalid, the answer is a plain list of the records found.

Every Ollama call goes through one `InferenceGate`:

- Report and completion jobs always take the next free slot.
- A chat runs only when no job is waiting. It gives up after `SEEFIX_ASSISTANT_QUEUE_WAIT_SECONDS` and is limited to
  `SEEFIX_ASSISTANT_TIMEOUT_SECONDS`.
- At most `SEEFIX_ASSISTANT_MAX_WAITING` chats can wait at once.

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

POST /api/assistant/respond

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

## PostgreSQL connectivity troubleshooting

`[COMPLETION WORKER]` or `[AGENT WORKER]` logs `Unable to connect to PostgreSQL: connection timeout expired`. What to check:

1. `GET http://127.0.0.1:8000/health` shows `components.database.reachable`. When it is `false`, `status` is `degraded`. If `ollama.reachable` is still `true`, the problem is the database, not the model. An HTTP `202` from `POST /api/reports/{id}/process` only means the request was accepted, not that analysis finished.
2. Find the host and port in `DATABASE_URL` **without printing it**. A database on this PC (`127.0.0.1`/`localhost`) does not depend on Wi-Fi: check the PostgreSQL process with `Test-NetConnection 127.0.0.1 -Port 5432`. For a hosted database, run `Resolve-DnsName <host>` and `Test-NetConnection <host> -Port <port>`. After a network switch, also check VPN, proxy, firewall and the provider's IP allowlist, since your public IP changes with the network. Use a DNS name, not a temporary IP.
3. No restart and no `.env` change are needed once the database is reachable again. Both workers stay alive during an outage and retry with backoff (poll interval doubling to 60 s). They print one traceback, then a line at 2, 4, 8… consecutive failures, then `Recovered after N failed attempt(s).` They resume claiming pending work automatically. A polling timeout never marks a Report or Work Order `FAILED`.

## Tests

```powershell
.venv\Scripts\python.exe -m compileall app
.venv\Scripts\python.exe -m unittest discover -s tests
```

The database contract tests are skipped by default. Run them only against a known SEEFIX test database by setting
`SEEFIX_RUN_DB_TESTS=true`.

The queue-fence tests (`tests/test_queue_fences_db.py`) run the real claim and write SQL and triggers inside one
transaction that is rolled back. They run only when `SEEFIX_TEST_DATABASE_URL` points at a disposable localhost copy,
for example `postgresql://postgres:<password>@127.0.0.1/seefix_v2_dev`.
