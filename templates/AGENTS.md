# Frontend and UI/UX Instructions

These instructions apply specifically to templates and user-facing interface work.

They supplement the repository-level `AGENTS.md`.

When these instructions conflict with general visual preferences, prioritize usability and consistency with the existing application.

---

# Before Editing UI

Before redesigning a screen:

1. Inspect the current template.
2. Inspect the base layout.
3. Inspect shared partials/components.
4. Inspect the styles currently used by neighboring screens.
5. Identify whether the problem is structural, visual, or interaction-related.
6. Preserve working forms, URLs, permissions, and backend integrations.

Do not rebuild a page from scratch merely because its appearance can be improved.

---

# Overall Visual Direction

Target:

**Modern professional institutional administration interface.**

The application should feel:

- organized
- trustworthy
- calm
- efficient
- consistent
- information-focused

It should not look like:

- a marketing landing page
- a gaming interface
- a crypto dashboard
- an overly decorative SaaS template
- an AI-generated collection of cards

---

# Visual Hierarchy

Each page should normally have a clear hierarchy:

1. page title
2. short contextual information when needed
3. primary action
4. filters or controls
5. primary content
6. secondary actions/details

Do not give everything equal visual weight.

There should usually be only one obvious primary action per context.

---

# Layout

Prefer clear page structure over excessive containers.

Use:

- consistent maximum widths
- predictable page padding
- consistent section spacing
- aligned controls
- logical grouping

Avoid wrapping every section in a card.

Use cards only when they communicate meaningful grouping.

---

# Spacing

Use a consistent spacing scale.

Do not randomly use slightly different gaps across pages.

Nearby related elements should have less spacing than separate conceptual sections.

Avoid excessive whitespace in data-heavy administrative screens.

---

# Typography

Typography should prioritize legibility.

Use a limited hierarchy for:

- page title
- section title
- subsection title
- body
- secondary text
- labels
- metadata

Do not create new font sizes for every page.

Avoid excessive:

- bold text
- uppercase text
- letter spacing
- tiny muted text

Use muted text only for genuinely secondary information.

---

# Buttons

Maintain clear button hierarchy.

Typical hierarchy:

- Primary — main action
- Secondary — normal supporting action
- Ghost/text — low-priority action
- Destructive — dangerous action

Buttons performing the same type of action should look the same throughout the system.

Do not make every action a filled primary button.

Avoid ambiguous icon-only controls unless the icon is universally understood and accessible.

---

# Forms

Forms should have:

- persistent visible labels
- logical grouping
- consistent field heights
- clear required-state indication
- clear validation
- useful helper text only when needed
- reasonable input widths

Avoid making every input stretch across the entire screen when its expected value is short.

Examples:

- year → narrow
- date → narrow/medium
- email → medium
- description → wide

Use multi-column layouts only when they improve scanning.

On smaller screens, collapse forms into a readable single-column layout.

---

# Tables

Tables are important administrative interfaces.

Prioritize:

- scannability
- density
- alignment
- predictable actions
- understandable statuses

Avoid turning desktop tables into oversized cards simply for visual novelty.

For large tables:

- allow horizontal scrolling when necessary
- consider sticky headers when useful
- keep action columns compact
- truncate exceptionally long values carefully
- provide full values through suitable detail views or tooltips when necessary

Numeric values should be aligned consistently.

Actions should appear in a predictable location.

---

# Filters and Search

Search and filtering controls should clearly relate to the content they affect.

Prefer a structure such as:

Search | Filter | Filter | Filter | Reset

Do not create a huge form-like filter panel for only one or two filters.

Clearly communicate when filters are active.

If filtering returns nothing, distinguish:

"No records exist"

from:

"No records match the current filters."

---

# Status Badges

Use a small consistent set of status appearances.

Examples may include:

- Draft
- Pending
- Validated
- Approved
- Active
- Inactive
- Conflict
- Completed

Do not create a new arbitrary color for every label.

Never use color as the only status indicator.

---

# Dashboards

A dashboard should answer useful questions quickly.

Do not add metrics simply because there is available space.

Each dashboard card or visualization should have a practical reason to exist.

Prioritize:

- actionable information
- exceptions requiring attention
- recent relevant activity
- meaningful counts
- workload/schedule status when relevant

Avoid huge decorative numbers without context.

---

# Modals

Use modals for focused short interactions.

Do not place large complicated workflows inside a modal when a dedicated page would be clearer.

Modal requirements:

- clear title
- concise content
- obvious primary action
- obvious cancel/close option
- keyboard-accessible close behavior
- sensible focus handling

---

# Empty States

Empty states should explain what the user is seeing.

Good empty-state structure:

- short statement
- brief explanation if necessary
- next action when one exists

Do not use oversized illustrations unless they genuinely improve comprehension.

---

# Error States

Errors must help the user recover.

Prefer:

"Unable to save the schedule. Check the highlighted fields and try again."

over:

"Error."

Do not expose raw server errors.

---

# Success States

Use concise success feedback.

Do not interrupt normal workflows with unnecessary modal confirmations.

---

# Navigation

The active destination must be visually clear.

Navigation labels must remain consistent across the system.

Avoid changing a known term simply to make it shorter.

If a sidebar is used:

- group related destinations
- do not overload it
- keep the active section obvious
- keep icon style consistent
- keep labels visible unless the compact state is intentional

---

# Responsive Behavior

Test meaningful layout breakpoints rather than attempting to customize every possible screen width.

At minimum consider:

- standard desktop/laptop
- narrower laptop/tablet
- phone when the feature is expected to be used there

For mobile:

- stack controls where necessary
- maintain usable touch targets
- allow complex tables to scroll
- avoid tiny text
- keep primary actions accessible

---

# Accessibility

Every UI change must preserve or improve accessibility.

Check:

- label/input relationships
- button names
- keyboard navigation
- focus visibility
- contrast
- heading order
- dialog semantics
- form errors
- icon labels

Do not remove outlines unless replacing them with an equally visible focus state.

---

# Color Usage

Use the project's established palette.

Use strong colors primarily for:

- primary actions
- states
- alerts
- emphasis

Do not decorate normal containers with many competing accent colors.

Use semantic colors consistently.

For example, do not use the same red for both a harmless category and a critical destructive state.

---

# Borders and Shadows

Use subtle borders to establish structure.

Use shadows sparingly.

Administrative interfaces usually benefit more from:

- spacing
- alignment
- subtle borders
- typography

than from large floating shadows.

---

# Radius

Use a consistent border-radius system.

Do not combine:

- very square tables
- pill-shaped forms
- heavily rounded cards
- unrelated radius values

unless there is a deliberate reason.

---

# Icons

Use a consistent icon family.

Icons should generally use consistent:

- stroke weight
- size
- alignment

Do not mix emoji into the primary interface unless the existing product intentionally uses them.

---

# Loading

For asynchronous operations, provide appropriate feedback.

Use:

- button loading states
- skeletons where content structure is predictable
- small progress indicators

Avoid full-page loaders for small localized actions.

---

# Interaction

Interactive states should include appropriate:

- hover
- focus
- active
- disabled
- loading

behavior.

Do not make elements appear clickable when they are not interactive.

---

# Browser Verification Procedure

After implementing a meaningful group of UI changes:

1. Run the application.
2. Navigate to the affected screen.
3. Check the default state.
4. Test the main interaction.
5. Check at least one error or empty state when practical.
6. Inspect a narrower viewport if layout changed.
7. Check for console errors.
8. Correct obvious visual regressions.

Do not repeatedly screenshot after every minor spacing adjustment.

---

# UI Review Checklist

Before finishing frontend work, check:

- Is the primary action obvious?
- Is the page title clear?
- Is spacing consistent?
- Are forms easy to scan?
- Are validation errors understandable?
- Are tables readable?
- Are filters obvious?
- Are statuses consistent?
- Are destructive actions distinguishable?
- Are empty states useful?
- Is navigation predictable?
- Is the interface keyboard accessible?
- Does the page work at a smaller viewport?
- Does the screen visually match neighboring screens?
- Did the change accidentally alter backend behavior?

If the answer to an important item is no, correct it before finishing.
