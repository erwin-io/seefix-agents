from __future__ import annotations

from .schemas import AgentAssessment, PriorityBreakdown, Urgency


URGENCY_POINTS = {
    Urgency.LOW: 25,
    Urgency.MEDIUM: 50,
    Urgency.HIGH: 75,
    Urgency.CRITICAL: 100,
}


def calculate_priority(assessment: AgentAssessment) -> PriorityBreakdown:
    urgency_points = URGENCY_POINTS[assessment.recommended_urgency]
    recurrence_points = 0
    verification_points = 0
    public_exposure_points = 10 if assessment.risk_flags.public_access_exposure else 0
    total = (
        urgency_points
        + recurrence_points
        + verification_points
        + public_exposure_points
    )
    return PriorityBreakdown(
        urgency_points=urgency_points,
        recurrence_points=recurrence_points,
        verification_points=verification_points,
        public_exposure_points=public_exposure_points,
        total=total,
    )
