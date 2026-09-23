"""Audited timetable generation, preview, and atomic acceptance workflow."""

from __future__ import annotations

from dataclasses import asdict

from django.db import transaction
from django.utils import timezone

from audit.services import record_event

from .conflicts import get_schedule_conflicts, validate_candidate_schedule
from .generation_inputs import (
    GenerationOverrides, get_generation_run, prepare_generation_input,
    require_generation_access,
)
from .generation_validation import _resolve_generation_contract, serialize_proposals
from .locking import scheduling_lock
from .models import ScheduleEntry, ScheduleGenerationRun
from .mutations import mark_draft
from .queries import get_schedule


class InvalidRunTransition(Exception):
    """The run was not in the expected lifecycle state under its row lock."""


class StaleProposal(Exception):
    """The scheduling dependencies changed after the proposal was captured."""


class _InvalidProposal(Exception):
    def __init__(self, conflicts):
        self.conflicts = tuple(conflicts)
        super().__init__("Generation proposal failed validation.")


def _has_errors(issues) -> bool:
    return any(item.severity == "ERROR" for item in issues)


def _issue_json(issue):
    return {"code": issue.code, "severity": issue.severity, "message": issue.message}


def _conflict_json(conflict):
    return {"code": conflict.code, "severity": conflict.severity,
            "message": conflict.message, "remedy": conflict.remedy}


def _internal_diagnostic():
    return [{"code": "GENERATION_FAILED", "severity": "ERROR",
             "message": "Timetable generation could not be completed."}]


def _save(run, fields):
    run.save(update_fields=[*fields, "updated_at"])


def _require_status(run, expected):
    if run.status != expected:
        raise InvalidRunTransition(
            f"Generation run is {run.status}; expected {expected}."
        )


def _lock_internal_run(run_id, expected):
    run = ScheduleGenerationRun.objects.select_for_update(of=("self",)).get(pk=run_id)
    _require_status(run, expected)
    return run


def persist_snapshots_if_expected(run_id, expected_status, prepared):
    """Requires an active transaction with the scheduling advisory lock held."""
    run = _lock_internal_run(run_id, expected_status)
    run.configuration_snapshot = prepared.configuration_snapshot
    run.input_summary = prepared.input_summary
    run.source_signature = prepared.source_signature
    run.solver_time_limit_seconds = prepared.configuration_snapshot.get("solver_time_limit_seconds")
    _save(run, ("configuration_snapshot", "input_summary", "source_signature",
                "solver_time_limit_seconds"))
    return run


def mark_running_if_expected(run_id, expected_status="PENDING"):
    with transaction.atomic():
        run = _lock_internal_run(run_id, expected_status)
        run.status = ScheduleGenerationRun.Status.RUNNING
        run.started_at = timezone.now()
        _save(run, ("status", "started_at"))
        return run


def finish_if_expected(run_id, expected_status, target_status, *, diagnostics):
    with transaction.atomic():
        run = _lock_internal_run(run_id, expected_status)
        run.status = target_status
        run.finished_at = timezone.now()
        run.diagnostics = diagnostics
        _save(run, ("status", "finished_at", "diagnostics"))
        return run


def finish_failure_if_expected(*, user, run_id, expected_status, target_status, diagnostics):
    with transaction.atomic():
        run = get_generation_run(user, run_id, lock=True)
        _require_status(run, expected_status)
        run.status = target_status
        if expected_status in ("PENDING", "RUNNING"):
            run.finished_at = timezone.now()
            fields = ("status", "finished_at", "diagnostics")
        else:
            fields = ("status", "diagnostics")
        run.diagnostics = diagnostics
        _save(run, fields)
        record_event("generation.failed", actor=user, obj=run,
                     details={"status": target_status,
                              "codes": [item.get("code") for item in diagnostics]})
        return run


def _result_fields(run, result, prepared):
    if result.raw_status not in ScheduleGenerationRun.SolverStatus.values:
        raise ValueError("Unknown raw solver status.")
    run.solver_status = result.raw_status
    run.runtime_seconds = float(result.statistics.wall_time_seconds)
    run.solver_statistics = asdict(result.statistics)
    run.penalty_breakdown = asdict(result.penalties)
    run.objective_value = result.objective_value
    run.best_bound = result.best_bound
    run.diagnostics = [*map(_issue_json, prepared.issues), *map(_issue_json, result.issues)]


def finish_generation_if_expected(run_id, result, prepared, user):
    """Finalize an exact solver status; only validated successes gain proposal JSON."""
    with transaction.atomic():
        run = _lock_internal_run(run_id, "RUNNING")
        _result_fields(run, result, prepared)
        run.finished_at = timezone.now()
        if result.raw_status in ("OPTIMAL", "FEASIBLE"):
            rows = serialize_proposals(result.proposals, run.configuration_snapshot)
            resolved = _resolve_generation_contract(
                run=run, prepared=prepared, proposal_rows=rows, user=user,
            )
            conflicts = list(resolved.conflicts)
            if not _has_errors(conflicts):
                schedule = get_schedule(user, run.schedule_id, action="change")
                conflicts.extend(validate_candidate_schedule(
                    schedule, retained_entries=resolved.retained_entries,
                    proposed_entries=resolved.entries, user=user,
                ))
            run.diagnostics.extend(map(_conflict_json, conflicts))
            if _has_errors(conflicts):
                run.status = ScheduleGenerationRun.Status.VALIDATION_FAILED
            else:
                run.status = ScheduleGenerationRun.Status.PROPOSAL_READY
                run.proposed_meetings = rows
                run.proposed_meeting_count = len(rows)
        elif result.raw_status == "INFEASIBLE":
            run.status = ScheduleGenerationRun.Status.INFEASIBLE
        else:
            run.status = ScheduleGenerationRun.Status.FAILED
        fields = ["status", "solver_status", "finished_at", "runtime_seconds",
                  "solver_statistics", "penalty_breakdown", "objective_value",
                  "best_bound", "diagnostics"]
        if run.status == "PROPOSAL_READY":
            fields.extend(("proposed_meetings", "proposed_meeting_count"))
        _save(run, fields)
        if run.status in ("VALIDATION_FAILED", "INFEASIBLE", "FAILED"):
            record_event("generation.failed", actor=user, obj=run,
                         details={"status": run.status,
                                  "codes": [item.get("code") for item in run.diagnostics]})
        return run


def request_generation(*, user, schedule_id, strategy, overrides):
    if strategy not in ScheduleGenerationRun.Strategy.values:
        raise ValueError("Unknown timetable generation strategy.")
    if not isinstance(overrides, GenerationOverrides):
        raise TypeError("overrides must be a GenerationOverrides instance.")
    require_generation_access(user, strategy)
    with transaction.atomic():
        schedule = get_schedule(user, schedule_id, action="change")
        run = ScheduleGenerationRun.objects.create(
            schedule=schedule, academic_term=schedule.academic_term,
            department=schedule.department, requested_by=user,
            strategy=strategy, status=ScheduleGenerationRun.Status.PENDING,
        )
        record_event("generation.requested", actor=user, obj=run,
                     details={"strategy": strategy})
    try:
        with transaction.atomic():
            scheduling_lock()
            prepared = prepare_generation_input(
                user=user, schedule_id=schedule_id, strategy=strategy, overrides=overrides,
            )
            persist_snapshots_if_expected(run.pk, "PENDING", prepared)
        if _has_errors(prepared.issues) or prepared.solver_input is None:
            return finish_if_expected(
                run.pk, "PENDING", "INPUT_INVALID",
                diagnostics=list(map(_issue_json, prepared.issues)),
            )
        mark_running_if_expected(run.pk)
        # Importing CP-SAT is intentionally delayed until after readiness and lock release.
        from .solver.engine import solve
        result = solve(prepared.solver_input)
        return finish_generation_if_expected(run.pk, result, prepared, user)
    except InvalidRunTransition:
        raise
    except Exception:
        current = ScheduleGenerationRun.objects.only("status").get(pk=run.pk)
        if current.status in ("PENDING", "RUNNING"):
            finish_failure_if_expected(
                user=user, run_id=run.pk, expected_status=current.status,
                target_status="FAILED", diagnostics=_internal_diagnostic(),
            )
        raise


def _captured_overrides(snapshot):
    if type(snapshot) is not dict:
        raise _InvalidProposal(())
    fields = (
        "solver_time_limit_seconds", "faculty_preference_weight",
        "faculty_gap_weight", "section_gap_weight",
        "meeting_distribution_weight", "room_fit_weight",
    )
    values = {}
    for field in fields:
        value = snapshot.get(field)
        if type(value) is not int or value < (1 if field == "solver_time_limit_seconds" else 0):
            raise _InvalidProposal(())
        values[field] = value
    return GenerationOverrides(**values)


def discard_generation(*, user, run_id):
    require_generation_access(user, ScheduleGenerationRun.Strategy.FILL_GAPS)
    with transaction.atomic():
        run = get_generation_run(user, run_id, lock=True)
        require_generation_access(user, run.strategy)
        _require_status(run, "PROPOSAL_READY")
        run.status = ScheduleGenerationRun.Status.DISCARDED
        run.discarded_at = timezone.now()
        _save(run, ("status", "discarded_at"))
        record_event("generation.discarded", actor=user, obj=run,
                     details={"strategy": run.strategy})
        return run


def _before_accept_mutation(run, prepared):
    """Narrow test seam after validation while the advisory lock is held."""


def _accept_locked(*, user, run_id):
    with transaction.atomic():
        scheduling_lock()
        run = get_generation_run(user, run_id, lock=True)
        require_generation_access(user, run.strategy)
        _require_status(run, "PROPOSAL_READY")
        overrides = _captured_overrides(run.configuration_snapshot)
        prepared = prepare_generation_input(
            user=user, schedule_id=run.schedule_id, strategy=run.strategy,
            overrides=overrides,
        )
        if prepared.source_signature != run.source_signature:
            raise StaleProposal("Scheduling dependencies changed.")
        if _has_errors(prepared.issues) or prepared.solver_input is None:
            raise _InvalidProposal(())
        resolved = _resolve_generation_contract(
            run=run, prepared=prepared, proposal_rows=run.proposed_meetings, user=user,
        )
        conflicts = list(resolved.conflicts)
        schedule = get_schedule(user, run.schedule_id, action="change")
        if not _has_errors(conflicts):
            conflicts.extend(validate_candidate_schedule(
                schedule, retained_entries=resolved.retained_entries,
                proposed_entries=resolved.entries, user=user,
            ))
        if _has_errors(conflicts):
            raise _InvalidProposal(conflicts)
        _before_accept_mutation(run, prepared)
        deleted_ids = []
        if run.strategy == ScheduleGenerationRun.Strategy.REPLACE_UNLOCKED:
            deleted_ids = list(ScheduleEntry.objects.filter(
                schedule=schedule, is_locked=False,
                pk__in=prepared.replace_entry_ids,
            ).order_by("pk").values_list("pk", flat=True))
            if deleted_ids != list(prepared.replace_entry_ids):
                raise _InvalidProposal(())
            ScheduleEntry.objects.filter(pk__in=deleted_ids, schedule=schedule).delete()
        for entry in resolved.entries:
            entry.created_by = user
            entry.generation_run = run
            entry.is_locked = False
        ScheduleEntry.objects.bulk_create(resolved.entries)
        persisted = get_schedule_conflicts(schedule, user=user)
        if _has_errors(persisted):
            raise _InvalidProposal(persisted)
        mark_draft(schedule, user)
        run.status = ScheduleGenerationRun.Status.ACCEPTED
        run.accepted_at = timezone.now()
        run.accepted_by = user
        run.accepted_meeting_count = len(resolved.entries)
        _save(run, ("status", "accepted_at", "accepted_by", "accepted_meeting_count"))
        record_event("generation.succeeded", actor=user, obj=run,
                     details={"created_count": len(resolved.entries)})
        record_event("generation.accepted", actor=user, obj=run,
                     details={"created_count": len(resolved.entries)})
        if run.strategy == ScheduleGenerationRun.Strategy.REPLACE_UNLOCKED:
            record_event("schedule.entries_replaced", actor=user, obj=schedule,
                         details={"generation_run_id": run.pk,
                                  "deleted_entry_ids": deleted_ids,
                                  "deleted_count": len(deleted_ids),
                                  "created_count": len(resolved.entries)})
        return run


def accept_generation(*, user, run_id):
    require_generation_access(user, ScheduleGenerationRun.Strategy.FILL_GAPS)
    # Scope is checked before taking the advisory lock; the locked lookup checks again.
    scoped_run = get_generation_run(user, run_id)
    require_generation_access(user, scoped_run.strategy)
    try:
        return _accept_locked(user=user, run_id=run_id)
    except InvalidRunTransition:
        raise
    except StaleProposal:
        return finish_failure_if_expected(
            user=user, run_id=run_id, expected_status="PROPOSAL_READY",
            target_status="STALE", diagnostics=[{"code": "SOURCE_CHANGED",
                "severity": "ERROR", "message": "Scheduling data changed; generate a new proposal."}],
        )
    except _InvalidProposal as exc:
        diagnostics = list(map(_conflict_json, exc.conflicts)) or [{
            "code": "PROPOSAL_DEMAND", "severity": "ERROR",
            "message": "The stored proposal is no longer valid.",
        }]
        return finish_failure_if_expected(
            user=user, run_id=run_id, expected_status="PROPOSAL_READY",
            target_status="VALIDATION_FAILED", diagnostics=diagnostics,
        )
    except Exception:
        finish_failure_if_expected(
            user=user, run_id=run_id, expected_status="PROPOSAL_READY",
            target_status="FAILED", diagnostics=_internal_diagnostic(),
        )
        raise
