# CampusLoad Step 4B — release readiness repairs

Date: 18 October 2026 (Asia/Manila).
Base: `codex/campusload-timetable-ux`, `5005bd94a12a1576493e497b92056a4b86a16576`.
Repair branch: `codex/campusload-step4-readiness-fixes`.

## Completed repairs

1. **Faculty assignments remain manual.** Balancing still calculates scoped advisory recommendations and stores history, but its assignment application implementation is removed. The legacy acceptance service and POST URL repeat permission/scope checks and reject with an advisory-only error (HTTP 409), without changing assignments, recommendation history or audit records. Historical accepted records remain readable and clearly identified. Staff deliberately select instructors and shares through the existing Teaching Assignment Desk; normal scope, share and workload validation remain intact. Timetable generation still allocates meetings for the existing instructor assignments.
2. **Published labels use the version's own snapshot.** Staff timetable cards, summaries, review/history and schedule indexes use frozen publication metadata. Search and resource filters preserve authorized identities and frozen subject values. Draft/revision labels stay live. Missing legacy metadata is identified as not recorded at publication instead of supplied from current master records. Scope checks remain the access boundary; current conflict/coverage diagnostics are explicitly identified as current inputs. Historical snapshots are never rewritten.
3. **Future snapshots capture lunch/rest configuration.** Publication JSON now includes lunch_break_enabled, lunch_start, lunch_end and faculty_rest_minutes. Times are ISO strings, booleans/integers keep their native JSON types, and disabled null times remain null. Missing configuration remains distinct from configured-disabled settings; absent historical values remain unknown. No scheduling constraint, policy calculation or migration changed.
4. **Unicode assertion repaired.** The proposal-status assertion now expects the intended U+00B7 middle dot instead of the corrupted U+00C2 U+00B7 sequence. Its original status, filter and no-write assertions remain. The real proposal page displays the correct text.
5. **Mobile academic calendar is readable.** Scoped table sizing and nowrap date/year/status cells use the existing responsive scroll container, with keyboard focus and an accessible label. Dates no longer fragment at 390px and the document does not overflow. Desktop layout is preserved.
6. **Current documentation is accurate.** README and demo instructions reflect Admin, Authorized Staff and Faculty, college-scoped sample Staff, individually linked Faculty accounts and generated local credentials. Balancing instructions are advisory/manual-only. Earlier role/approval/acceptance records are explicitly historical.

## Verification

- Focused integration, balancing, manual-assignment desk, publication, snapshot, timetable and permission tests: **144 passed** in 142.311 seconds.
- Separate new publication module: **9 passed** in 15.653 seconds. This module is included in the focused and full totals; counts overlap.
- Corrected original Unicode regression: **1 passed** in 1.275 seconds; also included in later totals.
- Full isolated PostgreSQL regression: **767 passed in 917.236 seconds; no failures or errors**.
- Django system check: no issues (0 silenced).
- Migration drift: no changes detected; no migration added or modified.
- Dependency integrity: no broken requirements. All five existing JavaScript files passed node --check.
- Git whitespace and candidate-diff review passed. No local database, session, demo credential or token value was included in the release changes.

The focused checks include real optimizer advice without assignment writes, viewed advice without writes, role/status/scope/CSRF acceptance rejection, historical accepted audit preservation, rename-after-publication comparisons across Staff/Faculty/reports, published filters and indexes, separate revision/publication identities, missing/older snapshot data, and configured/disabled/missing/historical lunch/rest settings.

## Browser and end-to-end checks

Chromium was checked at **1440x1000** and **390x844** using a separate fictional PostgreSQL preview database. Actual Admin/Staff/Faculty sign-in, current validation/publication, draft revision, replacement publication and Faculty access were exercised. Staff and Faculty retained original published labels after source renames while the replacement draft displayed current labels. After publishing the replacement, Faculty consumed its new snapshot and the old snapshot payload remained exactly equal to its saved JSON value.

A runtime check confirmed the new official snapshot stores enabled lunch 12:00:00–13:00:00 and rest 30, with no changes to the old publication. A CSRF-valid direct legacy balancing POST returned 409 and preserved all assignment rows. Faculty direct staff timetable access returned 403. The real proposal heading contains the correct middle dot; no assignment-apply control appears on recommendations.

At 390px, the academic calendar's 617px table scrolls inside its 324px container. Whole dates and statuses remain readable; document clientWidth and scrollWidth both equal 390. Representative desktop/mobile screenshots were visually inspected. The final smoke console had no errors/warnings; intentional 403/409 negative paths were tested separately. Preview server and task-owned browser were stopped after checks.

## Data and integration boundaries

Verification used isolated PostgreSQL databases only: campusload_step4b_regression_20261018 with its Django test database, a separate snapshot-module test database, and campusload_step4b_browser_20261018. No SQLite was used. The normal development database, .env and existing local demo credentials were untouched. Runtime scripts, credentials, logs and screenshots remain outside Git.

The Step 3C-2 checkpoint branch is preserved. No reset, rebase, force push, branch deletion or merge into main was performed. Changes are confined to the repair branch. Integration into main and target-host deployment/restore rehearsal remain separate authorized work. Existing limitations such as instructional holiday/date exceptions are unchanged.

## Changed files

- `README.md`
- `docs/DEMO.md`
- `docs/STEP4B_READINESS_REPAIRS.md`
- `static/css/app.css`
- `templates/core/calendar.html`
- `templates/timetabling/_weekly_meetings.html`
- `templates/timetabling/detail.html`
- `templates/timetabling/list.html`
- `templates/timetabling/workflow_history.html`
- `templates/timetabling/workflow_list.html`
- `templates/timetabling/workflow_review.html`
- `templates/workloads/balancing_request.html`
- `templates/workloads/balancing_run_detail.html`
- `timetabling/test_publication_readiness.py`
- `timetabling/test_timetable_interfaces.py`
- `timetabling/timetable.py`
- `timetabling/views.py`
- `timetabling/workflow.py`
- `timetabling/workflow_views.py`
- `workloads/balancing_service.py`
- `workloads/balancing_views.py`
- `workloads/models.py`
- `workloads/test_balancing_service.py`
- `workloads/test_balancing_views.py`
