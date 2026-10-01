# Capstone demonstration path

Use a disposable `DEBUG=True` PostgreSQL database. Run `migrate`, then `seed_foundation --create-users --with-review --with-balancing`. The seed is idempotent, creates fictional teaching, timetable, generator and balancing examples, and submits one manual schedule for review. It creates no approval, official booking or generated/accepted recommendation. Credentials are written only to the ignored `.local/development-credentials.txt` when accounts are first created.

1. Sign in as `dev.chair`. Open **Overview** and select the fictional term; show scoped faculty, workload status, offerings and assignments.
2. Open the manual timetable and use its validation page to show the seeded valid meetings and a conflict preview. Review the separate generator workspace, request a bounded Phase 5 proposal, inspect its objective/status and accept only after reviewing the proposal.
3. Open the Phase 6 balancing example. Request a recommendation, compare before/after loads and accept or discard using the real review action.
4. Open **My schedules** and the already submitted fictional schedule. Show its revision token, findings and workflow history. Sign out, then sign in as `dev.dean` to open **Pending review** and approve only after the required warning acknowledgments. This creates a real frozen snapshot, dated bookings and active official selection.
5. Return to **Official schedules** and **Overview** to show the selected version. Create a revision of the approved version; show that the older version remains official until a replacement approval commits.
6. Open **Reports** as chair/dean. Compare current official and historical snapshot labels; print and export a scoped report. Use a second college or department choice as system administrator to demonstrate organization scoping.

The actual action order depends on the seeded version's current state; repeat seeds preserve decisions and do not reset passwords or approvals. Use a fresh disposable database for a repeatable live presentation. Never alter production data for the demo. The matching-weekday booking model has no holiday or date exceptions.
