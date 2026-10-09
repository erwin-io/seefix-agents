from __future__ import annotations

import os
import sys

from collections.abc import (
    Iterable,
)

import psycopg

from dotenv import load_dotenv
from psycopg.rows import dict_row


load_dotenv()


SCHEMA = "dbo"


EXPECTED_TABLES = {
    "Users",
    "SystemSettings",

    "Buildings",
    "FacilityLocations",

    "DamageCategories",
    "Skills",
    "CategorySkillRequirements",

    "Materials",
    "CategoryMaterialReferences",

    "Reports",
    "ReportImages",
    "ReportAssessmentHistory",
    "ReportDuplicateCandidates",
    "ReportVerifications",
    "ReportStatusHistory",

    "MaintenanceRequests",
    "MaintenanceRequestSkills",
    "MaintenanceRequestMaterials",
    "MaintenanceRequestRevisions",
    "MaintenanceRequestStatusHistory",

    "MaintenanceReviews",
    "MaintenanceReviewStatusHistory",

    "ProcurementHandoffs",
    "ProcurementClarifications",
    "ProcurementDocuments",
    "ProcurementOutcomes",
    "ProcurementHandoffStatusHistory",

    "WorkOrders",
    "WorkOrderAssignments",
    "WorkOrderPeople",
    "WorkOrderMaterials",
    "WorkOrderImages",
    "WorkOrderUpdates",
    "WorkOrderStatusHistory",

    "WorkflowActionItems",
    "Notifications",
    "OutboxEvents",
    "AuditLogs",
}


EXPECTED_VIEWS = {
    "v_ReportEffectiveClassification",
    "v_ReportPriorityLive",
    "v_PendingAgentReports",

    "v_MaintenanceReviewQueue",
    "v_MaintenanceActionCenter",

    "v_ProcurementInbox",
    "v_WorkerInbox",

    "v_CompletedWorkOrderKnowledge",
    "v_ReportTimeline",
}


EXPECTED_QUEUE_FUNCTIONS = {
    "ClaimReportForAgent",
    "ClaimNextPendingReport",
    "RequeueStaleAgentReports",

    "ClaimNextPendingCompletionWorkOrder",
    "RequeueStaleCompletionAgentWorkOrders",
}


CRITICAL_COLUMNS = {
    "DamageCategories": {
        "RequiresMaintenanceReview",
    },

    "Reports": {
        "Status",
        "AgentStatus",
        "FinalCategory",
        "FinalUrgency",
        "PriorityScore",
    },

    "MaintenanceRequests": {
        "ReportId",
        "Status",

        "EffectiveCategory",
        "EffectiveUrgency",

        "RequiredService",
        "RequiredCapability",

        "AuthorizedBy",
        "AuthorizedAt",
    },

    "MaintenanceReviews": {
        "ReportId",
        "MaintenanceRequestId",

        "Status",
        "Decision",

        "FinalCategory",
        "FinalUrgency",

        "ReviewedBy",
        "ReviewedAt",
    },

    "ProcurementHandoffs": {
        "MaintenanceRequestId",
        "MaintenanceReviewId",
        "Status",
    },

    "WorkOrders": {
        "ReportId",
        "MaintenanceRequestId",
        "MaintenanceReviewId",

        "ProcurementOutcomeId",

        "RouteType",
        "Status",
        "ExecutionType",

        "AssignedPartyName",
        "ResponsibleLeadUserId",

        "AssignedBy",
        "AssignedAt",

        "CompletionAgentStatus",
    },

    "WorkOrderAssignments": {
        "WorkOrderId",

        "AssignedPartyName",
        "ResponsibleLeadUserId",

        "AssignedBy",
        "AssignedAt",
    },
}


def _missing(
    expected: Iterable[str],
    actual: set[str],
) -> list[str]:
    return sorted(
        set(expected)
        -
        actual
    )


def main() -> int:
    database_url = (
        os.getenv(
            "DATABASE_URL"
        )
        or ""
    ).strip()

    if not database_url:
        print(
            "DATABASE_URL is required.",
            file=sys.stderr,
        )

        return 2

    problems: list[str] = []

    try:
        with psycopg.connect(
            database_url,
            row_factory=dict_row,
        ) as conn:

            with conn.cursor() as cur:

                # =================================================
                # TABLES + VIEWS
                # =================================================

                cur.execute(
                    """
                    SELECT
                        c.relname AS name,
                        c.relkind AS kind
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n
                      ON n.oid = c.relnamespace
                    WHERE n.nspname = %s
                      AND c.relkind IN
                      (
                          'r',
                          'p',
                          'v',
                          'm'
                      )
                    """,
                    (
                        SCHEMA,
                    ),
                )

                relations = (
                    cur.fetchall()
                )

                tables = {
                    str(
                        row["name"]
                    )
                    for row
                    in relations

                    if row["kind"]
                    in {
                        "r",
                        "p",
                    }
                }

                views = {
                    str(
                        row["name"]
                    )
                    for row
                    in relations

                    if row["kind"]
                    in {
                        "v",
                        "m",
                    }
                }

                for name in _missing(
                    EXPECTED_TABLES,
                    tables,
                ):
                    problems.append(
                        (
                            f"table "
                            f"{SCHEMA}.{name}"
                        )
                    )

                for name in _missing(
                    EXPECTED_VIEWS,
                    views,
                ):
                    problems.append(
                        (
                            f"view "
                            f"{SCHEMA}.{name}"
                        )
                    )

                if (
                    "v_PpoActionCenter"
                    in views
                ):
                    problems.append(
                        (
                            "legacy view "
                            "dbo.v_PpoActionCenter "
                            "should not exist in the "
                            "2026-10-08 contract"
                        )
                    )

                # =================================================
                # AGENT QUEUE FUNCTIONS
                # =================================================

                cur.execute(
                    """
                    SELECT DISTINCT
                        p.proname AS name
                    FROM pg_catalog.pg_proc p
                    JOIN pg_catalog.pg_namespace n
                      ON n.oid = p.pronamespace
                    WHERE n.nspname = %s
                    """,
                    (
                        SCHEMA,
                    ),
                )

                functions = {
                    str(
                        row["name"]
                    )
                    for row
                    in cur.fetchall()
                }

                for name in _missing(
                    EXPECTED_QUEUE_FUNCTIONS,
                    functions,
                ):
                    problems.append(
                        (
                            f"function "
                            f"{SCHEMA}.{name}"
                        )
                    )

                # =================================================
                # CRITICAL COLUMNS
                # =================================================

                cur.execute(
                    """
                    SELECT
                        table_name,
                        column_name,
                        is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    """,
                    (
                        SCHEMA,
                    ),
                )

                column_rows = (
                    cur.fetchall()
                )

                columns_by_table: dict[
                    str,
                    set[str],
                ] = {}

                nullable_by_column: dict[
                    tuple[str, str],
                    str,
                ] = {}

                for row in column_rows:
                    table_name = str(
                        row["table_name"]
                    )

                    column_name = str(
                        row["column_name"]
                    )

                    columns_by_table.setdefault(
                        table_name,
                        set(),
                    ).add(
                        column_name
                    )

                    nullable_by_column[
                        (
                            table_name,
                            column_name,
                        )
                    ] = str(
                        row["is_nullable"]
                    )

                for (
                    table_name,
                    expected_columns,
                ) in (
                    CRITICAL_COLUMNS
                    .items()
                ):

                    actual_columns = (
                        columns_by_table
                        .get(
                            table_name,
                            set(),
                        )
                    )

                    for (
                        column_name
                    ) in _missing(
                        expected_columns,
                        actual_columns,
                    ):
                        problems.append(
                            (
                                f"column "
                                f"{SCHEMA}."
                                f"{table_name}."
                                f"{column_name}"
                            )
                        )

                # =================================================
                # INTERNAL WORK ORDER CONTRACT
                # =================================================

                procurement_outcome_nullable = (
                    nullable_by_column
                    .get(
                        (
                            "WorkOrders",
                            "ProcurementOutcomeId",
                        )
                    )
                )

                if (
                    procurement_outcome_nullable
                    !=
                    "YES"
                ):
                    problems.append(
                        (
                            "column "
                            "dbo.WorkOrders."
                            "ProcurementOutcomeId "
                            "must be nullable"
                        )
                    )

    except Exception as exc:
        print(
            (
                "Database contract "
                "verification failed "
                f"to run: {exc}"
            ),
            file=sys.stderr,
        )

        return 2

    if problems:
        print(
            (
                "Canonical SEEFIX "
                "2026-10-08 database "
                "contract is NOT "
                "fully available:"
            )
        )

        for problem in problems:
            print(
                f" - {problem}"
            )

        return 1

    print(
        (
            "Canonical SEEFIX "
            "2026-10-08 database "
            "contract verified "
            "(read-only object/"
            "column check)."
        )
    )

    print(
        (
            f"Verified "
            f"{len(EXPECTED_TABLES)} tables, "
            f"{len(EXPECTED_VIEWS)} views, "
            f"{len(EXPECTED_QUEUE_FUNCTIONS)} "
            "Agent queue functions, "
            "and critical route/review columns."
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )