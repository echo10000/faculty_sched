# CampusLoad — Step 3C-1 room workspace

The Django room workspace uses the approved Academic Workbench / Arctic Emerald
styles and preserves the global sidebar. The existing room forms, scoped selectors,
write services, eligibility service and room rules remain authoritative.

The room directory provides server-side search, building/type/status/organization
filters, pagination, ownership, seating, resource checks and responsive cards.
Room profiles separate saved configuration from class suitability and time
availability. The existing class comparison now has readable mobile rows,
mandatory/preferred specialization indicators, mandatory/advisory capacity
explanations and room schedule links. Availability remains **Not checked** until
an authoritative timetable check is performed elsewhere.

Validated links retain the term, selected class, schedule version, guided Stage 4
context and directory filters through room details, editing and status changes.
Room ownership filters remain independent of the selected schedule's department.

## Original failures and fixes

| Original failing test | Cause |
| --- | --- |
| Directory → detail → edit/save | Offering context was passed to a timetable-only selector, producing 404 before directory rendering. |
| Directory pagination/context | The same 404 hid Next. Once fixed, the new test also needed to resolve the shared query-relative href against `/rooms/`, as a browser does. |
| Eligibility → detail → comparison | The room detail request became 404, so no return comparison link existed. |
| Existing selected-term navigation | The same 404 response had no `term` template context. |
| Status/context redirect | Context validation returned 404 before the existing status service could run. |

The helper now uses `workloads.selectors.scoped_records` for `SubjectOffering`.
Only malformed input IDs are converted to 404; internal selector errors are no
longer hidden by a broad `ValueError` handler. Existing expectations were retained.
Status assertions now make failed navigation explicit. Additional tests cover
inactive-room reactivation, read-only access, cross-scope status denial and room
schedule report context. No permission, status restriction or scoping rule was
weakened, and the existing eligibility tests were not modified.

## Verification

- Exact original five tests: **5 passed** (5.041 seconds).
- Focused room, eligibility, account permissions and guided navigation: **140 passed** (145.922 seconds).
- Full isolated PostgreSQL regression: **734 passed** (1195.349 seconds), exit code 0.
- Django system checks, migration drift, dependency health, all five JavaScript
  syntax checks and Git whitespace checks passed.
- Real browser at **1440×1000** and **390×844**: directory search and combined
  filters, pagination, detail, invalid form recovery, editing/saving,
  deactivate/reactivate, room schedule report, comparison round trips and guided
  Stage 4 links passed. Mandatory failures and preferred/advisory warnings were
  checked against fictional saved preview requirements.
- Checked screens had no document horizontal overflow or duplicate IDs. Controls
  had labels, field errors used `aria-invalid` / `aria-describedby`, and keyboard
  focus was visible. A final desktop reload had no runtime or console errors.
  The initial browser session recorded the existing unrelated favicon.ico 404.

Verification used a dedicated loopback PostgreSQL cluster on port 55433, separate
preview and Django test databases, and a repository-local virtual environment.
The normal development database and `.env` were untouched. No models, migrations,
requirements, eligibility rules, solver/allocation logic, workload calculations,
faculty assignment logic or publication rules changed. Logs and screenshots are
local evidence under `.local/room-workspace/` and are excluded from Git.

## Remaining limits

Each room still has one exact-match category, and one class room requirement
applies to all its lecture and laboratory meetings. There is no equipment inventory
or time-availability claim added here. Room schedule reports and existing
occupancy/conflict checks remain the routes for reviewing recorded meetings.
Step 3C-2 is outside this change.
