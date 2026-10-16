# CampusLoad — Step 3C-2 timetable interfaces

The existing Django timetable screens now use the Academic Workbench / Arctic
Emerald tokens for readable weekly cards, mobile day navigation and grouped
validation findings. The global sidebar and the seven-stage workflow remain in
place. All cards represent saved entries or explicitly labeled captured proposals.
Empty calendar space makes no claim about resource availability.

## Viewing and navigation

- Saved timetable cards show time, meeting type, subject, section, instructor,
  room, source and lock status. Meetings are sorted within each weekday and use
  document flow so conflicting meetings cannot visually overlap.
- Desktop uses seven day columns; narrower desktop widths can scroll the calendar
  region. At 390px the days stack, with a sticky seven-day navigation bar and
  touch-friendly actions. Day links support keyboard focus and visible outlines.
- Existing faculty, section, room, weekday and subject filters remain server-side.
  Validated filters follow schedule tabs, meeting edit/delete, generation history,
  proposal acceptance/discard and review transitions. Generation history can show
  only the selected saved version; its unfiltered history remains available.
- Draft, validated, current official and published historical versions retain
  their existing state labels and write restrictions. Accepting a proposal is
  explicitly distinguished from checking or publishing the saved draft.
- Faculty calendar and table views display frozen publication metadata. Reading
  or formatting a snapshot does not write back to its stored payload.

## Coverage and conflicts

Assignments without meetings, assignments with saved meetings and coverage
discrepancies, and unaccepted generated proposals have separate explanations.
The navigation count explicitly counts assignments **without meetings**; zero
does not imply that all required hours are scheduled.

The existing coverage validator's loop was extracted unchanged into
`assignment_requirement_conflicts`. Both canonical schedule validation and the
per-assignment presentation call that same function. No conflict detection,
meeting requirements or severity rules changed. Requirements are loaded once for
the already scoped assignments. Existing conflict messages, remedies and links
are retained, grouped by severity and code with native disclosure controls.

## Verification

- Focused timetable, generation, workflow, permission and frontend tests:
  **134 passed** (288.529 seconds).
- New interface module, including selected-version generation history:
  **17 passed** (24.223 seconds).
- Full isolated PostgreSQL regression: **751 passed** (905.025 seconds), exit code 0.
- Django system checks, migration-drift checks, dependency health, syntax checks
  for all five existing JavaScript files and Git whitespace checks passed.
- Real Chromium checks at **1440×1000** and **390×844** covered empty, populated,
  partial coverage, overlapping, validated, published official, published
  historical, proposed, accepted, unscheduled, conflicts and publication-review
  screens. All returned 200, with no document horizontal overflow, duplicate IDs
  or JavaScript runtime errors.
- Combined meeting filters, reset, faculty quick search, edit/cancel, contextual
  generation history, keyboard day navigation and actual proposal acceptance
  were exercised. Acceptance returned to a draft that remained not official.
- Meeting editing preserved invalid values with `aria-invalid` and a linked
  field error, then supported correction, conflict preview and saving. Final
  mobile edit links measured 44px high; the final reload had no console errors.
- At 768px, 1024px and 1280px the saved and proposed calendars scrolled within
  their own regions without horizontal document overflow.
- Faculty calendar and table views passed at both widths with frozen version,
  department and instructor labels, no editing links, and HTTP 403 for staff
  timetable access.

One new snapshot test initially expected the live department name. The canonical
snapshot intentionally stores the department's string label. The assertion now
checks that frozen value; the snapshot immutability and live-label exclusion
checks remain intact. Existing tests and canonical validator expectations were
not weakened.

Verification uses a dedicated loopback PostgreSQL cluster on port 55433, a
`timetable_workspace_preview` database and Django's separate test database. The
normal development database and `.env` are untouched. Test logs, preview sessions
and screenshots stay under Git-ignored `.local/timetable-workspace/`.

## Changed files

- `core/templatetags/workspace.py`
- `faculty/portal.py`
- `static/css/timetable-workspace.css`
- `templates/faculty/teaching_portal.html`
- `templates/timetabling/_conflict_groups.html`
- `templates/timetabling/_day_navigation.html`
- `templates/timetabling/_navigation.html`
- `templates/timetabling/_unscheduled.html`
- `templates/timetabling/_weekly_meetings.html`
- `templates/timetabling/detail.html`
- `templates/timetabling/generation_run_detail.html`
- `templates/timetabling/generation_run_list.html`
- `templates/timetabling/workflow_history.html`
- `templates/timetabling/workflow_review.html`
- `templates/timetabling/workspace_base.html`
- `timetabling/conflicts.py`
- `timetabling/presentation.py`
- `timetabling/test_timetable_interfaces.py`
- `timetabling/timetable.py`
- `timetabling/views.py`
- `timetabling/workflow_views.py`
- `docs/UI_TIMETABLE_WORKSPACE.md`

## Remaining limits

Coverage follows the existing distinction between explicit meeting requirements
and advisory weekly-hour findings. It introduces no new scheduling policy.
Captured proposals still resolve resource labels through the existing authorized
selectors and report unavailable source records. Staff saved-entry views retain
their existing live labels; publication review and faculty records use frozen
snapshots. No models, migrations, dependencies, roles, solver constraints,
allocation rules, workload calculations or publication transitions changed.
Step 3C-2 ends here; unrelated modules are outside this change.
