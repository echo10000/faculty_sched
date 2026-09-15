# Phase 5 Automated Timetable Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure, bounded CP-SAT timetable generator that creates an auditable proposal for an existing departmental schedule and writes meetings only after explicit, stale-safe acceptance.

**Architecture:** Keep `timetabling` authoritative. Django-facing modules resolve permissions and organizational scope, capture a coherent source snapshot, build immutable primitive DTOs, and persist runs; the `timetabling/solver` package contains no Django objects and only maps a `SolverInput` to a `SolverResult`. Manual and generated schedules share occupancy, conflict validation, advisory locking, and dependency signatures.

**Tech Stack:** Python 3.14, Django 5.2, PostgreSQL advisory locks and triggers, Google OR-Tools CP-SAT 9.15.6755, Bootstrap 5, Django `TestCase`/`TransactionTestCase`.

**Spec:** `docs/superpowers/specs/2026-09-15-phase-5-automated-timetable-generation-design.md`

## Global Constraints

- Keep `scheduling/autoscheduler.py` and all legacy `Term`, `Block`, `TimeSlot`, and `Assignment` URLs isolated and unmounted.
- Pin the solver exactly once as `ortools==9.15.6755`.
- Use only integer slot indexes inside CP-SAT; time intervals are half-open and a candidate may end exactly at `latest_end`.
- Solver execution is synchronous, bounded to at most 300 seconds, and never occurs while the scheduling advisory lock is held.
- Default preprocessing caps are 10 seconds, 100,000 candidate placements, and 2,000,000 candidate slot-coverage literals; all three settings must be positive environment-configurable integers.
- `FILL_GAPS` preserves every selected-schedule entry. `REPLACE_UNLOCKED` preserves locked entries and may delete unlocked entries only inside explicit acceptance.
- Existing and manually created `ScheduleEntry` rows are locked by default. Accepted generated rows are unlocked and retain their `generation_run` provenance through later manual edits.
- Only raw solver statuses `OPTIMAL` and `FEASIBLE` can produce lifecycle state `PROPOSAL_READY`; `UNKNOWN` is never success.
- Accept and discard are POST-only and require a locked run whose current state is exactly `PROPOSAL_READY`.
- Resolve every submitted identifier through current scoped querysets. Protected peer occupancy may block a result without revealing foreign names or primary keys.
- Never select faculty, alter assignment shares, add workload credit, publish or approve a schedule, or implement Phase 6 objectives.
- Use one solver worker and a fixed seed in relationship tests; assertions must test invariants and score relationships rather than a single arbitrary timetable.

## File Structure

### Runtime, persistence, and security

- Modify `.env.example`, `config/settings.py`, and `requirements.txt` for exact dependency and preprocessing settings.
- Modify `timetabling/models.py` for `AssignmentMeetingRequirement`, `SchedulingConfiguration`, `ScheduleGenerationRun`, the generation permission, and `ScheduleEntry` provenance/lock fields.
- Create `timetabling/migrations/0002_phase5_generation.py` for schema and the old-row lock default.
- Create `timetabling/migrations/0003_scheduling_dependency_lock_triggers.py` for the shared PostgreSQL writer lock.
- Create `timetabling/admin.py`; modify `core/admin_site.py` and `accounts/permissions.py` for guarded administration and role bundles.

### Shared timetable rules

- Create `timetabling/locking.py`, `timetabling/signatures.py`, and `timetabling/occupancy.py` for the shared advisory lock, deterministic digest, and peer occupancy.
- Modify `timetabling/conflicts.py`, `timetabling/datasets.py`, and `timetabling/mutations.py` so manual and generated paths use those shared services.

### Generator boundary

- Create `timetabling/solver/__init__.py`, `timetabling/solver/contracts.py`, `timetabling/solver/candidates.py`, `timetabling/solver/engine.py`, and `timetabling/solver/diagnostics.py`. These files may import only the standard library and `ortools`.
- Create `timetabling/generation_inputs.py` for scoped ORM loading, readiness, configuration overrides, snapshots, and the remaining-demand manifest.
- Create `timetabling/generation_validation.py` for exact proposal schema and current manifest checks.
- Create `timetabling/generation.py` for run lifecycle, lazy solver invocation, preview storage, discard, stale-safe acceptance, persistence, and auditing.

### Web, seed, tests, and documentation

- Modify `timetabling/queries.py`, `timetabling/forms.py`, `timetabling/views.py`, `timetabling/urls.py`, `core/context_processors.py`, `templates/timetabling/detail.html`, and `static/css/app.css`.
- Create `templates/timetabling/generator.html`, `templates/timetabling/generation_run_list.html`, and `templates/timetabling/generation_run_detail.html`.
- Modify `timetabling/management/commands/seed_timetables.py`, `README.md`, and `DEVELOPMENT_PLAN.md`.
- Create focused `timetabling/test_generation_*.py` modules rather than extending the already large `timetabling/tests.py`.

---

### Task 1: Runtime settings, Phase 5 schema, role grants, triggers, and admin

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `config/settings.py`
- Modify: `timetabling/models.py`
- Create: `timetabling/migrations/0002_phase5_generation.py`
- Create: `timetabling/migrations/0003_scheduling_dependency_lock_triggers.py`
- Create: `timetabling/admin.py`
- Modify: `core/admin_site.py`
- Modify: `accounts/permissions.py`
- Modify: `core/management/commands/bootstrap_roles.py`
- Create: `timetabling/test_generation_models.py`
- Create: `timetabling/test_generation_migrations.py`

**Interfaces:**
- Consumes: existing `CheckedRecord`, `TrackedModel`, `FacultySubjectAssignment`, `Schedule`, and scoped role backend.
- Produces: `AssignmentMeetingRequirement`, `SchedulingConfiguration`, `ScheduleGenerationRun`, `ScheduleEntry.is_locked`, `ScheduleEntry.generation_run`, `ScheduleGenerationRun.Strategy`, `ScheduleGenerationRun.Status`, `ScheduleGenerationRun.SolverStatus`, and permission `timetabling.generate_schedule`.

- [ ] **Step 1: Write failing model, settings, permission, and admin tests**

```python
class GenerationModelTests(TimetableFixture):
    def test_requirement_minutes_must_equal_assignment_component(self):
        requirement = AssignmentMeetingRequirement(
            assignment=self.assignment,
            meeting_type="lecture",
            meetings_per_week=1,
            duration_minutes=60,
        )
        with self.assertRaises(ValidationError):
            requirement.full_clean()

    def test_configuration_rejects_bad_grid_and_bounds(self):
        config = SchedulingConfiguration(
            academic_term=self.term, department=self.department,
            allowed_weekdays=[1, 1, 8], earliest_start=time(8, 5),
            latest_end=time(17), slot_increment_minutes=30,
            solver_time_limit_seconds=301, random_seed=17, worker_count=65,
        )
        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_dean_and_chair_generate_but_staff_needs_explicit_grant(self):
        self.assertTrue(self.dean.has_perm("timetabling.generate_schedule"))
        self.assertTrue(self.chair.has_perm("timetabling.generate_schedule"))
        self.assertFalse(self.staff.has_perm("timetabling.generate_schedule"))

    def test_generation_run_admin_is_read_only(self):
        model_admin = admin.site._registry[ScheduleGenerationRun]
        request = RequestFactory().get("/admin/")
        request.user = self.system_admin
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
```

- [ ] **Step 2: Run the focused tests and confirm the new imports/models are absent**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_models --settings=config.test_settings --noinput`

Expected: FAIL because the Phase 5 models and fields do not exist.

- [ ] **Step 3: Pin OR-Tools and add positive preprocessing settings**

```python
# config/settings.py
SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS = env(
    "SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS", default=10, cast=int
)
SCHEDULER_MAX_CANDIDATES = env("SCHEDULER_MAX_CANDIDATES", default=100000, cast=int)
SCHEDULER_MAX_SLOT_LITERALS = env("SCHEDULER_MAX_SLOT_LITERALS", default=2000000, cast=int)
for name in (
    "SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS",
    "SCHEDULER_MAX_CANDIDATES",
    "SCHEDULER_MAX_SLOT_LITERALS",
):
    if globals()[name] <= 0:
        raise ImproperlyConfigured(f"{name} must be a positive integer.")
```

Replace the existing range in `requirements.txt` with `ortools==9.15.6755`, and add the three variables with their defaults to `.env.example`.

- [ ] **Step 4: Implement the three models and `ScheduleEntry` fields with database constraints**

```python
class AssignmentMeetingRequirement(CheckedRecord):
    assignment = models.ForeignKey(
        "workloads.FacultySubjectAssignment", on_delete=models.PROTECT,
        related_name="meeting_requirements",
    )
    meeting_type = models.CharField(max_length=12, choices=ScheduleEntry.MEETING_TYPES)
    meetings_per_week = models.PositiveSmallIntegerField()
    duration_minutes = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assignment", "meeting_type"], name="assignment_meeting_type_unique"),
            models.CheckConstraint(condition=models.Q(meetings_per_week__gt=0), name="meeting_requirement_count_positive"),
            models.CheckConstraint(condition=models.Q(duration_minutes__gt=0), name="meeting_requirement_duration_positive"),
        ]

    def clean(self):
        component_hours = getattr(self.assignment.subject_offering, f"{self.meeting_type}_hours")
        required_minutes = component_hours * self.assignment.share * 60
        if Decimal(self.meetings_per_week * self.duration_minutes) != required_minutes:
            raise ValidationError("Meeting count and duration must equal this assignment's component minutes.")
```

Implement `SchedulingConfiguration` with the fields and exact 300-second/64-worker/nonnegative-weight checks in the spec. Normalize `allowed_weekdays` to a sorted unique list and reject duplicates, booleans, and values outside 1–7 before saving. Align both boundaries from midnight and require `(latest_end - earliest_start) % slot_increment_minutes == 0`.

Implement `ScheduleGenerationRun` with uppercase lifecycle/raw-status choices, nullable timestamps and accepting user, `FloatField(null=True)` objective/bound/runtime metrics, immutable JSON snapshots, source signature, proposed/accepted counts, and protected schedule/term/department/user foreign keys. Add `generate_schedule` to `Schedule.Meta.permissions`; add `is_locked=models.BooleanField(default=True)` and nullable protected `generation_run` to `ScheduleEntry`. Declare the meeting choices once on `ScheduleEntry` so requirements reuse them.

- [ ] **Step 5: Generate the schema migration and add a historical migration test**

Run: `\.\venv\Scripts\python.exe manage.py makemigrations timetabling --name phase5_generation`

In `timetabling/test_generation_migrations.py`, migrate from `0001_initial` to `0002_phase5_generation`, create a pre-migration entry, and assert after migration that `is_locked is True` and `generation_run_id is None`. Follow the `MigrationExecutor` setup/teardown pattern already used by `accounts/test_migrations.py`.

- [ ] **Step 6: Add the PostgreSQL feasibility-writer trigger migration**

```sql
CREATE OR REPLACE FUNCTION timetabling_acquire_scheduling_lock()
RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(74190304);
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

Create a `BEFORE INSERT OR UPDATE OR DELETE FOR EACH STATEMENT` trigger on the current database tables for academic years/terms/semesters/subjects, colleges/departments, faculty, room types/buildings/rooms, availability/offerings/assignments, sections/offering requirements/room closures/schedules/entries, and the two new configuration/meeting-requirement tables. The reverse SQL drops every named trigger and then the function. Exclude audit and generation-run tables. Add a test that queries `pg_trigger` and asserts the complete expected table set, including new-row-producing tables.

- [ ] **Step 7: Wire role permissions and guarded admin**

```python
# accounts/permissions.py
TIMETABLE_PERMISSIONS |= {
    "timetabling.generate_schedule",
    "timetabling.view_assignmentmeetingrequirement",
    "timetabling.add_assignmentmeetingrequirement",
    "timetabling.change_assignmentmeetingrequirement",
    "timetabling.view_schedulingconfiguration",
    "timetabling.add_schedulingconfiguration",
    "timetabling.change_schedulingconfiguration",
    "timetabling.view_schedulegenerationrun",
}
```

Update `bootstrap_roles` so existing dean/chair groups gain the new bundle idempotently. Register configuration and meeting requirements with an `AuditedAdmin` that uses scoped foreign-key choices and immutable identity fields on edit. Register runs with all concrete fields read-only and all add/change/delete methods returning `False`. Add all three model labels to `FoundationAdminSite.allowed`.

- [ ] **Step 8: Run schema/security tests, migration drift, and commit**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_models timetabling.test_generation_migrations accounts.tests --settings=config.test_settings --noinput`

Run: `\.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Expected: all tests pass and Django reports `No changes detected`.

```powershell
git add requirements.txt .env.example config/settings.py accounts/permissions.py core/admin_site.py core/management/commands/bootstrap_roles.py timetabling/models.py timetabling/admin.py timetabling/migrations timetabling/test_generation_models.py timetabling/test_generation_migrations.py
git commit -m "feat: add timetable generation schema"
```

---

### Task 2: Shared lock, signature, occupancy, and unsaved candidate validation

**Files:**
- Create: `timetabling/locking.py`
- Create: `timetabling/signatures.py`
- Create: `timetabling/occupancy.py`
- Modify: `timetabling/conflicts.py`
- Modify: `timetabling/datasets.py`
- Modify: `timetabling/mutations.py`
- Create: `timetabling/test_generation_validation.py`

**Interfaces:**
- Consumes: Phase 5 models from Task 1 and existing `terms_share_weekday`, `overlaps`, `entry_queryset`, and `Conflict`.
- Produces: `scheduling_lock() -> None`, `dependency_signature(schedule: Schedule) -> str`, `authoritative_occupancy(schedule: Schedule, excluded_entry_ids: Collection[int] = ()) -> tuple[ScheduleEntry, ...]`, and `validate_candidate_schedule(schedule, *, retained_entries, proposed_entries, user=None) -> list[Conflict]`.

- [ ] **Step 1: Write failing tests for global peer occupancy and unsaved proposals**

```python
def test_candidate_schedule_detects_conflicts_between_unsaved_meetings(self):
    first = self.candidate(start_time=time(9), end_time=time(10))
    second = self.candidate(room=self.other_room, start_time=time(9, 30), end_time=time(10, 30))
    conflicts = validate_candidate_schedule(
        self.schedule, retained_entries=[], proposed_entries=[first, second], user=self.chair,
    )
    self.assertIn("FACULTY_OVERLAP", {item.code for item in conflicts})

def test_candidate_schedule_redacts_foreign_peer_identity(self):
    conflicts = validate_candidate_schedule(
        self.schedule, retained_entries=[], proposed_entries=[self.candidate()], user=self.chair,
    )
    protected = [item for item in conflicts if item.code == "ROOM_OVERLAP"]
    self.assertTrue(protected)
    self.assertNotIn(self.foreign_schedule.name, str(protected))
    self.assertTrue(all(item.other_entry_id is None for item in protected))

def test_exact_requirement_replaces_aggregate_hours_warning(self):
    conflicts = validate_candidate_schedule(
        self.schedule, retained_entries=[], proposed_entries=[self.one_of_two_required_meetings()],
    )
    self.assertIn("MEETING_REQUIREMENT_COUNT", {item.code for item in conflicts})
    self.assertNotIn("MEETING_HOURS_WARNING", {item.code for item in conflicts})
```

- [ ] **Step 2: Run the validation tests and confirm the shared functions are absent**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_validation --settings=config.test_settings --noinput`

Expected: FAIL on missing modules/functions.

- [ ] **Step 3: Extract the advisory lock and dependency signature**

```python
# timetabling/locking.py
SCHEDULING_ADVISORY_LOCK_ID = 74190304

def scheduling_lock():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [SCHEDULING_ADVISORY_LOCK_ID])
```

Move the digest into `signatures.py` and extend its ordered field specs with configuration, assignment requirements, `ScheduleEntry.is_locked`, and `ScheduleEntry.generation_run_id`. Keep the conservative institution-wide payload and exclude run/audit data. Import these functions from `mutations.py`; retain `mutation_lock = scheduling_lock` as a compatibility alias until all current callers and tests are updated.

- [ ] **Step 4: Implement authoritative occupancy with exact calendar intersection behavior**

```python
def authoritative_occupancy(schedule, excluded_entry_ids=()):
    return tuple(
        entry_queryset()
        .filter(
            schedule__academic_term__start_date__lte=schedule.academic_term.end_date,
            schedule__academic_term__end_date__gte=schedule.academic_term.start_date,
        )
        .exclude(pk__in=tuple(excluded_entry_ids))
        .order_by("day_of_week", "start_time", "pk")
    )
```

The service deliberately does not scope peer rows by the requester. Every consumer must call `terms_share_weekday(schedule.academic_term, peer.schedule.academic_term, day)` before treating a row as occupancy and must redact details unless `scoped(user, ScheduleEntry.objects.all())` includes the peer.

- [ ] **Step 5: Refactor conflict validation around a combined candidate set**

Implement `validate_candidate_schedule` by combining retained objects, unsaved proposed objects, and authoritative peer occupancy. Pass every persisted selected-schedule entry ID through `excluded_entry_ids` when loading occupancy so a retained row is evaluated once. Call a shared per-entry rule function with that combined set; de-duplicate symmetric conflicts by code and stable candidate/entry identity. When a meeting requirement exists, emit blocking `MEETING_REQUIREMENT_COUNT` and `MEETING_REQUIREMENT_DURATION` conflicts for exact count/duration mismatches. Only assignments without such a requirement receive the legacy `MEETING_HOURS_WARNING`.

```python
def get_schedule_conflicts(schedule, *, user=None):
    entries = list(entry_queryset().filter(schedule=schedule))
    return validate_candidate_schedule(
        schedule, retained_entries=entries, proposed_entries=(), user=user,
    )
```

Keep `detect_entry_conflicts(entry, *, user=None, peers=None)` as the manual editor interface, delegating its rule evaluation to the same internal function. Update `datasets.scheduling_input` to obtain selected and peer occupancy through `authoritative_occupancy` while preserving redaction.

- [ ] **Step 6: Run the new validation suite and all Phase 4 timetable tests**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_validation timetabling.tests timetabling.test_concurrency --settings=config.test_settings --noinput`

Expected: all tests pass; adjacency remains legal and foreign details remain absent.

- [ ] **Step 7: Commit the shared rule foundation**

```powershell
git add timetabling/locking.py timetabling/signatures.py timetabling/occupancy.py timetabling/conflicts.py timetabling/datasets.py timetabling/mutations.py timetabling/test_generation_validation.py
git commit -m "refactor: share timetable occupancy and validation"
```

---

### Task 3: Pure contracts and bounded deterministic candidate generation

**Files:**
- Create: `timetabling/solver/__init__.py`
- Create: `timetabling/solver/contracts.py`
- Create: `timetabling/solver/candidates.py`
- Create: `timetabling/test_generation_candidates.py`

**Interfaces:**
- Consumes: primitive IDs, weekdays, integer slot indexes, tuples, and an injected monotonic clock.
- Produces: frozen DTOs `ObjectiveWeights`, `SchedulingPolicy`, `MeetingDemand`, `FixedMeeting`, `RoomOption`, `WeeklyBlock`, `CandidatePlacement`, `CandidateLimits`, `CandidateBuildResult`, `SolverInput`, `ProposedMeeting`, `ReadinessIssue`, `PenaltyBreakdown`, `SolverStatistics`, and `SolverResult`; plus `build_candidates(*, policy, demands, rooms, unavailable, room_closures, fixed_meetings, preferred, limits, clock=time.monotonic) -> CandidateBuildResult`.

- [ ] **Step 1: Write failing pure-unit tests for ordering, hard filtering, boundaries, and caps**

```python
class CandidateBuilderTests(SimpleTestCase):
    def test_candidates_are_sparse_ordered_and_may_end_at_latest_end(self):
        result = build_candidates(
            policy=self.policy(allowed_weekdays=(1,), latest_slot=4),
            demands=(self.demand(duration_slots=2),), rooms=(self.room(2), self.room(1)),
            unavailable=(), room_closures=(), fixed_meetings=(), preferred=(),
            limits=CandidateLimits(10, 100_000, 2_000_000),
        )
        self.assertEqual(
            [(c.start_slot, c.room_id) for c in result.candidates],
            [(0, 1), (0, 2), (1, 1), (1, 2), (2, 1), (2, 2)],
        )

    def test_unavailable_closure_and_fixed_faculty_room_section_remove_candidates(self):
        result = build_candidates(
            policy=self.policy(allowed_weekdays=(1,), latest_slot=4),
            demands=(self.demand(duration_slots=2),), rooms=(self.room(1),),
            unavailable=(WeeklyBlock(10, 1, 0, 2),), room_closures=(),
            fixed_meetings=(), preferred=(), limits=CandidateLimits(10, 100, 1000),
        )
        self.assertEqual(result.candidate_counts[self.demand_key], 2)

    def test_model_size_and_elapsed_caps_stop_before_solver(self):
        result = build_candidates(
            policy=self.policy(), demands=(self.demand(),), rooms=(self.room(1),),
            unavailable=(), room_closures=(), fixed_meetings=(), preferred=(),
            limits=CandidateLimits(10, 1, 1),
        )
        self.assertEqual(result.issues[0].code, "MODEL_SIZE_LIMIT")
        timed = build_candidates(
            policy=self.policy(), demands=(self.demand(),), rooms=(self.room(1),),
            unavailable=(), room_closures=(), fixed_meetings=(), preferred=(),
            clock=self.advancing_clock, limits=CandidateLimits(1, 100, 100),
        )
        self.assertEqual(timed.issues[0].code, "PREPROCESSING_TIMEOUT")
```

Use complete one-demand/two-room DTO fixtures in the real test; do not build ORM rows in this module.

- [ ] **Step 2: Run the pure tests and confirm the package is absent**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_candidates --settings=config.test_settings --noinput`

Expected: FAIL because `timetabling.solver` does not exist.

- [ ] **Step 3: Define immutable primitive contracts**

```python
@dataclass(frozen=True, slots=True)
class MeetingDemand:
    assignment_id: int
    meeting_requirement_id: int
    occurrence_index: int
    faculty_id: int
    section_id: int
    meeting_type: str
    duration_slots: int
    expected_size: int | None
    required_room_type_id: int | None
    room_type_mandatory: bool
    capacity_is_hard: bool

    @property
    def key(self) -> tuple[int, int]:
        return self.meeting_requirement_id, self.occurrence_index

@dataclass(frozen=True, slots=True)
class CandidatePlacement:
    demand_key: tuple[int, int]
    assignment_id: int
    faculty_id: int
    section_id: int
    room_id: int
    day_of_week: int
    start_slot: int
    end_slot: int
    meeting_type: str
    preferred_penalty: int
    room_fit_penalty: int
```

Give `FixedMeeting` a `counts_for_distribution: bool` flag so selected-schedule retained meetings affect the distribution term while peer occupancy affects only faculty/section gaps and occupancy. `SchedulingPolicy` carries integer `earliest_minute`, `latest_minute`, `slot_increment_minutes`, solver limits/seed/workers, allowed weekdays, and `ObjectiveWeights`.

- [ ] **Step 4: Implement bounded deterministic sparse candidate expansion**

```python
for demand in sorted(demands, key=demand_order):
    for day in policy.allowed_weekdays:
        for start_slot in range(policy.slot_count - demand.duration_slots + 1):
            for room in sorted(rooms, key=attrgetter("room_id")):
                if placement_is_hard_invalid(demand, room, day, start_slot, policy, unavailable, room_closures, fixed_meetings):
                    continue
                candidates.append(candidate_for(demand, room, day, start_slot, preferred))
                slot_literals += demand.duration_slots
                if len(candidates) > limits.max_candidates or slot_literals > limits.max_slot_literals:
                    issue = ReadinessIssue("MODEL_SIZE_LIMIT", "ERROR", "Candidate model exceeds the configured deployment limit.")
                    return CandidateBuildResult((), counts, (issue,))
            if clock() - started > limits.max_seconds:
                issue = ReadinessIssue("PREPROCESSING_TIMEOUT", "ERROR", "Candidate preprocessing exceeded its configured time limit.")
                return CandidateBuildResult((), counts, (issue,))
```

Reject candidates outside the configured day/window, intersecting faculty unavailable blocks, room closures, or fixed faculty/room/section occupancy, and candidates violating mandatory type or hard capacity. Set preference penalty to one only when that faculty has at least one preferred block and the meeting is not fully contained in any preferred block for the day. Set room-fit penalty to optional type mismatch plus nonnegative known capacity slack; unknown expected size adds zero.

- [ ] **Step 5: Prove the solver package has no Django dependency and run tests**

Add a test that patches Python import resolution to fail on `django` while reloading `timetabling.solver.contracts` and `timetabling.solver.candidates`.

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_candidates --settings=config.test_settings --noinput`

Expected: all pure candidate tests pass.

- [ ] **Step 6: Commit pure contracts and preprocessing**

```powershell
git add timetabling/solver timetabling/test_generation_candidates.py
git commit -m "feat: build bounded timetable candidates"
```

---

### Task 4: Scoped ORM input capture and readiness

**Files:**
- Create: `timetabling/generation_inputs.py`
- Modify: `timetabling/queries.py`
- Create: `timetabling/test_generation_inputs.py`

**Interfaces:**
- Consumes: `get_schedule`, organizational selectors, `authoritative_occupancy`, Phase 5 models, `dependency_signature`, and `build_candidates`.
- Produces: `GenerationOverrides`, `PreparedGeneration`, `require_generation_access(user, strategy) -> None`, `prepare_generation_input(*, user, schedule_id: int, strategy: str, overrides: GenerationOverrides) -> PreparedGeneration`, `generation_run_queryset(user)`, and `get_generation_run(user, pk, *, lock=False)`.

- [ ] **Step 1: Write failing scoped-input and readiness tests**

```python
def test_builder_includes_active_offering_without_assignment(self):
    orphan = SubjectOffering.objects.create(
        subject=self.other_subject, academic_term=self.term, department=self.department,
        code="ORPHAN", lecture_units=1, laboratory_units=0,
        lecture_hours=1, laboratory_hours=0,
    )
    prepared = prepare_generation_input(
        user=self.chair, schedule_id=self.schedule.pk,
        strategy="FILL_GAPS", overrides=GenerationOverrides(),
    )
    self.assertIn("ASSIGNMENT_REQUIRED", {issue.code for issue in prepared.issues})
    self.assertIn(orphan.pk, prepared.input_summary["offering_ids"])

def test_builder_rejects_incomplete_shares_before_candidate_generation(self):
    self.assignment.share = Decimal("0.50")
    self.assignment.save()
    with patch("timetabling.generation_inputs.build_candidates") as candidates:
        prepared = self.build()
    self.assertIn("ASSIGNMENT_SHARES_INCOMPLETE", {issue.code for issue in prepared.issues})
    candidates.assert_not_called()

def test_foreign_schedule_id_is_404_after_capability_gate(self):
    with self.assertRaises(Http404):
        prepare_generation_input(
            user=self.chair, schedule_id=self.foreign_schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides(),
        )
```

Cover missing/invalid configuration, inactive related rows, missing section, missing/duplicate/contradictory/misaligned requirements, invalid or excess fixed entries, unavailable eligible rooms, zero-candidate demand, `AVAILABLE` being informational, and `REPLACE_UNLOCKED` requiring delete permission.

- [ ] **Step 2: Run focused tests and confirm the builder is absent**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_inputs --settings=config.test_settings --noinput`

Expected: FAIL on the missing `generation_inputs` module.

- [ ] **Step 3: Add scoped run selectors and the exact permission gate**

```python
GENERATION_PERMISSIONS = (
    "timetabling.generate_schedule", "timetabling.change_schedule",
    "timetabling.add_scheduleentry", "timetabling.view_scheduleentry",
    "timetabling.view_offeringrequirement", "timetabling.view_assignmentmeetingrequirement",
    "timetabling.view_schedulingconfiguration", "timetabling.view_roomunavailability",
    "academics.view_academicterm", "workloads.view_workload",
    "workloads.view_facultyavailability", "workloads.view_facultysubjectassignment",
    "scheduling.view_room",
)

def require_generation_access(user, strategy):
    for permission in GENERATION_PERMISSIONS:
        require_access(user, permission)
    if strategy == "REPLACE_UNLOCKED":
        require_access(user, "timetabling.delete_scheduleentry")
```

Scope runs through `schedule__department`; a dean receives the dean's college, a chair only the chair's department, and a system admin all rows. Use the scoped queryset before every run primary-key lookup.

- [ ] **Step 4: Implement normalized configuration and snapshot construction**

Use `Decimal` for the share-total comparison, convert wall-clock times to minutes from midnight, and reject any fixed duration or requirement that is not a positive multiple of the slot increment. Apply run overrides only to time limit and the five weights, then call the same bounds checks as model configuration. Store JSON-compatible configuration values with times formatted `HH:MM` and sort every ID list.

```python
@dataclass(frozen=True, slots=True)
class PreparedGeneration:
    schedule_id: int
    configuration_id: int | None
    source_signature: str
    configuration_snapshot: dict
    input_summary: dict
    solver_input: SolverInput | None
    issues: tuple[ReadinessIssue, ...]
    retained_entry_ids: tuple[int, ...]
    replace_entry_ids: tuple[int, ...]
```

- [ ] **Step 5: Build the exact remaining-demand manifest and candidates**

For each active offering in the schedule's exact term/department, require shares totaling exactly `Decimal("1.0")`. Match retained fixed entries to their assignment/type requirement in deterministic entry order. Record each unsatisfied occurrence as `[meeting_requirement_id, occurrence_index]`; include the same values in `MeetingDemand.key`. Under `FILL_GAPS`, all selected entries are retained. Under `REPLACE_UNLOCKED`, locked entries are retained and unlocked IDs go only into `replace_entry_ids`. Add protected peer entries as fixed occupancy with `counts_for_distribution=False` and no display metadata.

Call `build_candidates` with deployment caps from Django settings. A cap issue, timeout, or zero-candidate demand returns `solver_input=None` and an `INPUT_INVALID` issue. The module must not import `ortools` or `timetabling.solver.engine`.

- [ ] **Step 6: Run focused input tests and existing scope tests**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_inputs timetabling.tests.IntegrationTests accounts.tests.AuthenticationAndScopeTests --settings=config.test_settings --noinput`

Expected: all tests pass with foreign IDs returning 404 and no protected resource names in issues.

- [ ] **Step 7: Commit scoped input preparation**

```powershell
git add timetabling/generation_inputs.py timetabling/queries.py timetabling/test_generation_inputs.py
git commit -m "feat: prepare scoped generation input"
```

---

### Task 5: CP-SAT hard constraints and exact status mapping

**Files:**
- Create: `timetabling/solver/engine.py`
- Create: `timetabling/test_generation_solver.py`

**Interfaces:**
- Consumes: `SolverInput` whose candidates already passed deterministic hard filtering.
- Produces: `solve(input_data: SolverInput) -> SolverResult` with a raw status from the five approved OR-Tools states and proposals only for complete `FEASIBLE`/`OPTIMAL` solutions.

- [ ] **Step 1: Write failing tiny solver tests for exactly-one and collision buckets**

```python
def test_solver_selects_exactly_one_candidate_for_each_demand(self):
    result = solve(self.two_demand_input())
    self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})
    self.assertEqual({p.demand_key for p in result.proposals}, set(self.demand_keys))

def test_faculty_room_and_section_slot_buckets_cannot_overlap(self):
    result = solve(self.input_with_only_colliding_choices())
    self.assertEqual(result.raw_status, "INFEASIBLE")
    self.assertEqual(result.proposals, ())

def test_adjacent_multi_slot_candidates_are_legal(self):
    result = solve(self.adjacent_input())
    self.assertIn(result.raw_status, {"OPTIMAL", "FEASIBLE"})
```

Also patch a solver response per OR-Tools constant and assert exact mappings for `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `MODEL_INVALID`, and `UNKNOWN`; non-success states return no proposals.

- [ ] **Step 2: Run focused tests and confirm the adapter is absent**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_solver --settings=config.test_settings --noinput`

Expected: FAIL because `timetabling.solver.engine` does not exist.

- [ ] **Step 3: Build one Boolean per candidate and exactly-one per demand**

```python
model = cp_model.CpModel()
variables = [model.new_bool_var(f"p_{index}") for index, _ in enumerate(data.candidates)]
for demand in data.demands:
    indexes = candidates_by_demand[demand.key]
    model.add_exactly_one(variables[index] for index in indexes)
```

Build faculty/day/slot, room/day/slot, and section/day/slot dictionaries. Add one `add_at_most_one` per bucket containing at least two variables. Do not enumerate candidate pairs and do not create a faculty-choice variable.

- [ ] **Step 4: Configure bounded solving and map statuses without collapsing them**

```python
solver.parameters.max_time_in_seconds = data.policy.solver_time_limit_seconds
solver.parameters.random_seed = data.policy.random_seed
solver.parameters.num_search_workers = data.policy.worker_count
raw_status = {
    cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE", cp_model.MODEL_INVALID: "MODEL_INVALID",
    cp_model.UNKNOWN: "UNKNOWN",
}[solver.solve(model)]
```

For success, reconstruct one `ProposedMeeting` from every selected candidate and verify the count equals the input demand count. Capture wall time, branches, conflicts, candidate/variable counts, seed, workers, and time limit. Objective and best bound remain `None` until Task 6 adds the objective.

- [ ] **Step 5: Run solver tests and verify the exact dependency import**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_solver --settings=config.test_settings --noinput`

Run: `\.\venv\Scripts\python.exe -c "import ortools; print(ortools.__version__)"`

Expected: tests pass and version output is `9.15.6755`.

- [ ] **Step 6: Commit the hard-constraint adapter**

```powershell
git add timetabling/solver/engine.py timetabling/test_generation_solver.py
git commit -m "feat: solve timetable hard constraints"
```

---

### Task 6: Objective terms and conservative diagnostics

**Files:**
- Modify: `timetabling/solver/engine.py`
- Create: `timetabling/solver/diagnostics.py`
- Create: `timetabling/test_generation_objectives.py`

**Interfaces:**
- Consumes: candidate-local preferred/room-fit penalties, selected/retained meeting facts, and objective weights.
- Produces: exact `PenaltyBreakdown`, integer weighted objective, best bound, and `diagnose_unsolved(input_data, raw_status) -> tuple[ReadinessIssue, ...]` using only conservative candidate/capacity evidence.

- [ ] **Step 1: Write failing objective relationship tests**

```python
def test_preferred_weight_moves_meeting_into_preferred_period(self):
    unweighted = solve(self.choice_input(weights=ObjectiveWeights()))
    preferred = solve(self.choice_input(weights=ObjectiveWeights(faculty_preference=10)))
    self.assertGreater(unweighted.penalties.faculty_preference, preferred.penalties.faculty_preference)

def test_gap_distribution_and_room_fit_components_are_exact(self):
    result = solve(self.known_penalty_input())
    self.assertEqual(result.penalties.faculty_gap, 1)
    self.assertEqual(result.penalties.section_gap, 1)
    self.assertEqual(result.penalties.meeting_distribution, 1)
    self.assertEqual(result.penalties.room_fit, 11)

def test_zero_weight_removes_component_from_total(self):
    result = solve(self.known_penalty_input(weights=ObjectiveWeights()))
    self.assertEqual(result.objective_value, 0)
```

Use one worker/fixed seed. Include a faculty with no preferred records and assert its preference penalty is zero.

- [ ] **Step 2: Run objective tests and observe missing penalty behavior**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_objectives --settings=config.test_settings --noinput`

Expected: FAIL because the engine has no soft objective.

- [ ] **Step 3: Encode each approved component with integer auxiliary variables**

Add selected-candidate coefficients directly for faculty preference and room fit. For each faculty/day and section/day, derive first/last occupied slot and occupied-slot booleans across selected variables plus fixed occupancy, then add `last - first + 1 - occupied_count` when at least one meeting occupies the day. For each assignment/type/day, add `max(0, retained_or_selected_occurrences - 1)` using `add_max_equality`. Do not count peer meetings in distribution.

```python
weighted_terms = (
    weights.faculty_preference * preferred_total
    + weights.faculty_gap * faculty_gap_total
    + weights.section_gap * section_gap_total
    + weights.meeting_distribution * distribution_total
    + weights.room_fit * room_fit_total
)
model.minimize(weighted_terms)
```

After solving, evaluate each unweighted component separately and populate `PenaltyBreakdown`; return the integer objective and floating best bound only for successful solutions.

- [ ] **Step 4: Add honest infeasibility/unknown diagnostics**

```python
def diagnose_unsolved(input_data, raw_status):
    issues = [ReadinessIssue("SOLVER_" + raw_status, "ERROR", solver_message(raw_status))]
    issues.extend(zero_candidate_findings(input_data))
    issues.extend(conservative_room_and_faculty_capacity_findings(input_data))
    return tuple(issues)
```

Use the heading/message “Potential blocking conditions detected.” Never describe these checks as a minimal unsatisfiable core. `MODEL_INVALID` should advise reviewing configuration/model diagnostics; `UNKNOWN` should state that no complete solution was returned within the bound.

- [ ] **Step 5: Run all pure solver/candidate tests and commit**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_candidates timetabling.test_generation_solver timetabling.test_generation_objectives --settings=config.test_settings --noinput`

Expected: all tests pass.

```powershell
git add timetabling/solver/engine.py timetabling/solver/diagnostics.py timetabling/test_generation_objectives.py
git commit -m "feat: score and diagnose generated timetables"
```

---

### Task 7: Run lifecycle, proposal contract, stale-safe acceptance, and concurrency

**Files:**
- Create: `timetabling/generation_validation.py`
- Create: `timetabling/generation.py`
- Create: `timetabling/test_generation_services.py`
- Create: `timetabling/test_generation_concurrency.py`

**Interfaces:**
- Consumes: Task 4 prepared input, Task 5/6 `solve`, shared candidate validation, scheduling lock/signature, and audit service.
- Produces: `request_generation(*, user, schedule_id, strategy, overrides) -> ScheduleGenerationRun`, `accept_generation(*, user, run_id) -> ScheduleGenerationRun`, `discard_generation(*, user, run_id) -> ScheduleGenerationRun`, `validate_generation_contract(*, run, prepared, proposal_rows, user) -> list[Conflict]`, `InvalidRunTransition`, `StaleProposal`, and exact proposal JSON serialization.

- [ ] **Step 1: Write failing lifecycle and lazy-solver tests**

```python
def test_invalid_input_never_imports_or_calls_solver(self):
    with patch.dict(sys.modules, {"timetabling.solver.engine": None}):
        run = request_generation(
            user=self.chair, schedule_id=self.schedule.pk,
            strategy="FILL_GAPS", overrides=GenerationOverrides(),
        )
    self.assertEqual(run.status, "INPUT_INVALID")
    self.assertEqual(run.solver_status, "")

def test_feasible_result_stores_preview_without_writing_entries(self):
    with patch("timetabling.solver.engine.solve", return_value=self.feasible_result()):
        run = self.generate()
    self.assertEqual(run.status, "PROPOSAL_READY")
    self.assertEqual(ScheduleEntry.objects.filter(generation_run=run).count(), 0)
    self.assertEqual(set(run.proposed_meetings[0]), {
        "assignment_id", "meeting_requirement_id", "occurrence_index", "room_id",
        "day_of_week", "start_time", "end_time", "meeting_type",
    })
```

Cover lifecycle/raw-status mapping, requested/running/finished timestamps, immutable snapshots, measured/configured limits, and new-row-per-request behavior.

- [ ] **Step 2: Write failing acceptance, atomicity, terminal-transition, and provenance tests**

```python
def test_accept_rejects_changed_source_and_writes_no_entries(self):
    run = self.ready_run()
    FacultyAvailability.objects.create(
        faculty=self.faculty, academic_term=self.term, day_of_week=2,
        start_time=time(8), end_time=time(9), availability_type="unavailable",
    )
    accepted = accept_generation(user=self.chair, run_id=run.pk)
    self.assertEqual(accepted.status, "STALE")
    self.assertFalse(ScheduleEntry.objects.filter(generation_run=run).exists())

def test_replace_accept_deletes_only_unlocked_and_sets_provenance(self):
    run = self.ready_replace_run()
    accept_generation(user=self.chair, run_id=run.pk)
    self.assertTrue(ScheduleEntry.objects.filter(pk=self.locked.pk).exists())
    self.assertFalse(ScheduleEntry.objects.filter(pk=self.unlocked.pk).exists())
    generated = ScheduleEntry.objects.get(generation_run=run)
    self.assertFalse(generated.is_locked)

def test_repeated_accept_and_discard_leave_terminal_run_unchanged(self):
    run = self.accepted_run()
    with self.assertRaises(InvalidRunTransition):
        discard_generation(user=self.chair, run_id=run.pk)
    run.refresh_from_db()
    self.assertEqual(run.status, "ACCEPTED")
```

Patch `record_event` to raise during request and acceptance; assert request/run or accepted schedule mutations roll back respectively. Assert accepted meetings do not change `calculate_workload`.

- [ ] **Step 3: Implement exact proposal serialization and generation-contract validation**

Serialize slots to `HH:MM` using the captured configuration. The validator must reject missing/extra keys, wrong primitive types, duplicate `(meeting_requirement_id, occurrence_index)`, unscoped/missing assignment or room IDs, assignment/requirement/type mismatch, a demand not in the current remaining manifest, wrong duration, day/window/grid mismatch, ineligible room, strategy retention mismatch, or any missing/extra proposal occurrence.

```python
PROPOSAL_KEYS = frozenset({
    "assignment_id", "meeting_requirement_id", "occurrence_index", "room_id",
    "day_of_week", "start_time", "end_time", "meeting_type",
})

PROPOSAL_CONFLICT_CODES = frozenset({
    "PROPOSAL_SCHEMA", "PROPOSAL_SCOPE", "PROPOSAL_DEMAND", "PROPOSAL_GRID",
})
```

Resolve identifiers from scoped querysets and reconstruct unsaved `ScheduleEntry` objects. Never trust posted proposal content or cached model instances.

- [ ] **Step 4: Implement request lifecycle with a short coherent capture transaction**

```python
def request_generation(*, user, schedule_id, strategy, overrides):
    require_generation_access(user, strategy)
    with transaction.atomic():
        schedule = get_schedule(user, schedule_id, "change")
        run = ScheduleGenerationRun.objects.create(
            schedule=schedule, academic_term=schedule.academic_term,
            department=schedule.department, requested_by=user,
            strategy=strategy, status="PENDING",
        )
        record_event("generation.requested", actor=user, obj=run, details={"strategy": strategy})
    with transaction.atomic():
        scheduling_lock()
        prepared = prepare_generation_input(
            user=user, schedule_id=schedule_id, strategy=strategy, overrides=overrides,
        )
        persist_snapshots_if_expected(run.pk, "PENDING", prepared)
    if prepared.issues:
        return finish_if_expected(run.pk, "PENDING", "INPUT_INVALID", diagnostics=prepared.issues)
    mark_running_if_expected(run.pk)
    from timetabling.solver.engine import solve
    result = solve(prepared.solver_input)
    return finish_generation_if_expected(run.pk, result, prepared)
```

Run CP-SAT after the capture transaction releases its advisory lock. For successful raw statuses, reconstruct unsaved entries and call both validators before storing the proposal and `PROPOSAL_READY`. Failure updates lock the run and compare the expected current state so they cannot overwrite accepted/discarded terminal states.

- [ ] **Step 5: Implement discard and atomic acceptance**

Inside acceptance, acquire `scheduling_lock()`, `select_for_update()` the scoped run, require `PROPOSAL_READY`, re-resolve all dependencies, recompute the signature, reconstruct the proposal, and run both validators. Raise a typed exception on stale/invalid data so the mutation transaction rolls back; a separate `finish_failure_if_expected` transaction marks the still-ready run `STALE`, `VALIDATION_FAILED`, or `FAILED` and records only a failure event.

For a valid `REPLACE_UNLOCKED` proposal, delete only current `is_locked=False` rows recorded by the current prepared input. Bulk-create proposed entries with `created_by=user`, `generation_run=run`, and `is_locked=False`; validate the persisted full schedule; set the schedule to `DRAFT`; update run to `ACCEPTED`; and record `generation.succeeded`, `generation.accepted`, and `schedule.entries_replaced` (including deleted IDs/count) inside the same transaction. Any blocking persisted conflict or audit error raises and rolls back the full acceptance.

- [ ] **Step 6: Add deterministic PostgreSQL concurrency tests**

Use `TransactionTestCase`, `ThreadPoolExecutor`, `Barrier`, and `close_old_connections`. Test that a writer changing a feasibility table after input capture makes acceptance stale, that trigger-protected insert/update/delete writers cannot commit through an acceptance signature-and-write section, and that two terminal actions on one run produce exactly one terminal winner. The losing action must raise `InvalidRunTransition` and cannot alter entries or snapshots.

- [ ] **Step 7: Run service/concurrency tests plus workload and audit regression tests**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_services timetabling.test_generation_concurrency workloads.tests.CalculationTests audit --settings=config.test_settings --noinput`

Expected: all tests pass; accepted generation remains workload-neutral and audit failures are atomic.

- [ ] **Step 8: Commit transactional generation**

```powershell
git add timetabling/generation_validation.py timetabling/generation.py timetabling/test_generation_services.py timetabling/test_generation_concurrency.py
git commit -m "feat: add audited timetable generation workflow"
```

---

### Task 8: Permission-protected generator, preview, and history UI

**Files:**
- Modify: `timetabling/forms.py`
- Modify: `timetabling/views.py`
- Modify: `timetabling/urls.py`
- Modify: `core/context_processors.py`
- Modify: `templates/timetabling/detail.html`
- Create: `templates/timetabling/generator.html`
- Create: `templates/timetabling/generation_run_list.html`
- Create: `templates/timetabling/generation_run_detail.html`
- Modify: `static/css/app.css`
- Create: `timetabling/test_generation_views.py`

**Interfaces:**
- Consumes: generation service APIs and scoped run/schedule/configuration querysets.
- Produces: routes `timetabling:generator`, `timetabling:generation-runs`, `timetabling:generation-run-detail`, `timetabling:generation-run-accept`, and `timetabling:generation-run-discard`; plus permission-aware sidebar links.

- [ ] **Step 1: Write failing page, RBAC, 404, CSRF, and method tests**

```python
def test_chair_sees_scoped_generator_and_history(self):
    self.client.force_login(self.chair)
    self.assertContains(self.client.get(reverse("timetabling:generator")), self.schedule.name)
    self.assertNotContains(self.client.get(reverse("timetabling:generator")), self.foreign_schedule.name)

def test_explicit_staff_needs_every_generation_capability(self):
    self.client.force_login(self.staff)
    self.assertEqual(self.client.get(reverse("timetabling:generator")).status_code, 403)
    self.grant_complete_generation_bundle(self.staff)
    self.assertEqual(self.client.get(reverse("timetabling:generator")).status_code, 200)

def test_accept_and_discard_are_post_only_and_csrf_protected(self):
    self.assertEqual(self.client.get(self.accept_url).status_code, 405)
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(self.chair)
    self.assertEqual(secure.post(self.accept_url).status_code, 403)

def test_foreign_run_is_404_after_permission_gate(self):
    self.client.force_login(self.chair)
    self.assertEqual(self.client.get(self.foreign_run_url).status_code, 404)
```

- [ ] **Step 2: Run view tests and confirm routes are absent**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_views --settings=config.test_settings --noinput`

Expected: FAIL because generator URLs/views/templates do not exist.

- [ ] **Step 3: Implement scoped configuration/requirement forms and request form**

Add `MeetingRequirementForm` and `SchedulingConfigurationForm` to the existing generic timetable records, with organizationally scoped assignment/term/department querysets and weekday checkboxes. Add `GenerationRequestForm(user, data=None)` with term filter, scoped schedule, explicit strategy, optional time limit, and five optional weight overrides. In `clean()`, make the selected schedule authoritative, re-resolve its configuration, enforce limits, and require delete permission for `REPLACE_UNLOCKED`.

```python
class GenerationRequestForm(StyledFormMixin, forms.Form):
    academic_term = forms.ModelChoiceField(queryset=None, required=False)
    schedule = forms.ModelChoiceField(queryset=None)
    strategy = forms.ChoiceField(choices=ScheduleGenerationRun.Strategy.choices)
    solver_time_limit_seconds = forms.IntegerField(required=False, min_value=1, max_value=300)
    faculty_preference_weight = forms.IntegerField(required=False, min_value=0)
    faculty_gap_weight = forms.IntegerField(required=False, min_value=0)
    section_gap_weight = forms.IntegerField(required=False, min_value=0)
    meeting_distribution_weight = forms.IntegerField(required=False, min_value=0)
    room_fit_weight = forms.IntegerField(required=False, min_value=0)
```

- [ ] **Step 4: Implement views and POST-only terminal endpoints**

The generator GET renders readiness/input counts and configuration summary for the selected scoped schedule. POST binds the form, calls `request_generation`, and redirects to the run detail. History paginates scoped runs newest first. Detail renders the proposal as weekday columns, status explanation, strategy/source summary, objective/penalty/runtime/statistics, warnings, diagnostics, and accept/discard controls only for `PROPOSAL_READY`. Convert `InvalidRunTransition` into a clear 409 response or error message without mutating the run.

```python
class GenerationAcceptView(ProtectedViewMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        run = accept_generation(user=request.user, run_id=pk)
        messages.success(request, "Generated meetings accepted into the draft schedule.")
        return redirect("timetabling:generation-run-detail", pk=run.pk)
```

- [ ] **Step 5: Build responsive Bootstrap templates and permission-aware navigation**

Add “Automated generator” only when the user has the complete generation capability set and “Generation history” when the user can view runs/calendar. Add the schedule-detail generation link for authorized users. Render strategy warning copy before `REPLACE_UNLOCKED`; display generated/manual provenance and locked/unlocked badges on schedule rows. Use semantic tables with a stacked card treatment below the existing Bootstrap breakpoint, visible focus states, labels for every input, and status text in addition to color.

- [ ] **Step 6: Run page and existing navigation tests**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_views timetabling.tests.PageTests accounts.tests.AuthenticationAndScopeTests --settings=config.test_settings --noinput`

Expected: all tests pass; protected records never appear in HTML.

- [ ] **Step 7: Commit the generator UI**

```powershell
git add timetabling/forms.py timetabling/views.py timetabling/urls.py core/context_processors.py templates/timetabling static/css/app.css timetabling/test_generation_views.py
git commit -m "feat: add timetable generator preview UI"
```

---

### Task 9: Development seed, operator documentation, and full Phase 5 verification

**Files:**
- Modify: `timetabling/management/commands/seed_timetables.py`
- Create: `timetabling/test_generation_seed.py`
- Modify: `README.md`
- Modify: `DEVELOPMENT_PLAN.md`

**Interfaces:**
- Consumes: all Phase 5 models/services and existing fictional Phase 1–4 seed chain.
- Produces: idempotent DEBUG-only feasible generator data, optional `--with-infeasible-generator-example`, local run instructions, Phase 5 evidence, and an explicit stop before Phase 6.

- [ ] **Step 1: Write failing seed idempotence and solver-smoke tests**

```python
@override_settings(DEBUG=True)
def test_default_seed_is_idempotent_and_feasible(self):
    call_command("seed_timetables", stdout=StringIO())
    counts = tuple(model.objects.count() for model in self.phase5_models)
    call_command("seed_timetables", stdout=StringIO())
    self.assertEqual(counts, tuple(model.objects.count() for model in self.phase5_models))
    schedule = Schedule.objects.get(name="Example generator workspace")
    prepared = prepare_generation_input(user=self.system_admin, schedule_id=schedule.pk, strategy="FILL_GAPS", overrides=GenerationOverrides())
    self.assertFalse(prepared.issues)
    self.assertIn(solve(prepared.solver_input).raw_status, {"OPTIMAL", "FEASIBLE"})

@override_settings(DEBUG=True)
def test_optional_infeasible_seed_does_not_damage_feasible_workspace(self):
    call_command("seed_timetables", "--with-infeasible-generator-example", stdout=StringIO())
    self.assertIn("ZERO_CANDIDATES", {i.code for i in self.prepare_impossible().issues})
    self.assertFalse(self.prepare_default().issues)
```

Retain the production-denial test and assert the command never resets users/passwords or accepts a generated proposal.

- [ ] **Step 2: Run seed tests and confirm Phase 5 fixtures are missing**

Run: `\.\venv\Scripts\python.exe manage.py test timetabling.test_generation_seed --settings=config.test_settings --noinput`

Expected: FAIL because configuration, assignment requirements, and generator workspace are absent.

- [ ] **Step 3: Extend the DEBUG-only seed**

Create fictional configuration for each demo department/`DEMO-TERM` using weekdays 1–5, 08:00–17:00, 30-minute slots, a 10-second run limit, one worker, fixed seed 17, and explicit example weights. Create lecture `1 × 120` and laboratory `1 × 180` assignment requirements, and a separate empty `Example generator workspace`; do not alter the existing manual schedule. Ensure active rooms and availability leave at least one feasible placement.

Add `--with-infeasible-generator-example`. It must create a separate non-overlapping academic term and its own offering/assignment/section/configuration whose two-hour demand cannot fit a one-hour operating window. This keeps the default demo workspace feasible while producing a relationally valid zero-candidate example.

- [ ] **Step 4: Repair and update documentation**

Remove only the malformed UTF-16 tail from `README.md`, preserving its readable content. Document the authoritative/legacy boundary, exact OR-Tools pin, environment caps, configuration fields, readiness, both strategies, lifecycle/raw statuses, hard and soft constraints, diagnostic limits, preview/accept/discard/history flow, seed options, and local commands. Update `DEVELOPMENT_PLAN.md` with Phase 5 files, migrations, automated-test count, migration/check/dependency/seed/browser evidence, and keep Phases 6 and 7 marked deferred.

- [ ] **Step 5: Run migrations against the configured local database**

Run: `\.\venv\Scripts\python.exe manage.py migrate --noinput`

Expected: `timetabling.0002_phase5_generation` and `timetabling.0003_scheduling_dependency_lock_triggers` apply successfully.

- [ ] **Step 6: Run the complete Django test suite sequentially**

Run: `\.\venv\Scripts\python.exe manage.py test --settings=config.test_settings --noinput`

Expected: all Phase 1–5 tests pass with no failures or errors. Record the exact count and elapsed time in `DEVELOPMENT_PLAN.md` only after this run.

- [ ] **Step 7: Run system, schema, dependency, and whitespace checks**

Run: `\.\venv\Scripts\python.exe manage.py check`

Run: `\.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Run: `\.\venv\Scripts\python.exe -m pip check`

Run: `git diff --check`

Expected: no system issues, no migration drift, no broken dependencies, and no whitespace errors.

- [ ] **Step 8: Exercise the seed and a real bounded solver request**

Run: `\.\venv\Scripts\python.exe manage.py seed_timetables`

Run: `\.\venv\Scripts\python.exe manage.py shell -c "from accounts.models import AdminProfile; from timetabling.generation import request_generation; from timetabling.generation_inputs import GenerationOverrides; from timetabling.models import Schedule; u=AdminProfile.objects.filter(role='super_admin',is_enabled=True).select_related('user').first().user; s=Schedule.objects.get(name='Example generator workspace'); r=request_generation(user=u,schedule_id=s.pk,strategy='FILL_GAPS',overrides=GenerationOverrides()); print(r.status,r.solver_status,r.proposed_meeting_count)"`

Expected: the seed succeeds and the run prints `PROPOSAL_READY` followed by `OPTIMAL` or `FEASIBLE` and a positive proposal count. Do not accept it from this smoke command.

- [ ] **Step 9: Review desktop and mobile workflows in a real browser**

Run: `\.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000`

Sign in with a seeded authorized account. At desktop and mobile widths, verify generator filters/configuration summary, replacement warning, proposal week, diagnostics, status text, history, POST accept/discard controls, schedule provenance badges, sidebar collapse, keyboard focus, and absence of foreign-unit labels. Correct any functional or accessibility issue and rerun the affected automated tests.

- [ ] **Step 10: Re-run final evidence after browser fixes and commit**

Run: `\.\venv\Scripts\python.exe manage.py test --settings=config.test_settings --noinput`

Run: `\.\venv\Scripts\python.exe manage.py check`

Run: `\.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Run: `\.\venv\Scripts\python.exe -m pip check`

Run: `git diff --check`

```powershell
git add timetabling/management/commands/seed_timetables.py timetabling/test_generation_seed.py README.md DEVELOPMENT_PLAN.md
git commit -m "docs: complete Phase 5 timetable generation"
```

- [ ] **Step 11: Stop before Phase 6**

Report the models, migrations, solver modules, services, pages, tests, commands, and verification evidence created in Phase 5. Do not implement advanced workload balancing or scheduling recommendation optimization.
