# CampusLoad

Faculty Workload and Academic Scheduling System for Negros Oriental State University - Bais Campus (NORSU-BSC).

**Phases 1–4 are implemented.** The application provides authentication, scoped RBAC, faculty/subject/room management, term availability, subject offerings, faculty teaching assignments, workload monitoring, configurable capacity enforcement, manual weekly timetables, deterministic conflict validation and transactional auditing in the existing Bootstrap 5 shell. Retained legacy scheduling routes are not exposed.

See [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md#14-phase-4-implementation-record) for the completed phases, architecture, migration decisions and later roadmap.

## Implemented foundation

- Existing Django User, secure password hashing, login, CSRF-protected POST logout and inactive/disabled account denial.
- System Admin, College Dean, Department Chair and Authorized Staff; organizational scope enforced independently of model permissions.
- College, Department, AcademicYear, configurable Semester, AcademicTerm and initial SystemSetting models.
- Protected hierarchy, date/uniqueness/scope constraints and PostgreSQL validation triggers.
- Permission-protected dashboard, scoped read-only college/department pages, academic calendar and locally bundled Bootstrap 5.3.8.
- System-admin-only Django admin, including user/group/profile management.
- Append-only audit records for authentication, admin mutations and development seeding; database protection against audit editing/deletion.
- Additive migrations, idempotent development seeds and 165 automated tests including all 134 Phase 1–3 tests.
- Faculty, subject and room list/detail/create/edit/status pages, scoped search/filters/pagination and dashboard counts.
- Configurable employment categories, academic ranks, buildings, room types and academic-term teaching-capacity policies/overrides.

Term-specific teaching assignments and manual timetable creation/conflict checks are available. No automated timetable generation or AI feature is exposed. No paid AI API is needed.

## Run this prepared workspace

The review found no running PostgreSQL service. An isolated loopback-only development database was prepared under Git-ignored `.local/`, on port **55432**, with generated credentials. `.env` points to this database; its previous contents are backed up in `.local/original.env`.

From the repository root in PowerShell:

```powershell
.\venv\Scripts\python.exe scripts/dev_database.py start
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py seed_foundation --create-users --with-timetables
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open [the application](http://127.0.0.1:8000/). Development accounts are:

| Account | Role |
| --- | --- |
| `dev.admin` | System Admin; can open Django admin |
| `dev.dean` | College Dean for Example College A |
| `dev.chair` | Department Chair for Example Department One |
| `dev.staff` | Authorized Staff; dashboard access only initially |

Their generated passwords are in `.local/development-credentials.txt`. Do not commit/share this file or `.env`. The seed never resets existing account passwords.

Stop the development database when finished:

```powershell
.\venv\Scripts\python.exe scripts/dev_database.py stop
```

The Windows helper uses PostgreSQL 17.6 packaged by [Zonky](https://github.com/zonkyio/embedded-postgres-binaries) from Maven Central with SHA-256 verification. It is an optional isolated development helper, not a production database installer. It creates a separate cluster/database and never deletes or reinitializes an existing cluster. `--configure-env` explicitly backs up and updates `.env`; ordinary `start` leaves `.env` unchanged.

## Fresh installation

Python 3.14.6 / Django 5.2.16 were used for this implementation. Requirements include Django, psycopg2-binary, python-decouple and retained legacy dependencies. PostgreSQL is required; SQLite does not support the current migrations/exclusions.

### 1. Install Python dependencies

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use `.\.venv\Scripts\python.exe` throughout a fresh installation; the prepared workspace uses `venv` instead. Calling the interpreter directly avoids PowerShell activation-policy issues.

### 2. Choose a development database

Either use the Windows helper:

```powershell
.\.venv\Scripts\python.exe scripts/dev_database.py start --configure-env
```

Or use an existing PostgreSQL server. In `psql` as a database administrator, create a dedicated local role/database (choose different names if they already exist):

```sql
CREATE ROLE campusload_app LOGIN;
\password campusload_app
CREATE DATABASE campusload OWNER campusload_app;
\connect campusload
CREATE EXTENSION IF NOT EXISTS btree_gist;
```

The local migration role needs schema ownership/DDL privileges. Tests also require CREATEDB and extension installation in their separate database. Do not give a production runtime role these development privileges. PostgreSQL documents the extension privileges in [btree_gist](https://www.postgresql.org/docs/current/btree-gist.html).

### 3. Configure environment variables

If not using the helper, copy the example without replacing an existing `.env`:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Paste the generated secret into `.env` and set the DB credentials. Supported configuration:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Required unique secret; production rejects short/insecure values |
| `DEBUG` | Defaults False; set True only for local development |
| `ALLOWED_HOSTS` | Comma-separated hostnames, trimmed |
| `CSRF_TRUSTED_ORIGINS` | Optional comma-separated trusted origins |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection |
| `DB_SSLMODE`, `DB_CONN_MAX_AGE` | Transport mode and connection persistence |
| `TIME_ZONE` | Valid IANA zone; defaults Asia/Manila |
| `INSTITUTION_NAME` | Fallback display name; editable registered SystemSetting takes precedence |
| `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS` | Deployment HTTPS policy |

Secure session/CSRF cookies default on outside DEBUG. There is no SQLite switch or DATABASE_URL parser. Secrets live in environment variables or Git-ignored `.env`, not SystemSetting. Production proxy/TLS configuration remains release work.

### 4. Migrate, seed and start

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_foundation --create-users --with-timetables
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

`seed_foundation` requires DEBUG=True. Without an optional data flag, its Phase 1 behavior is unchanged: fictional organizations/calendar and optional accounts only. `--with-resources` also calls `seed_resources`, adding two faculty members, two subjects, two rooms, configurable reference data, and example department policies/faculty term overrides. `manage.py seed_resources` also works independently and ensures foundation data exists. `--with-teaching` also calls `seed_teaching`, which includes resource seeding and adds two offerings, two teaching assignments and six availability records. `manage.py seed_teaching` works independently. Only this new option creates teaching assignments; `--with-timetables` additionally creates the manual draft examples described below. None creates enrollment data. Repeated runs preserve edited records, account scopes and passwords. Omit `--create-users` for data only. Example load values are explicitly fictional and must be replaced with approved policy before institutional use.

For a manually managed administrator, use `manage.py createsuperuser` instead. Django superusers can sign in and administer the institution without an AdminProfile. For non-superusers, create a User and valid AdminProfile via [Django admin](http://127.0.0.1:8000/admin/). System Admin profiles additionally need `is_staff=True` for admin-site access.

## Access model

| Role | Scope and initial permissions |
| --- | --- |
| System Admin | Institution-wide foundation, resource and teaching management; workload/reference configuration in admin |
| College Dean | View/add/edit/activate faculty, subjects and rooms owned by their college or its departments; term availability, offerings, assignments and workload monitoring; shared calendar |
| Department Chair | View/add/edit/activate faculty, subjects and rooms owned by their department; term availability, offerings, assignments and workload monitoring; shared calendar |
| Authorized Staff | One college OR department; dashboard plus explicit model/group grants |

Staff grants are managed on the Django User's permissions/groups. Role defaults come from the authorization backend; `bootstrap_roles` also creates initial reusable permission bundles without overwriting existing custom grants. A permission or group name never expands an organizational scope. Only an explicit System Admin role or Django superuser status grants institution-wide access.

Business Authorized Staff does **not** mean Django `is_staff`. Non-system roles cannot use Django admin, even if `is_staff` or broad model permissions are mistakenly assigned. Missing/disabled profiles and inactive organizational scopes are denied. Out-of-scope object URLs return 404; denied actions return 403.

Academic calendars are institution-wide reference data behind calendar permission. College/department records and all resource lists, detail lookups, submitted foreign keys, filters and dashboard counts are scoped server-side. Invalid/out-of-scope filters return errors and no results. Faculty capacity by term additionally requires calendar access.

Phase 2 uses Django `view`, `add`, `change` and a separate `activate` permission for each resource. Staff receive none of these automatically. Explicit grants never widen scope; `change` alone cannot toggle active status. There are no hard-delete endpoints. College/department and global lookup/policy configuration remain restricted to system administration.

Room ownership is explicit: department-owned, college-owned or institution-owned (both ownership fields blank). College is derived from the owning department when present. Chairs manage only department-owned rooms; deans also manage their college-owned rooms. Only system administrators manage institution-owned rooms. This ownership rule does not establish future shared-room booking eligibility.

## Routes

| URL | Purpose |
| --- | --- |
| `/accounts/login/` | Application sign-in |
| `/accounts/logout/` | POST-only logout |
| `/` | Permission-protected dashboard |
| `/colleges/`, `/colleges/<id>/` | Scoped search/list/detail |
| `/departments/`, `/departments/<id>/` | Scoped search/list/detail |
| `/academic-calendar/` | Authorized calendar reference |
| `/faculty/`, `/subjects/`, `/rooms/` | Scoped directories with search, filters and pagination |
| Each directory + `add/` | Create master record |
| Each directory + `<id>/` and `<id>/edit/` | Detail and edit |
| Each directory + `<id>/status/` | Confirmation GET and CSRF-protected status POST |
| `/admin/` | System administration and read-only audit history |

The faculty URL namespace is `faculty-management`; subject and room namespaces are `subjects` and `rooms`. All expose `list`, `add`, `detail`, `edit`, and `status` names. `/faculty/dashboard/` and `/scheduling/` remain unavailable. Resource records are managed through the application, not legacy model-admin screens.

## Phase 2 architecture

Existing `faculty.Faculty`, `academics.Subject`, and `scheduling.Room` tables were extended in place. All use the existing `TrackedModel` timestamps/author fields. `resources` provides shared scoped forms, selectors, transactional save/status services and Bootstrap views/templates; it does not create a second Room table. Views delegate writes to services that recheck permissions, lock the original row, validate target ownership, save, and append the audit event atomically. A failed audit rolls back the record change. Audits identify changed fields rather than copying contact information or notes.

| Model | Purpose |
| --- | --- |
| `Faculty` | Unique employee ID, names/contact, department-derived college, employment category/rank, active status, optional recommended/maximum teaching units and notes |
| `EmploymentCategory`, `AcademicRank` | Configurable reference data managed by system administrators |
| `Subject` | Globally unique code, title/description, department, lecture/laboratory units/hours, calculated total and active status |
| `Room` | Unique code, retained name, building/type, capacity, explicit ownership and active status |
| `resources.Building`, `resources.RoomType` | Configurable room reference data, with active flags |
| `workloads.WorkloadPolicy` | One institution/college/department policy per AcademicTerm; optional limits and lecture/laboratory unit weights |
| `workloads.FacultyTermCapacity` | One optional faculty-specific limit override per AcademicTerm |

Identifiers are normalized to uppercase by application writes and protected by case-insensitive unique database constraints. Units, hours, capacity, limits and weights cannot be negative; known recommended limits cannot exceed known maxima. New forms require department ownership for faculty/subjects, employment category for faculty, and code/type for rooms. Inactive organizations and new selections of retired lookup entries are rejected. `resources.selectors.available_resources` supplies active, authorized master records for later integration.

`workloads.services.resolve_capacity` resolves each limit independently: faculty-term override → faculty baseline → department policy → college policy → institution policy. Lecture/laboratory weights inherit through the policy levels only. A weight denotes workload units per corresponding subject unit. Blank means unconfigured/inherit; zero is explicit. Conflicting inherited limits raise a validation error and are shown on the faculty detail page. Actual assigned workload is computed by the Phase 3 service described below. Policies and term overrides are maintained by system administrators under `/admin/workloads/`.

### Additive Phase 2 migrations

- `academics/0006`: extend Subject, preserve its existing total, add component/status/tracking fields and constraints.
- `faculty/0002`: extend Faculty, add employment/rank references and load/identifier constraints.
- `resources/0001`: RoomType and Building; `resources/0002`: map existing employment/room-type strings to references and backfill legacy room codes/explicit department ownership.
- `scheduling/0006`: extend the existing Room table with code, building/type, ownership, status and tracking fields.
- `workloads/0001`: term policies and faculty-term overrides, scope/uniqueness/nonnegative constraints.

Existing subject totals do not reveal their lecture/lab split, so legacy `lecture_units` remains NULL until reviewed. The retained `units` column supports old queries; once components are supplied, the model and a database constraint keep it equal to their sum. New forms never accept a separately editable total. Legacy room names/IDs remain unchanged; blank codes receive `LEGACY-<id>`, and an existing department restriction becomes explicit ownership. Unowned legacy subjects and institution-owned rooms remain system-admin-only. No legacy department or subject ownership is guessed.

Existing legacy faculty load targets are preserved; new capacity limits default to NULL. Existing applied migrations were not edited, removed or reset. Preexisting identifiers that collide case-insensitively or invalid negative data must be reviewed before deploying the new constraints to another database; migrations will fail rather than delete or merge those records. Two retained legacy boundaries normalize redundant decimal zeros for compatibility with their original one-decimal validation; the scheduling algorithm and test expectations are unchanged.

## Tests and migration validation

```powershell
.\venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\venv\Scripts\python.exe manage.py check
```

The test module disables HTTPS redirects and uses fast hashing only for test speed. **Never serve the application with `config.test_settings`.** Normal settings use Django's secure password hashing; real browser sign-in was verified with those settings.

Tests create/remove a separate `test_<DB_NAME>` PostgreSQL database. The local test role needs CREATEDB; if Django asks to replace an existing test database, verify it before responding. The optional development helper supplies an isolated role capable of running these tests.

Verified on 2026-09-12: additive migrations and teaching seed succeeded; **134 tests passed** (90 unchanged Phase 1–2 tests + 44 Phase 3 tests), including concurrent hard-limit enforcement; no migration drift; system and whitespace checks passed. Browser checks include assignment preview/save, scoped monitoring, faculty workload/policy details, 390px availability filters/table, offering form and mobile sidebar navigation. Previous phases also verified faculty save/status restoration, scoped directories and the shared mobile shell. Legacy tests use a test-only URL configuration to preserve regression coverage without exposing unfinished routes in the application.

## Migration notes and limitations

- Original migrations and existing User/College/Department IDs are preserved. New calendar entities coexist with the legacy scheduling.Term until a later reviewed migration; dates are not guessed from old labels.
- Existing department-admin profiles migrate to Authorized Staff. Legacy deans are disabled pending an explicit college assignment. Resolve these through Admin profiles before granting access.
- Audit is append-only and includes admin/auth/seed operations. Arbitrary ORM writes outside these entry points are not automatically audited; later services must call the audit service inside their transactions.
- Legacy `seed_demo_data` remains only for old regression fixtures. Use `seed_foundation --with-teaching` or `seed_teaching` for this phase; do not seed enrollment/curriculum demo data into the workspace.
- Production deployment, operational login throttling, monitoring, TLS/proxy/static serving, least-privilege runtime roles, dependency locking and backup/restore rehearsals remain later phases.

## Phase 3 teaching workspace

`Subject` remains a catalog definition. `SubjectOffering` is a particular subject offered in one AcademicTerm, identified by an offering code within that subject/term. `FacultySubjectAssignment` joins faculty to that offering; its term is derived from the offering, avoiding contradictory duplicated term fields. Legacy scheduling assignments require rooms/times and are neither reused nor exposed.

| Model/change | Purpose |
| --- | --- |
| `FacultyAvailability` | Faculty + term + weekday/time range, AVAILABLE / UNAVAILABLE / PREFERRED, notes and tracking |
| `SubjectOffering` | Catalog subject + term + department + code; explicit lecture/lab units and weekly contact hours, active flag and tracking |
| `FacultySubjectAssignment` | Faculty + offering + fractional teaching share, notes and tracking; unique faculty/offering pair |
| `WorkloadPolicy.enforce_maximum` | Nullable inherited hard-limit mode at institution/college/department level |
| `FacultyTermCapacity.enforce_maximum` | Nullable faculty/term enforcement override |

One additive migration, `workloads/0002_facultytermcapacity_enforce_maximum_and_more`, creates the new tables, range/uniqueness/share/nonnegative constraints and enforcement fields. It ensures `btree_gist` exists without dropping the shared extension on rollback. Existing User, primary keys, records and Phase 1–2 migration history are preserved.

### Pages and workflows

| URL | Purpose |
| --- | --- |
| `/workloads/` | Scoped workload monitoring, search, term/college/department/faculty/employment/rank/status filters and pagination |
| `/workloads/faculty/<id>/` | Faculty totals, assignments, policy sources, warnings and permission-gated availability summary |
| `/workloads/faculty/<id>/availability/` | Faculty-specific availability |
| `/workloads/availability/` | Availability list, term/department/faculty/day/type filters |
| `/workloads/offerings/` | Term subject offerings |
| `/workloads/assignments/` | Term teaching assignments |
| Each of the last three + `add/`, `<id>/`, `<id>/edit/` | Create, detail and edit |
| Availability or assignments + `<id>/delete/` | Confirmation GET followed by CSRF-protected removal POST |

Select an academic term before making changes; the selected term is displayed and preserved in links. When absent, the latest active term is selected, falling back to the latest historical term for viewing. There is no permanently hard-coded current semester. Calendar permission remains required.

1. In **Subject offerings**, create an offering from an active catalog subject. Blank unit/hour fields initially copy its catalog values; legacy unsplit units require explicit values. Offering identity stays fixed. Later catalog edits do not rewrite the offering's units/hours, and these quantities cannot change while assignments exist. Deactivate an offering through edit instead of deleting it.
2. Open a faculty workload detail and choose **Add subject assignment**. Select a same-department offering and teaching share (1 means the entire offering). Preview shows subject/title, units/hours, current load, change, resulting load and maximum. Save recalculates against current records under locks; an old preview cannot authorize an overload. Total shares across faculty cannot exceed 1.
3. In **Faculty availability**, add weekday/time ranges for the selected term. Start must precede end. Same-type overlaps and any overlap with UNAVAILABLE are rejected by validation and PostgreSQL exclusions. AVAILABLE and PREFERRED may overlap; adjacent intervals are allowed. Split overnight ranges across days. Unspecified times mean no recorded information, not automatic availability. Availability does not impose a teaching-hours quota in this phase.

Faculty/offering identity and original term cannot be reassigned during edit. New or edited teaching records require active faculty, subjects/offerings and active organizational/calendar parents. Existing records remain visible and counted after deactivation. Removal of availability/assignments retains an audit snapshot, but is not a restore/recycle-bin workflow.

### Calculation and policy behavior

`workloads/calculation.py` centralizes calculation and validation:

- Teaching units = sum of offering lecture + laboratory units multiplied by each assignment share.
- Weekly contact hours = sum of offering lecture + laboratory hours multiplied by each share.
- Weighted workload = assigned lecture units × lecture weight + assigned laboratory units × laboratory weight. Decimal arithmetic is retained without per-assignment rounding.
- Remaining capacity = configured maximum − weighted workload. Utilization uses the maximum, or recommended target when no maximum exists, only with a positive denominator and known workload.
- `UNDERLOAD`: below a configured recommended target. `AT_CAPACITY`: exactly at the configured maximum. `OVERLOAD`: above maximum, or above recommended when no maximum exists. Other known values within configured bounds are `WITHIN_LOAD`.
- Missing relevant limits or weights for nonzero teaching components produce `UNCONFIGURED` and explicit warnings. Zero is a configured value, not missing data. Availability and legacy scheduling assignments do not enter the totals.

Phase 2 `resolve_capacity` keeps its existing contract and per-field precedence: **faculty term override → faculty baseline → department → college → institution → unconfigured**. Weights inherit through department/college/institution policies. `resolve_policy` adds each field's source and the enforcement mode without changing that contract.

Maximum enforcement resolves **faculty term override → department → college → institution → warning-only default**. Blank inherits, False warns, True enforces. System administrators configure it through audited workload policy/capacity admin. A configured maximum alone does not make overload forbidden. Hard mode requires an explicit maximum and usable weights; otherwise the assignment is rejected with a configuration error. Warning mode permits over-target/over-maximum assignments with visible warnings. Invalid inherited recommended/maximum combinations cannot silently become permissive saves.

### Security, services and audit

`workloads/selectors.py` scopes records and term access; forms restrict every faculty/offering/department choice. `workloads/operations.py` checks action permissions and scope again inside transactions, locks faculty/offering rows, validates aggregate shares and resulting workload, then saves with its audit event. A failed audit rolls back the business change. Concurrent assignments cannot both bypass the same faculty's hard maximum.

Deans/chairs receive teaching capabilities within their existing organizational scope. Staff require explicit model permissions, calendar access, and `workloads.view_workload` for monitoring. Permission grants never widen scope. All direct URLs, lists/counts, filters and submitted IDs are checked server-side. Assignments require the same department even for system administrators; cross-department teaching needs a separately reviewed workflow. Transfers of faculty with availability/assignments or subjects with offerings are blocked to preserve ownership/history.

Audits cover availability create/update/delete, offering create/update, assignment create/update/remove and existing capacity-admin changes. They identify actor, entity, term and controlled before/after values. Notes are represented by a change indicator, not copied into audit content. Append-only protection is unchanged. Shell/ORM writes outside authorized services are not an audited application API.

`workloads/datasets.py` supplies scoped `faculty_candidates(user, term)` and `offering_data(user, term)` for future consumers: workload/capacity/remaining load, department, active flag, permitted availability, assignment IDs and offering units/hours. These are Python service contracts, not public HTTP APIs or recommendations; they do not invent qualifications or call AI services.

### Boundaries and verification

The 44 new tests cover availability CRUD/ranges/exclusions, offering snapshots and validation, assignment preview/save/remove/shares, policy precedence and all workload statuses, term isolation, malicious URL/form/POST/filter inputs, staff permissions, transactional audit rollback, seed idempotence/password preservation and competing assignment transactions. All 90 existing tests remain unchanged and pass.

Single-assignment workflows are complete; optional bulk assignment is deferred. Workload reports use **current effective policy**, not immutable approved historical policy snapshots. Offering quantities are retained independently of catalog edits. Policy/approval history and reviewed organizational transfers remain future work. Monitoring currently calculates faculty reports before applying workload-status pagination; larger deployments should profile and batch these queries.

**Phase 3 stop point (historical):** Completed before the owner authorized Phase 4 below.

## Phase 4 manual timetables

Manual scheduling is available at [Manual schedules](http://127.0.0.1:8000/timetables/). The separate `timetabling` app uses existing AcademicTerm, faculty/teaching assignments, rooms, room types/buildings, Program and faculty availability. Legacy `scheduling.Term`, Block, TimeSlot, Assignment and their old approval/solver interfaces remain unmounted and unchanged. Their enrollment coupling is not suitable for the current domain.

### Models and migration

| New model | Responsibility |
| --- | --- |
| `ClassSection` | A class group identified by department, AcademicTerm and code; optional existing Program, year level and expected size; active flag |
| `OfferingRequirement` | One-to-one scheduling configuration for an existing SubjectOffering: section, optional room type, mandatory-type flag and capacity hard-rule flag |
| `RoomUnavailability` | Room/term/weekday blocked interval with notes and tracking |
| `Schedule` | Named departmental AcademicTerm workspace, DRAFT/VALIDATED status and last-validation dependency digest |
| `ScheduleEntry` | One weekly meeting: schedule, teaching assignment, room, weekday, start/end, lecture/laboratory type and notes |

Faculty and offering derive from the teaching assignment; section derives from the offering requirement. Entries do not duplicate teaching load or academic-term columns. Section/offering/schedule term and department must agree. Existing SubjectOffering and FacultySubjectAssignment tables were not replaced. A required offering configuration is used rather than adding student enrollment or legacy curriculum blocks.

Applied additive migration: **`timetabling/0001_initial`**. Foreign keys protect historical references; database checks enforce valid weekday, ordered times, meeting type and status. Section identifiers are unique case-insensitively per department/term, and equivalent room closures cannot be duplicated. No database reset, User replacement, migration squash or primary-key recreation occurred.

### Manual workflow

1. Use **Class sections** to create a group in the correct term/department. Expected size is optional; do not invent it.
2. In **Offering requirements**, connect each term offering to its class section and configure any room requirement. Offering-level room-type configuration overrides the retained catalog requirement; absent an override, a catalog required type is mandatory. Capacity defaults to warning-only and can explicitly be made a hard rule for that offering.
3. Record room closures under **Room unavailability** when needed. Faculty availability continues to use the Phase 3 pages.
4. Create a schedule for a department and AcademicTerm. Its organizational/term identity remains fixed; its name is editable. Schedule/section/requirement deletion is not exposed; meetings and room closures have explicit removal confirmations.
5. **Add meeting** selects an existing teaching assignment, room, weekday, start/end and lecture/laboratory type. **Check conflicts** shows subject/faculty/section, permitted workload and faculty-availability context, blocking errors and warnings. **Save meeting** runs the engine again against current data. A forged or stale preview does not bypass validation.
6. Use **Weekly timetable** for Monday–Sunday meeting cards in time order. Desktop has horizontally scrollable day columns; mobile stacks the days. Faculty, room, section, day, department, term and subject search filters stay within organizational scope. The meeting list is another view of the same data.
7. **Validate schedule** scans the whole workspace and reports entry count, error/warning totals, categories and affected entries. No errors permits VALIDATED; an empty schedule cannot validate. Edits/removals return the workspace to DRAFT. VALIDATED does not mean approved, complete or published.

### Conflict rules

The engine returns structured `Conflict` values containing code, severity, explanation, remedy and affected entry IDs when disclosure is permitted. `detect_entry_conflicts(entry, user=None, peers=None)` accepts an unsaved candidate independently of a form. `get_schedule_conflicts(schedule, user=None)` is the full recalculation engine. Authorized callers use the scoped service boundary; these internal functions are not public HTTP endpoints.

| Conflict | Behavior |
| --- | --- |
| `FACULTY_OVERLAP` | ERROR: same faculty in overlapping meetings |
| `ROOM_OVERLAP` | ERROR: same physical room in overlapping meetings |
| `SECTION_OVERLAP` | ERROR: same configured class group in overlapping meetings |
| `FACULTY_UNAVAILABLE` | ERROR: meeting overlaps that term's hard faculty unavailability |
| `ROOM_UNAVAILABLE` | ERROR: meeting overlaps a room closure |
| `ROOM_TYPE_MISMATCH` | ERROR for mandatory type, WARNING for optional preference |
| `ROOM_CAPACITY_WARNING` | WARNING when known expected size exceeds known capacity; ERROR only with explicit hard-capacity configuration |
| `INVALID_TIME_RANGE` | ERROR: invalid weekday, reversed/equal times or no occurrence in the term |
| `INACTIVE_RESOURCE` | ERROR: inactive faculty, offering, subject, section, room or relevant parent/reference/calendar |
| `TERM_MISMATCH`, `ORGANIZATION_MISMATCH` | ERROR: inconsistent term or department relationships |
| `SECTION_REQUIRED`, `INVALID_MEETING_TYPE` | ERROR: missing class identity or unsupported meeting type |
| `MEETING_HOURS_WARNING` | WARNING: recorded weekly lecture/lab hours differ from offering hours × teaching share |
| `EMPTY_SCHEDULE`, `PROTECTED_ENTRY`, `CONCURRENT_CHANGE` | ERROR during full validation: no meetings, inaccessible/inconsistent entry, or dependencies changed during the scan |

Overlap uses `a.start < b.end and b.start < a.end`; adjacent meetings are valid. Cross-workspace resource conflicts are checked, including overlapping term calendars only when an actual common weekday occurs within both date ranges. All draft workspaces participate: they are not independent alternatives. Class identity is term-specific, so different term sections are not guessed to contain the same students.

Faculty AVAILABLE/PREFERRED remain descriptive; sparse records do not make all unlisted times unavailable. PREFERRED never blocks. Only UNAVAILABLE blocks. Room closures are physical restrictions and apply across overlapping term calendars. No implicit laboratory requirement or student count is fabricated. Workload still comes only from FacultySubjectAssignment/teaching share, regardless of the number of timetable meetings.

### Services, security and transactions

- `timetabling/intervals.py`: shared overlap, duration and calendar-weekday intersection helpers.
- `conflicts.py`: deterministic candidate/full-schedule rules, structured results and summaries.
- `queries.py`: permission and ownership-scoped schedule/entry/reference lookup.
- `mutations.py`: transactional create/edit/remove, preview, validation, auditing and validation-status freshness.
- `timetable.py`: scoped filtering and chronological weekly presentation.
- `datasets.py`: `scheduling_input(user, schedule_id)` supplies authorized assignment/section/hour/room requirements, faculty availability/workload, room capacities/closures, dates and existing manual entries. No solver runs, no time grid is invented, and institutional allowable hours remain explicitly unconfigured.

Deans retain college scope, chairs department scope, administrators institution-wide scope, and staff need explicit capabilities. Calendar access is also required; schedule detail requires entry-view permission. Related room/faculty/section/offering choices, posted IDs, direct URLs and filters are scoped server-side. Room choices preserve Phase 2 ownership rules; this phase does not introduce cross-scope shared-room eligibility. Protected peer meetings produce a generic resource-conflict explanation without another unit's titles or IDs. Inconsistent imported entries are reported for administrator review rather than leaking their details.

A PostgreSQL transaction advisory lock serializes Phase 4 mutations and validation across terms. Final saves recheck conflicts inside their transaction; concurrency tests prove competing room/faculty requests cannot both save. Successful changes, removals, full validation and status changes are audited atomically with actor, term and controlled before/after values. Failed saves produce no false success event; notes content is omitted. Scheduled teaching assignments cannot be removed until their meetings are removed, and the Phase 3 page explains that dependency.

Stored conflicts are unnecessary: pages always recalculate. The stored validation digest is compared to current dependencies; `effective_status(schedule)` is the authoritative status for callers. A changed dependency yields DRAFT even if the persisted last-check status still says validated. Validation compares snapshots before and after the scan and retains the original digest, preventing a concurrent resource edit from being stamped as already checked.

### Routes and development examples

| Route | Purpose |
| --- | --- |
| `/timetables/` | Scoped schedule directory |
| `/timetables/schedules/add/` | Create departmental draft |
| `/timetables/schedules/<id>/` and `edit/` | Meeting list and schedule edit |
| Schedule + `timetable/`, `conflicts/`, `validate/` | Weekly view, conflict report and POST-only full validation |
| Schedule + `entries/add/`, `entries/<id>/edit/`, `entries/<id>/delete/` | Preview/save/edit/remove meeting |
| `/timetables/sections/`, `requirements/`, `closures/` | Supporting configuration lists, each with `add/` and `<id>/edit/` |
| `/timetables/closures/<id>/delete/` | Confirm/remove room closure |

`seed_foundation --create-users --with-timetables` includes teaching/resource data and calls DEBUG-only `seed_timetables`. It adds two class groups, two offering requirements, two draft schedules and four valid meetings across the existing example departments. Existing accounts/passwords and edited records are preserved. Repeated unchanged runs are idempotent; edited data that would cause a new seed conflict aborts the transaction. To test rejection, preview Monday 10:00–11:00 using the seeded faculty or room, whose lecture is already Monday 09:00–11:00. No invalid meetings are seeded. Existing --with-resources and --with-teaching semantics remain unchanged.

Verified on **2026-09-15**: **165 tests pass**, including all 134 existing tests and 31 Phase 4 tests. Coverage includes intervals, model/database invariants, scoped CRUD and forged IDs, warnings/errors, term isolation and overlapping calendars, workload invariance, audit rollback, stale validation, cross-scope redaction, seed idempotence and two real PostgreSQL concurrent booking tests. Migrations apply; system check, migration drift and whitespace checks pass. Browser verification covers conflict rejection, valid edit preview/save, full validation and DRAFT reset, room/day filtering and desktop/mobile weekly layout. Run test commands sequentially because they share a temporary test database.

### Phase 4 limits

Meetings recur every matching weekday within the term; holidays, exceptional dates, travel buffers, per-meeting locked constraints and institutional opening-hour policies are not modeled yet. Teaching shares are represented by separate faculty meetings, not a simultaneous co-teaching group. Class groups have no enrollment. Weekly hours are completeness warnings; VALIDATED is not a completeness/approval guarantee. Multiple workspaces reserve the same resources and cannot serve as mutually exclusive alternatives.

The advisory lock favors correctness over throughput; dependency hashing conservatively includes shared scheduling data across the institution, so unrelated edits may require revalidation. Both hashing and conflict queries should be profiled and narrowed before large deployment. Resource edits outside Phase 4 do not take its lock and may invalidate meetings later; reports must consume `effective_status` and recalculate, not trust the raw status column. Direct shell/ORM writes bypass application conflict/audit services and are not a supported scheduling interface.

**Stop point: Phase 4 complete.** Phase 5 is OR-Tools automated generation; Phase 6 is optimization-based workload balancing/recommendations; Phase 7 is review, approval and schedule versioning. **Phase 5 has not started; no OR-Tools or automated timetable generation was implemented.**
#   f a c u l t y _ s c h e d  
 #   f a c u l t y _ s c h e d  
 