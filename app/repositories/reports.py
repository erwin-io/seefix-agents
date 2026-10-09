from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..database import Database
from ..schemas import (
    AssessmentDatabaseContext,
    CategoryReference,
    DuplicateCandidate,
    FacilityCategory,
    HistoricalDurationStats,
    HistoricalManpowerStats,
    HistoricalMaterialStat,
    InspectionResult,
    MaterialReference,
    SkillReference,
)


@dataclass(frozen=True)
class ClaimedReport:
    id: UUID
    report_no: str
    attempt_count: int


@dataclass(frozen=True)
class ReportImageReference:
    id: UUID
    public_id: str
    secure_url: str
    is_primary: bool


@dataclass(frozen=True)
class ReportBundle:
    id: UUID
    report_no: str
    created_at: datetime
    description: str | None
    notes: str | None
    building: str | None
    floor: str | None
    room_or_area: str | None
    gps_lat: float | None
    gps_lng: float | None
    verification_count: int
    images: tuple[ReportImageReference, ...]

    def prompt_context(self) -> dict:
        values = {
            "reportNo": self.report_no,
            "description": self.description,
            "notes": self.notes,
            "building": self.building,
            "floor": self.floor,
            "roomOrArea": self.room_or_area,
            "gpsLat": self.gps_lat,
            "gpsLng": self.gps_lng,
        }
        return {key: value for key, value in values.items() if value not in (None, "")}


class ReportRepository:
    def __init__(
        self,
        *,
        database: Database,
        worker_name: str,
        recurrence_threshold: int,
        recurrence_lookback_days: int,
        verification_threshold: int,
        duplicate_lookback_days: int,
    ) -> None:
        self.database = database
        self.worker_name = worker_name
        self.recurrence_threshold = recurrence_threshold
        self.recurrence_lookback_days = recurrence_lookback_days
        self.verification_threshold = verification_threshold
        self.duplicate_lookback_days = duplicate_lookback_days

    def requeue_stale(self, stale_minutes: int) -> int:
        with self.database.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT "dbo"."RequeueStaleAgentReports"(%s)', (stale_minutes,))
            row = cur.fetchone()
            return int(row[0] if row else 0)

    def claim_specific(self, report_id: UUID) -> ClaimedReport | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT * FROM "dbo"."ClaimReportForAgent"(%s, %s)',
                (report_id, self.worker_name),
            )
            row = cur.fetchone()
            return self._claimed(row) if row else None

    def claim_next_pending(self) -> ClaimedReport | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT * FROM "dbo"."ClaimNextPendingReport"(%s)',
                (self.worker_name,),
            )
            row = cur.fetchone()
            return self._claimed(row) if row else None

    @staticmethod
    def _claimed(row) -> ClaimedReport:
        return ClaimedReport(
            id=row["Id"],
            report_no=str(row["ReportNo"]),
            attempt_count=int(row["AgentAttemptCount"]),
        )

    def get_state(self, report_id: UUID) -> dict | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT "Id", "ReportNo", "Status", "AgentStatus", "AgentAttemptCount",
                       "AgentLastError", "AgentStartedAt", "AgentCompletedAt"
                FROM "dbo"."Reports" WHERE "Id" = %s
                ''',
                (report_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_completed_result(self, report_id: UUID) -> InspectionResult | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT "AssessmentJson" FROM "dbo"."Reports" WHERE "Id"=%s AND "AgentStatus"=\'COMPLETED\'',
                (report_id,),
            )
            row = cur.fetchone()
        if not row or row["AssessmentJson"] is None:
            return None
        return InspectionResult.model_validate(row["AssessmentJson"])

    def get_bundle(self, report_id: UUID) -> ReportBundle:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT r."Id", r."ReportNo", r."CreatedAt", r."Description", r."Notes",
                       r."Building", r."Floor", r."RoomOrArea", r."GpsLat", r."GpsLng",
                       r."VerificationCount"
                FROM "dbo"."Reports" r WHERE r."Id" = %s
                ''',
                (report_id,),
            )
            report = cur.fetchone()
            if report is None:
                raise RuntimeError(f"Report does not exist: {report_id}")
            cur.execute(
                '''
                SELECT ri."Id", ri."PublicId", ri."SecureUrl", ri."IsPrimary"
                FROM "dbo"."ReportImages" ri
                WHERE ri."ReportId" = %s
                ORDER BY ri."IsPrimary" DESC, ri."CreatedAt" ASC
                ''',
                (report_id,),
            )
            image_rows = cur.fetchall()

        images = tuple(
            ReportImageReference(
                id=row["Id"],
                public_id=str(row["PublicId"]),
                secure_url=str(row["SecureUrl"]),
                is_primary=bool(row["IsPrimary"]),
            )
            for row in image_rows
        )
        if not images:
            raise RuntimeError("Report has no image record.")
        return ReportBundle(
            id=report["Id"],
            report_no=str(report["ReportNo"]),
            created_at=report["CreatedAt"],
            description=report["Description"],
            notes=report["Notes"],
            building=report["Building"],
            floor=report["Floor"],
            room_or_area=report["RoomOrArea"],
            gps_lat=float(report["GpsLat"]) if report["GpsLat"] is not None else None,
            gps_lng=float(report["GpsLng"]) if report["GpsLng"] is not None else None,
            verification_count=int(report["VerificationCount"] or 0),
            images=images,
        )

    def get_assessment_context(
        self,
        *,
        bundle: ReportBundle,
        category: FacilityCategory,
    ) -> AssessmentDatabaseContext:
        category_reference = self.get_category_reference(category)
        recurrence_threshold = self._get_setting_int(
            "AgentRecurrenceThreshold", self.recurrence_threshold
        )
        verification_threshold = self._get_setting_int(
            "AgentVerificationThreshold", self.verification_threshold
        )
        recurrence_lookback_days = self._get_setting_int(
            "AgentRecurrenceLookbackDays", self.recurrence_lookback_days
        )
        recurrence_count = self._get_recurrence_count(
            bundle=bundle,
            category=category,
            lookback_days=recurrence_lookback_days,
        )
        age_days = max(
            0.0,
            (datetime.now(timezone.utc) - bundle.created_at).total_seconds() / 86400.0,
        )
        duplicate_lookback_days = self._get_setting_int(
            "AgentDuplicateLookbackDays", self.duplicate_lookback_days
        )
        duplicates = self._get_duplicate_candidates(
            bundle=bundle,
            category=category,
            lookback_days=duplicate_lookback_days,
        )
        return AssessmentDatabaseContext(
            recurrence_count=recurrence_count,
            is_recurring=recurrence_count >= recurrence_threshold,
            verification_count=bundle.verification_count,
            age_days=age_days,
            recurrence_threshold=recurrence_threshold,
            verification_threshold=verification_threshold,
            category_reference=category_reference,
            skills=self.get_skill_references(category_reference.id),
            materials=self.get_material_references(category_reference.id),
            historical_duration=self._get_historical_duration(category),
            historical_manpower=self._get_historical_manpower(category),
            historical_materials=self._get_historical_materials(category),
            duplicate_candidates=duplicates,
        )

    def _get_setting_int(self, key: str, fallback: int) -> int:
        try:
            with self.database.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ("Value" #>> '{}')::INTEGER
                    FROM "dbo"."SystemSettings"
                    WHERE "Key"=%s
                    """,
                    (key,),
                )
                row = cur.fetchone()
                return max(1, int(row[0] if row and row[0] is not None else fallback))
        except Exception:
            return fallback

    def get_category_reference(self, category: FacilityCategory) -> CategoryReference:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT "Id", "Code", "Name", "Description", "DefaultUrgency",
                       "UrgencyGuidance", "DefaultMinHours", "DefaultMaxHours",
                       "DefaultRequiredService", "DefaultRequiredCapability",
                       "SafetyGuidance", "PreferredTrade", "RequiresMaintenanceReview"
                FROM "dbo"."DamageCategories"
                WHERE "Name" = %s AND "IsActive" = TRUE
                ''',
                (category.value,),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"Active DamageCategories reference missing for {category.value}.")
        return CategoryReference(
            id=row["Id"], code=str(row["Code"]), name=FacilityCategory(str(row["Name"])),
            description=row["Description"], default_urgency=row["DefaultUrgency"],
            urgency_guidance=row["UrgencyGuidance"],
            default_min_hours=float(row["DefaultMinHours"]) if row["DefaultMinHours"] is not None else None,
            default_max_hours=float(row["DefaultMaxHours"]) if row["DefaultMaxHours"] is not None else None,
            default_required_service=row["DefaultRequiredService"],
            default_required_capability=row["DefaultRequiredCapability"],
            safety_guidance=row["SafetyGuidance"], preferred_trade=row["PreferredTrade"],
            requires_maintenance_review=bool(row["RequiresMaintenanceReview"]),
        )

    def get_skill_references(self, category_id: UUID) -> list[SkillReference]:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT s."Id" AS "SkillId", s."Name" AS "SkillName",
                       csr."MinimumProficiencyLevel", csr."IsRequired", csr."IsLeadSkill", csr."Notes"
                FROM "dbo"."CategorySkillRequirements" csr
                INNER JOIN "dbo"."Skills" s ON s."Id" = csr."SkillId"
                WHERE csr."CategoryId" = %s AND s."IsActive" = TRUE
                ORDER BY csr."IsLeadSkill" DESC, csr."IsRequired" DESC, s."Name"
                ''',
                (category_id,),
            )
            rows = cur.fetchall()
        return [
            SkillReference(
                skill_id=row["SkillId"], skill_name=str(row["SkillName"]),
                minimum_proficiency_level=row["MinimumProficiencyLevel"],
                is_required=bool(row["IsRequired"]), is_lead_skill=bool(row["IsLeadSkill"]),
                notes=row["Notes"],
            )
            for row in rows
        ]

    def get_material_references(self, category_id: UUID) -> list[MaterialReference]:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT m."Id" AS "MaterialId", m."Name", m."Unit",
                       cmr."DefaultQtyMin", cmr."DefaultQtyMax", cmr."IsCommon", cmr."Notes"
                FROM "dbo"."CategoryMaterialReferences" cmr
                INNER JOIN "dbo"."Materials" m ON m."Id" = cmr."MaterialId"
                WHERE cmr."CategoryId" = %s AND m."IsActive" = TRUE
                ORDER BY cmr."IsCommon" DESC, m."Name"
                ''',
                (category_id,),
            )
            rows = cur.fetchall()
        return [
            MaterialReference(
                material_id=row["MaterialId"], name=str(row["Name"]), unit=row["Unit"],
                default_qty_min=float(row["DefaultQtyMin"]) if row["DefaultQtyMin"] is not None else None,
                default_qty_max=float(row["DefaultQtyMax"]) if row["DefaultQtyMax"] is not None else None,
                is_common=bool(row["IsCommon"]), notes=row["Notes"],
            )
            for row in rows
        ]

    def _get_recurrence_count(
        self,
        *,
        bundle: ReportBundle,
        category: FacilityCategory,
        lookback_days: int,
    ) -> int:
        if not bundle.building:
            return 0
        start_at = bundle.created_at - timedelta(days=lookback_days)
        params: list[object] = [
            bundle.id, category.value, start_at, bundle.created_at, bundle.building
        ]
        location_sql = 'AND LOWER(r."Building") = LOWER(%s)'
        if bundle.room_or_area:
            location_sql += ' AND LOWER(r."RoomOrArea") = LOWER(%s)'
            params.append(bundle.room_or_area)
            if bundle.floor:
                location_sql += ' AND LOWER(COALESCE(r."Floor", \'\')) = LOWER(%s)'
                params.append(bundle.floor)
        query = f'''
            SELECT COUNT(*)::INTEGER
            FROM "dbo"."Reports" r
            WHERE r."Id" <> %s
              AND r."AgentStatus" = 'COMPLETED'
              AND r."ScopeDecision" = 'Facility Issue'
              AND COALESCE(r."FinalCategory", r."AiCategory") = %s
              AND r."CreatedAt" >= %s AND r."CreatedAt" < %s
              AND r."Status" <> 'CANCELLED'
              {location_sql}
        '''
        with self.database.connect() as conn, conn.cursor() as cur:
            cur.execute(query, tuple(params))
            row = cur.fetchone()
            return int(row[0] if row else 0)

    def _get_duplicate_candidates(
        self,
        *,
        bundle: ReportBundle,
        category: FacilityCategory,
        lookback_days: int,
    ) -> list[DuplicateCandidate]:
        if not bundle.building:
            return []
        start_at = bundle.created_at - timedelta(days=lookback_days)
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT r."Id", r."ReportNo",
                    CASE
                      WHEN %s::text IS NOT NULL AND r."RoomOrArea" IS NOT NULL
                           AND LOWER(r."RoomOrArea") = LOWER(%s::text) THEN 1.00
                      WHEN %s::text IS NOT NULL AND r."Floor" IS NOT NULL
                           AND LOWER(r."Floor") = LOWER(%s::text) THEN 0.80
                      ELSE 0.60
                    END::NUMERIC AS "MatchScore",
                    CASE
                      WHEN %s::text IS NOT NULL AND r."RoomOrArea" IS NOT NULL
                           AND LOWER(r."RoomOrArea") = LOWER(%s::text)
                        THEN 'Same building, room/area, and effective category.'
                      WHEN %s::text IS NOT NULL AND r."Floor" IS NOT NULL
                           AND LOWER(r."Floor") = LOWER(%s::text)
                        THEN 'Same building, floor, and effective category.'
                      ELSE 'Same building and effective category.'
                    END AS "MatchReason"
                FROM "dbo"."Reports" r
                WHERE r."Id" <> %s
                  AND r."CreatedAt" >= %s AND r."CreatedAt" <= %s
                  AND r."AgentStatus" = 'COMPLETED'
                  AND r."ScopeDecision" = 'Facility Issue'
                  AND r."Status" NOT IN ('RESOLVED','CANCELLED','DUPLICATE')
                  AND COALESCE(r."FinalCategory", r."AiCategory") = %s
                  AND LOWER(r."Building") = LOWER(%s)
                ORDER BY "MatchScore" DESC, r."CreatedAt" DESC
                LIMIT 10
                ''',
                (
                    bundle.room_or_area, bundle.room_or_area,
                    bundle.floor, bundle.floor,
                    bundle.room_or_area, bundle.room_or_area,
                    bundle.floor, bundle.floor,
                    bundle.id, start_at, bundle.created_at,
                    category.value, bundle.building,
                ),
            )
            rows = cur.fetchall()
        return [
            DuplicateCandidate(
                candidate_report_id=row["Id"], report_no=str(row["ReportNo"]),
                match_score=float(row["MatchScore"]), match_reason=str(row["MatchReason"]),
            )
            for row in rows
        ]

    def _get_historical_duration(self, category: FacilityCategory) -> HistoricalDurationStats | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT COUNT("ObservedElapsedHours")::INTEGER AS "SampleCount",
                       AVG("ObservedElapsedHours") AS "AverageHours",
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY "ObservedElapsedHours") AS "MedianHours"
                FROM "dbo"."v_CompletedWorkOrderKnowledge"
                WHERE "EffectiveCategory" = %s AND "ObservedElapsedHours" IS NOT NULL
                ''',
                (category.value,),
            )
            row = cur.fetchone()
        if not row or int(row["SampleCount"] or 0) == 0:
            return None
        return HistoricalDurationStats(
            sample_count=int(row["SampleCount"]), average_hours=float(row["AverageHours"]),
            median_hours=float(row["MedianHours"]),
        )

    def _get_historical_manpower(self, category: FacilityCategory) -> HistoricalManpowerStats | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT COUNT("ActualCrewSize")::INTEGER AS "SampleCount",
                       AVG("ActualCrewSize") AS "AverageCrew",
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY "ActualCrewSize") AS "MedianCrew"
                FROM "dbo"."v_CompletedWorkOrderKnowledge"
                WHERE "EffectiveCategory" = %s AND "ActualCrewSize" IS NOT NULL
                ''',
                (category.value,),
            )
            row = cur.fetchone()
        if not row or int(row["SampleCount"] or 0) == 0:
            return None
        return HistoricalManpowerStats(
            sample_count=int(row["SampleCount"]),
            average_actual_crew_size=float(row["AverageCrew"]),
            median_actual_crew_size=float(row["MedianCrew"]),
        )

    def _get_historical_materials(self, category: FacilityCategory) -> list[HistoricalMaterialStat]:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT wom."MaterialName", wom."Unit", COUNT(*)::INTEGER AS "UseCount",
                       AVG(wom."Quantity") FILTER (WHERE wom."Quantity" IS NOT NULL) AS "AverageQuantity",
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wom."Quantity")
                         FILTER (WHERE wom."Quantity" IS NOT NULL) AS "MedianQuantity"
                FROM "dbo"."WorkOrderMaterials" wom
                INNER JOIN "dbo"."WorkOrders" wo ON wo."Id" = wom."WorkOrderId"
                INNER JOIN "dbo"."Reports" r ON r."Id" = wo."ReportId"
                WHERE wo."Status" = 'COMPLETED'
                  AND wom."Stage" = 'ACTUAL'
                  AND COALESCE(r."FinalCategory", r."AiCategory") = %s
                GROUP BY wom."MaterialName", wom."Unit"
                ORDER BY COUNT(*) DESC, wom."MaterialName"
                LIMIT 12
                ''',
                (category.value,),
            )
            rows = cur.fetchall()
        return [
            HistoricalMaterialStat(
                material_name=str(row["MaterialName"]), unit=row["Unit"],
                use_count=int(row["UseCount"]),
                average_quantity=float(row["AverageQuantity"]) if row["AverageQuantity"] is not None else None,
                median_quantity=float(row["MedianQuantity"]) if row["MedianQuantity"] is not None else None,
            )
            for row in rows
        ]

    def complete_report(
        self,
        *,
        report_id: UUID,
        attempt_count: int,
        result: InspectionResult,
        context: AssessmentDatabaseContext | None,
    ) -> bool:
        assessment = result.assessment
        priority = result.priority
        policy = result.policy
        recurrence_count = context.recurrence_count if context else 0
        is_recurring = context.is_recurring if context else False

        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                UPDATE "dbo"."Reports"
                SET "AgentStatus"='COMPLETED', "AgentCompletedAt"=NOW(),
                    "AgentNextRetryAt"=NULL, "AgentLastError"=NULL,
                    "AnalysisStatus"=%s, "ScopeDecision"=%s, "ScopeShouldAnalyze"=%s,
                    "AiCategory"=%s, "AiSummary"=%s,
                    "AiRawRecommendedUrgency"=%s, "AiRecommendedUrgency"=%s,
                    "AiPolicyApplied"=%s, "AiPolicyVersion"=%s, "AiPolicyReason"=%s,
                    "AiAnalysisCertainty"=%s, "AiNeedsReview"=%s,
                    "AiEstimatedMinHours"=%s, "AiEstimatedMaxHours"=%s,
                    "RiskImmediateDanger"=%s, "RiskElectricalExposure"=%s,
                    "RiskFireOrSmokeIndicator"=%s, "RiskStructuralInstabilityIndicator"=%s,
                    "RiskActiveFlooding"=%s, "RiskBlockedAccessOrExit"=%s,
                    "RiskPublicAccessExposure"=%s,
                    "RecurrenceCount"=%s, "IsRecurring"=%s,
                    "PriorityUrgencyPoints"=%s, "PriorityRecurrencePoints"=%s,
                    "PriorityVerificationPoints"=%s, "PriorityAgePoints"=%s,
                    "PriorityPublicExposurePoints"=%s, "PriorityScore"=%s,
                    "AiProvider"=%s, "AiModelId"=%s, "AiPromptVersion"=%s,
                    "OriginalWidth"=%s, "OriginalHeight"=%s,
                    "ProcessedWidth"=%s, "ProcessedHeight"=%s,
                    "AiProcessingTimeMs"=%s, "AssessmentJson"=%s
                WHERE "Id"=%s AND "AgentStatus"='PROCESSING'
                  AND "Status"='SUBMITTED' AND "AgentAttemptCount"=%s
                RETURNING "ReportNo", "AgentAttemptCount", "ReporterId", "AgentCompletedAt"
                ''',
                (
                    result.analysis_status.value, result.scope_validation.decision.value,
                    result.scope_validation.should_analyze,
                    assessment.category.value if assessment else None,
                    assessment.summary if assessment else None,
                    policy.raw_urgency.value if policy else None,
                    policy.effective_urgency.value if policy else None,
                    policy.policy_applied if policy else False,
                    policy.policy_version if policy else None,
                    policy.reason if policy else None,
                    assessment.analysis_certainty.value if assessment else None,
                    assessment.needs_review if assessment else None,
                    assessment.estimated_min_hours if assessment else None,
                    assessment.estimated_max_hours if assessment else None,
                    assessment.risk_flags.immediate_danger if assessment else False,
                    assessment.risk_flags.electrical_exposure if assessment else False,
                    assessment.risk_flags.fire_or_smoke_indicator if assessment else False,
                    assessment.risk_flags.structural_instability_indicator if assessment else False,
                    assessment.risk_flags.active_flooding if assessment else False,
                    assessment.risk_flags.blocked_access_or_exit if assessment else False,
                    assessment.risk_flags.public_access_exposure if assessment else False,
                    recurrence_count, is_recurring,
                    priority.urgency_points if priority else None,
                    priority.recurrence_points if priority else None,
                    priority.verification_points if priority else None,
                    priority.age_points if priority else None,
                    priority.public_exposure_points if priority else None,
                    priority.total if priority else None,
                    result.provider, result.model_id, result.prompt_version,
                    result.original_width, result.original_height,
                    result.processed_width, result.processed_height,
                    result.processing_time_ms,
                    Jsonb(result.model_dump(mode="json")), report_id, attempt_count,
                ),
            )
            row = cur.fetchone()
            if row is None:
                # Cancellation or stale re-claim won. Never write results from
                # an obsolete Agent attempt or overwrite a cancelled report.
                return False

            # The revised database keeps ReportAssessmentHistory as an explicit
            # audit table. Persist the completed attempt in the same transaction
            # instead of relying on the removed legacy archive trigger.
            cur.execute(
                """
                INSERT INTO "dbo"."ReportAssessmentHistory"
                ("ReportId","AgentAttemptCount","AnalysisStatus","ScopeDecision",
                 "AiCategory","AiRawRecommendedUrgency","AiRecommendedUrgency",
                 "PriorityScore","AiProvider","AiModelId","AiPromptVersion",
                 "AiPolicyVersion","AssessmentJson","ProcessingTimeMs")
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT ("ReportId","AgentAttemptCount") DO NOTHING
                """,
                (
                    report_id,
                    int(row["AgentAttemptCount"]),
                    result.analysis_status.value,
                    result.scope_validation.decision.value,
                    assessment.category.value if assessment else None,
                    policy.raw_urgency.value if policy else None,
                    policy.effective_urgency.value if policy else None,
                    priority.total if priority else None,
                    result.provider,
                    result.model_id,
                    result.prompt_version,
                    policy.policy_version if policy else None,
                    Jsonb(result.model_dump(mode="json")),
                    result.processing_time_ms,
                ),
            )

            if context:
                for candidate in context.duplicate_candidates:
                    cur.execute(
                        '''
                        INSERT INTO "dbo"."ReportDuplicateCandidates"
                        ("SourceReportId","CandidateReportId","MatchMethod","MatchScore","MatchReason")
                        VALUES (%s,%s,'LOCATION_CATEGORY',%s,%s)
                        ON CONFLICT ("SourceReportId","CandidateReportId") DO UPDATE SET
                          "MatchScore"=EXCLUDED."MatchScore", "MatchReason"=EXCLUDED."MatchReason"
                        WHERE "dbo"."ReportDuplicateCandidates"."Status"='PENDING'
                        ''',
                        (report_id, candidate.candidate_report_id, candidate.match_score, candidate.match_reason),
                    )

            event_name = "report.assessment.completed" if assessment else "report.assessment.no_assessment"
            self._insert_outbox_event(
                cur=cur, report_id=report_id, report_no=str(row["ReportNo"]),
                attempt_count=int(row["AgentAttemptCount"]), event_name=event_name, status="COMPLETED",
            )
            dedupe = f"report:{report_id}:assessment-completed"
            payload = {
                "reportNo": str(row["ReportNo"]),
                "status": "ASSESSED",
                "deduplicationKey": dedupe,
            }
            # Use the same advisory-lock namespace as seefix-api so a Reporter
            # polling notifications at the exact moment the Agent completes
            # cannot race the API lifecycle backfill into a duplicate row.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                (f"notification:{row['ReporterId']}:{dedupe}",),
            )
            cur.execute(
                """
                INSERT INTO "dbo"."Notifications"
                  ("UserId","Type","Title","Message","EntityType","EntityId","Payload","CreatedAt","UpdatedAt")
                SELECT %s,'REPORT_ASSESSED','Report assessment ready',%s,'REPORT',%s,%s,%s,%s
                WHERE NOT EXISTS (
                    SELECT 1 FROM "dbo"."Notifications" n
                    WHERE n."UserId"=%s AND n."Payload"->>'deduplicationKey'=%s
                )
                """,
                (
                    row["ReporterId"],
                    f"Automated assessment for {row['ReportNo']} is ready.",
                    report_id,
                    Jsonb(payload),
                    row["AgentCompletedAt"],
                    row["AgentCompletedAt"],
                    row["ReporterId"],
                    dedupe,
                ),
            )
        return True

    def fail_report(self, *, report_id: UUID, attempt_count: int, error: str) -> None:
        message = str(error).strip()[:2000]
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                UPDATE "dbo"."Reports"
                SET "AgentStatus"='FAILED', "AgentCompletedAt"=NOW(),
                    "AgentLastError"=%s, "AgentNextRetryAt"=NULL
                WHERE "Id"=%s AND "AgentStatus"='PROCESSING'
                  AND "Status"='SUBMITTED' AND "AgentAttemptCount"=%s
                RETURNING "ReportNo", "AgentAttemptCount"
                ''',
                (message, report_id, attempt_count),
            )
            row = cur.fetchone()
            if row:
                self._insert_outbox_event(
                    cur=cur, report_id=report_id, report_no=str(row["ReportNo"]),
                    attempt_count=int(row["AgentAttemptCount"]),
                    event_name="report.assessment.failed", status="FAILED",
                )

    @staticmethod
    def _insert_outbox_event(
        *, cur, report_id: UUID, report_no: str, attempt_count: int, event_name: str, status: str
    ) -> None:
        deduplication_key = f"report:{report_id}:assessment:{attempt_count}:{status.lower()}"
        cur.execute(
            '''
            INSERT INTO "dbo"."OutboxEvents"
              ("AggregateType","AggregateId","Transport","ChannelName","EventName","Payload","DeduplicationKey")
            VALUES ('REPORT',%s,'PUSHER',%s,%s,%s,%s)
            ON CONFLICT ("DeduplicationKey") WHERE "DeduplicationKey" IS NOT NULL DO NOTHING
            ''',
            (
                report_id, f"report-{report_id}", event_name,
                Jsonb({"reportId": str(report_id), "reportNo": report_no, "agentStatus": status}),
                deduplication_key,
            ),
        )
