"""Scoped, reviewable workload recommendations and atomic acceptance."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.permissions import department_scoped_queryset, require_access
from audit.services import record_event
from core.models import Department
from timetabling.locking import scheduling_lock

from .balancing_inputs import prepare_balancing_input
from .balancing_signature import compute_balancing_source_signature
from .calculation import calculate_workload, resolve_policy
from .models import FacultySubjectAssignment, SubjectOffering, WorkloadRecommendationRun
from .operations import check_share


class BalancingError(Exception):
    """A run cannot make the requested lifecycle transition."""


def scoped_balancing_runs(user):
    require_access(user, "workloads.view_workloadrecommendationrun")
    return department_scoped_queryset(
        user, WorkloadRecommendationRun.objects.select_related(
            "academic_term", "department", "initiated_by", "accepted_by",
        ),
    )


def scoped_balancing_run(user, pk):
    if user.has_perm("workloads.view_workloadrecommendationrun"):
        return get_object_or_404(scoped_balancing_runs(user), pk=pk)
    require_access(user, "workloads.generate_workloadrecommendation")
    return get_object_or_404(
        department_scoped_queryset(user, WorkloadRecommendationRun.objects.all())
        .filter(initiated_by=user), pk=pk,
    )


def _scope(user, academic_term, department):
    require_access(user, "workloads.generate_workloadrecommendation")
    require_access(user, "academics.view_academicterm")
    department_id = getattr(department, "pk", department)
    term_id = getattr(academic_term, "pk", academic_term)
    scoped_department = get_object_or_404(
        department_scoped_queryset(user, Department.objects.select_related("college"), "pk"),
        pk=department_id,
    )
    from academics.models import AcademicTerm
    term = get_object_or_404(AcademicTerm.objects.select_related("academic_year", "semester"), pk=term_id)
    return term, scoped_department


def _shares(option):
    return tuple(sorted((share.faculty_id, share.share_centis) for share in option.shares))


def _current(prepared, offering_id):
    return tuple(sorted(
        (row["faculty_id"], int(Decimal(row["share"]) * 100))
        for row in prepared.current_assignments if row["offering_id"] == offering_id
    ))


def _proposal_rows(prepared, chosen, comparison):
    offerings = {offering.pk: offering for offering in prepared.offerings}
    faculty = {member.pk: str(member) for member in prepared.faculty}
    reports = {row["faculty_id"]: row for row in comparison["faculty"]}
    rows = []
    for option in sorted(chosen, key=lambda item: item.offering_id):
        old, new = _current(prepared, option.offering_id), _shares(option)
        status = "KEEP" if old == new else "NEW" if not old else "REASSIGN"
        if status == "KEEP":
            reason = ("Retains protected teaching shares linked to timetable data." if option.offering_id in prepared.fixed_offering_ids
                      else "Retains the current teaching shares; changing them did not improve the balance enough to justify reassignment.")
        else:
            affected_ids = sorted({pk for pk, _ in (*old, *new)})
            load_changes = "; ".join(
                f"{faculty[pk]}: {reports[pk]['before']['assigned_load']} → {reports[pk]['after']['assigned_load']} workload units"
                for pk in affected_ids
            )
            imbalance = comparison["summary"]
            reason = (
                f"{load_changes}. Department utilization spread: "
                f"{imbalance['imbalance_before']} → {imbalance['imbalance_after']} percentage points. "
                "The recommended shares satisfy configured hard maximums."
            )
            if option.qualification_match_count:
                reason += " Recorded subject qualification supports this placement."
        rows.append({
            "offering_id": option.offering_id,
            "offering": str(offerings[option.offering_id]),
            "status": status,
            "current": [{"faculty_id": pk, "faculty": faculty.get(pk, str(pk)),
                         "share": f"{centis / 100:.2f}"} for pk, centis in old],
            "proposed": [{"faculty_id": pk, "faculty": faculty.get(pk, str(pk)),
                          "share": f"{centis / 100:.2f}"} for pk, centis in new],
            "reason": reason,
        })
    return rows


def _report_fields(report):
    policy = report["policy"]
    return {
        "assigned_load": f"{report['assigned_load']:.4f}" if report["assigned_load"] is not None else None,
        "recommended_load": f"{policy['recommended_load']:.2f}" if policy["recommended_load"] is not None else None,
        "maximum_load": f"{policy['maximum_load']:.2f}" if policy["maximum_load"] is not None else None,
        "utilization": f"{report['utilization']:.2f}" if report["utilization"] is not None else None,
        "remaining_capacity": f"{report['remaining_capacity']:.4f}" if report["remaining_capacity"] is not None else None,
        "status": report["status"],
    }


def _imbalance(rows, key):
    values = [Decimal(row[key]["utilization"]) for row in rows if row[key]["utilization"] is not None]
    return f"{max(values) - min(values):.2f}" if values else None


def _comparison(prepared, chosen, term):
    selected_ids = {offering.pk for offering in prepared.offerings}
    by_faculty = defaultdict(list)
    for option in chosen:
        offering = next(item for item in prepared.offerings if item.pk == option.offering_id)
        for share in option.shares:
            by_faculty[share.faculty_id].append(FacultySubjectAssignment(
                faculty_id=share.faculty_id, subject_offering=offering,
                share=Decimal(share.share_centis) / 100,
            ))
    faculty_rows = []
    for member in prepared.faculty:
        before = calculate_workload(member, term)
        retained = list(FacultySubjectAssignment.objects.filter(
            faculty=member, subject_offering__academic_term=term,
        ).exclude(subject_offering_id__in=selected_ids).select_related("subject_offering"))
        after = calculate_workload(member, term, assignments_override=[
            *retained, *by_faculty[member.pk],
        ])
        faculty_rows.append({
            "faculty_id": member.pk, "faculty": str(member),
            "before": _report_fields(before), "after": _report_fields(after),
            "difference": f"{after['assigned_load'] - before['assigned_load']:.4f}"
            if after["assigned_load"] is not None and before["assigned_load"] is not None else None,
        })
    changes = sum(option.changed for option in chosen)
    affected_ids = set()
    for option in chosen:
        if option.changed:
            affected_ids.update(pk for pk, _ in _current(prepared, option.offering_id))
            affected_ids.update(pk for pk, _ in _shares(option))
    return {
        "faculty": faculty_rows,
        "summary": {
            "affected_faculty": len(affected_ids),
            "assignment_changes": changes,
            "imbalance_before": _imbalance(faculty_rows, "before"),
            "imbalance_after": _imbalance(faculty_rows, "after"),
            "overload_before": sum(row["before"]["status"] == "OVERLOAD" for row in faculty_rows),
            "overload_after": sum(row["after"]["status"] == "OVERLOAD" for row in faculty_rows),
        },
    }


def _diagnostic(code, message):
    return {"code": code, "severity": "ERROR", "message": message}


def request_balancing_run(*, user, academic_term, department):
    term, scope = _scope(user, academic_term, department)
    with transaction.atomic():
        run = WorkloadRecommendationRun.objects.create(
            academic_term=term, department=scope, initiated_by=user,
        )
        record_event("balancing.requested", actor=user, obj=run,
                     details={"academic_term_id": term.pk})
    try:
        with transaction.atomic():
            scheduling_lock()
            prepared = prepare_balancing_input(academic_term=term, department=scope)
            signature = compute_balancing_source_signature(academic_term=term, department=scope)
            run = WorkloadRecommendationRun.objects.select_for_update().get(pk=run.pk)
            run.source_signature = signature
            run.input_summary = {
                "faculty_count": len(prepared.faculty),
                "offering_count": len(prepared.offerings),
                "mutable_offering_count": len(prepared.mutable_offering_ids),
                "fixed_offering_count": len(prepared.fixed_offering_ids),
                "qualification_note": "A recorded qualification is positive evidence; a missing record is unknown, not disqualification.",
            }
            run.diagnostics = list(prepared.diagnostics)
            if prepared.solver_input is None or any(item["severity"] == "ERROR" for item in prepared.diagnostics):
                run.status = WorkloadRecommendationRun.Status.INPUT_INVALID
            run.save()
            if run.status == WorkloadRecommendationRun.Status.INPUT_INVALID:
                record_event("balancing.failed", actor=user, obj=run,
                             details={"status": run.status, "codes": [row["code"] for row in run.diagnostics]})
        if run.status == WorkloadRecommendationRun.Status.INPUT_INVALID:
            return run
        from .balancing_solver import solve_balancing
        result = solve_balancing(prepared.solver_input)
        with transaction.atomic():
            scheduling_lock()
            run = WorkloadRecommendationRun.objects.select_for_update().get(pk=run.pk)
            if run.status != WorkloadRecommendationRun.Status.PENDING:
                raise BalancingError("This recommendation run has already completed.")
            current_signature = compute_balancing_source_signature(academic_term=term, department=scope)
            run.raw_solver_status = result.raw_status
            run.solver_stats = asdict(result.statistics)
            if current_signature != signature:
                run.status = WorkloadRecommendationRun.Status.STALE
                run.diagnostics = [_diagnostic("INPUT_CHANGED", "Teaching or policy data changed while solving. Request a new run.")]
            elif result.raw_status in ("OPTIMAL", "FEASIBLE"):
                run.comparison = _comparison(prepared, result.chosen_options, term)
                run.proposed_assignments = _proposal_rows(prepared, result.chosen_options, run.comparison)
                run.status = WorkloadRecommendationRun.Status.PROPOSAL_READY
            elif result.raw_status == "INFEASIBLE":
                run.status = WorkloadRecommendationRun.Status.INFEASIBLE
                run.diagnostics = [_diagnostic("NO_FEASIBLE_BALANCE", "No assignment satisfies all hard requirements. Review capacity and offering coverage.")]
            else:
                run.status = WorkloadRecommendationRun.Status.FAILED
                run.diagnostics = [_diagnostic("SOLVER_UNRESOLVED", "The bounded optimizer did not produce a valid recommendation.")]
            run.save()
            record_event("balancing.generated" if run.status == WorkloadRecommendationRun.Status.PROPOSAL_READY else "balancing.failed",
                         actor=user, obj=run, details={"status": run.status, "raw_solver_status": run.raw_solver_status,
                                                       "assignment_changes": run.comparison.get("summary", {}).get("assignment_changes", 0)})
            return run
    except (PermissionDenied, Http404, BalancingError):
        raise
    except Exception:
        with transaction.atomic():
            current = WorkloadRecommendationRun.objects.select_for_update().get(pk=run.pk)
            if current.status == WorkloadRecommendationRun.Status.PENDING:
                current.status = WorkloadRecommendationRun.Status.FAILED
                current.diagnostics = [_diagnostic("GENERATION_FAILED", "Recommendation generation could not be completed.")]
                current.save(update_fields=["status", "diagnostics", "updated_at"])
                record_event("balancing.failed", actor=user, obj=current,
                             details={"status": current.status, "codes": ["GENERATION_FAILED"]})
        raise


def _authorized_run(user, run):
    row = scoped_balancing_run(user, run.pk if isinstance(run, WorkloadRecommendationRun) else run)
    require_access(user, "workloads.generate_workloadrecommendation")
    require_access(user, "academics.view_academicterm")
    return row


def _match_proposal(run, prepared):
    by_offering = {demand.offering_id: demand for demand in prepared.solver_input.offerings}
    rows = run.proposed_assignments
    if not isinstance(rows, list) or len(rows) != len(by_offering):
        raise ValidationError("The stored recommendation has an invalid offering count.")
    choices = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("offering_id")) is not int:
            raise ValidationError("The stored recommendation contains an invalid offering.")
        offering_id = row["offering_id"]
        if offering_id in seen or offering_id not in by_offering:
            raise ValidationError("The stored recommendation contains a duplicate or foreign offering.")
        seen.add(offering_id)
        proposed = row.get("proposed")
        if not isinstance(proposed, list):
            raise ValidationError("The stored recommendation has invalid teaching shares.")
        try:
            pairs = tuple(sorted((item["faculty_id"], int(Decimal(item["share"]) * 100)) for item in proposed))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise ValidationError("The stored recommendation has invalid teaching shares.") from None
        matches = [option for option in by_offering[offering_id].options if _shares(option) == pairs]
        if len(matches) != 1:
            raise ValidationError("The stored recommendation is no longer an eligible option.")
        choices.append(matches[0])
    if _proposal_rows(prepared, choices, _comparison(prepared, choices, run.academic_term)) != rows:
        raise ValidationError("The stored recommendation details were modified.")
    return choices


def _apply_proposal(run, prepared, choices, user):
    options = {option.offering_id: option for option in choices}
    mutable_ids = set(prepared.mutable_offering_ids)
    offerings = {offering.pk: offering for offering in prepared.offerings}
    for offering_id in sorted(mutable_ids):
        option = options[offering_id]
        if _shares(option) == _current(prepared, offering_id):
            continue
        existing = list(FacultySubjectAssignment.objects.select_for_update().filter(
            subject_offering_id=offering_id,
        ))
        for assignment in existing:
            record_event("facultysubjectassignment.deleted", actor=user, obj=assignment,
                         details={"academic_term_id": run.academic_term_id, "before": {
                             "faculty_id": assignment.faculty_id, "subject_offering_id": offering_id,
                             "share": str(assignment.share),
                         }, "balancing_run_id": run.pk})
            assignment.delete()
        for share in option.shares:
            assignment = FacultySubjectAssignment(
                faculty_id=share.faculty_id, subject_offering=offerings[offering_id],
                share=Decimal(share.share_centis) / 100, created_by=user,
            )
            check_share(assignment)
            assignment.save()
            record_event("facultysubjectassignment.created", actor=user, obj=assignment,
                         details={"academic_term_id": run.academic_term_id, "after": {
                             "faculty_id": share.faculty_id, "subject_offering_id": offering_id,
                             "share": str(assignment.share),
                         }, "balancing_run_id": run.pk})
    for member in prepared.faculty:
        report = calculate_workload(member, run.academic_term)
        policy = resolve_policy(member, run.academic_term)
        if policy["enforce_maximum"]:
            if policy["maximum_load"] is None or report["assigned_load"] is None or report["assigned_load"] > policy["maximum_load"]:
                raise ValidationError("The accepted assignments would exceed a hard workload maximum.")


def accept_balancing_run(*, user, run):
    row = _authorized_run(user, run)
    try:
        with transaction.atomic():
            scheduling_lock()
            row = WorkloadRecommendationRun.objects.select_for_update().get(pk=row.pk)
            if row.status != WorkloadRecommendationRun.Status.PROPOSAL_READY:
                raise BalancingError("Only a ready recommendation can be accepted.")
            signature = compute_balancing_source_signature(academic_term=row.academic_term, department=row.department)
            if signature != row.source_signature:
                failure = WorkloadRecommendationRun.Status.STALE
            else:
                prepared = prepare_balancing_input(academic_term=row.academic_term, department=row.department)
                if prepared.solver_input is None or any(item["severity"] == "ERROR" for item in prepared.diagnostics):
                    failure = WorkloadRecommendationRun.Status.VALIDATION_FAILED
                else:
                    choices = _match_proposal(row, prepared)
                    if _comparison(prepared, choices, row.academic_term) != row.comparison:
                        raise ValidationError("The stored before/after comparison was modified.")
                    _apply_proposal(row, prepared, choices, user)
                    row.status = WorkloadRecommendationRun.Status.ACCEPTED
                    row.accepted_by = user
                    row.accepted_at = timezone.now()
                    row.save(update_fields=["status", "accepted_by", "accepted_at", "updated_at"])
                    record_event("balancing.accepted", actor=user, obj=row,
                                 details={"assignment_changes": row.comparison["summary"]["assignment_changes"]})
                    return row
            row.status = failure
            row.diagnostics = [_diagnostic("INPUT_CHANGED" if failure == "STALE" else "INPUT_INVALID",
                                           "Teaching or policy inputs changed; request a fresh recommendation.")]
            row.save(update_fields=["status", "diagnostics", "updated_at"])
            record_event("balancing.failed", actor=user, obj=row,
                         details={"status": row.status, "codes": [item["code"] for item in row.diagnostics]})
            return row
    except ValidationError as error:
        with transaction.atomic():
            row = WorkloadRecommendationRun.objects.select_for_update().get(pk=row.pk)
            if row.status == WorkloadRecommendationRun.Status.PROPOSAL_READY:
                row.status = WorkloadRecommendationRun.Status.VALIDATION_FAILED
                row.diagnostics = [_diagnostic("PROPOSAL_INVALID", "; ".join(error.messages))]
                row.save(update_fields=["status", "diagnostics", "updated_at"])
                record_event("balancing.failed", actor=user, obj=row,
                             details={"status": row.status, "codes": ["PROPOSAL_INVALID"]})
            return row


def discard_balancing_run(*, user, run):
    row = _authorized_run(user, run)
    with transaction.atomic():
        row = WorkloadRecommendationRun.objects.select_for_update().get(pk=row.pk)
        if row.status != WorkloadRecommendationRun.Status.PROPOSAL_READY:
            raise BalancingError("Only a ready recommendation can be discarded.")
        row.status = WorkloadRecommendationRun.Status.DISCARDED
        row.discarded_at = timezone.now()
        row.save(update_fields=["status", "discarded_at", "updated_at"])
        record_event("balancing.discarded", actor=user, obj=row,
                     details={"assignment_changes": row.comparison.get("summary", {}).get("assignment_changes", 0)})
        return row
