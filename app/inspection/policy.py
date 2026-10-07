from __future__ import annotations

from ..schemas import AgentAssessment, CategoryReference, FacilityCategory, PolicyResult, Urgency

POLICY_VERSION = "facility-policy-v1"

_URGENCY_RANK = {
    Urgency.LOW: 1,
    Urgency.MEDIUM: 2,
    Urgency.HIGH: 3,
    Urgency.CRITICAL: 4,
}

_HARD_FLOORS: dict[FacilityCategory, Urgency | None] = {
    FacilityCategory.ELECTRICAL_FIRE: Urgency.HIGH,
    FacilityCategory.STRUCTURAL_SURFACE: Urgency.MEDIUM,
    FacilityCategory.PLUMBING_WATER: Urgency.MEDIUM,
    FacilityCategory.ACCESS_SAFETY: Urgency.HIGH,
    FacilityCategory.FIXTURES_EQUIPMENT: Urgency.MEDIUM,
    FacilityCategory.SANITATION_ENVIRONMENTAL: Urgency.MEDIUM,
    FacilityCategory.ROADS_GROUNDS: Urgency.MEDIUM,
    FacilityCategory.OTHER_UNCERTAIN: None,
}


def _max_urgency(*values: Urgency | None) -> Urgency:
    present = [value for value in values if value is not None]
    if not present:
        return Urgency.LOW
    return max(present, key=lambda value: _URGENCY_RANK[value])


def apply_urgency_policy(
    assessment: AgentAssessment,
    category_reference: CategoryReference | None = None,
) -> tuple[AgentAssessment, PolicyResult]:
    """Apply deterministic, non-downgrading SEEFIX category/safety policy."""

    raw = assessment.recommended_urgency
    effective = raw
    needs_review = assessment.needs_review
    reasons: list[str] = []

    hard_floor = _HARD_FLOORS[assessment.category]
    if hard_floor is not None and _URGENCY_RANK[effective] < _URGENCY_RANK[hard_floor]:
        effective = hard_floor
        reasons.append(f"Category safety floor raised urgency to {hard_floor.value}.")

    if category_reference and category_reference.default_urgency:
        db_floor = category_reference.default_urgency
        if _URGENCY_RANK[effective] < _URGENCY_RANK[db_floor]:
            effective = db_floor
            reasons.append(
                f"Active DamageCategories default raised urgency to {db_floor.value}."
            )

    flags = assessment.risk_flags

    if flags.immediate_danger:
        if effective != Urgency.CRITICAL:
            reasons.append("Visible immediate-danger flag requires Critical urgency.")
        effective = Urgency.CRITICAL
        needs_review = True

    if assessment.category == FacilityCategory.STRUCTURAL_SURFACE and flags.structural_instability_indicator:
        if _URGENCY_RANK[effective] < _URGENCY_RANK[Urgency.HIGH]:
            effective = Urgency.HIGH
            reasons.append("Structural-instability indicator raises urgency to at least High.")
        needs_review = True

    if assessment.category == FacilityCategory.PLUMBING_WATER and flags.active_flooding:
        if _URGENCY_RANK[effective] < _URGENCY_RANK[Urgency.HIGH]:
            effective = Urgency.HIGH
            reasons.append("Active flooding raises plumbing/water urgency to at least High.")

    if assessment.category in {FacilityCategory.ACCESS_SAFETY, FacilityCategory.ROADS_GROUNDS} and flags.blocked_access_or_exit:
        if _URGENCY_RANK[effective] < _URGENCY_RANK[Urgency.HIGH]:
            effective = Urgency.HIGH
            reasons.append("Blocked access/exit raises urgency to at least High.")
        if assessment.category == FacilityCategory.ACCESS_SAFETY:
            needs_review = True

    if flags.electrical_exposure:
        needs_review = True
        if assessment.category in {FacilityCategory.ELECTRICAL_FIRE, FacilityCategory.PLUMBING_WATER}:
            if _URGENCY_RANK[effective] < _URGENCY_RANK[Urgency.HIGH]:
                effective = Urgency.HIGH
                reasons.append("Electrical-exposure indicator raises urgency to at least High.")

    if flags.fire_or_smoke_indicator:
        needs_review = True
        if assessment.category == FacilityCategory.ELECTRICAL_FIRE and _URGENCY_RANK[effective] < _URGENCY_RANK[Urgency.HIGH]:
            effective = Urgency.HIGH
            reasons.append("Fire/smoke indicator raises electrical/fire urgency to at least High.")

    if flags.structural_instability_indicator:
        needs_review = True

    if assessment.category == FacilityCategory.OTHER_UNCERTAIN:
        needs_review = True
        reasons.append("Other or Uncertain always requires PPO review.")

    if category_reference and category_reference.requires_ppo_review:
        needs_review = True
        reasons.append("DamageCategories reference requires PPO review.")

    # The policy never downgrades the model recommendation.
    effective = _max_urgency(raw, effective)

    policy_changed = effective != raw or needs_review != assessment.needs_review
    if not reasons:
        reasons.append("Model urgency already satisfies deterministic SEEFIX policy.")

    updated_reasons = list(assessment.urgency_reasons)
    if effective != raw:
        updated_reasons.append(
            f"Deterministic SEEFIX policy normalized urgency from {raw.value} to {effective.value}."
        )

    updated = assessment.model_copy(
        update={
            "recommended_urgency": effective,
            "needs_review": needs_review,
            "urgency_reasons": updated_reasons[:8],
        }
    )

    return updated, PolicyResult(
        raw_urgency=raw,
        effective_urgency=effective,
        policy_applied=policy_changed,
        policy_version=POLICY_VERSION,
        reason=" ".join(reasons),
        needs_review=needs_review,
    )
