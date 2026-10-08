# Capstone demonstration path

Use a disposable `DEBUG=True` PostgreSQL database. Run `migrate`, then `seed_foundation --create-users --with-review --with-balancing`. The seed is idempotent, creates fictional teaching, timetable, generator and balancing examples, and validates one manual schedule for staff review. It creates no publication, official booking or generated/accepted recommendation. Credentials are written only to the ignored `.local/development-credentials.txt` when accounts are first created.

The sample accounts use the application's three existing roles:

| Login | Role and access |
| --- | --- |
| `dev.admin` | Admin, institution-wide administration |
| `dev.staff` | Authorized Staff, all departments in Example College A (`DEMO-A`) |
| `dev.faculty` | Faculty, Alex Santos (`DEMO-F1`), own teaching records |
| `dev.faculty2` | Faculty, Jamie Reyes (`DEMO-F2`), own teaching records |
| `dev.faculty3` | Faculty, Morgan Example (`DEMO-F1B`), own teaching records; available with the balancing example |

For an existing demo database, run `configure_demo_accounts --college DEMO-A`. This assigns `dev.staff` to the college, disables the duplicate `dev.chair` and `dev.dean` accounts, and creates missing individual Faculty logins. Historical authors and reviewer references remain intact. Existing passwords and custom permission grants are preserved. Repeating the seed does not re-enable these disabled accounts. There is no separate Dean or Chair role.

1. Sign in as `dev.staff`. Open **Overview** and select the fictional term; show college-scoped faculty, workload status, offerings and assignments.
2. Open the manual timetable and use its validation page to show the seeded valid meetings and a conflict preview. Review the separate generator workspace, request a bounded Phase 5 proposal, inspect its objective/status and accept only after reviewing the proposal.
3. Open the Phase 6 balancing example. Request a recommendation, compare before/after loads and accept or discard using the real review action.
4. Open **My schedules** and the validated fictional schedule. Show its revision token, findings and workflow history. As Authorized Staff, review the validation results and finalize only after the required warning acknowledgments. This creates a real frozen snapshot, dated bookings and active official selection.
5. Return to **Official schedules** and **Overview** to show the selected version. Create a revision of the approved version; show that the older version remains official until a replacement approval commits.
6. Open **Reports** as Authorized Staff. Compare current official and historical snapshot labels; print and export a scoped report. Use a second college or department choice as Admin to demonstrate organization scoping.
7. Sign in with a Faculty account. Show their own published schedule and workload. Faculty without a selected published schedule see an empty state. Creating or editing a replacement draft does not change the Faculty account's frozen official records.

For real Faculty accounts, use the existing account administration: create a user, link the Faculty record under **Teaching identity**, then assign an enabled **Faculty** profile. Each Faculty record has its own linked user. Use college-scoped **Authorized Staff** profiles for the people responsible for scheduling that college.

The actual action order depends on the seeded version's current state; repeat seeds preserve decisions and do not reset passwords or approvals. Use a fresh disposable database for a repeatable live presentation. Never alter production data for the demo. The matching-weekday booking model has no holiday or date exceptions.

## CAS section-generation demo

In the local DEBUG database, run `manage.py seed_section_demo --actor dev.admin`. This adds **College of Arts and Sciences**, **BSIT Department** and **BSCS Department**, and the school-wide **2026-2027 — First semester (FIRST)** term. Each department has first-year sections A/B and second-year section A, with four fictional subjects per section, example faculty assignments, shared college rooms, meeting requirements and generation settings. Subject selections and teaching values are examples, not an official curriculum or institutional policy. Existing records and accounts are preserved; repeating the command does not reset accepted meetings or published decisions. Older installations using CAS Demo/CAS-DEMO labels are reused rather than duplicated.

Open **Academic scheduling → Generate by section**. Select **2026-2027 — First semester (FIRST)**, a department and year level, then click **Load sections / show subjects**. Choose the section and show subjects again. An existing editable department draft is selected automatically; change the draft selection if needed. Subjects appear as **Not scheduled** until meetings are accepted. Click **Generate schedule for [section]**, review the proposal and click **Accept proposal**. **Return to section subjects** shows the days, times and rooms.

Generate a second section in the same draft to demonstrate that earlier meetings are retained and shared faculty/rooms cannot overlap. **Fill gaps** retains all meetings. **Replace unlocked meetings** replaces only the selected section's unlocked meetings when accepted; other sections and locked meetings remain. Department-wide generation remains available through **Generate schedule**.

Section generation does not publish. This demo retains the existing department-wide publication contract: prepare all active classes in the department before finalizing its draft. Individual section publication is not introduced.

## Expanded college section demo

Staff use two main Faculty & workload pages: **Faculty** for teaching summaries and weekly available times, and **Teaching assignments** to choose a program, year (1–4) and section. Subjects appear before assignment. Choosing an instructor previews the resulting term workload without saving; **Assign instructor** rechecks the latest teaching shares and limits and records the change in the audit log. **Continue to timetable** carries the selected section into generation. All-assignment lists, workload exceptions and recommendations remain secondary tools. Administrators alone edit individual faculty workload limits; staff updates leave existing limits intact. Existing assignments retain their immutable instructor identity and any protected meeting records.

Run `manage.py seed_section_demo --actor dev.admin --all-colleges --archive-legacy` in the local DEBUG database. This preserves the existing CAS records and accepted meetings, adds A/B sections for years 1–4 to every listed program, and uses the same **2026-2027 — First semester (FIRST)** academic term for all colleges:

| College | Departments / programs |
| --- | --- |
| CAS — College of Arts and Sciences | BSIT — Information Technology; BSCS — Computer Science |
| CCJE — College of Criminal Justice Education | CRIM — Criminology |
| CIT — College of Industrial Technology | IT — Industrial Technology; COMPTECH — Computer Technology; AUTO — Automotive Technology |
| CAF — College of Agriculture and Forestry | ANSCI — Animal Science; AGRON — Agronomy |
| CBA — College of Business Administration | HM — Hospitality Management; OA — Office Administration; BA — Business Administration |

There are 11 programs, 88 sections and 352 class offerings, with four fictional subjects per section. Each program has a draft, sample faculty assignments, availability, meeting requirements and generation settings. Colleges have lecture rooms and suitable example laboratories/workshops. Newly added classes have no meetings until a proposal is generated and accepted. These are testing examples, not approved degree titles, curricula or policies.

`--archive-legacy` deactivates the recognized Example College A/B records, their old DEMO terms, and related example subjects, faculty, rooms, sections and offerings. They disappear from active generation choices. Existing accounts, audit records, approved schedules, snapshots, bookings and workflow history remain intact; archived records can still appear as inactive in administrative/history pages. No accounts are reassigned or deleted. Repeating the command preserves existing teaching assignments, meetings and published decisions. Do not rerun the older foundation/teaching demo seeds to repopulate those examples.

## One sample staff account per college

### Browsing academic records

Subjects are catalog records; classes offered are their term-specific instances. The subject directory groups records by program and the year levels recorded on sections linked to the selected term's offerings. It does not infer a year from a subject code or create a curriculum. Subjects without a recorded year stay in **Year not assigned**. A subject used in several sections is listed once, with all recorded years in its group label.

Classes offered, class sections, class scheduling requirements and class meeting requirements use matching program/year filters; offered classes and requirements also support section filtering. Staff keep their existing college scope and current-term restriction. Related-page links preserve the selected context. Requirement lists show subject names, sections, room needs or weekly meeting patterns rather than only record identifiers.

The shared **Back** link returns to a valid same-site referring page, keeping its filters; otherwise it opens the dashboard. It performs a GET navigation rather than resubmitting form history. The separate sidebar button says **Hide sidebar / Show sidebar**.

Meeting-pattern automation is not part of this browsing change. Weekly contact hours and academic units remain separate, and existing meeting requirements, workload calculations and scheduling validations are unchanged.

After creating the expanded colleges, run `manage.py create_college_demo_staff --actor dev.admin` in the local DEBUG database:

| Username | Authorized college |
| --- | --- |
| `dev.staff.cas` | CAS — BSIT, BSCS |
| `dev.staff.ccje` | CCJE — Criminology |
| `dev.staff.cit` | CIT — Industrial Technology, Computer Technology, Automotive Technology |
| `dev.staff.caf` | CAF — Animal Science, Agronomy |
| `dev.staff.cba` | CBA — Hospitality Management, Office Administration, Business Administration |

Each account uses the existing **Authorized Staff** role with a college scope and no department scope. Staff can work across all departments in their college; other colleges' records are excluded from their lists and selectors and direct record access is rejected by the server. Staff have no system administration access. With the automatic calendar enabled, staff work in the current term only; shared lookup data retains its existing behavior.

Each new account receives a unique randomly generated password stored only in the ignored `.local/development-credentials.txt`. Passwords are not printed or written to audit records. Repeating the command preserves existing passwords, disabled-account decisions and scopes. An existing username with another role or scope causes the transaction to roll back rather than overwrite that account. The older `dev.staff` login remains associated with its archived example college; use the five college-specific logins above for the current demo.

## Editable sample calendar and staff workflow

After applying migrations, run `manage.py configure_sample_calendar --actor dev.admin` in DEBUG. The sample academic year is June 1–May 31, first semester August 1–December 20, and second semester January 5–May 20. No summer term is included. These are example dates, not an institutional policy.

Admins can open **Academic setup → Academic calendar → Automatic calendar settings** to edit the year start and semester patterns, or add a summer pattern. **Manage existing terms** edits already-created dates. Rule changes apply to newly-created periods; they do not replace existing year/term IDs or dates. Repeating the sample command preserves any existing calendar configuration.

On the first signed-in request each day, the app atomically ensures the current and following academic year and their configured terms exist. It reuses the existing first semester and keeps its subjects, sections, faculty assignments and schedules. It records automatic period creation in the existing audit log. When the app is closed, nothing runs; the next signed-in visit catches up. Creation does not copy classes, promote students, advance section year levels, or carry teaching assignments into another term.

Staff automatically use the in-progress term from this calendar. They cannot select another term through forms, URLs, direct schedule access or recommendation actions. During gaps between semester dates, the app says no term is currently in progress. Admins retain access to historical and upcoming terms. If automatic calendar setup is disabled or absent, the preexisting term selection behavior applies.

Faculty availability now starts with one row per faculty member and a **View times** action. Department/faculty/search controls are prominent; day/type and employment/rank filters are under **More filters**. Teaching shares display as percentages. Workload shows assigned class count, weekly teaching hours, weighted workload and status; the existing policy sources, limits and full calculation remain available in **How workload is calculated** on a faculty detail page. Workload calculations and scheduling constraints are unchanged.
