from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from academics.models import AcademicTerm, AcademicYear, Semester, Subject
from accounts.models import AdminProfile
from audit.models import AuditLog
from core.models import College, Department
from faculty.models import AcademicRank, EmploymentCategory, Faculty
from scheduling.models import Assignment, Room
from workloads.models import FacultyTermCapacity, WorkloadPolicy
from workloads.services import resolve_capacity
from .forms import FacultyForm, RoomForm, SubjectForm
from .models import Building, RoomType
from .selectors import available_resources
from .services import save_resource, set_resource_status


class ResourceFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(code="A", name="College A")
        cls.other_college = College.objects.create(code="B", name="College B")
        cls.department = Department.objects.create(code="A1", name="Alpha", college=cls.college)
        cls.sibling = Department.objects.create(code="A2", name="Sibling", college=cls.college)
        cls.external = Department.objects.create(code="B1", name="External", college=cls.other_college)
        cls.admin = cls.make_user("admin", "super_admin", is_staff=True)
        cls.dean = cls.make_user("dean", "dean", college=cls.college)
        cls.chair = cls.make_user("chair", "dept_chair", department=cls.department)
        cls.staff = cls.make_user("staff", "staff", department=cls.department)
        cls.college_staff = cls.make_user("college-staff", "staff", college=cls.college)
        cls.category = EmploymentCategory.objects.create(code="full_time", name="Full time")
        cls.rank = AcademicRank.objects.create(name="Assistant Professor")
        cls.room_type = RoomType.objects.create(code="lecture", name="Lecture room")
        cls.building = Building.objects.create(code="HALL", name="Academic Hall")
        cls.records = {}
        for index, dept in enumerate([cls.department, cls.sibling, cls.external], 1):
            cls.records[dept.pk] = {
                "faculty-management": Faculty.objects.create(employee_id=f"F{index}", first_name=f"Person{index}", last_name="Example", home_department=dept, employment_category=cls.category),
                "subjects": Subject.objects.create(code=f"S{index}", title=f"Subject {index}", owning_department=dept, lecture_units=Decimal("2"), laboratory_units=Decimal("1")),
                "rooms": Room.objects.create(code=f"R{index}", name=f"Room {index}", capacity=40, owner_department=dept, category=cls.room_type),
            }
        cls.faculty = cls.records[cls.department.pk]["faculty-management"]
        year = AcademicYear.objects.create(label="Test year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        semester = Semester.objects.create(code="FIRST", name="First semester")
        cls.term = AcademicTerm.objects.create(academic_year=year, semester=semester, code="T1", start_date=date(2026, 1, 1), end_date=date(2026, 5, 31))

    @classmethod
    def make_user(cls, name, role, is_staff=False, **scope):
        user = get_user_model().objects.create_user(username=name, password="test-only-password", is_staff=is_staff)
        AdminProfile.objects.create(user=user, role=role, **scope)
        return user

    def grant(self, user, model, *actions):
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label=model._meta.app_label, codename__in=[f"{action}_{model._meta.model_name}" for action in actions]))

    def payload(self, namespace, department=None, identifier="NEW"):
        dept = department or self.department
        base = {"college": dept.college_id}
        if namespace == "faculty-management":
            return {**base, "employee_id": identifier, "first_name": "Taylor", "middle_name": "M", "last_name": "Rivera", "email": "taylor@example.invalid", "home_department": dept.pk, "employment_category": self.category.pk, "academic_rank": self.rank.pk, "recommended_load": "18", "maximum_load": "24", "notes": "Private note"}
        if namespace == "subjects":
            return {**base, "code": identifier, "title": "New subject", "owning_department": dept.pk, "lecture_units": "2.25", "laboratory_units": "1", "lecture_hours": "2", "laboratory_hours": "3"}
        return {**base, "code": identifier, "name": f"Room {identifier}", "owner_department": dept.pk, "category": self.room_type.pk, "building": self.building.pk, "capacity": "35"}


class ResourceManagementTests(ResourceFixture):
    def test_create_each_resource_tracks_author_and_audit(self):
        self.client.force_login(self.chair)
        for namespace, model in [("faculty-management", Faculty), ("subjects", Subject), ("rooms", Room)]:
            with self.subTest(namespace=namespace):
                self.assertEqual(self.client.get(reverse(f"{namespace}:add")).status_code, 200)
                response = self.client.post(reverse(f"{namespace}:add"), self.payload(namespace))
                self.assertEqual(response.status_code, 302)
                obj = model.objects.latest("pk")
                self.assertEqual(obj.created_by, self.chair)
                event = AuditLog.objects.get(action=f"{model._meta.model_name}.created", object_id=str(obj.pk))
                self.assertEqual(event.department_id, self.department.pk)
                self.assertEqual(event.college_id, self.college.pk)
                self.assertNotIn("Private note", str(event.details))
                self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_edit_each_resource_and_subject_total(self):
        self.client.force_login(self.dean)
        for namespace, obj in self.records[self.department.pk].items():
            with self.subTest(namespace=namespace):
                self.assertEqual(self.client.get(reverse(f"{namespace}:edit", args=[obj.pk])).status_code, 200)
                response = self.client.post(reverse(f"{namespace}:edit", args=[obj.pk]), self.payload(namespace, identifier="EDITED"))
                self.assertEqual(response.status_code, 302)
                obj.refresh_from_db()
                self.assertTrue(AuditLog.objects.filter(action=f"{obj._meta.model_name}.updated", object_id=str(obj.pk)).exists())
                if namespace == "subjects":
                    self.assertEqual(obj.total_units, Decimal("3.25"))
                    self.assertEqual(obj.units, obj.total_units)

    def test_detail_get_cannot_modify_and_no_delete_endpoints(self):
        self.client.force_login(self.admin)
        for namespace, obj in self.records[self.department.pk].items():
            self.assertEqual(self.client.post(reverse(f"{namespace}:detail", args=[obj.pk]), {}).status_code, 405)
            self.assertEqual(self.client.delete(reverse(f"{namespace}:edit", args=[obj.pk])).status_code, 405)
            self.assertEqual(self.client.get(reverse(f"{namespace}:detail", args=[obj.pk]) + "delete/").status_code, 404)

    def test_status_confirmation_is_read_only_and_posts_are_idempotent(self):
        self.client.force_login(self.chair)
        for namespace, obj in self.records[self.department.pk].items():
            url = reverse(f"{namespace}:status", args=[obj.pk])
            self.assertEqual(self.client.get(url).status_code, 200)
            obj.refresh_from_db()
            self.assertTrue(obj.is_active)
            self.assertEqual(self.client.post(url, {"active": "false"}).status_code, 302)
            obj.refresh_from_db()
            self.assertFalse(obj.is_active)
            self.client.post(url, {"active": "false"})
            self.assertEqual(AuditLog.objects.filter(action=f"{obj._meta.model_name}.deactivated", object_id=str(obj.pk)).count(), 1)
            self.client.post(url, {"active": "true"})
            obj.refresh_from_db()
            self.assertTrue(obj.is_active)
            self.assertTrue(AuditLog.objects.filter(action=f"{obj._meta.model_name}.activated", object_id=str(obj.pk)).exists())

    def test_status_requires_valid_value(self):
        self.client.force_login(self.chair)
        url = reverse("faculty-management:status", args=[self.faculty.pk])
        self.assertEqual(self.client.post(url, {"active": "anything"}).status_code, 400)
        self.faculty.refresh_from_db()
        self.assertTrue(self.faculty.is_active)

    def test_csrf_enforced_for_all_mutation_types(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.chair)
        for namespace, obj in self.records[self.department.pk].items():
            for action in ("add", "edit", "status"):
                url = reverse(f"{namespace}:{action}", args=[] if action == "add" else [obj.pk])
                self.assertEqual(client.post(url, self.payload(namespace)).status_code, 403)

    def test_validation_empty_form_and_invalid_numbers_never_500(self):
        self.client.force_login(self.chair)
        for namespace, numeric in [("faculty-management", "recommended_load"), ("subjects", "lecture_units"), ("rooms", "capacity")]:
            for value in ("", "-1", "nan", "Infinity", "bad", "99999999999999999999"):
                with self.subTest(namespace=namespace, value=value):
                    data = self.payload(namespace)
                    data[numeric] = value
                    response = self.client.post(reverse(f"{namespace}:add"), data)
                    if namespace == "faculty-management" and value == "":
                        self.assertEqual(response.status_code, 302)
                    else:
                        self.assertEqual(response.status_code, 200)
                        self.assertTrue(response.context["form"].errors)
            response = self.client.post(reverse(f"{namespace}:add"), {})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)

    def test_duplicate_identifiers_are_case_insensitive(self):
        self.client.force_login(self.chair)
        for namespace, obj in self.records[self.department.pk].items():
            code = getattr(obj, "employee_id", None) or obj.code
            response = self.client.post(reverse(f"{namespace}:add"), self.payload(namespace, identifier=code.lower()))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)

    def test_negative_components_and_inconsistent_limits_rejected(self):
        self.client.force_login(self.chair)
        for field in ("lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours"):
            response = self.client.post(reverse("subjects:add"), {**self.payload("subjects"), field: "-0.5"})
            self.assertTrue(response.context["form"].errors)
        response = self.client.post(reverse("faculty-management:add"), {**self.payload("faculty-management"), "recommended_load": "25", "maximum_load": "20"})
        self.assertTrue(response.context["form"].errors)

    def test_foreign_keys_and_selected_college_must_match(self):
        self.client.force_login(self.admin)
        for namespace in ("faculty-management", "subjects", "rooms"):
            response = self.client.post(reverse(f"{namespace}:add"), {**self.payload(namespace), "college": self.other_college.pk})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)

    def test_inactive_reference_choices_rejected_for_new_records(self):
        self.client.force_login(self.chair)
        for model in (EmploymentCategory, AcademicRank, RoomType, Building):
            model.objects.all().update(is_active=False)
        for namespace in ("faculty-management", "rooms"):
            response = self.client.post(reverse(f"{namespace}:add"), self.payload(namespace))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)

    def test_edit_payload_cannot_change_status_author_or_user(self):
        self.client.force_login(self.chair)
        data = {**self.payload("faculty-management", identifier="F1"), "is_active": "false", "created_by": self.admin.pk, "user": self.admin.pk}
        self.assertEqual(self.client.post(reverse("faculty-management:edit", args=[self.faculty.pk]), data).status_code, 302)
        self.faculty.refresh_from_db()
        self.assertTrue(self.faculty.is_active)
        self.assertIsNone(self.faculty.created_by_id)
        self.assertIsNone(self.faculty.user_id)

    def test_audit_failure_rolls_back_create_edit_and_activation(self):
        with patch("resources.services.record_event", side_effect=RuntimeError("audit unavailable")):
            for form, namespace in [(FacultyForm, "faculty-management"), (SubjectForm, "subjects"), (RoomForm, "rooms")]:
                model = form._meta.model
                count = model.objects.count()
                with self.assertRaises(RuntimeError):
                    save_resource(user=self.chair, form_class=form, data=self.payload(namespace))
                self.assertEqual(model.objects.count(), count)
                obj = self.records[self.department.pk][namespace]
                with self.assertRaises(RuntimeError):
                    save_resource(user=self.chair, form_class=form, data=self.payload(namespace, identifier="EDIT"), pk=obj.pk)
                obj.refresh_from_db()
                self.assertNotEqual(getattr(obj, "employee_id", None) or obj.code, "EDIT")
                with self.assertRaises(RuntimeError):
                    set_resource_status(user=self.chair, model=model, pk=obj.pk, active=False)
                obj.refresh_from_db()
                self.assertTrue(obj.is_active)


class ResourceIsolationTests(ResourceFixture):
    def test_anonymous_and_ungranted_staff_cannot_access_any_resource_page(self):
        for namespace, obj in self.records[self.department.pk].items():
            for action in ("list", "add", "detail", "edit", "status"):
                url = reverse(f"{namespace}:{action}", args=[] if action in ("list", "add") else [obj.pk])
                self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        for namespace, obj in self.records[self.department.pk].items():
            self.assertEqual(self.client.get(reverse(f"{namespace}:list")).status_code, 403)
            self.assertEqual(self.client.post(reverse(f"{namespace}:add"), self.payload(namespace)).status_code, 403)
            self.assertEqual(self.client.post(reverse(f"{namespace}:edit", args=[obj.pk]), self.payload(namespace)).status_code, 403)
            self.assertEqual(self.client.post(reverse(f"{namespace}:status", args=[obj.pk]), {"active": "false"}).status_code, 403)
            self.assertNotContains(self.client.get("/"), f'href="{reverse(f"{namespace}:list")}"')

    def test_lists_and_dashboard_counts_follow_each_role_scope(self):
        for user, expected in [(self.admin, 3), (self.dean, 2), (self.chair, 1)]:
            self.client.force_login(user)
            for namespace in ("faculty-management", "subjects", "rooms"):
                response = self.client.get(reverse(f"{namespace}:list"))
                self.assertEqual(response.context["paginator"].count, expected)
            dashboard = self.client.get("/")
            for name in ("faculty", "subject", "room"):
                self.assertEqual(dashboard.context[f"{name}_count"], expected)
                self.assertEqual(dashboard.context[f"active_{name}_count"], expected)
        set_resource_status(user=self.chair, model=Faculty, pk=self.faculty.pk, active=False)
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.context["faculty_count"], 1)
        self.assertEqual(dashboard.context["active_faculty_count"], 0)

    def test_foreign_direct_get_and_post_urls_are_404(self):
        for user, blocked in [(self.dean, self.external), (self.chair, self.sibling), (self.chair, self.external)]:
            self.client.force_login(user)
            for namespace, obj in self.records[blocked.pk].items():
                for action in ("detail", "edit", "status"):
                    url = reverse(f"{namespace}:{action}", args=[obj.pk])
                    self.assertEqual(self.client.get(url).status_code, 404)
                    if action != "detail":
                        data = self.payload(namespace) if action == "edit" else {"active": "false"}
                        self.assertEqual(self.client.post(url, data).status_code, 404)
        self.assertFalse(AuditLog.objects.exclude(action="auth.login").exists())

    def test_cannot_forge_foreign_ownership_on_add_or_edit(self):
        for user, blocked in [(self.dean, self.external), (self.chair, self.sibling)]:
            self.client.force_login(user)
            for namespace, obj in self.records[self.department.pk].items():
                for action in ("add", "edit"):
                    response = self.client.post(reverse(f"{namespace}:{action}", args=[] if action == "add" else [obj.pk]), self.payload(namespace, blocked))
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context["form"].errors)
                obj.refresh_from_db()
                self.assertEqual((getattr(obj, "home_department_id", None) or getattr(obj, "owning_department_id", None) or obj.owner_department_id), self.department.pk)

    def test_forged_filter_values_do_not_widen_queries(self):
        self.client.force_login(self.chair)
        for namespace in ("faculty-management", "subjects", "rooms"):
            for query in ({"department": self.sibling.pk}, {"college": self.other_college.pk}, {"department": "bad"}, {"status": "unknown"}):
                response = self.client.get(reverse(f"{namespace}:list"), query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["paginator"].count, 0)
                self.assertTrue(response.context["filter_form"].errors)

    def test_explicit_staff_grants_do_not_expand_scope_or_other_actions(self):
        self.grant(self.staff, Faculty, "view", "add")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("faculty-management:list")).context["paginator"].count, 1)
        self.assertEqual(self.client.post(reverse("faculty-management:add"), self.payload("faculty-management")).status_code, 302)
        self.assertEqual(self.client.get(reverse("faculty-management:detail", args=[self.records[self.sibling.pk]["faculty-management"].pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("faculty-management:status", args=[self.faculty.pk]), {"active": "false"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("faculty-management:edit", args=[self.faculty.pk])).status_code, 403)
        self.assertNotIn("subject_count", self.client.get("/").context)

    def test_broad_groups_and_is_staff_do_not_grant_institution_access(self):
        group = Group.objects.create(name="Broad resource grants")
        group.permissions.set(Permission.objects.all())
        self.college_staff.groups.add(group)
        self.college_staff.is_staff = True
        self.college_staff.save()
        self.client.force_login(self.college_staff)
        for namespace, obj in self.records[self.external.pk].items():
            self.assertEqual(self.client.get(reverse(f"{namespace}:detail", args=[obj.pk])).status_code, 404)
            self.assertEqual(self.client.get(reverse(f"{namespace}:list")).context["paginator"].count, 2)
        self.assertEqual(self.client.get("/admin/workloads/workloadpolicy/").status_code, 302)

    def test_room_ownership_has_department_college_and_institution_boundaries(self):
        shared = Room.objects.create(code="SHARED", name="College room", capacity=0, owner_college=self.college)
        global_room = Room.objects.create(code="GLOBAL", name="Institution room", capacity=0)
        for user, shared_status, global_status in [(self.chair, 404, 404), (self.dean, 200, 404), (self.admin, 200, 200)]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("rooms:detail", args=[shared.pk])).status_code, shared_status)
            self.assertEqual(self.client.get(reverse("rooms:detail", args=[global_room.pk])).status_code, global_status)
        self.client.force_login(self.chair)
        data = {**self.payload("rooms"), "owner_department": ""}
        self.assertEqual(self.client.post(reverse("rooms:add"), data).status_code, 403)
        self.client.force_login(self.dean)
        self.assertEqual(self.client.post(reverse("rooms:add"), data).status_code, 302)

    def test_service_layer_checks_authorization_without_view(self):
        with self.assertRaises(PermissionDenied):
            save_resource(user=self.staff, form_class=FacultyForm, data=self.payload("faculty-management"))
        with self.assertRaises(PermissionDenied):
            save_resource(user=self.chair, form_class=RoomForm, data={**self.payload("rooms"), "owner_department": ""})

    def test_profile_revocation_applies_to_existing_session_and_writes(self):
        self.client.force_login(self.chair)
        AdminProfile.objects.filter(user=self.chair).update(is_enabled=False)
        self.assertEqual(self.client.get(reverse("faculty-management:list")).status_code, 403)
        self.assertEqual(self.client.post(reverse("faculty-management:add"), self.payload("faculty-management")).status_code, 403)

    def test_faculty_detail_respects_calendar_grant_and_highlights_navigation(self):
        self.grant(self.staff, Faculty, "view")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("faculty-management:detail", args=[self.faculty.pk]))
        self.assertNotContains(response, "Teaching capacity by active term")
        self.assertContains(response, 'aria-current="page" href="/faculty/"')
        self.client.force_login(self.chair)
        self.assertContains(self.client.get(reverse("faculty-management:detail", args=[self.faculty.pk])), "Teaching capacity by active term")


class ResourceSearchAndIntegrityTests(ResourceFixture):
    def test_search_filters_and_pagination_preserve_scope(self):
        self.client.force_login(self.dean)
        for namespace, query in [("faculty-management", "Person1"), ("subjects", "Subject 1"), ("rooms", "Room 1")]:
            response = self.client.get(reverse(f"{namespace}:list"), {"q": query, "department": self.department.pk, "status": "active"})
            self.assertEqual(response.context["paginator"].count, 1)
            self.assertEqual(self.client.get(reverse(f"{namespace}:list"), {"status": "inactive"}).context["paginator"].count, 0)
        for index in range(24):
            Faculty.objects.create(employee_id=f"P{index}", first_name="Page", last_name=str(index), home_department=self.department)
        response = self.client.get(reverse("faculty-management:list"), {"q": "Page", "department": self.department.pk, "page": 2})
        self.assertEqual(len(response.context["object_list"]), 4)
        self.assertContains(response, "q=Page")
        self.assertContains(response, f"department={self.department.pk}")

    def test_employment_rank_room_type_building_filters(self):
        self.faculty.academic_rank = self.rank
        self.faculty.save()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("faculty-management:list"), {"employment_category": self.category.pk, "academic_rank": self.rank.pk})
        self.assertEqual(response.context["paginator"].count, 1)
        room = self.records[self.department.pk]["rooms"]
        room.building = self.building
        room.save()
        self.assertEqual(self.client.get(reverse("rooms:list"), {"category": self.room_type.pk, "building": self.building.pk}).context["paginator"].count, 1)

    def test_database_constraints_block_invalid_direct_updates(self):
        attempts = [
            (Faculty.objects.filter(pk=self.faculty.pk), {"recommended_load": -1}),
            (Faculty.objects.filter(pk=self.faculty.pk), {"recommended_load": 20, "maximum_load": 10}),
            (Subject.objects.filter(pk=self.records[self.department.pk]["subjects"].pk), {"laboratory_hours": -1}),
            (Subject.objects.filter(pk=self.records[self.department.pk]["subjects"].pk), {"units": 99}),
            (Room.objects.filter(pk=self.records[self.department.pk]["rooms"].pk), {"capacity": -1}),
            (Room.objects.filter(pk=self.records[self.department.pk]["rooms"].pk), {"owner_college": self.college}),
            (Faculty.objects.filter(pk=self.records[self.sibling.pk]["faculty-management"].pk), {"employee_id": "f1"}),
            (Subject.objects.filter(pk=self.records[self.sibling.pk]["subjects"].pk), {"code": "s1"}),
            (Room.objects.filter(pk=self.records[self.sibling.pk]["rooms"].pk), {"code": "r1"}),
        ]
        for qs, changes in attempts:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                qs.update(**changes)

    def test_active_selector_excludes_inactive_and_foreign_records(self):
        for namespace, obj in self.records[self.department.pk].items():
            model = type(obj)
            self.assertEqual(list(available_resources(self.chair, model)), [obj])
            set_resource_status(user=self.chair, model=model, pk=obj.pk, active=False)
            self.assertFalse(available_resources(self.chair, model).exists())

    def test_inactive_organization_blocks_new_records_and_activation(self):
        Department.objects.filter(pk=self.sibling.pk).update(is_active=False)
        self.client.force_login(self.dean)
        for namespace in ("faculty-management", "subjects", "rooms"):
            response = self.client.post(reverse(f"{namespace}:add"), self.payload(namespace, self.sibling))
            self.assertTrue(response.context["form"].errors)
            obj = self.records[self.sibling.pk][namespace]
            set_resource_status(user=self.dean, model=type(obj), pk=obj.pk, active=False)
            with self.assertRaises(ValidationError):
                set_resource_status(user=self.dean, model=type(obj), pk=obj.pk, active=True)

    def test_legacy_unsplit_subject_total_is_preserved(self):
        subject = Subject.objects.create(code="OLD", title="Legacy", units=Decimal("3.5"))
        subject.refresh_from_db()
        self.assertIsNone(subject.lecture_units)
        self.assertEqual(subject.total_units, Decimal("3.5"))


class WorkloadConfigurationTests(ResourceFixture):
    def test_missing_policies_leave_capacity_unconfigured(self):
        self.assertEqual(resolve_capacity(self.faculty, self.term), dict.fromkeys(["recommended_load", "maximum_load", "lecture_weight", "laboratory_weight"]))

    def test_capacity_precedence_and_zero_are_explicit(self):
        WorkloadPolicy.objects.create(academic_term=self.term, recommended_load=18, maximum_load=24, lecture_weight=1, laboratory_weight=Decimal("1.5"))
        WorkloadPolicy.objects.create(academic_term=self.term, college=self.college, maximum_load=23)
        WorkloadPolicy.objects.create(academic_term=self.term, department=self.department, maximum_load=22, lecture_weight=0)
        WorkloadPolicy.objects.create(academic_term=self.term, department=self.external, maximum_load=99)
        self.assertEqual(resolve_capacity(self.faculty, self.term)["maximum_load"], 22)
        self.faculty.maximum_load = 21
        self.faculty.save()
        self.assertEqual(resolve_capacity(self.faculty, self.term)["maximum_load"], 21)
        FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term, recommended_load=0, maximum_load=20)
        resolved = resolve_capacity(self.faculty, self.term)
        self.assertEqual(resolved["maximum_load"], 20)
        self.assertEqual(resolved["recommended_load"], 0)
        self.assertEqual(resolved["lecture_weight"], 0)
        self.assertEqual(resolved["laboratory_weight"], Decimal("1.5"))

    def test_incompatible_inherited_limits_rejected(self):
        WorkloadPolicy.objects.create(academic_term=self.term, recommended_load=18, maximum_load=24)
        with self.assertRaises(ValidationError):
            FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term, maximum_load=10)

    def test_policy_and_override_constraints(self):
        policy = WorkloadPolicy.objects.create(academic_term=self.term, recommended_load=18, maximum_load=24)
        for data in ({"recommended_load": -1}, {"lecture_weight": -1}, {"laboratory_weight": -1}, {"recommended_load": 25}, {"college": self.college, "department": self.department}):
            with self.subTest(data=data), self.assertRaises(IntegrityError), transaction.atomic():
                WorkloadPolicy.objects.filter(pk=policy.pk).update(**data)
        with self.assertRaises(ValidationError):
            WorkloadPolicy.objects.create(academic_term=self.term)
        FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term)
        with self.assertRaises(ValidationError):
            FacultyTermCapacity.objects.create(faculty=self.faculty, academic_term=self.term)

    def test_admin_configuration_is_restricted_and_audited(self):
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get("/admin/workloads/workloadpolicy/").status_code, 302)
        self.client.force_login(self.admin)
        response = self.client.post("/admin/workloads/workloadpolicy/add/", {"academic_term": self.term.pk, "department": self.department.pk, "recommended_load": "18", "maximum_load": "24", "lecture_weight": "1", "laboratory_weight": "1.5", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        policy = WorkloadPolicy.objects.get(department=self.department)
        self.assertEqual(policy.created_by, self.admin)
        self.assertTrue(AuditLog.objects.filter(action="record.create", object_type="workloads.WorkloadPolicy", object_id=str(policy.pk)).exists())
        self.assertEqual(self.client.post(f"/admin/workloads/workloadpolicy/{policy.pk}/delete/", {"post": "yes"}).status_code, 403)


class ResourceSeedTests(TestCase):
    def test_seeds_are_idempotent_preserve_edits_passwords_and_create_no_assignments(self):
        with TemporaryDirectory() as directory, override_settings(DEBUG=True, BASE_DIR=Path(directory)):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("seed_foundation", create_users=True, with_resources=True, stdout=StringIO())
            hashes = dict(get_user_model().objects.values_list("username", "password"))
            faculty = Faculty.objects.get(employee_id="DEMO-F1")
            faculty.first_name = "Preserve my edit"
            faculty.save()
            counts = [model.objects.count() for model in (Faculty, Subject, Room, WorkloadPolicy, FacultyTermCapacity)]
            with self.captureOnCommitCallbacks(execute=True):
                call_command("seed_foundation", create_users=True, with_resources=True, stdout=StringIO())
            self.assertEqual(dict(get_user_model().objects.values_list("username", "password")), hashes)
            self.assertEqual([model.objects.count() for model in (Faculty, Subject, Room, WorkloadPolicy, FacultyTermCapacity)], counts)
            faculty.refresh_from_db()
            self.assertEqual(faculty.first_name, "Preserve my edit")
            self.assertEqual(set(hashes), {"dev.admin", "dev.dean", "dev.chair", "dev.staff"})
            self.assertEqual(Assignment.objects.count(), 0)
            self.assertEqual(Faculty.objects.count(), 2)

    def test_resource_seed_is_development_only(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("seed_resources", stdout=StringIO())
