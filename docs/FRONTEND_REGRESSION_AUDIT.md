# Frontend regression audit

## Scope

Reviewed the current refactor against the existing Django URLs, permission checks,
scoped selectors, forms, templates, dashboard datasets and workflow services.
The automated page audit covers System Admin, Dean, Department Chair and default
Staff: dashboard/sidebar destinations, faculty detail/workload, every schedule
tab, and one further level of rendered internal links. It resolves form actions,
checks CSRF fields on POST forms, and rejects duplicate active sidebar entries.
Existing tests cover write actions, custom grants, direct URL access, exports,
concurrency and the business workflows. No production approval or assignment was
changed during browser testing.

## Issues found and fixed

1. Prepare Schedule submitted the old schedule ID when changing academic term.
   Split term selection from schedule selection, preserving scoped lookups.
2. Faculty directory/detail/navigation could lose the selected workload term.
   Carry the validated term through faculty context links.
3. Dashboard workflow links attached academic_term to pages that do not consume
   that parameter. Removed misleading parameters on those destinations.
4. Official Schedules had an Approved heading but still rendered submitted_at.
   It now displays approval_snapshot.approved_at and approved_by.
5. The review list's Created / submitted heading still displayed only the
   submission date. It now includes creator, creation date, submitter and date.

The refactor verification also corrected inserted text encoding and ensured
schedule tabs appear before collapsible secondary actions on mobile.

## Browser verification

- Chair: term changes between populated terms; schedule filtering; schedule
  overview, unscheduled and official timetable; mobile drawer navigation.
- Dean: college context, review queue and official schedule list.
- Staff: dashboard only, with no unauthorized sidebar actions.
- Admin: institution context, administration link, desktop collapse/expand.
- Login and POST logout redirects verified.
- Inspected widths: 390, 768, 1366 and 1920px. No page-level horizontal overflow
  on the inspected screens. Narrow schedule tables scroll inside their wrapper;
  the weekly timetable stacks day columns on mobile.
- Corrected official approval values verified against the rendered local page.

## Backend preservation

No modifications to models, migrations, permission definitions, scoping
selectors, CP-SAT input/candidates/objectives, conflict detection, workload
calculation or limits, KEEP/REASSIGN/NEW decisions, generation acceptance,
validation mutations, schedule families/versions, approval transitions,
immutable snapshots/history, official selection, resource bookings or audit.
The new view context only reads existing scoped data and calls existing services.

## Known limits

- Review/official/history index pages retain their existing all-term behavior;
  the dashboard's selected term is not a new global session setting.
- Unscheduled lists distinguish assignments with no meetings from the validator's
  partial/mismatched coverage findings. They do not claim a per-class solver cause.
- Scheduling workflow stages are navigation guidance, not completion percentages.
- No unsupported notification, AI-confidence, automatic resolution or version-diff
  controls were found. No new business functionality was added during this audit.
- Automated traversal uses fixture data and two link levels; it is not a claim
  that every possible data/permission combination was manually browsed.

## Final verification

- Complete PostgreSQL suite: `python manage.py test --settings=config.test_settings --noinput`.
  **458 tests passed**, 234.115 seconds, with no failures or errors.
  This includes eight workspace UX tests and four frontend audit tests.
- `python manage.py check`: no issues.
- `python manage.py makemigrations --check --dry-run`: no model changes detected
  (exit 0). On the final recheck, PostgreSQL at 127.0.0.1:55432 was unavailable,
  so Django warned that it could not recheck database migration-history
  consistency. The completed PostgreSQL test run above predates this shutdown.
- `git -c core.autocrlf=false diff --check`: clean.
- All Django templates compiled successfully.
- Expected CSRF-denial log entries are assertions of protection, not test failures.

No confirmed regression remains within the inspected and tested scope.
