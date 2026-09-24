# CampusLoad

Faculty Workload and Academic Scheduling System for Negros Oriental State University - Bais Campus (NORSU-BSC).

**Phases 1–7 are implemented.** The application provides authentication, scoped RBAC, faculty/subject/room management, term availability, subject offerings, faculty teaching assignments, workload monitoring, configurable capacity enforcement, manual weekly timetables, deterministic conflict validation, bounded automated timetable generation, reviewed workload balancing recommendations, human schedule approval, active official selection, dated resource bookings and transactional auditing in the existing Bootstrap 5 shell. Retained legacy scheduling routes are not exposed.

See [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md#16-phase-6-implementation-record) for the completed phases, architecture, migration decisions and later roadmap.

## Implemented foundation

- Existing Django User, secure password hashing, login, CSRF-protected POST logout and inactive/disabled account denial.
- System Admin, College Dean, Department Chair and Authorized Staff; organizational scope enforced independently of model permissions.
- College, Department, AcademicYear, configurable Semester, AcademicTerm and initial SystemSetting models.
- Protected hierarchy, date/uniqueness/scope constraints and PostgreSQL validation triggers.
- Permission-protected dashboard, scoped read-only college/department pages, academic calendar and locally bundled Bootstrap 5.3.8.
- System-admin-only Django admin, including user/group/profile management.
- Append-only audit records for authentication, admin mutations and development seeding; database protection against audit editing/deletion.
- Additive migrations, idempotent development seeds and 376 passing PostgreSQL tests, including all 332 Phase 1–5 tests.
- Faculty, subject and room list/detail/create/edit/status pages, scoped search/filters/pagination and dashboard counts.
- Configurable employment categories, academic ranks, buildings, room types and academic-term teaching-capacity policies/overrides.

Term-specific teaching assignments, manual timetable creation/conflict checks, and automated timetable proposals are available. No paid AI API is needed.

## Run this prepared workspace

The review found no running PostgreSQL service. An isolated loopback-only development database was prepared under Git-ignored `.local/`, on port **55432**, with generated credentials. `.env` points to this database; its previous contents are backed up in `.local/original.env`.

From the repository root in PowerShell:

```powershell
.\venv\Scripts\python.exe scripts/dev_database.py start
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py seed_foundation --create-users --with-balancing
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

Python 3.14.6 / Django 5.2.16 were used for this implementation. Requirements include Django, psycopg2-binary, python-decouple, `ortools==9.15.6755` and retained legacy dependencies. PostgreSQL is required; SQLite does not support the current migrations/exclusions.

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
| `SCHEDULER_PREPROCESSING_TIME_LIMIT_SECONDS` | Positive preprocessing time cap; default 10 seconds |
| `SCHEDULER_MAX_CANDIDATES` | Positive candidate-count cap; default 100,000 |
| `SCHEDULER_MAX_SLOT_LITERALS` | Positive slot-literal cap; default 2,000,000 |

Secure session/CSRF cookies default on outside DEBUG. There is no SQLite switch or DATABASE_URL parser. Secrets live in environment variables or Git-ignored `.env`, not SystemSetting. Production proxy/TLS configuration remains release work.

### 4. Migrate, seed and start

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_foundation --create-users --with-timetables
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

`seed_foundation` requires DEBUG=True. Without an optional data flag, its Phase 1 behavior is unchanged: fictional organizations/calendar and optional accounts only. `--with-resources` also calls `seed_resources`, adding two faculty members, two subjects, two rooms, configurable reference data, and example department policies/faculty term overrides. `manage.py seed_resources` also works independently and ensures foundation data exists. `--with-teaching` also calls `seed_teaching`, which includes resource seeding and adds two offerings, two teaching assignments and six availability records. `manage.py seed_teaching` works independently. `--with-timetables` additionally creates the manual draft examples and the Phase 5 generation examples described below. None creates enrollment data. Repeated runs preserve edited records, account scopes and passwords. Omit `--create-users` for data only. Example load values are fictional and must be replaced with approved policy before institutional use.

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
.\venv\Scripts\python.exe -m pip check
git diff --check
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

**Historical Phase 4 stop point:** At that time, Phase 5 generation had not started. The Phase 5 implementation is documented below; Phase 6 balancing/recommendations and Phase 7 approval/versioning remain deferred.

## Phase 5 automated timetable generation

### Authority, dependency and bounds

The `timetabling` models, scoped input builder, conflict engine and mutation services are the source of truth. `scheduling/autoscheduler.py` still targets legacy `scheduling.Term`, `Block`, `TimeSlot` and `Assignment`. It is unmounted, unchanged and never imported by Phase 5; it is neither a fallback nor a second scheduler.

Generation uses `ortools==9.15.6755` locally and does not call a paid AI API. `SchedulingConfiguration` belongs to one department and academic term. It stores allowed weekdays (1–7), earliest/latest whole-minute times, a slot increment, a solver time limit (1–300 seconds), a random seed, worker count (1–64), and nonnegative integer weights for faculty preference, faculty gaps, section gaps, same-day meeting distribution and room fit. Missing configuration is a readiness error. Environment caps on preprocessing time, candidates and slot literals stop oversized requests as `INPUT_INVALID` before CP-SAT.

Each `AssignmentMeetingRequirement` divides an assigned offering's lecture or laboratory component into a positive meeting count and duration. Its total minutes must equal the component's weekly hours multiplied by the teaching share. Readiness also checks scope, active term/resources, section and room eligibility, grid alignment, existing occupancy and whether each demand has at least one candidate. A valid configuration does not guarantee a feasible complete timetable.

### Strategies and constraints

`FILL_GAPS` retains all existing meetings in the selected schedule. `REPLACE_UNLOCKED` retains locked meetings and proposes replacements for unlocked ones; those rows are removed only during an accepted proposal, and the user needs `delete_scheduleentry` permission. Solving and previewing leave all entries untouched.

The hard model places each required occurrence exactly once at its specified duration. It prevents faculty, room and class-section overlaps with selected retained rows, eligible protected-peer occupancy, unavailability and room closures. It enforces scoped active resources, matching term and department, mandatory room type, hard capacity where configured, allowed weekdays and the configured operating window/grid. Intervals are half-open, and a meeting ending exactly at the latest configured time is valid. Faculty AVAILABLE intervals are informational and PREFERRED intervals affect only the soft score. Zero weight disables a soft component; the result still reports every component separately. A high score is a ranking within the configured model, not institutional approval.

### Proposal lifecycle and security

A request creates a `PENDING` run, captures source data under an advisory lock, moves to `RUNNING`, then solves outside the transaction. Only raw `OPTIMAL` or `FEASIBLE` results can become `PROPOSAL_READY`; raw `INFEASIBLE`, `MODEL_INVALID` and `UNKNOWN` cannot. Lifecycle states also include `ACCEPTED`, `DISCARDED`, `INPUT_INVALID`, `INFEASIBLE`, `STALE`, `VALIDATION_FAILED` and `FAILED`. A blank raw status means the solver did not start.

The generator presents readiness, aggregate input counts, effective configuration, explicit strategy and optional bounded overrides. Run history and detail show status text, runtime, score/penalties, a weekly proposal and conservative diagnostics. “Potential blocking conditions detected” is a diagnostic category, not an exact unsatisfiable core. Protected peer schedule, faculty, subject, section and entry identities never reach the HTML; only approved aggregate counts may appear.

Acceptance and discard are POST-only and CSRF-protected. Acceptance re-resolves all proposal IDs within organizational scope, checks the captured source signature and exact proposal contract, repeats generation and Phase 4 validation under the scheduling lock, and atomically writes unlocked entries with generation provenance and audit. The schedule remains DRAFT. A stale, invalid or audit-failed proposal writes no entries. A terminal run cannot be accepted twice. System administrators have institution-wide scope, deans college scope, chairs department scope, and staff require the complete explicit permission bundle. Foreign direct IDs return 404 after permission checking.

### Routes and development examples

| Route | Purpose |
| --- | --- |
| `/timetables/generator/` | Scoped readiness and synchronous generation request |
| `/timetables/generation-runs/` | Scoped, paginated run history |
| `/timetables/generation-runs/<id>/` | Proposal, score, status and diagnostics |
| Run detail + `accept/` or `discard/` | POST-only terminal action |
| `/timetables/configurations/` and `/timetables/meeting-requirements/` | Scoped configuration and requirement management |

With `DEBUG=True`, run `seed_foundation --create-users --with-timetables` for the two manual schedules plus two fictional configurations, four meeting requirements and one empty `Example generator workspace` in `DEMO-D1`. The separate `seed_timetables --with-infeasible-generator-example` option adds a non-overlapping term and an empty `Example infeasible generator workspace` whose two-hour meeting cannot fit a one-hour daily window; readiness reports `ZERO_CANDIDATES`. These seeds are idempotent and preserve passwords, roles/scopes and edited rows. They never request, accept, discard or delete a generation run.

```powershell
.\venv\Scripts\python.exe scripts\dev_database.py start
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py seed_foundation --create-users --with-timetables
.\venv\Scripts\python.exe manage.py runserver
.\venv\Scripts\python.exe manage.py test --settings=config.test_settings --noinput
```

Final Phase 5 verification on 2026-09-23: **332 tests passed in 419.237 seconds** (165 prior tests and 167 Phase 5 additions); `manage.py check`, `makemigrations --check --dry-run`, `python -m pip check`, and `git diff --check` passed. Both Phase 5 migrations are applied to the prepared PostgreSQL database. The default seed ran twice, the optional zero-candidate seed ran twice, and a real request returned `PROPOSAL_READY`/`OPTIMAL` with two proposed meetings and zero persisted schedule entries. Browser review at 1440px and 390px covered readiness, proposal/detail, the weekly layout and mobile navigation without page overflow. Run the full test suite sequentially because Django uses a shared temporary PostgreSQL test database.

At the Phase 5 stop point, generation did not choose faculty, balance workloads or recommend assignments. Those capabilities are now provided separately by Phase 6 below. Phase 5 still does not model holidays/date exceptions or travel, support simultaneous team teaching, approve/publish versions, run a background queue, or prove exact infeasibility.

## Phase 6 faculty workload balancing

Open **Workloads → Workload Balancing** (`/workloads/balancing/`), choose an active term and an accessible department, then generate a recommendation. The run checks source readiness and records a proposal without modifying teaching assignments. Its detail page compares each faculty member's current and recommended weighted load, configured target/maximum, utilization and status, along with offering-level KEEP/REASSIGN/NEW explanations. A reviewer may accept or discard a ready proposal. Acceptance is a separate POST action; it applies all changed assignment shares atomically after repeating scope, freshness, eligibility and workload validation. A stale or invalid proposal changes no assignments and must be regenerated. Run history remains scoped to the viewer's organizational unit.

The scoped adapter reads active departmental faculty and subject offerings for one term, current `FacultySubjectAssignment` shares, effective `WorkloadPolicy` and `FacultyTermCapacity`, and the authoritative `workloads.calculation` service. It rejects inactive/inconsistent records, missing positive load targets, missing required component weights and partially assigned offerings. Unassigned offerings may receive a NEW full-share assignment. Existing assignments linked to timetable meetings or meeting requirements are fixed and cannot be reassigned by this feature. Recorded `FacultyQualification` links provide positive preference evidence; absent links are treated as unknown because the Phase 3 assignment workflow does not require qualification records. Academic rank, employment category and names never imply expertise. Availability is not used to decide faculty suitability because actual meeting times are chosen by Phase 5.

The pure OR-Tools CP-SAT service chooses exactly one complete, valid share pattern per offering. It enforces active/same-department eligibility through the adapter, complete 100% teaching coverage and effective hard maxima when enforcement is configured. It minimizes target-normalized workload deviation across faculty and penalizes unnecessary changes to good existing assignments. Recorded qualification matches break otherwise equal scores. The solver is bounded to 10 seconds and one worker with a fixed seed. Its deterministic explanations report the outcome of those rules; no LLM chooses or explains assignments. Warning-only maxima remain warnings, and no unconfigured limit is invented. The comparison's imbalance number is the range in displayed faculty utilization percentages (maximum load as denominator, or recommended target if no maximum); the optimizer itself uses target-normalized deviation.

`WorkloadRecommendationRun` stores the scoped source signature, solver outcome, proposal, comparisons and lifecycle. The source signature covers term, department, faculty, offerings, assignments, applicable policies, capacity overrides, qualification evidence and protected timetable dependencies. Phase 6 shares the Phase 5 PostgreSQL advisory lock for writes to those dependencies. Acceptance rechecks the signature and exact proposal shape under that lock, then audits each changed assignment and the accepted run in the same transaction. Existing schedules are never silently changed or regenerated; after acceptance, the authorized user may explicitly run the Phase 5 generator again. A changed assignment invalidates Phase 5's generation source signature.

System admins may use any department; deans are college-scoped; chairs are department-scoped. Staff need explicit recommendation permission grants and remain organizationally scoped. No new editable Django admin path bypasses the review workflow. Phase 6 does not include predictive analytics, automated approval/publication, room/time placement, a full qualification management subsystem or Phase 7 versioning.

On the prepared local PostgreSQL development database, run:

```powershell
.\venv\Scripts\python.exe scripts\dev_database.py start
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py seed_foundation --create-users --with-balancing
.\venv\Scripts\python.exe manage.py runserver
```

For verification, run `manage.py test --settings=config.test_settings --noinput`, then `manage.py check`, `manage.py makemigrations --check --dry-run`, `python -m pip check`, and `git diff --check`. Run PostgreSQL test commands sequentially because they share a test database. `--with-balancing` includes the Phase 5 examples and adds a separate `DEMO-BALANCING` term with two active same-department faculty, two unscheduled offerings, complete current shares, and effective targets/weights. It is DEBUG-only and idempotent; it never generates a recommendation or resets accepted assignments. `manage.py seed_balancing` also runs independently.

Final Phase 6 verification on 2026-09-23: **376 PostgreSQL tests passed in 289.295 seconds** (332 existing and 44 Phase 6 tests). Both additive migrations applied to the prepared development database. Django system and migration-drift checks passed; `pip check` found no broken requirements. A signed-in dean generated and accepted a real `OPTIMAL` recommendation: one of two offerings moved to the second faculty member, the displayed utilization spread improved from 100 to 0 percentage points, and no timetable was generated. The repeat seed preserved the two accepted teaching assignments. Desktop at 1440px and mobile at 390px showed no horizontal page overflow; the mobile sidebar exposed balancing/history, and the temporary viewport override was reset.

## Phase 7 human review and official schedules

Each existing `timetabling.Schedule` is now a numbered version in a `ScheduleFamily`. A version retains its own timetable entries, validation status and revision token. Migration preserves every existing schedule and entry ID and each draft/validated status; it assigns each existing schedule a family and version 1 without inventing submissions, approvals, snapshots, active selections or bookings. A new schedule starts as an editable draft. The states are draft, validated, under review, needs revision and approved. A generated proposal remains a proposal until explicitly accepted into an editable version; generation never submits or approves it.

An editor submits an eligible version through **Review and actions** on its schedule page. The form includes the current revision token and requires explicit acknowledgment of any nonblocking Phase 4 warning codes. Submission repeats the authoritative conflict validation. A scoped reviewer sees the version in **Pending review**, may return it with remarks, or may approve if granted approval permission and different from the submitter. Approval again checks the current token, input signature, Phase 4 findings, warning acknowledgments and active official occupancy in one transaction. Stale or conflicting submissions remain unapproved.

Approval stores an append-only decision history and a frozen JSON snapshot containing approved timetable entries and their faculty, subject, section and room labels. The snapshot supports historical display after source labels change. An explicit `ActiveSchedule` selects the current official version for each academic term and department. Only this selection has dated `OfficialResourceBooking` rows for faculty, rooms and sections. PostgreSQL exclusion constraints reject overlapping bookings on the same date using half-open times, including across departments and overlapping terms. Booking error messages do not expose another department's private schedule details.

An approved version is read-only. **Revise schedule** clones its meetings into a new editable version in the same family; the old approved version remains current official while the revision is prepared. Approval of the replacement atomically writes the new snapshot, history, audit and bookings, changes the active selection and releases the old active booking rows. A failure rolls the whole replacement back, retaining the old official version and reservations. Earlier snapshots and workflow history remain available through **Version history**.

| Route | Purpose |
| --- | --- |
| `/timetables/my-schedules/` | Scoped schedules created or submitted by the current user |
| `/timetables/review/` | Scoped pending review queue for reviewers |
| `/timetables/official/` | Current official schedules in the user's scope |
| `/timetables/schedules/<id>/review/` | Version, findings, snapshot and available actions |
| `/timetables/schedules/<id>/history/` | Family versions and immutable workflow events |

Submit, return, approve and revise URLs accept CSRF-protected POST only. Deans have college scope and approval permission; chairs have department scope and may submit/review but cannot approve by default. System administrators have institution-wide capabilities. Authorized Staff require explicit grants, which never widen their assigned organization scope. The server enforces permissions and lifecycle even for direct URL requests. Schedule changes and workflow decisions are recorded by the transactional audit service.

The optional development seed `seed_foundation --create-users --with-review` includes the timetable examples and submits the fictional manual schedule as `dev.chair`. It is DEBUG-only and idempotent, and it creates no approval, snapshot, official selection or booking. Run `seed_review` independently after `seed_foundation --create-users --with-timetables` if desired. Edited example data that cannot pass current validation causes a seed error rather than a fabricated decision.

```powershell
.\venv\Scripts\python.exe scripts\dev_database.py start
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py seed_foundation --create-users --with-review
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
.\venv\Scripts\python.exe manage.py test --settings=config.test_settings --noinput
```

Phase 7 verification on 2026-09-24: **403 PostgreSQL tests passed sequentially**, including all 376 Phase 6 tests and 27 new regression tests. Migrations `timetabling.0004`–`0006` applied; the optional review seed ran twice without creating duplicate history or an official booking. Desktop (1440px) and mobile (390px) browser checks covered the chair's submitted schedule, the dean's decision form and mobile navigation without horizontal overflow. The current dated booking expansion treats every matching weekday between term dates as instructional because no holiday/date-exception model exists.

Run the full PostgreSQL suite sequentially. `manage.py check`, `manage.py makemigrations --check --dry-run`, `python -m pip check` and `git diff --check` remain the verification commands. Phase 8 dashboard expansion has not started.
