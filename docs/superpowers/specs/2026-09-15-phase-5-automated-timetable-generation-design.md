# Phase 5 Automated Timetable Generation Design

**Date:** 2026-09-15
**Status:** Approved for implementation
**Authoritative domain:** `timetabling`
**Solver:** Google OR-Tools CP-SAT 9.15.6755

## Purpose

Phase 5 adds bounded, synchronous timetable generation for an existing departmental `Schedule`. It decides when and where already-assigned teaching will meet. It creates a persistent proposal, validates that proposal with the same deterministic rules used by manual scheduling, and requires an authorized user to accept it before any `ScheduleEntry` rows are written.

The solver produces draft work only. It does not select faculty, approve or publish schedules, choose among workload recommendations, call an LLM, or modify the isolated legacy scheduler.

## Authority and legacy boundary

The Phase 4 `timetabling` models and conflict engine are authoritative. The existing `scheduling/autoscheduler.py` continues to target the legacy `Term`, `Block`, `TimeSlot`, and `Assignment` models. Its URLs remain unmounted, its code is not imported by Phase 5, and it is neither adapted nor deleted.

OR-Tools is declared once as `ortools==9.15.6755`. The active Windows/Python 3.14 environment already imports this release successfully.

## Domain changes

### AssignmentMeetingRequirement

`AssignmentMeetingRequirement` is a tracked timetable record with:

- `assignment`: protected foreign key to `FacultySubjectAssignment`;
- `meeting_type`: `lecture` or `laboratory`;
- `meetings_per_week`: positive small integer;
- `duration_minutes`: positive integer.

The pair `(assignment, meeting_type)` is unique. The configured total, `meetings_per_week × duration_minutes`, must equal the assignment's authoritative component minutes, `SubjectOffering.<component>_hours × assignment.share × 60`. A component with zero assigned hours cannot have a positive meeting requirement. A requirement may be stored before scheduling configuration exists, but the readiness checker rejects it if its duration is not aligned with the active configuration's slot increment.

Requirements are assignment-specific because Phase 4 measures meeting completeness per teaching assignment and share. This supports split teaching as separate meetings. Simultaneous team teaching remains outside the domain.

An offering is ready only when its existing teaching shares total exactly `1.0`, using the Phase 3 assignment/share semantics. Phase 5 never adds, removes, or chooses faculty assignments.

### SchedulingConfiguration

`SchedulingConfiguration` is a tracked record unique by `(academic_term, department)` with:

- `allowed_weekdays`: a JSON list containing unique integers from 1 through 7;
- `earliest_start` and `latest_end`;
- `slot_increment_minutes`;
- `solver_time_limit_seconds`;
- `random_seed`;
- `worker_count`;
- `faculty_preference_weight`;
- `faculty_gap_weight`;
- `section_gap_weight`;
- `meeting_distribution_weight`;
- `room_fit_weight`.

The time range cannot cross midnight. Both boundaries use whole minutes and align to the increment from midnight; the range length is divisible by the increment. Slot increment, runtime, and worker count are positive. Runtime is capped at 300 seconds and workers at 64. Objective weights are nonnegative integers. The model contains no institutional operating-hour defaults: a missing configuration is `INPUT_INVALID`. Development seed values are explicitly fictional examples.

### ScheduleEntry changes

`ScheduleEntry.is_locked` defaults to `True`. The migration gives every pre-Phase-5 entry that value. Manual creation leaves `generation_run` null and defaults to locked.

`ScheduleEntry.generation_run` is a nullable, protected foreign key to `ScheduleGenerationRun`. Accepted generated rows explicitly set `is_locked=False` and populate this field. Manual editing never clears provenance. A generated row can be locked later, after which all generation strategies preserve it.

### ScheduleGenerationRun

Each generation request creates a new historical row. A run is never reused or deleted through the application. It contains:

- schedule, academic term, department, and initiating user;
- generation strategy;
- generation lifecycle status and separate raw solver status;
- requested, started, finished, accepted, and discarded timestamps where applicable;
- accepting user where applicable;
- configured time limit and measured runtime;
- objective value and best bound when defined;
- immutable configuration and input-summary JSON snapshots;
- deterministic source signature;
- proposed-meeting JSON snapshot;
- structured diagnostics and solver statistics;
- proposed and accepted meeting counts.

Lifecycle states are `PENDING`, `RUNNING`, `PROPOSAL_READY`, `ACCEPTED`, `DISCARDED`, `INPUT_INVALID`, `INFEASIBLE`, `STALE`, `VALIDATION_FAILED`, and `FAILED`. Raw solver states are blank before solving and otherwise `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `MODEL_INVALID`, or `UNKNOWN`.

Only `OPTIMAL` and `FEASIBLE` may lead to `PROPOSAL_READY`. Neither accepts a proposal automatically. `UNKNOWN` is not success. Accept and discard operations lock the run row and require its current status to be exactly `PROPOSAL_READY`. Repeated or competing terminal actions fail without changing the existing terminal state. A failure-record update also checks that the run is still in the state that failed, so it cannot overwrite an accepted or discarded run. Once a run reaches a terminal state, the generation service does not alter its snapshots or reuse it for another attempt. The proposal JSON remains historical evidence and is never treated as current database truth.

Each proposal item contains exactly `assignment_id`, `meeting_requirement_id`, `occurrence_index`, `room_id`, `day_of_week`, `start_time`, `end_time`, and `meeting_type`. The acceptance service resolves those identifiers again and verifies all derived term, department, faculty, offering, and section relationships.

## Time and interval model

CP-SAT works in integer slot indexes. Index zero is `earliest_start`; each subsequent index adds `slot_increment_minutes`. A meeting duration must be an exact positive number of slots. Candidate end times may equal `latest_end`.

Weekdays use Phase 4 integers 1 through 7. Intervals remain half-open: `[start, end)`. Meetings ending when another starts are adjacent and legal. A configured weekday that does not occur within the academic term yields no candidates for that day.

## Solver input and readiness

The scoped input builder accepts an authenticated user, a submitted schedule ID, an explicit strategy, and run overrides for the time limit and objective weights. It resolves the schedule through `get_schedule` and applies existing teaching and room selectors before exposing resources as eligible choices. Submitted IDs never become authority.

The builder collects every active `SubjectOffering` in the selected schedule's exact term and department, including offerings with missing assignments so readiness can report them. It then collects assignments and shares, assignment meeting requirements, the offering section and room policy, faculty availability, eligible scoped rooms, room closures, and schedule occupancy.

Other schedules remain authoritative occupancy. A shared `authoritative_occupancy(schedule, excluded_entry_ids=())` query service supplies persisted peer meetings to manual validation, the existing dataset, and solver input preparation. For each candidate it calls the Phase 4 `terms_share_weekday(first_term, second_term, weekday)` helper, which requires the weekday to occur inside the intersection of the two term date ranges. These entries may constrain generation even when the requester cannot view their details; diagnostics redact their identity.

Strategies are explicit:

- `FILL_GAPS` preserves every current entry on the selected schedule. Valid existing entries count toward matching assignment/type requirements and all existing entries are fixed occupancy.
- `REPLACE_UNLOCKED` preserves locked entries and excludes unlocked entries on the selected schedule from the proposed final timetable. No rows are deleted while solving or previewing.

Readiness returns structured issues and does not call CP-SAT when any error exists. Errors include:

- missing or invalid scheduling configuration;
- inactive or inconsistent schedule, term, organization, offering, faculty, section, room type, or parent resource;
- missing offering section;
- no faculty assignment or incomplete/over-complete teaching shares;
- missing, duplicate, contradictory, or grid-misaligned meeting requirements;
- fixed entries with invalid duration, excess count, conflicts, or no matching requirement;
- no active scoped room satisfying a mandatory room type or hard capacity rule;
- a required meeting with zero valid candidate placements.

`AVAILABLE` availability is informational. Only `UNAVAILABLE` removes candidates; a fully-contained `PREFERRED` interval affects the objective.

## Pure solver contract

The solver package contains no Django models, querysets, users, requests, messages, or audit calls. Immutable dataclasses carry primitive identifiers, integer slots, and tuples:

- `ObjectiveWeights`;
- `SchedulingPolicy`;
- `MeetingDemand` and `FixedMeeting`;
- `CandidatePlacement`;
- `SolverInput`;
- `ProposedMeeting`;
- `ReadinessIssue`;
- `PenaltyBreakdown`;
- `SolverStatistics`;
- `SolverResult`.

The ORM input builder converts scoped records into this contract. The CP-SAT adapter accepts `SolverInput` and returns `SolverResult`.

## Candidate generation

Candidate generation is deterministic and ordered by assignment, meeting type, occurrence, weekday, start slot, and room ID. It creates sparse `(meeting, day, start, room)` placements only after checking:

- configured day and operating window;
- exact duration fit;
- term weekday occurrence;
- hard faculty unavailability;
- room closure across overlapping term calendars;
- fixed faculty, room, and section occupancy;
- active resource and ownership scope;
- mandatory room type;
- hard room capacity.

Optional room-type mismatch and warning-only capacity inefficiency remain candidates with objective penalties. The builder records candidate counts for diagnostics.

Preprocessing is also bounded. Deployment settings cap preprocessing at 10 seconds, total candidate placements at 100,000, and candidate slot-coverage literals at 2,000,000; each value is environment-configurable to a positive integer. Crossing a cap stops before CP-SAT, records `INPUT_INVALID` with a `MODEL_SIZE_LIMIT` or `PREPROCESSING_TIMEOUT` diagnostic, and suggests narrowing the configured window or raising the documented deployment cap. The builder checks elapsed monotonic time while expanding each demand. It never materializes an unbounded Cartesian product.

## CP-SAT model

Each candidate placement has one Boolean decision variable. Every unsatisfied meeting occurrence has an exactly-one constraint. Because all meetings align to the configured grid, faculty/day/slot, room/day/slot, and section/day/slot buckets use `AddAtMostOne` across the candidate variables occupying that slot. This enforces overlap without enumerating all candidate pairs. Fixed occupancy has already removed impossible candidates, while the same facts remain in the structured input for diagnostics.

This implements:

- faculty, room, and section non-overlap;
- faculty hard unavailability;
- room closures;
- active, scoped, term-compatible resources;
- exact required meeting count and duration;
- mandatory room type and hard capacity;
- locked/manual occupancy;
- configured scheduling windows;
- the existing assigned faculty;
- one placement per required occurrence.

The model never contains a faculty-choice variable.

## Objective

The model minimizes one integer weighted sum. Every component is reported separately:

- faculty preferred-period penalty: when at least one preferred interval exists for that faculty, one unit for each selected meeting not fully contained in any preferred interval for that faculty/day; no recorded preference contributes zero;
- faculty idle-gap penalty: the number of unoccupied grid slots strictly between the first and last occupied slots for each faculty/day in the proposed final timetable, including retained and applicable peer meetings as fixed occupancy;
- section idle-gap penalty: the same span-minus-occupied calculation for each section/day, including retained and applicable peer meetings for that section;
- meeting-distribution penalty: for each assignment/type/day in the proposed final timetable, `max(0, retained_or_selected_occurrences - 1)`;
- room-fit penalty: one unit for an optional room-type mismatch plus `max(0, room_capacity - expected_size)` when expected size is known; unknown expected size contributes zero.

A zero weight removes that component from the objective. Tests compare invariants and objective relationships using one worker and a fixed seed. Excessive-consecutive-class, undesirable-period, faculty-selection, and workload-balancing objectives are excluded.

## Status mapping and diagnostics

The CP-SAT adapter maps the five documented OR-Tools states without collapsing them. `FEASIBLE` and `OPTIMAL` return complete proposed meetings. `INFEASIBLE`, `MODEL_INVALID`, and `UNKNOWN` return no proposal.

Readiness diagnostics state concrete input defects. Infeasibility diagnostics use candidate counts and conservative resource-capacity checks to display “Potential blocking conditions detected.” They may identify zero candidate sets, room bottlenecks, faculty-window scarcity, locked conflicts, or aggregate weekly capacity. They do not claim a minimal unsatisfiable core.

Recorded normal-user statistics are limited to wall time, objective, bound, branches, conflicts, candidate/variable counts, generated meeting count, seed, worker count, and time limit.

## Shared deterministic validation

Phase 4 validation is refactored around:

```python
validate_candidate_schedule(
    schedule,
    *,
    retained_entries,
    proposed_entries,
    user=None,
) -> list[Conflict]
```

It validates unsaved proposals without temporary database writes. It combines retained rows, proposed rows, and applicable persisted peer occupancy. `detect_entry_conflicts` remains the single-entry interface. `get_schedule_conflicts` becomes the persisted-schedule wrapper around the same underlying rule and de-duplication logic.

When an `AssignmentMeetingRequirement` exists, the shared validator enforces exact count and duration as blocking conflicts. Assignments without Phase 5 requirements retain the Phase 4 aggregate-hours warning for manual compatibility. The source remains authoritative for term, organization, activity, faculty availability, room rules, capacity severity, and overlap semantics.

Every successful CP-SAT result is reconstructed as unsaved `ScheduleEntry` objects and passed through this validator before a proposal becomes ready. A separate generation-contract validator checks the exact proposal JSON schema, scoped identifier resolution, one-to-one membership in the current remaining-demand manifest, occurrence uniqueness, configured count and duration, allowed weekday, window/grid alignment, eligible room membership, strategy-specific retained/replaced membership, and absence of extra meetings. These checks cover Phase 5 rules that do not apply to an arbitrary Phase 4 manual entry. Acceptance repeats both validators against freshly resolved objects.

## Generation and acceptance workflow

Generation is synchronous and bounded:

1. Authorize generation and resolve the scoped schedule.
2. Create a new `PENDING` run and atomic `generation.requested` audit event.
3. In a short transaction, acquire the shared scheduling advisory lock before collecting any source rows; build the scoped input, configuration snapshot, and source signature while that lock prevents every feasibility-dependency writer from committing a change. Release the transaction before solving.
4. If readiness fails, finish as `INPUT_INVALID` without importing or invoking the solver adapter.
5. Mark the run `RUNNING`, call CP-SAT, and capture status and metrics.
6. For `FEASIBLE` or `OPTIMAL`, reconstruct unsaved entries and run candidate-set validation.
7. Validation errors produce `VALIDATION_FAILED`; otherwise store the immutable proposal snapshot and mark `PROPOSAL_READY`.
8. Display the weekly proposal, strategy, source-entry summary, objective penalties, runtime, warnings, and diagnostics.
9. An authorized user explicitly accepts or discards the proposal through POST-only endpoints.

Acceptance uses one database transaction:

1. Acquire the Phase 4 PostgreSQL advisory transaction lock.
2. Lock the run row and require `PROPOSAL_READY`, then re-resolve the schedule, configuration, proposal identifiers, and relevant dependency rows through scoped queries.
3. Recompute the deterministic dependency signature before mutation.
4. If it differs, roll back and finish the run as `STALE`; no schedule rows change.
5. Reconstruct candidates from stable identifiers and values rather than trusting model instances or posted data.
6. Revalidate the complete proposed schedule.
7. For `REPLACE_UNLOCKED`, delete only current unlocked entries and record their IDs/count in the controlled audit payload. `FILL_GAPS` deletes nothing.
8. Save every proposed row with `generation_run=run` and explicit `is_locked=False`.
9. Re-run persisted full-schedule validation; any blocking conflict raises and rolls back all entry changes.
10. Mark the schedule `DRAFT`, mark the run `ACCEPTED`, and write generation-success, acceptance, and replacement audit events inside the same transaction.

If persistence or success auditing fails, all entry, schedule, and accepted-run changes roll back. A separate failure-record transaction may mark the unchanged proposal run `VALIDATION_FAILED`, `STALE`, or `FAILED` and record a failure event. It never records success.

## Dependency signature

The signature extends Phase 4's deterministic digest to include configuration, assignment meeting requirements, lock/provenance fields, offering requirements, sections, assignments, availability, eligible rooms, room closures, the selected schedule, and applicable entries. Generation-run lifecycle and proposal JSON are excluded so recording a run cannot stale itself.

The current conservative institution-wide digest is retained. That can cause unrelated changes to require regeneration, but it cannot accept a proposal after relevant source data changes. A PostgreSQL trigger function acquires advisory transaction lock `74190304` before every insert, update, or delete on the feasibility dependency tables, including calendars, organizations, subjects, faculty, rooms, offerings, assignments, availability, timetable configuration/requirements, closures, schedules, and entries. Input capture and acceptance acquire the same lock explicitly before reading. This serializes relevant writers for each short capture/acceptance transaction, covers new-row phantoms, and makes the signature a coherent snapshot. Solver execution occurs after releasing the lock, so ordinary edits are not blocked for the solver runtime; any such edit makes acceptance stale. Generation-run and audit tables are excluded from the trigger set because they do not affect feasibility.

## Permissions and organizational isolation

`Schedule` gains `generate_schedule`. Dean and chair role bundles receive it; authorized staff receive it only through an explicit grant. Generation also requires the existing schedule-change, entry-add/view, calendar-view, workload-view, availability-view, requirement-view, and room-view capabilities. `REPLACE_UNLOCKED` additionally requires entry-delete permission.

Configuration, requirement, run, schedule, assignment, section, and room querysets use existing organizational selectors. Runs are scoped through their schedule department. A dean sees runs inside the dean's college, a chair only the chair's department, and a system administrator all runs. Submitted schedule, run, term, department, assignment, section, offering, room, and configuration IDs are re-resolved within those querysets. Outside-scope objects return 404 after the capability gate.

Protected peer occupancy can prevent a proposal but never reveals another unit's subject, faculty, section, schedule name, or entry ID.

## UI

The permission-aware sidebar adds **Automated generator** and **Generation history**. The workflow provides:

- a term filter and scoped schedule choice;
- readiness and input counts;
- configuration summary and edit link;
- explicit `FILL_GAPS` or `REPLACE_UNLOCKED` selection with deletion warning;
- run-specific time-limit and objective-weight overrides bounded by configuration validation;
- generate action;
- weekly proposal preview;
- solver status explanation, score, penalty breakdown, runtime, warnings, and diagnostics;
- POST-only accept and discard actions;
- scoped run history and run detail.

The schedule detail links to generation when authorized. Accepted generated rows display generated/manual provenance and locked/unlocked state. The interface remains responsive under the existing Bootstrap 5 shell.

## Administration, seed, and documentation

Django admin registers scheduling configuration and meeting requirements using organizational admin safeguards. Generation runs are read-only and cannot be added, changed, or deleted through admin.

The DEBUG-only timetable seed adds explicit fictional configuration, valid assignment meeting requirements, a separate empty generator schedule, and enough active rooms and availability for a small feasible run. An option creates a relationally valid but deliberately infeasible example without corrupting the default feasible schedule.

README documentation explains the legacy boundary, dependency pin, configuration, strategies, statuses, preview/accept workflow, hard and soft constraints, diagnostics, history, runtime limit, seed, and local commands. Only the malformed UTF-16 tail is removed. `DEVELOPMENT_PLAN.md` records Phase 5 evidence after validation and keeps Phases 6 and 7 deferred.

## Verification

Tests use tiny deterministic fixtures and cover:

- OR-Tools import and status mapping;
- model constraints and migration defaults;
- scoped input and forged IDs;
- readiness defects and zero-candidate diagnostics;
- every hard constraint and half-open boundary behavior;
- preferred-time, gap, distribution, room-fit, weight-change, and zero-weight behavior;
- `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `MODEL_INVALID`, `UNKNOWN`, and time-limit safety;
- unsaved candidate validation and corrupted-proposal rejection;
- preview, discard, stale rejection, atomic acceptance, replacement, provenance, and locking;
- generation-versus-manual concurrency;
- workload invariance and audit rollback;
- dean, chair, system administrator, and explicitly authorized staff behavior;
- seed idempotence and feasible solver performance;
- desktop and mobile browser workflows.

Final verification runs the complete Django suite sequentially, system checks, migration drift, dependency checks, whitespace checks, seed generation, and browser review.

## Explicit exclusions and limits

Phase 5 does not select faculty, optimize workload, support simultaneous team teaching, model holidays or date exceptions, add travel time, approve schedules, publish schedules, choose an active schedule version, create background jobs, or claim exact infeasibility proofs. Every schedule workspace continues to reserve resources under Phase 4 semantics.

Phase 6 advanced workload balancing and scheduling recommendation optimization are not part of this design.
