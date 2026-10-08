# CampusLoad pre-workflow checkpoint review

Date: 8 October 2026

This report records the original checkpoint. Subsequent fixes on a separate branch are documented in the follow-up section below.

Repository: echo10000/faculty_sched
Original branch: codex/phase-10
Original HEAD: c6bdb0191f6f229f174263217d544a4c887737c0
Verified GitHub main: 0506d8e58439f520fdc8ade9d0822045b721647e

Original HEAD and main have identical committed trees; the local branch is one merge commit behind main.

Review scope: 70 modified tracked files and 35 untracked files. Preserve all 70 tracked changes and 34 reviewed source/test/documentation files. Exclude .audit-final-tests.log. This report is a new documentation-only checkpoint artifact.

## Features absent from GitHub main

- Automatic academic calendar: editable year start and semester date patterns, current/next-year creation on a signed-in request, preserved existing periods, transactional auditing, and current-term-only staff selectors/actions when enabled. Additive academics migrations 0007 and 0008.
- Section-based instructor assignment: show subjects before instructors; preview current effective workload before saving through the existing audited service; support teaching shares; administrator-only editing of individual faculty limits. Existing workload calculation and the distinction between academic units, weekly contact hours and weighted load are retained.
- Section-based timetable proposals: scope demands to the selected section; fill gaps or replace only its unlocked meetings; retain other sections and locked meetings; preserve peer faculty/room occupancy checks; revalidate section metadata and dependencies on acceptance. Department-wide generation remains available.
- Room requirements: readable required/preferred room-type and hard-capacity summaries in requirement tables; related program/year/section filters; fictional lecture/lab/workshop demo resources. Existing room models and core enforcement rules remain.
- Connected navigation and frontend: shared context-aware URL tag, program/year browsing from recorded section relationships, scoped conflict links, preselected unscheduled assignment, faculty picker, calendar/table views of frozen faculty publications, weekly availability, clearer workload summaries, filters, statuses, table density, keyboard focus, and shared CSS tokens.
- Development utilities: random-password sample account provisioning, DEBUG-guarded account setup, five college-scoped staff logins, sample calendar and expanded section demo seed; optional archival of recognized legacy samples.

## Redesign boundaries and risks

- Calendar enablement changes staff term access on the server, including direct URLs and recommendation actions. During term gaps, staff have no current term. Admins retain historical/upcoming access. Automatic creation is request-driven; there is no background scheduler and no class/assignment/student rollover.
- Section generation is a proposal workflow; it writes meetings only when accepted. Publication still requires a complete department schedule. There is no individual-section publication.
- Weekly meeting patterns still require explicit configuration. Units, contact hours, shares and weighted workload are separate concepts. Do not replace the authoritative calculator or validators in a UI redesign.
- Shared teaching does not replace an existing instructor identity. The demo seed looks up an assignment by offering alone, so reseeding after adding multiple instructor shares could fail with MultipleObjectsReturned; its transaction should roll back. This is a source-review concern, not a reproduced failure.
- The optional DEBUG --archive-legacy seed deactivates recognized sample records; it must be used deliberately on disposable demo data. This review does not execute it against the development database.
- Browser visual QA remains pending according to docs/FRONTEND_DESIGN_PLAN.md. Template compilation and request tests do not establish responsive visual correctness.
- README prepared-workspace account/role instructions predate the updated demo documentation; the frontend plan also contains historical test counts. Treat them as historical, not current verification.

## Safety review

- No embedded credential-format matches or matches to actual local .env/database/demo-account secret values found in candidates. No secret values are included in this report.
- All checkpoint candidates are text project files; no local databases, private keys, archives, binary artifacts, generated runtime assets, or credential files are selected.
- .env and .local/ remain ignored, including development credentials, PostgreSQL clusters/passwords/logs, local data dumps, screenshots, browser fixtures and helper scripts. staticfiles/, virtual environments and Python caches remain excluded.
- config/settings.py adds only the calendar middleware; it adds no machine-specific connection configuration or hardcoded credentials.
- Demo utilities generate random passwords and write them after transaction commit to the ignored local credential file; command entry points are DEBUG-guarded. Fixture password strings are synthetic test data.
- No application functionality was edited for this checkpoint. Original file hashes are checked before staging.

## Verification

- Django system check: passed.
- Migration drift check: no changes detected. Migration graph: no conflicts, academics leaf 0008. Existing development database records already include 0007 and 0008.
- Changed/new Python AST syntax: passed. All 58 templates compile. JavaScript syntax: passed. Dependency integrity: no broken requirements. Git whitespace check: passed. CSS brace totals balance (not a browser or full CSS parser check).
- Corrected full PostgreSQL suite: 562 tests in 551.548 seconds; 560 passed, 2 failed, 0 errors. Tests use a unique disposable database; all migrations apply there. No migrate or seed commands are applied to the development database.
- The first verification harness redirected BASE_DIR, hiding the PDF logo asset. The entire suite was rerun with actual asset paths; the PDF errors did not recur. The two remaining failures are recorded below.

## Reviewed original file manifest

Categories below are primary ownership for inventory purposes; files can support several features. Room requirements span scheduling/view/template/demo files, so they have no exclusive extra file count.

### Academic calendar and migrations (10 project files)

- `academics/admin.py`
- `academics/models.py`
- `config/settings.py`
- `core/admin_site.py`
- `academics/calendar.py`
- `academics/management/__init__.py`
- `academics/management/commands/__init__.py`
- `academics/management/commands/configure_sample_calendar.py`
- `academics/migrations/0007_academiccalendar_calendartermpattern_and_more.py`
- `academics/migrations/0008_academiccalendar_academic_years.py`

### Faculty workload and teaching units (8 project files)

- `faculty/portal.py`
- `workloads/balancing_forms.py`
- `workloads/balancing_service.py`
- `workloads/forms.py`
- `workloads/selectors.py`
- `workloads/urls.py`
- `workloads/views.py`
- `workloads/assignment_views.py`

### Scheduling and conflict detection (10 project files)

- `timetabling/conflicts.py`
- `timetabling/forms.py`
- `timetabling/generation.py`
- `timetabling/generation_inputs.py`
- `timetabling/generation_validation.py`
- `timetabling/queries.py`
- `timetabling/urls.py`
- `timetabling/views.py`
- `timetabling/workflow_views.py`
- `timetabling/presentation.py`

### Navigation and frontend (53 project files)

- `core/context_processors.py`
- `core/views.py`
- `reporting/services.py`
- `reporting/views.py`
- `resources/forms.py`
- `resources/views.py`
- `static/css/app.css`
- `static/css/refinement.css`
- `static/js/app.js`
- `templates/base.html`
- `templates/core/calendar.html`
- `templates/core/dashboard.html`
- `templates/faculty/teaching_portal.html`
- `templates/includes/breadcrumbs.html`
- `templates/includes/faculty_navigation.html`
- `templates/includes/pagination.html`
- `templates/includes/sidebar_navigation.html`
- `templates/includes/topbar.html`
- `templates/registration/login.html`
- `templates/reporting/detail.html`
- `templates/resources/detail.html`
- `templates/resources/fields.html`
- `templates/resources/form.html`
- `templates/resources/list.html`
- `templates/timetabling/_navigation.html`
- `templates/timetabling/_unscheduled.html`
- `templates/timetabling/conflict_list.html`
- `templates/timetabling/detail.html`
- `templates/timetabling/form.html`
- `templates/timetabling/generation_run_detail.html`
- `templates/timetabling/generator.html`
- `templates/timetabling/list.html`
- `templates/timetabling/prepare.html`
- `templates/timetabling/workflow_history.html`
- `templates/timetabling/workflow_list.html`
- `templates/timetabling/workflow_review.html`
- `templates/workloads/balancing_request.html`
- `templates/workloads/faculty.html`
- `templates/workloads/form.html`
- `templates/workloads/list.html`
- `templates/workloads/record.html`
- `templates/workloads/term.html`
- `core/templatetags/__init__.py`
- `core/templatetags/workspace.py`
- `resources/browsing.py`
- `templates/includes/academic_browse_navigation.html`
- `templates/includes/filter_fields.html`
- `templates/resources/subject_directory.html`
- `templates/timetabling/section_generator.html`
- `templates/timetabling/status.html`
- `templates/workloads/faculty_availability.html`
- `templates/workloads/faculty_directory.html`
- `templates/workloads/section_assignments.html`

### Tests and documentation (17 project files)

- `core/tests.py`
- `docs/DEMO.md`
- `resources/tests.py`
- `timetabling/test_workflow_views.py`
- `timetabling/test_workspace_ux.py`
- `workloads/tests.py`
- `academics/test_rollover.py`
- `accounts/test_college_demo_staff.py`
- `accounts/test_demo_accounts.py`
- `core/test_back_navigation.py`
- `docs/FRONTEND_DESIGN_PLAN.md`
- `resources/test_academic_browsing.py`
- `resources/test_frontend_design.py`
- `timetabling/test_connected_workspace.py`
- `timetabling/test_section_generation.py`
- `workloads/test_section_assignments.py`
- `workloads/test_staff_clarity.py`

### Local/development-only (6 project files)

- `core/management/commands/seed_foundation.py`
- `timetabling/management/commands/seed_review.py`
- `accounts/demo.py`
- `core/management/commands/configure_demo_accounts.py`
- `core/management/commands/create_college_demo_staff.py`
- `timetabling/management/commands/seed_section_demo.py`

## Confirmed issues requiring follow-up

- `timetabling.tests.IntegrationTests.test_scheduled_teaching_assignment_removal_explains_dependency` fails at `timetabling/tests.py:357`: it expects the old message "Remove its timetable meetings first". The existing uncommitted view now explains both meeting-requirement and timetable dependencies. The protected-delete response remains HTTP 200. Preserve the current code/test mismatch for review; no test was changed to make it pass.
- `workloads.test_balancing_views.BalancingPageTests.test_navigation_permissions` fails at `workloads/test_balancing_views.py:139`: it expects "Balance faculty workload" and recommendation-history links in primary staff navigation. Staff navigation now favors Faculty and section-based Teaching assignments, with recommendations as secondary workspace tools. Reconcile the navigation contract before redesign.
- **Confirmed calendar inconsistency:** with automatic calendar enabled, the official-schedule list can display historical official selections in the staff member's own organizational scope if `academic_term` is explicitly blank, or if no current term exists during a calendar gap. Direct access to the same historical schedule returns 404. An isolated reproduction test passed, observing both cases through the real official-list view. The list begins with organization-only `ActiveSchedule` scoping and an optional term filter; an empty valid field skips the term restriction. This is inconsistent with the documented current-term-only policy. No cross-college disclosure was observed; no real records were exported or committed. Relevant code: `timetabling/workflow_views.py` (`_workflow_filters`, `official_schedules`), `academics/calendar.py` (`use_current_term_field`). This checkpoint preserves the defect and does not deploy it.

## Intentionally excluded

- `.audit-final-tests.log` — existing generated local test output.
- All existing ignored `.env`, `.local/`, database/runtime, credentials, build output, cache and virtual-environment files.
- New ignored review manifests and verification logs used during this checkpoint.

## Readiness

Safe to preserve as a review checkpoint. The project is not ready to begin a broad workflow redesign without first resolving the official-list term inconsistency and reconciling the two failing test contracts. Browser visual verification is also pending. No UX redesign or application fixes were performed during this checkpoint.

## Checkpoint artifact

- Branch: `checkpoint/campusload-pre-workflow-ux`
- This new report: `docs/CHECKPOINT_REVIEW.md`
- Expected commit scope: 105 files (70 modified tracked + 34 previously untracked project files + this review report).
- Original application/source/test/documentation files remain byte-for-byte unchanged in the working directory.

## Follow-up regression fixes (8 October 2026)

Branch: `codex/checkpoint-regression-fixes`, based on checkpoint commit `e88d944`. The checkpoint branch remains unchanged.

- Official schedule selections now use `timetabling.queries.scoped`, which supports `ActiveSchedule` through the existing calendar constraint and organizational scope. Blank or omitted term filters cannot widen staff access; calendar gaps yield no staff selections. Administrators retain historical access. No calendar rules or form behavior were changed.
- The protected-assignment deletion test now checks the improved explanation of meeting-requirement and timetable dependencies. It also verifies that the assignment and meeting remain and no deletion audit event is committed. An additional test covers meeting requirements without timetable entries. The deletion implementation and message remain unchanged.
- Navigation tests now verify staff primary links for section assignments and timetable generation, and follow the labeled secondary recommendation/history links under the native `More assignment tools` disclosure. They check administrator links and deny read-only Faculty access to these staff routes. No navigation templates or permission rules were changed.
- Calendar regressions cover valid, blank, omitted, historical and invalid term parameters; calendar gaps; department and college boundaries; direct detail/review/history access; administrator history; and disabled-calendar behavior. Fixtures use the real validation and publication services.
- Verification: reproduced the calendar bypass and the two outdated assertions before fixing them; 50 focused PostgreSQL tests passed in 43.773 seconds; the full PostgreSQL suite passed all 571 tests in 636.357 seconds with zero failures/errors. Default and test-settings Django checks passed. Migration drift check found no changes; Git whitespace checks passed.
- Tests used unique disposable PostgreSQL databases, which were removed afterward. No development-data migrations or seeding were performed. Solver, workload calculations, migrations, academic rules and publication semantics remain unchanged.

The three confirmed follow-up issues above are resolved on the fix branch. The workflow/navigation redesign has not started. The checkpoint's broader browser visual QA remains a separate pending activity; these fixes were verified through rendered-page and permission regression tests without frontend changes.
