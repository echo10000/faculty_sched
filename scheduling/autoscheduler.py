"""Constraint-plan documentation for the timetable suggestion engine.

CP-SAT is a constraint-satisfaction solver: it searches combinations of
decisions that obey every hard rule we give it.  It is not inherently chasing
one "best" timetable; it only optimizes when we provide an objective.  Here,
load balance will be a soft objective after feasibility has been established.

The eventual decision variables will represent whether a particular unassigned
subject for a block is taught by a particular qualified faculty member in a
particular eligible room at a particular time slot.  A selected combination
therefore supplies the faculty, room, and time for one subject/block demand.

Hard constraints will prohibit faculty, room, and block double-booking; require
that faculty are qualified for the subject; require a subject's room type when
one is specified; and require room capacity to meet the block enrolment.  The
solver will also respect the existing assignments for the term.

This is deliberately a whole-term suggestion tool, not a live incremental
scheduler.  It will propose a complete feasible timetable that an administrator
reviews and may adjust before creating real Assignments.  The existing Django
validation and database exclusion constraints remain the final safeguards when
the approved suggestions are saved.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ortools.sat.python import cp_model

if TYPE_CHECKING:
    from .models import Term
