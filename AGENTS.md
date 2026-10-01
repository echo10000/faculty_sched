# Project Agent Instructions

## Project Goal

This is a production-style web application intended to be reliable, maintainable, usable, and appropriate for an academic/institutional environment.

Treat the existing repository as the source of truth.

Before changing code:

1. Inspect the relevant existing implementation.
2. Understand the current architecture and conventions.
3. Reuse existing components, utilities, models, services, and patterns where reasonable.
4. Avoid introducing a second way of doing something that the project already handles.
5. Do not assume functionality exists without verifying it in the codebase.

---

# Core Principles

## Preserve Working Behavior

Do not change working business logic merely to make implementation easier.

Unless explicitly requested, do not modify:

- database schemas
- migrations
- authentication behavior
- authorization rules
- user roles
- permissions
- scheduling/business rules
- conflict-detection rules
- workload calculations
- approval workflows
- audit behavior
- record immutability rules
- official/draft state behavior
- existing API contracts

Frontend improvements must not silently change backend behavior.

If a UI improvement requires a business-rule change, explain the dependency rather than changing the rule automatically.

---

## Avoid Unnecessary Rewrites

Prefer focused improvements over large rewrites.

Do not:

- replace a working framework without a strong technical reason
- migrate the frontend to another framework only for styling
- introduce large dependencies for simple functionality
- redesign unrelated pages while implementing one feature
- duplicate existing utilities or components
- restructure directories unnecessarily

When several solutions are possible, prefer the one that fits the existing architecture.

---

# Code Quality

Write code that is:

- readable
- maintainable
- explicit
- reusable
- testable
- consistent with the repository

Prefer descriptive names over clever abstractions.

Avoid premature abstraction.

Extract reusable components when the same interface or behavior appears repeatedly.

Do not leave:

- commented-out obsolete code
- temporary debugging statements
- unused imports
- unused CSS
- dead functions
- placeholder TODOs that are no longer necessary

---

# Existing Architecture First

Before creating a new component, helper, service, stylesheet, or utility:

1. Search the repository for an existing equivalent.
2. Reuse or extend it when appropriate.
3. Create something new only when the existing implementation cannot reasonably support the requirement.

Follow the project's existing:

- naming conventions
- directory structure
- template structure
- CSS conventions
- JavaScript conventions
- form patterns
- validation patterns
- testing patterns

---

# Available Skills and Tools

Use available project skills and tools when they materially improve implementation quality.

For frontend and UI/UX work:

- Use the shadcn skill for compatible React/shadcn component work, theming, composition, accessibility, and design-system consistency.
- Use browser verification tools after meaningful frontend changes to inspect the real rendered interface, interactions, responsiveness, and console errors.
- Use React best-practices guidance when modifying multiple React components.
- Use v0 guidance when generating or adapting interface concepts or reusable React UI patterns.
- Treat provided Figma designs as the visual source of truth when a Figma design is supplied or connected.
- Use Canva primarily for branding, graphics, visual assets, and design references rather than application implementation.

Do not use a tool simply because it is available.

Prefer the simplest workflow that can reliably complete the task.

Do not repeatedly invoke browser or design-generation tools for trivial visual adjustments.

When using generated UI as inspiration, adapt it to the existing project architecture and design system rather than copying it blindly.

External design tools must not override established business logic, permissions, data models, workflows, or institutional requirements.

---

# Django Guidelines

When this repository uses Django:

- Keep views reasonably small.
- Place substantial business logic outside templates.
- Avoid complex logic inside templates.
- Use Django forms/formsets where the existing project already uses them.
- Preserve CSRF protection.
- Preserve authorization checks on the server.
- Never rely on hidden buttons or frontend visibility as authorization.
- Use named URLs instead of hard-coded internal paths.
- Avoid N+1 database queries.
- Use `select_related()` and `prefetch_related()` when appropriate.
- Do not modify migrations unless the task requires a data-model change.
- Keep database operations atomic when multiple related writes must succeed together.

Do not expose internal IDs, sensitive values, secrets, or debugging information unnecessarily in the frontend.

---

# UI/UX Philosophy

The application should feel like a professional institutional management system rather than a generic AI-generated dashboard.

Prioritize:

1. usability
2. clarity
3. information hierarchy
4. consistency
5. accessibility
6. responsiveness
7. visual polish

Visual decoration is lower priority than usability.

---

# Design Direction

Use a clean, modern, professional interface suitable for university or administrative use.

Prefer:

- restrained color usage
- clear hierarchy
- consistent spacing
- readable typography
- structured layouts
- compact but comfortable administrative interfaces
- clear status indicators
- consistent controls
- predictable navigation

Avoid:

- excessive gradients
- excessive glassmorphism
- excessive shadows
- excessive animations
- oversized rounded cards
- huge dashboard statistics without purpose
- unnecessary decorative whitespace
- excessive floating containers
- random colors
- inconsistent border radii
- generic AI/SaaS visual patterns
- redesigning every screen differently

Use visual emphasis intentionally.

---

# Design System

Use shared design tokens wherever practical.

Keep the following consistent across the application:

- typography
- font sizes
- spacing
- colors
- borders
- border radius
- shadows
- button heights
- form-control heights
- table density
- status colors
- focus states
- icon sizing

Do not repeatedly hard-code slightly different versions of the same design.

Prefer reusable classes, variables, components, or template partials.

---

# Components

Where appropriate, establish reusable components/partials for:

- buttons
- inputs
- select controls
- textareas
- checkboxes
- radio buttons
- badges
- alerts
- cards
- tables
- pagination
- breadcrumbs
- modal dialogs
- confirmation dialogs
- empty states
- loading states
- navigation items
- page headers
- filters
- search fields
- form fields

Do not create abstraction merely for abstraction's sake.

---

# Forms

Forms must be easy to understand and recover from errors.

Requirements:

- Every input must have a clear label.
- Clearly distinguish required and optional fields.
- Do not rely solely on placeholder text as a label.
- Display validation errors near the affected field.
- Preserve entered values after validation failure whenever possible.
- Provide helpful validation messages.
- Group related fields.
- Keep destructive actions visually distinct from normal actions.
- Disable submit buttons only when doing so improves clarity.
- Provide a visible submission/loading state when an operation takes noticeable time.

Avoid unnecessary confirmation dialogs for routine operations.

Use confirmation for destructive or difficult-to-reverse actions.

---

# Tables and Data-Heavy Screens

Administrative tables should prioritize information density and readability.

Tables should:

- have clear column headings
- align related data consistently
- keep actions predictable
- provide useful empty states
- avoid excessive columns when secondary data can be progressively disclosed
- handle long text without destroying layout
- handle smaller screens appropriately
- maintain readable row density

When filters exist:

- place them near the data they affect
- clearly show active filters
- make reset/clear behavior obvious
- preserve relevant filters during normal navigation when appropriate

Do not hide important information merely to make a table visually minimal.

---

# Statuses

Use status badges consistently.

Do not communicate critical status using color alone.

Where practical, combine:

- color
- label
- icon or shape

The same status must use the same wording and visual treatment throughout the application.

---

# Empty, Loading, Error, and Success States

Every significant data-driven screen should account for:

- initial loading
- empty data
- filtered empty results
- validation errors
- permission errors
- request failures
- successful operations

An empty table should not simply appear broken.

Explain:

- what happened
- whether the state is expected
- what the user can do next

Do not expose raw exceptions or stack traces to normal users.

---

# Navigation

Navigation must remain predictable.

Ensure:

- the current section is visibly active
- page titles are clear
- breadcrumbs are used when hierarchy benefits from them
- users can return from detail screens without confusion
- important actions are easy to find
- destructive actions are not placed beside primary actions without distinction

Do not change navigation terminology casually.

Prefer language already established by the project and documentation.

---

# Responsive Design

The application must remain usable at common desktop, laptop, tablet, and mobile widths.

Desktop administration is the primary experience unless the project specifies otherwise.

On smaller screens:

- avoid horizontal page overflow
- allow data tables to scroll horizontally when necessary
- stack form fields sensibly
- preserve action accessibility
- collapse navigation appropriately
- avoid shrinking text until it becomes difficult to read

Do not sacrifice desktop usability merely to force every table into a mobile card layout.

---

# Accessibility

Accessibility is part of implementation quality.

Maintain:

- semantic HTML
- proper labels
- keyboard accessibility
- visible focus states
- appropriate heading hierarchy
- adequate contrast
- useful button names
- descriptive link text
- accessible form validation
- sensible tab order

Do not make interaction dependent exclusively on hover.

Icons used as buttons must have accessible labels.

---

# Icons

Use the project's existing icon system.

Do not introduce multiple competing icon libraries unless necessary.

Icons should clarify actions rather than replace understandable labels everywhere.

For unfamiliar or important actions, use an icon plus text.

---

# Interaction and Animation

Animations should communicate state, not decorate the interface.

Use subtle transitions for:

- menus
- dialogs
- expanding sections
- loading feedback
- state changes

Avoid:

- long animations
- distracting motion
- bouncing UI
- unnecessary page transitions
- animations that delay interaction

---

# JavaScript

Do not add JavaScript when HTML/CSS or an existing framework mechanism can solve the problem cleanly.

When JavaScript is necessary:

- keep behavior modular
- prevent duplicate event listeners
- handle failure states
- avoid polluting global scope
- preserve accessibility
- avoid manipulating backend-controlled values insecurely

---

# User Feedback

Actions should provide appropriate feedback.

Examples:

- saved successfully
- changes failed
- validation failed
- record deleted
- operation is processing
- no results found

Avoid excessive toast notifications for trivial events.

Use inline feedback when the message relates directly to a specific form or component.

---

# Destructive Actions

For destructive operations:

- visually distinguish the action
- explain what will be affected
- request confirmation when appropriate
- prevent accidental repeated submissions
- respect backend authorization

Never make destructive actions the default primary button on a screen unless destruction is the explicit purpose of that screen.

---

# Security

Never weaken security for UI convenience.

Do not:

- bypass authorization
- remove server-side validation
- expose secrets
- commit credentials
- trust browser-provided permission information
- expose sensitive data through templates
- disable CSRF protection
- suppress security checks to make tests pass

---

# Performance

Avoid UI changes that unnecessarily increase page weight.

Prefer:

- reusable CSS
- optimized assets
- appropriate pagination
- efficient queries
- lazy loading where useful
- avoiding repeated requests
- avoiding large dependencies for small features

Do not optimize prematurely, but do not knowingly introduce obvious performance problems.

---

# Testing

After modifying behavior, run the relevant existing tests.

Do not alter tests merely to make a broken implementation pass unless the test itself is genuinely incorrect.

When fixing a bug, add or update a test when practical to protect against regression.

Do not delete meaningful tests without explicit justification.

---

# Browser Verification

For significant frontend work, verify the actual rendered application rather than assuming that valid code means correct UI.

After completing a meaningful batch of frontend changes:

1. Start or use the existing development server.
2. Open the affected pages in a browser.
3. Confirm the pages render correctly.
4. Check for obvious console/runtime errors.
5. Test the primary affected interaction.
6. Check one smaller viewport when responsiveness is relevant.
7. Correct visible regressions before finishing.

Do not run a complete browser audit after every tiny CSS change.

Batch related changes first, then verify.

Avoid wasteful loops such as:

change → screenshot → tiny change → screenshot → tiny change → screenshot

Prefer:

inspect → plan → implement related changes → verify → fix remaining issues

---

# Scope Discipline

Only modify files relevant to the current task unless another file must change for correctness.

If unrelated problems are discovered:

- mention them
- do not automatically expand the task into a repository-wide rewrite

Do not opportunistically refactor unrelated code.

---

# Documentation and Project Truth

Do not invent:

- requirements
- policies
- institutional rules
- user roles
- workflows
- system capabilities
- algorithms
- research findings
- evaluation results

When something is unclear, inspect the repository and existing documentation.

If the answer still cannot be established, preserve current behavior instead of guessing.

---

# Completion Standard

Before considering a substantial task complete:

- implementation works
- existing relevant behavior remains intact
- UI is visually consistent
- affected forms/actions work
- obvious errors are handled
- relevant tests pass
- no unnecessary files or dependencies were introduced
- significant frontend changes were visually verified
- no unrelated functionality was silently changed

Provide a concise summary of:

1. what changed
2. important files changed
3. verification performed
4. any unresolved issue or decision that genuinely requires user input
