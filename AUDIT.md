# Phase 0 model audit

## Findings

1. Django automatically indexes every `ForeignKey`; the department-scoping and block lookup relations therefore already have database indexes. Unique fields (`College.code`, `Subject.code`, `Faculty.employee_id`, and `Student.student_number`) are also indexed by their unique constraints.
2. `Faculty.is_active`, `Student.is_active`, `Term.is_active`, and the status/type fields are likely future filter targets. Add indexes only after reviewing real query patterns; a composite index should be designed around the eventual assignment/search queries rather than adding isolated low-selectivity boolean indexes now.
3. Every concrete model has a `__str__` method. The hierarchy, curriculum, faculty, room, term, block, and student representations are useful in the admin.
4. The composite uniqueness constraints are scoped correctly for departments, programs, curriculum versions, curriculum subject placement, faculty qualifications, terms, blocks, and irregular enrollments.
5. Before Phase 1 scheduling assignments, add validation or database constraints that ensure an assignment's block belongs to the same term/curriculum context as its subject offering. Also decide whether exactly one term may be active: the current schema permits multiple active terms. PostgreSQL exclusion constraints for room/faculty time overlaps will need a concrete daterange/time range field and the `btree_gist` extension.
6. Cross-record consistency is intentionally not enforced yet: a Student may reference a program/curriculum/block combination that does not match. Enforce it with model validation (and, if needed, database triggers) when enrollment workflow is introduced.
7. A designation's release can exceed a faculty member's base load and produce a negative `effective_load_units`. Add a validation constraint if the institution prohibits that configuration.
