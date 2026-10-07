from __future__ import annotations

from ..schemas import WorkOrderVarianceItem, WorkOrderVarianceResult


def _compare_range(
    items: list[WorkOrderVarianceItem],
    *,
    field: str,
    estimated_min,
    estimated_max,
    planned,
    label: str,
) -> None:
    if planned is None or estimated_min is None or estimated_max is None:
        return
    e_min = float(estimated_min)
    e_max = float(estimated_max)
    p = float(planned)
    if p < e_min or p > e_max:
        items.append(
            WorkOrderVarianceItem(
                field=field,
                severity="WARNING",
                message=(
                    f"{label} ({p:g}) is outside the preliminary Maintenance Request estimate "
                    f"({e_min:g}-{e_max:g}). Field/Procurement information may be more complete; review rather than auto-reject."
                ),
                estimated_min=e_min,
                estimated_max=e_max,
                planned_value=p,
            )
        )
    else:
        items.append(
            WorkOrderVarianceItem(
                field=field,
                severity="INFO",
                message=f"{label} is within the preliminary estimated range.",
                estimated_min=e_min,
                estimated_max=e_max,
                planned_value=p,
            )
        )


def calculate_work_order_variance(context: dict) -> WorkOrderVarianceResult:
    items: list[WorkOrderVarianceItem] = []
    _compare_range(
        items,
        field="durationDays",
        estimated_min=context.get("estimatedDurationDaysMin"),
        estimated_max=context.get("estimatedDurationDaysMax"),
        planned=context.get("plannedDurationDays"),
        label="Planned duration (days)",
    )
    _compare_range(
        items,
        field="crewSize",
        estimated_min=context.get("estimatedManpowerMin"),
        estimated_max=context.get("estimatedManpowerMax"),
        planned=context.get("plannedCrewSize"),
        label="Planned crew size",
    )
    _compare_range(
        items,
        field="laborHours",
        estimated_min=context.get("estimatedLaborHoursMin"),
        estimated_max=context.get("estimatedLaborHoursMax"),
        planned=context.get("plannedLaborHours"),
        label="Planned labor hours",
    )
    return WorkOrderVarianceResult(
        items=items,
        has_warnings=any(item.severity == "WARNING" for item in items),
    )
