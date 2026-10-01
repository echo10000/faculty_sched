# CampusLoad frontend audit — 29 September 2026

## Current structure and implementation map

The existing sidebar groups Overview, Faculty & workload, Academic setup,
Scheduling, Review & approval, Reports, More & settings, and Administration.
Groups are permission filtered and ordered by role. Bootstrap supplies the mobile
drawer; app.js stores the desktop collapse preference. No AGENTS.md was found.
The working tree was clean before this task.

| Existing pages (including their forms) | Classification | Implementation |
| --- | --- | --- |
| Public landing, login, logout, error pages | KEEP | Existing public/authentication routes |
| Dashboard `/dashboard/` | REDESIGN | Existing monitoring + explicit workflow navigation |
| Academic calendar `/academic-calendar/` | MOVE | Academic setup; keep year/semester/term data |
| Colleges/departments list and detail | KEEP | Administration and existing scoped detail pages |
| Faculty, subjects, rooms list/detail/edit/status | REDESIGN / KEEP | Existing resources templates and scoped views; faculty contextual tabs |
| Offerings and sections | MOVE | Academic setup |
| Assignments, availability, workload list and faculty workload | MERGE navigation | Keep routes/forms; connect faculty pages with shared navigation |
| Workload request, result, accept/discard, run history | KEEP / MOVE | Faculty & workload group; existing human decisions |
| Prepare schedule | KEEP | Existing workflow guide |
| Schedule list/add/edit | REDESIGN | Keep all-version list; add explicit draft filter and view destinations |
| Schedule detail/timetable/conflicts | MERGE navigation / REDESIGN | Shared schedule tabs; same underlying views |
| Generation request/result/history/accept/discard | KEEP / REDESIGN | Read-only hard-rule and preference explanations |
| Requirements, meeting requirements, room closures | MOVE | Academic setup alongside the data they constrain |
| Generation configuration | MOVE | Administration, retaining existing permission grants |
| My schedules, review queue, official list | KEEP / REDESIGN | Contextual creator/approval data and existing actions |
| Review and history | MERGE navigation | Shared schedule tabs; approval service stays authoritative |
| Report catalog/detail/print/exports | KEEP | Existing catalog is already permission filtered |
| System administration | KEEP | Existing site gate and registered models only |
| Unmounted legacy faculty/scheduling templates | KEEP unmounted | Do not reactivate or remove retained legacy functionality |

Apps inspected: accounts, core, academics, faculty, resources, workloads,
timetabling, scheduling, reporting, audit. Active URL namespaces are accounts,
faculty-management, subjects, rooms, workloads, timetabling, reporting and admin.
`config/urls.py` intentionally leaves legacy assignment/scheduling URLs unmounted.

## Permissions and data

System Admin has institution scope; Dean has college scope and approval;
Department Chair has department scope and submission/review without approval.
Staff starts with dashboard access and uses explicit grants for additional pages.
The existing enabled-profile check, scoped selectors, direct URL permissions,
CSRF and submitter/reviewer separation remain authoritative.

Reuse base.html, resources fields/list/detail/form/status, workloads list/faculty,
timetabling detail/conflict_list/generator/workflow templates, reporting templates,
pagination, Bootstrap, app.css/refinement.css, and app.js. Add shared breadcrumbs,
faculty navigation, schedule navigation, and a presentation-only unscheduled panel.

Missing: a contextual Unscheduled screen and a unified schedule navigation bar.
Overlaps: faculty master/workload/availability pages; schedule meeting list,
timetable, conflict and review/history pages. Keep their URLs and connect them.

Programs/curriculum and qualifications have retained models but no supported
administrative CRUD in the allowlisted admin site. Do not expose unfinished CRUD.
Qualifications may be read only with their existing explicit model permission.
No notification service, reliable faculty audit-history page, or version diff is
available. Do not create decorative tabs or fabricated data for these.

## Backend boundary

No changes to models, migrations, permission definitions, scoping selectors,
workload calculation/policy, balancing KEEP/REASSIGN/NEW decisions, CP-SAT input,
candidates/objectives, validation, generation acceptance, schedule families,
numbered versions, workflow transitions, snapshots, official bookings or audit.
Unscheduled presentation will reuse current validation findings; missing meetings
must not be labeled with an invented solver cause. Metrics will identify whether
they describe assignments, meetings, versions, or official selections.

## Incremental implementation

1. Shared navigation, breadcrumbs and layout: core/context_processors.py,
   templates/base.html, templates/includes, static/css/refinement.css.
2. Faculty context and workload presentation: resources/views.py and forms.py,
   resources/workloads templates, existing workload services only.
3. Scheduling workspace and unscheduled route: timetabling/views.py/urls.py,
   workflow_views.py, scheduling templates and focused authorization tests.
4. Dashboard workflow links, generator explanation and review metadata.
5. Full tests, checks, migration dry run, browser verification, separate commits.

Audit outcome: no page was removed, no legacy module was reactivated, and no business-service or schema change was needed.
