"""Capacity presentation for authoritative workload reports; never saves records."""
from decimal import Decimal

from .calculation import weighted_load_for


CONDITIONS = (
    ("UNDERLOAD", "Below target", "underload", "Below recommended target"),
    ("WITHIN_LOAD", "Within maximum", "within", "Meets target or below maximum"),
    ("AT_CAPACITY", "At maximum", "capacity", "At configured maximum"),
    ("OVERLOAD", "Above maximum", "overload", "Above configured maximum"),
    ("UNCONFIGURED", "Policy review", "unconfigured", "Missing weights or maximum"),
)


def decorate_report(report, *, sections=None):
    """Add a display grouping without changing backend status or policy values.

    The existing OVERLOAD result also covers target-only policies. A capacity
    label needs the effective maximum; recommended targets remain advisory.
    Missing a target alone does not invalidate a known maximum comparison.
    """
    load = report["assigned_load"]
    policy = report["policy"]
    target, maximum = policy["recommended_load"], policy["maximum_load"]
    condition = "UNCONFIGURED" if load is None or maximum is None else report["status"]
    _, label, tone, _ = next(item for item in CONDITIONS if item[0] == condition)
    explanation = "Based on saved teaching assignments for this academic term."
    above_target = False
    if load is None:
        position = "Weighting configuration missing"
        explanation = "Weighted load and maximum-capacity status cannot be determined until the applicable policy is configured."
    elif maximum is None:
        if target is None:
            position = "Target and maximum not configured"
        else:
            relation = "Above target" if load > target else "Meets target" if load == target else "Below target"
            position = f"{relation} · Maximum not configured"
        explanation = "Maximum-capacity status cannot be determined until the applicable policy is configured."
    elif load > maximum:
        position = "Above configured maximum"
        mode = "Hard-limit enforcement is enabled." if policy["enforce_maximum"] else "This policy uses warnings-only enforcement."
        explanation = f"{load - maximum:.2f} above the configured maximum. {mode}"
    elif load == maximum:
        position = "At configured maximum"
    elif target is None:
        position = "Within maximum · Target not configured"
        explanation = "Maximum capacity is known; a recommended teaching target has not been configured."
    elif load < target:
        position = "Below recommended target"
    elif load == target:
        position = "Meets recommended target"
    else:
        position = "Above target · Within maximum"
        above_target = True
        explanation = f"{load - target:.2f} above the advisory target; {maximum - load:.2f} below the maximum."

    meter = {"available": load is not None and (target is not None or maximum is not None),
             "fill_percent": None, "target_percent": None, "maximum_percent": None}
    if meter["available"]:
        # Geometry only. Policy, workload and remaining capacity are unchanged.
        scale = max(Decimal("1"), load, target if target is not None else Decimal("0"),
                    maximum if maximum is not None else Decimal("0"))
        meter.update(fill_percent=load / scale * 100,
                     target_percent=target / scale * 100 if target is not None else None,
                     maximum_percent=maximum / scale * 100 if maximum is not None else None)

    sections = sections or {}
    contributions = []
    for assignment in report["assignments"]:
        offering = assignment.subject_offering
        contributions.append({
            "assignment": assignment, "share_percent": assignment.share * 100,
            "units": offering.total_units * assignment.share,
            "hours": (offering.lecture_hours + offering.laboratory_hours) * assignment.share,
            "load": weighted_load_for(offering, assignment.share, policy),
            "section_code": sections.get(offering.pk, ""),
        })
    return {**report, "monitor": {
        "condition": condition, "label": label, "tone": tone, "position": position,
        "explanation": explanation, "above_target": above_target, "meter": meter,
        "contributions": contributions,
    }}
