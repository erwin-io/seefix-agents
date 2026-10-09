from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FacilityCategory(str, Enum):
    ELECTRICAL_FIRE = "Electrical and Fire Safety"
    STRUCTURAL_SURFACE = "Structural and Surface Damage"
    PLUMBING_WATER = "Plumbing and Water Damage"
    ACCESS_SAFETY = "Access and Safety Hazards"
    FIXTURES_EQUIPMENT = "Building Fixtures and Equipment"
    SANITATION_ENVIRONMENTAL = "Sanitation and Environmental"
    ROADS_GROUNDS = "Roads, Walkways and Grounds"
    OTHER_UNCERTAIN = "Other or Uncertain"


class Urgency(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class AnalysisCertainty(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class AnalysisStatus(str, Enum):
    ASSESSED = "Assessed"
    NO_ASSESSMENT = "No Assessment"


class ScopeDecision(str, Enum):
    FACILITY_ISSUE = "Facility Issue"
    NO_VISIBLE_ISSUE = "No Visible Maintenance Issue"
    OUT_OF_SCOPE = "Out of Scope"
    INSUFFICIENT_IMAGE = "Insufficient Image"


class WorkOrderRouteType(str, Enum):
    INTERNAL = "INTERNAL"
    PROCUREMENT = "PROCUREMENT"


class ScopeValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ScopeDecision
    should_analyze: bool
    reason: str = Field(min_length=5, max_length=300)
    detected_subjects: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_should_analyze(self) -> "ScopeValidation":
        expected = self.decision == ScopeDecision.FACILITY_ISSUE
        if self.should_analyze != expected:
            raise ValueError("should_analyze must match the scope decision")
        return self


class RiskFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    immediate_danger: bool = False
    electrical_exposure: bool = False
    fire_or_smoke_indicator: bool = False
    structural_instability_indicator: bool = False
    active_flooding: bool = False
    blocked_access_or_exit: bool = False
    public_access_exposure: bool = False


class AgentAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: FacilityCategory
    summary: str = Field(min_length=5, max_length=600)
    observed_evidence: list[str] = Field(min_length=1, max_length=8)
    possible_causes: list[str] = Field(default_factory=list, max_length=5)
    safety_indicators: list[str] = Field(default_factory=list, max_length=8)
    risk_flags: RiskFlags = Field(default_factory=RiskFlags)
    recommended_urgency: Urgency
    urgency_reasons: list[str] = Field(min_length=1, max_length=8)
    estimated_min_hours: float = Field(ge=0.25, le=720)
    estimated_max_hours: float = Field(ge=0.25, le=1440)
    duration_assumptions: list[str] = Field(default_factory=list, max_length=8)
    analysis_certainty: AnalysisCertainty
    needs_review: bool
    follow_up_questions: list[str] = Field(default_factory=list, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_duration_range(self) -> "AgentAssessment":
        if self.estimated_max_hours < self.estimated_min_hours:
            raise ValueError("estimated_max_hours must be >= estimated_min_hours")
        return self


class ModelAssessment(BaseModel):
    """Compact schema produced directly by Qwen3-VL."""

    model_config = ConfigDict(extra="forbid")

    category: FacilityCategory
    summary: str = Field(min_length=5, max_length=240)
    observed_evidence: list[str] = Field(min_length=1, max_length=4)
    possible_causes: list[str] = Field(default_factory=list, max_length=2)
    risk_flags: RiskFlags = Field(default_factory=RiskFlags)
    recommended_urgency: Urgency
    urgency_reason: str = Field(min_length=3, max_length=240)
    estimated_min_hours: float = Field(ge=0.25, le=720)
    estimated_max_hours: float = Field(ge=0.25, le=1440)
    analysis_certainty: AnalysisCertainty
    needs_review: bool
    limitation: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_duration_range(self) -> "ModelAssessment":
        if self.estimated_max_hours < self.estimated_min_hours:
            raise ValueError("estimated_max_hours must be >= estimated_min_hours")
        return self

    def to_agent_assessment(self) -> AgentAssessment:
        safety_labels = {
            "immediate_danger": "Possible immediate danger is visible.",
            "electrical_exposure": "Possible electrical exposure is visible.",
            "fire_or_smoke_indicator": "A possible fire or smoke indicator is visible.",
            "structural_instability_indicator": "A possible structural-instability indicator is visible.",
            "active_flooding": "Possible active flooding is visible.",
            "blocked_access_or_exit": "Access or an exit may be blocked.",
            "public_access_exposure": "The issue may expose a public access area.",
        }
        safety_indicators = [
            label
            for field_name, label in safety_labels.items()
            if getattr(self.risk_flags, field_name)
        ]
        limitations = [self.limitation] if self.limitation else []
        follow_up_questions = (
            ["Can Maintenance Office staff inspect and confirm the affected area?"]
            if self.needs_review
            else []
        )
        return AgentAssessment(
            category=self.category,
            summary=self.summary,
            observed_evidence=self.observed_evidence,
            possible_causes=self.possible_causes,
            safety_indicators=safety_indicators,
            risk_flags=self.risk_flags,
            recommended_urgency=self.recommended_urgency,
            urgency_reasons=[self.urgency_reason],
            estimated_min_hours=self.estimated_min_hours,
            estimated_max_hours=self.estimated_max_hours,
            duration_assumptions=[
                "Preliminary estimate based on visible scope and report context."
            ],
            analysis_certainty=self.analysis_certainty,
            needs_review=self.needs_review,
            follow_up_questions=follow_up_questions,
            limitations=limitations,
        )


class ModelInspectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_decision: ScopeDecision
    scope_reason: str = Field(min_length=5, max_length=300)
    detected_subjects: list[str] = Field(default_factory=list, max_length=6)
    assessment: ModelAssessment | None = None

    @model_validator(mode="after")
    def validate_scope_and_assessment(self) -> "ModelInspectionResponse":
        should_have_assessment = self.scope_decision == ScopeDecision.FACILITY_ISSUE
        if should_have_assessment and self.assessment is None:
            raise ValueError("assessment is required for a visible facility issue")
        if not should_have_assessment and self.assessment is not None:
            raise ValueError("assessment must be null when no assessment is appropriate")
        return self

    def to_provider_result(self) -> "ProviderAnalysis":
        should_analyze = self.scope_decision == ScopeDecision.FACILITY_ISSUE
        return ProviderAnalysis(
            scope_validation=ScopeValidation(
                decision=self.scope_decision,
                should_analyze=should_analyze,
                reason=self.scope_reason,
                detected_subjects=self.detected_subjects,
            ),
            assessment=(
                self.assessment.to_agent_assessment()
                if self.assessment is not None
                else None
            ),
        )


class ProviderAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_validation: ScopeValidation
    assessment: AgentAssessment | None = None


class PriorityBreakdown(BaseModel):
    urgency_points: int
    recurrence_points: int
    verification_points: int
    age_points: int
    public_exposure_points: int
    total: int


class CategoryReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    name: FacilityCategory
    description: str | None = None
    default_urgency: Urgency | None = None
    urgency_guidance: str | None = None
    default_min_hours: float | None = None
    default_max_hours: float | None = None
    default_required_service: str | None = None
    default_required_capability: str | None = None
    safety_guidance: str | None = None
    preferred_trade: str | None = None
    requires_maintenance_review: bool = False


class SkillReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: UUID
    skill_name: str
    minimum_proficiency_level: int | None = Field(default=None, ge=1, le=5)
    is_required: bool = True
    is_lead_skill: bool = False
    notes: str | None = None


class MaterialReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: UUID
    name: str
    unit: str | None = None
    default_qty_min: float | None = Field(default=None, ge=0)
    default_qty_max: float | None = Field(default=None, ge=0)
    is_common: bool = True
    notes: str | None = None


class HistoricalDurationStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_count: int = Field(ge=0)
    average_hours: float
    median_hours: float


class HistoricalManpowerStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_count: int = Field(ge=0)
    average_actual_crew_size: float
    median_actual_crew_size: float


class HistoricalMaterialStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_name: str
    unit: str | None = None
    use_count: int = Field(ge=1)
    average_quantity: float | None = None
    median_quantity: float | None = None


class DuplicateCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_report_id: UUID
    report_no: str
    match_score: float = Field(ge=0, le=1)
    match_reason: str


class PolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_urgency: Urgency
    effective_urgency: Urgency
    policy_applied: bool
    policy_version: str
    reason: str
    needs_review: bool


class AssessmentDatabaseContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recurrence_count: int = Field(ge=0)
    is_recurring: bool
    verification_count: int = Field(ge=0)
    age_days: float = Field(ge=0)
    recurrence_threshold: int = Field(ge=1)
    verification_threshold: int = Field(ge=1)
    category_reference: CategoryReference
    skills: list[SkillReference] = Field(default_factory=list)
    materials: list[MaterialReference] = Field(default_factory=list)
    historical_duration: HistoricalDurationStats | None = None
    historical_manpower: HistoricalManpowerStats | None = None
    historical_materials: list[HistoricalMaterialStat] = Field(default_factory=list)
    duplicate_candidates: list[DuplicateCandidate] = Field(default_factory=list)


class InspectionResult(BaseModel):
    analysis_status: AnalysisStatus
    scope_validation: ScopeValidation
    assessment: AgentAssessment | None
    priority: PriorityBreakdown | None
    policy: PolicyResult | None = None
    provider: str
    model_id: str
    prompt_version: str
    original_width: int
    original_height: int
    processed_width: int
    processed_height: int
    processing_time_ms: int

    @model_validator(mode="after")
    def validate_result_state(self) -> "InspectionResult":
        assessed = self.analysis_status == AnalysisStatus.ASSESSED
        if assessed != self.scope_validation.should_analyze:
            raise ValueError("analysis_status must match scope_validation")
        if assessed and (self.assessment is None or self.priority is None or self.policy is None):
            raise ValueError("assessed results require assessment, policy and priority")
        if not assessed and (
            self.assessment is not None or self.priority is not None or self.policy is not None
        ):
            raise ValueError("no-assessment results must have null assessment, policy and priority")
        return self


# ---------------------------------------------------------------------------
# Maintenance Request model-facing and service-facing contracts
# ---------------------------------------------------------------------------

class MaintenanceRequestModelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_service: str = Field(min_length=5, max_length=700)
    required_capability: str = Field(min_length=5, max_length=700)
    scope_of_work: str = Field(min_length=10, max_length=1800)
    safety_requirements: str | None = Field(default=None, max_length=1200)
    estimated_manpower_min: int = Field(ge=1, le=100)
    estimated_manpower_max: int = Field(ge=1, le=100)
    estimated_duration_days_min: float = Field(gt=0, le=365)
    estimated_duration_days_max: float = Field(gt=0, le=365)
    additional_preliminary_materials: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_ranges(self) -> "MaintenanceRequestModelDraft":
        if self.estimated_manpower_max < self.estimated_manpower_min:
            raise ValueError("estimated_manpower_max must be >= estimated_manpower_min")
        if self.estimated_duration_days_max < self.estimated_duration_days_min:
            raise ValueError("estimated_duration_days_max must be >= estimated_duration_days_min")
        return self


class MaintenanceRequestDraftResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_category: FacilityCategory
    effective_urgency: Urgency
    required_service: str
    required_capability: str
    scope_of_work: str
    safety_requirements: str | None = None
    preliminary_materials_notes: str | None = None
    estimated_labor_hours_min: float | None = None
    estimated_labor_hours_max: float | None = None
    estimated_manpower_min: int | None = None
    estimated_manpower_max: int | None = None
    estimated_duration_days_min: float | None = None
    estimated_duration_days_max: float | None = None
    generation_mode: str
    model_id: str | None = None
    prompt_version: str
    skills: list[SkillReference] = Field(default_factory=list)
    materials: list[MaterialReference] = Field(default_factory=list)
    historical_materials: list[HistoricalMaterialStat] = Field(default_factory=list)
    additional_materials: list[str] = Field(default_factory=list)
    knowledge_snapshot: dict[str, Any] = Field(default_factory=dict)
    recommendation: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Procurement assistance
# ---------------------------------------------------------------------------

class ProcurementClarificationModelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_draft: str = Field(min_length=5, max_length=1800)
    uncertainties: list[str] = Field(default_factory=list, max_length=6)
    field_inspection_required: bool = False


class ProcurementClarificationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: UUID
    response_draft: str
    uncertainties: list[str] = Field(default_factory=list)
    field_inspection_required: bool = False
    generation_mode: str
    model_id: str | None = None
    prompt_version: str


# ---------------------------------------------------------------------------
# Work Order assistance
# ---------------------------------------------------------------------------

class WorkOrderReadinessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    blocking_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    information: list[str] = Field(default_factory=list)


class WorkOrderVarianceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    severity: str
    message: str
    estimated_min: float | None = None
    estimated_max: float | None = None
    planned_value: float | None = None


class WorkOrderVarianceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkOrderVarianceItem] = Field(default_factory=list)
    has_warnings: bool = False


class WorkOrderDraftPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    maintenance_request_id: UUID
    maintenance_review_id: UUID
    route_type: WorkOrderRouteType
    procurement_outcome_id: UUID | None = None
    status: str = "PENDING_ASSIGNMENT"
    execution_type: str
    assigned_party_name: str | None = None
    responsible_lead_user_id: UUID | None = None
    responsible_lead_name: str | None = None
    responsible_lead_contact: str | None = None
    responsible_lead_email: str | None = None
    assigned_by: UUID | None = None
    assigned_at: str | None = None
    planned_start_at: str | None = None
    deadline: str | None = None
    planned_duration_days: float | None = None
    planned_crew_size: int | None = None
    planned_labor_hours: float | None = None
    instructions: str | None = None
    safety_requirements: str | None = None
    procurement_reference_no: str | None = None
    readiness: WorkOrderReadinessResult
    variance: WorkOrderVarianceResult


# ---------------------------------------------------------------------------
# Completion assistance
# ---------------------------------------------------------------------------

class CompletionModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible_issue_improved: bool
    visible_issue_still_present: bool
    insufficient_completion_image: bool
    additional_visible_concern: bool
    summary: str = Field(min_length=5, max_length=500)
    observed_changes: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    needs_maintenance_supervisor_review: bool


class CompletionAssessmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_order_id: UUID
    visual_result: str
    model_response: CompletionModelResponse
    planned_vs_actual: dict[str, Any] = Field(default_factory=dict)
    provider: str
    model_id: str
    prompt_version: str
    processing_time_ms: int
