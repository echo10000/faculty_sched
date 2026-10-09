# CampusLoad — approved Academic Workbench

Implements the user-approved Concept B prototype from parent commit
`41b2a734c80a5a59b8da72494f3b7b0b5e65b73e` on the separate feature branch
`codex/campusload-academic-workbench`.

The dashboard uses editorial major headings, a warm neutral canvas, an open
working-version ledger and a prominent navy scheduling desk. Operational metrics,
tables, forms and navigation remain in the readable system sans-serif stack.
Compact real version-state counts and actionable validation findings extend the
approved composition without adding repetitive cards. Historical published
versions and current official selections remain distinct.

The seven stages guide navigation and never certify completion or readiness.
The scheduling desk comes first in source order and in the mobile layout; explicit
desktop placement retains the approved ledger-left/desk-right composition.
Term selection uses the existing GET flow. Secondary official schedules, history,
workload, room use, generation, recommendations and campus details remain in an
expandable section. Existing informational zero states are retained there.

The six navigation destinations, three roles, scoped backend reads, routes and
business logic are unchanged. No dependencies, database models, migrations,
solver rules, workload calculations or publication behavior were changed.

Verification: 38 focused tests passed in 22.987 seconds. The full PostgreSQL suite
passed all 670 tests in 505.815 seconds (534.455 seconds including startup and
teardown), exit code 0. Django checks, migration drift, JavaScript syntax,
dependency health and whitespace checks passed. Independent review is clean.

Real-browser captures at 1440×1000, 768×1000 and 390×844 were compared with the
approved prototype using the same existing Administrator preview records.
Additional checks covered scoped Staff data, Faculty read-only pages, shared
forms/tables, term changes, stage navigation, compact navigation, mobile drawer,
account controls and visible keyboard focus. No document horizontal overflow,
duplicate IDs or warning/error console messages were observed on checked pages.

Local evidence is in `.local/workbench-ui/`: `comparison.md`, three `admin-*.jpg`
captures, supplementary page captures, `browser-checks.json`, focused and full
PostgreSQL logs. The preview uses its existing separate disposable database;
the development database was not reset or overwritten.
