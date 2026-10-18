# CampusLoad frontend design improvement plan

Prepared 1 October 2026. Implementation progress is recorded below.

## Objective

Give CampusLoad a modern, professional institutional interface with clearer hierarchy and subtle depth. Keep administrative screens efficient, readable and familiar. Preserve existing routes, scoped data, permissions, forms, business rules and workflow terminology.

## Current foundation

The application uses Django templates, locally bundled Bootstrap, shared `app.css` and `refinement.css`, and a small `app.js`. It already has a navy sidebar, teal actions, university identity, shared surface/table/form styles, SVG navigation icons, responsive navigation, contextual workspace tabs and status treatments. Extend this foundation; a React/shadcn migration is unnecessary for the requested design work.

Inspected: base layout, navigation, dashboard, resource list/form/fields, timetable workspace, report catalog, shared CSS/JavaScript, both AGENTS.md files, and the existing frontend audit documents.

Code findings to address:

- Many sections use the same white surface, border, radius and heading treatment. Improve emphasis through grouping and surface contrast, rather than applying more shadows everywhere.
- The dashboard combines workflow guidance, action tiles, attention items, large metrics, count chips and many similarly styled sections. Establish a clearer order and reduce repetitive visual framing without removing information.
- Shared selectors such as headings, surfaces and tables are defined in both stylesheets. Make ownership explicit when touching these rules instead of adding another override layer.
- Some newer rules use `--cl-*` token fallbacks while the established tokens use names such as `--line`, `--surface` and `--teal`. Normalize these touched rules to the existing system.
- Schedule status presentation mixes shared status classes with local Bootstrap badge choices. Standardize visual mapping while preserving the actual status values and wording.

These are source-code findings, not a completed visual audit. The local server was listening on port 8000, but a browser connection was unavailable during planning. Earlier audit results are historical evidence, not verification of this proposed redesign. Existing uncommitted changes must be preserved.

## Proposed visual direction

| Area | Direction |
| --- | --- |
| Palette | Retain navy, teal and restrained institutional gold. Use semantic colors for statuses and errors. |
| Depth | Use a slightly stronger neutral canvas, white primary surfaces and tinted secondary sections. Reserve a soft shadow for major workspace surfaces and elevated menus. |
| Typography | Retain the existing font stack. Establish consistent page titles, section headings, body text and metadata. Keep critical data readable. |
| Spacing | Use the existing spacing conventions, formalized around a small scale such as 4/8/12/16/24/32px. Tighten repeated table and dashboard padding where useful. |
| Shape | Start with 6px badges, 8px controls and 10–12px panels. Use pills only where they communicate a distinct purpose. |
| Actions | One clear primary action per context; supporting actions outlined or quiet; destructive actions visibly distinct. |
| Motion | Brief color/border transitions. Avoid lifting every card on hover; respect reduced-motion preferences. |

These are starting values to validate in the pilot, not final specifications. The intended result is visible layering and stronger information hierarchy with restrained decoration.

## Implementation sequence

### 1. Capture the baseline

- Inspect the current rendered dashboard, faculty directory/form, timetable and review page at desktop and phone widths.
- Include populated, empty, filtered and validation-error states where available.
- Record the current primary actions, role-dependent navigation and selected-term behavior.
- Use `FRONTEND_AUDIT.md` and `FRONTEND_REGRESSION_AUDIT.md` as background, checking their historical statements against current code.

Deliverable: baseline screenshots and a short list of concrete visual issues.

### 2. Establish the shared foundation and pilot

- Extend existing CSS tokens for spacing, radius, elevation and surface emphasis.
- Adjust touched shared selectors in their existing stylesheets; do not introduce a third global stylesheet or perform an unrelated CSS rewrite.
- Refine the sidebar active state, topbar proportions, page header alignment and content spacing. Preserve collapse, mobile drawer and account behavior.
- Build the pilot using the shared shell, dashboard and faculty directory. Include a representative form before broader rollout.
- Reuse existing template includes. Extract a further partial only when repeated markup benefits from it.

Primary files: `static/css/app.css`, `static/css/refinement.css`, `templates/base.html`, `templates/includes/topbar.html`, `templates/includes/sidebar_navigation.html`, `templates/core/dashboard.html`, and `templates/resources/list.html`.

Deliverable: a reviewable visual direction demonstrated on real application pages.

### 3. Improve dashboard hierarchy

- Make the selected term and authorized scope easy to locate.
- Give Needs attention and relevant quick actions priority using existing role-aware data.
- Keep operational counts compact and contextual. Preserve distinctions between faculty, assignments, meetings, schedule versions and current official selections.
- Group secondary monitoring into clear sections, reducing repeated cards within cards.
- Use existing status rows/meters where useful. Add no decorative charts, invented metrics or completion percentages.

Deliverable: a dashboard that makes the next action and current state easy to identify.

### 4. Apply the design to directories and forms

- Align titles, filters, result context, tables and pagination.
- Improve table header contrast, predictable action placement, numeric alignment and long-text handling.
- Group related form fields, keep reasonable input widths, and make labels, helper text and errors consistent.
- Distinguish empty data from filtered empty results only where existing context establishes which state applies.
- Preserve submitted values, CSRF, existing validation and filter query parameters.

Primary files: existing resource templates/fields, workload list/faculty templates, and pagination include. Improve existing Django form widgets only if needed for accessible presentation.

### 5. Extend to scheduling, review and reports

- Make schedule name, term, version and official/draft state clear without competing with workspace tabs.
- Improve timetable day headers, time labels and meeting grouping. Preserve important faculty, section, room, source and lock information.
- Standardize existing status visuals and validation severity. Keep blocking errors distinct from warnings.
- Group review context and decision actions clearly while preserving the current server-controlled workflow.
- Apply consistent catalog/filter/table styling to reports. Preserve formal print identity and verify print/export behavior after shared styling changes.

Primary files: existing `templates/timetabling/`, `templates/workloads/` and `templates/reporting/` templates. Keep public landing/login work as a separate final consistency pass if it is needed; prioritize the signed-in workspace.

### 6. Verify and finish each meaningful batch

- Browse at approximately 1366–1440px desktop, 768px tablet and 390px phone widths.
- Test active navigation, collapse/expand, mobile drawer, filters, pagination, form error recovery and the affected primary interaction.
- Check keyboard navigation, visible focus, labels, contrast, status text and reduced motion.
- Check console/runtime errors and page overflow. Keep wide tables scrollable within their wrapper.
- Check role-dependent screens and access presentation using existing development fixtures; do not modify real approvals or assignments for visual QA.
- Run relevant existing tests when template or interaction behavior changes. Use existing regression coverage for scope, CSRF and workflow preservation.
- Before final handoff, run Django checks and whitespace checks; use the existing full PostgreSQL suite for a broad rollout, sequentially as documented in the README.

Deliverable: verified pages, a concise change summary, and any remaining concrete limitations.

## Scope and completion criteria

No database, authentication, authorization, workload calculation, conflict detection, solver, approval, official-state or audit changes are part of this redesign. Keep all existing working-tree changes intact and review the implementation diff for accidental backend changes.

Complete when the pilot style is applied consistently to the affected pages, primary actions are obvious, tables remain useful, forms recover from errors, mobile layouts work, relevant tests pass and rendered pages have been checked. Do not describe browser verification as complete if the browser connection remains unavailable.

## AGENTS.md guidance

Keep the existing root `AGENTS.md` and `templates/AGENTS.md`. They already cover architecture, visual restraint, accessibility, backend boundaries, testing and browser verification. Another instructions file is unnecessary.

This document holds the task-specific sequence and proposed visual decisions. Once the pilot establishes final tokens and component conventions, document those near the shared styles or in a short design-system reference. Update AGENTS.md only for durable rules that should govern future work; do not duplicate this implementation plan there.

## Implementation progress — 1 October 2026

- Inspected both AGENTS.md files, the current templates/styles/scripts, historical audits and existing uncommitted changes. Saved a working-tree baseline outside the repository. Baseline automated coverage: **51 tests passed**.
- Browser baseline attempted before edits: inventory contained no browsers; opening both IAB and Chrome returned “Browser is not available.” A later connection recheck also returned an empty inventory. Screenshots and visual QA remain pending; historical screenshots are not verification of this implementation.
- Shared foundation and pilot implemented in the existing stylesheets: canvas/primary/secondary surfaces, restrained elevation, 12/8/6px radii, compact typography/spacing, action hierarchy, focus and semantic colors. `app.css` owns shared tokens and controls; `refinement.css` owns institutional/workspace compositions. Dashboard actions/attention precede workflow guidance; directory filters, result states, shared pagination and grouped faculty fields are updated. **55 pilot tests passed**, including new accessibility/recovery/pagination regressions.
- Rollout implemented across workload lists/forms, timetable/workspace, review/history and reports: shared schedule badges, secondary context panels, numeric alignment, clearer report source labels, consistent pagination and keyboard-focusable scroll regions. Existing URLs, status values/wording, submitted fields, CSRF and scoped context are preserved. Pre-existing Python and JavaScript changes were checked byte-for-byte against the saved baseline.
- Final PostgreSQL suite: **498 tests passed in 363.639 seconds**. Django system checks, migration-drift checks, dependency checks, all **51 template** compilations and Git whitespace checks passed. Shared JavaScript syntax is valid; CSS variables resolve and rule delimiters balance. Core text/action/status colors meet **4.5:1** contrast; control boundaries were strengthened. No new runtime dependencies or backend changes.
- Restored the README's prepared sibling PostgreSQL setup and started the application at `http://127.0.0.1:8000/`. Existing tables/accounts were reused without migrations or seeding. Landing/login and shared CSS/JavaScript returned HTTP 200. This verifies HTTP delivery, not visual layout.
- **Visual QA remains incomplete:** the final IAB attempt still returned “Browser is not available.” Pending checks are rendered dashboard/directory/form/workload/timetable/review/report layouts at approximately **1366–1440, 768 and 390px**, drawer/collapse/account controls, filters/pagination/form recovery in the browser, keyboard focus/scrolling, page overflow, reduced motion, print layout and browser console errors. Automated tests cover the underlying interactions and report exports; no screenshots or browser success is claimed.
- Sidebar follow-up: the supplied screenshot showed the remembered compact view. Added visible Expand/Collapse text and removed redundant group-header icons from desktop compact mode, retaining destination links, saved preference and expanded/mobile navigation. Toggle checks cover default/saved state, unavailable storage, ARIA and group restoration; **8 workspace UX tests passed**. Browser inventory remains empty.
