from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TransactionTestCase

from academics.models import AcademicTerm, AcademicYear, Semester, Subject
from accounts.models import AdminProfile
from core.models import College, Department
from faculty.models import Faculty
from .forms import AssignmentForm
from .models import FacultySubjectAssignment, SubjectOffering, WorkloadPolicy
from .operations import save_term_record


class ConcurrentAssignmentTests(TransactionTestCase):
    def test_concurrent_adds_cannot_both_exceed_one_facultys_hard_capacity(self):
        college = College.objects.create(code="RACE", name="Race test college")
        department = Department.objects.create(code="RACE-D", name="Race department", college=college)
        user = get_user_model().objects.create_user(username="race-chair")
        AdminProfile.objects.create(user=user, role="staff", department=department)
        faculty = Faculty.objects.create(employee_id="RACE-F", first_name="Race", last_name="Teacher", home_department=department)
        year = AcademicYear.objects.create(label="Race year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        semester = Semester.objects.create(code="RACE", name="Race semester")
        term = AcademicTerm.objects.create(code="RACE", academic_year=year, semester=semester, start_date=year.start_date, end_date=year.end_date)
        subject = Subject.objects.create(code="RACE", title="Race subject", owning_department=department, lecture_units=3)
        offerings = [SubjectOffering.objects.create(subject=subject, academic_term=term, department=department, code=f"RACE-{i}", lecture_units=3, laboratory_units=0, lecture_hours=3, laboratory_hours=0) for i in (1, 2)]
        WorkloadPolicy.objects.create(academic_term=term, department=department, maximum_load=4, lecture_weight=1, enforce_maximum=True)
        barrier = Barrier(2)

        def submit(offering_id):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user.pk)
                selected_term = AcademicTerm.objects.get(pk=term.pk)
                barrier.wait(timeout=15)
                try:
                    obj, form, report = save_term_record(user=actor, form_class=AssignmentForm, term=selected_term,
                        data={"faculty": faculty.pk, "academic_term": term.pk, "subject_offering": offering_id, "share": "1"})
                    return "saved" if obj else str(form.errors)
                except ValidationError:
                    return "blocked"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, [offering.pk for offering in offerings]))
        self.assertCountEqual(outcomes, ["saved", "blocked"])
        self.assertEqual(FacultySubjectAssignment.objects.count(), 1)
