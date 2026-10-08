"""Program/year browsing uses recorded section links, never demo naming conventions."""
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from academics.models import Subject
from audit.models import AuditLog
from timetabling.models import AssignmentMeetingRequirement, ClassSection, OfferingRequirement
from workloads.models import FacultySubjectAssignment, SubjectOffering
from workloads.tests import TeachingFixture


class RelatedAcademicLinks(HTMLParser):
    """Read the actual related-record navigation without depending on CSS layout."""

    def __init__(self, content):
        super().__init__()
        self.inside_navigation = False
        self.links = {}
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "nav" and values.get("aria-label") == "Related academic records":
            self.inside_navigation = True
        elif tag == "a" and self.inside_navigation:
            url = urlsplit(values["href"])
            self.links[url.path] = parse_qs(url.query)

    def handle_endtag(self, tag):
        if tag == "nav":
            self.inside_navigation = False


class AcademicBrowsingTests(TeachingFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sections = {}
        cls.requirements = {}
        cls.meeting_requirements = {}
        cls.year_offerings = {}
        placements = [
            ("north", cls.department, 1, cls.term, cls.offering),
            ("south", cls.department, 1, cls.term, None),
            ("evening", cls.department, 3, cls.term, None),
            ("future", cls.department, 4, cls.later, None),
            ("sibling", cls.sibling, 2, cls.term, cls.offerings[cls.sibling.pk]),
            ("external", cls.external, 1, cls.term, cls.offerings[cls.external.pk]),
        ]
        for code, department, year, term, offering in placements:
            section = ClassSection.objects.create(
                academic_term=term, department=department, code=code, year_level=year,
            )
            if offering is None:
                offering = SubjectOffering.objects.create(
                    subject=cls.records[department.pk]["subjects"], academic_term=term,
                    department=department, code=code, lecture_units=2, laboratory_units=1,
                    lecture_hours=2, laboratory_hours=3,
                )
            requirement = OfferingRequirement.objects.create(subject_offering=offering, section=section)
            assignment = FacultySubjectAssignment.objects.create(
                faculty=cls.records[department.pk]["faculty-management"], subject_offering=offering,
                share=Decimal("1"),
            )
            meeting = AssignmentMeetingRequirement.objects.create(
                assignment=assignment, meeting_type="lecture", meetings_per_week=1, duration_minutes=120,
            )
            cls.sections[code] = section
            cls.year_offerings[code] = offering
            cls.requirements[code] = requirement
            cls.meeting_requirements[code] = meeting
        cls.unplaced = Subject.objects.create(
            code="CATALOG-ONLY", title="Catalog subject with no section", owning_department=cls.department,
            lecture_units=3, laboratory_units=0,
        )

    def setUp(self):
        self.client.force_login(self.college_staff)

    def browse(self, namespace, name="list", **filters):
        response = self.client.get(reverse(f"{namespace}:{name}"), {
            "academic_term": self.term.pk, **filters,
        })
        self.assertEqual(response.status_code, 200)
        return response

    def rows(self, response):
        return list(response.context["page_obj"])

    def test_subject_year_comes_from_section_relationship_not_subject_code(self):
        response = self.browse("subjects", year_level=1, department=self.department.pk)
        self.assertEqual([obj.pk for obj in self.rows(response)], [self.offering.subject_id])
        self.assertFalse(response.context["filter_form"].errors)
        # Both year-one sections use the same subject; the catalog contains one row.
        self.assertEqual(response.context["paginator"].count, 1)

    def test_subject_in_several_years_preserves_all_recorded_years(self):
        for year in (1, 3):
            with self.subTest(year=year):
                response = self.browse("subjects", year_level=year, department=self.department.pk)
                self.assertEqual([obj.pk for obj in self.rows(response)], [self.offering.subject_id])
                self.assertIn("Years 1, 3", self.rows(response)[0].browse_group)

    def test_selected_term_defines_subject_year_placement(self):
        response = self.browse("subjects", year_level=4, department=self.department.pk)
        self.assertEqual(response.context["paginator"].count, 0)
        response = self.browse("subjects", academic_term=self.later.pk, year_level=4, department=self.department.pk)
        self.assertEqual([obj.pk for obj in self.rows(response)], [self.offering.subject_id])
        self.assertIn("Year 4", self.rows(response)[0].browse_group)
        self.assertNotIn("Year 1", self.rows(response)[0].browse_group)

    def test_catalog_subject_without_placement_remains_available(self):
        response = self.browse("subjects", department=self.department.pk)
        self.assertEqual({obj.pk for obj in self.rows(response)}, {self.offering.subject_id, self.unplaced.pk})
        response = self.browse("subjects", year_level="unplaced", department=self.department.pk)
        self.assertEqual([obj.pk for obj in self.rows(response)], [self.unplaced.pk])
        self.assertIn("Year not assigned", self.rows(response)[0].browse_group)

    def test_staff_subject_directory_contains_only_own_college(self):
        response = self.browse("subjects")
        self.assertEqual({obj.pk for obj in self.rows(response)}, {
            self.offering.subject_id, self.offerings[self.sibling.pk].subject_id, self.unplaced.pk,
        })
        departments = response.context["filter_form"].fields["department"].queryset
        self.assertEqual(set(departments.values_list("pk", flat=True)), {self.department.pk, self.sibling.pk})
        self.assertNotIn("college", [field.name for field in response.context["primary_filters"]])

    def test_administrator_group_includes_college(self):
        self.client.force_login(self.admin)
        response = self.browse("subjects", department=self.external.pk, year_level=1)
        self.assertEqual([obj.pk for obj in self.rows(response)], [self.offerings[self.external.pk].subject_id])
        self.assertIn(self.other_college.name, self.rows(response)[0].browse_group)
        self.assertIn("college", [field.name for field in response.context["primary_filters"]])

    def test_classes_offered_filter_by_program_year_and_section(self):
        response = self.browse("workloads", "offerings", department=self.department.pk, year_level=1)
        self.assertEqual({obj.pk for obj in self.rows(response)}, {
            self.year_offerings["north"].pk, self.year_offerings["south"].pk,
        })
        response = self.browse("workloads", "offerings", department=self.department.pk,
            year_level=1, section=self.sections["south"].pk)
        self.assertEqual([obj.pk for obj in self.rows(response)], [self.year_offerings["south"].pk])
        self.assertEqual(self.rows(response)[0].browse_section.pk, self.sections["south"].pk)
        self.assertIn("Year 1", self.rows(response)[0].browse_group)

    def test_section_choices_follow_selected_program_year_and_term(self):
        for namespace, name in (("workloads", "offerings"), ("timetabling", "requirements"),
                ("timetabling", "meeting-requirements")):
            with self.subTest(page=name):
                response = self.browse(namespace, name, department=self.department.pk, year_level=1)
                choices = response.context["filter_form"].fields["section"].queryset
                self.assertEqual(set(choices.values_list("pk", flat=True)), {
                    self.sections["north"].pk, self.sections["south"].pk,
                })

    def test_sections_list_filters_year_and_program(self):
        response = self.browse("timetabling", "sections", department=self.department.pk, year_level=3)
        self.assertEqual([obj.pk for obj in self.rows(response)], [self.sections["evening"].pk])
        self.assertIn("Year 3", self.rows(response)[0].browse_group)

    def test_class_scheduling_and_meeting_requirements_follow_section_filters(self):
        for name, records in (("requirements", self.requirements), ("meeting-requirements", self.meeting_requirements)):
            with self.subTest(page=name):
                response = self.browse("timetabling", name, department=self.department.pk,
                    year_level=1, section=self.sections["south"].pk)
                self.assertEqual([obj.pk for obj in self.rows(response)], [records["south"].pk])
                self.assertIn("Year 1", self.rows(response)[0].browse_group)

    def test_forged_foreign_program_and_mismatched_section_are_rejected(self):
        for namespace, name in (("subjects", "list"), ("workloads", "offerings"),
                ("timetabling", "sections"), ("timetabling", "requirements"),
                ("timetabling", "meeting-requirements")):
            with self.subTest(page=name):
                response = self.browse(namespace, name, department=self.external.pk)
                self.assertIn("department", response.context["filter_form"].errors)
                self.assertEqual(response.context["page_obj"].paginator.count, 0)
        for namespace, name in (("workloads", "offerings"), ("timetabling", "requirements"),
                ("timetabling", "meeting-requirements")):
            with self.subTest(page=name):
                response = self.browse(namespace, name, department=self.department.pk,
                    year_level=1, section=self.sections["evening"].pk)
                self.assertIn("section", response.context["filter_form"].errors)
                self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_unsupported_year_is_rejected_on_every_academic_directory(self):
        for namespace, name in (("subjects", "list"), ("workloads", "offerings"),
                ("timetabling", "sections"), ("timetabling", "requirements"),
                ("timetabling", "meeting-requirements")):
            with self.subTest(page=name):
                response = self.browse(namespace, name, year_level=5)
                self.assertIn("year_level", response.context["filter_form"].errors)
                self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_subject_pagination_preserves_program_year_search_and_term(self):
        for index in range(21):
            Subject.objects.create(code=f"UNPLACED-{index:02}", title=f"Extra catalog {index}",
                owning_department=self.department, lecture_units=3, laboratory_units=0)
        response = self.browse("subjects", department=self.department.pk,
            year_level="unplaced", q="Extra catalog", page=2)
        self.assertEqual(response.context["paginator"].count, 21)
        self.assertEqual(len(self.rows(response)), 1)
        self.assertEqual(parse_qs(response.context["page_query"]), {
            "academic_term": [str(self.term.pk)], "department": [str(self.department.pk)],
            "year_level": ["unplaced"], "q": ["Extra catalog"],
        })

    def test_academic_browsing_is_read_only(self):
        before = (Subject.objects.count(), SubjectOffering.objects.count(), OfferingRequirement.objects.count(),
            AssignmentMeetingRequirement.objects.count(), AuditLog.objects.count())
        for namespace, name in (("subjects", "list"), ("workloads", "offerings"),
                ("timetabling", "sections"), ("timetabling", "requirements"),
                ("timetabling", "meeting-requirements")):
            self.browse(namespace, name, department=self.department.pk, year_level=1)
        self.assertEqual(before, (Subject.objects.count(), SubjectOffering.objects.count(), OfferingRequirement.objects.count(),
            AssignmentMeetingRequirement.objects.count(), AuditLog.objects.count()))

    def test_related_navigation_preserves_program_year_and_supported_section_context(self):
        destinations = (("subjects", "list", False), ("workloads", "offerings", True),
            ("timetabling", "sections", False), ("timetabling", "requirements", True),
            ("timetabling", "meeting-requirements", True))
        for source_namespace, source_name in (("subjects", "list"), ("workloads", "offerings"),
                ("timetabling", "requirements"), ("timetabling", "meeting-requirements")):
            with self.subTest(source=source_name):
                selected_section = {} if source_namespace == "subjects" else {"section": self.sections["south"].pk}
                response = self.browse(source_namespace, source_name, department=self.department.pk,
                    year_level=1, **selected_section)
                links = RelatedAcademicLinks(response.content.decode()).links
                for namespace, name, supports_section in destinations:
                    query = links[reverse(f"{namespace}:{name}")]
                    self.assertEqual(query["academic_term"], [str(self.term.pk)])
                    self.assertEqual(query["department"], [str(self.department.pk)])
                    self.assertEqual(query["year_level"], ["1"])
                    if supports_section and selected_section:
                        self.assertEqual(query["section"], [str(self.sections["south"].pk)])
                    else:
                        self.assertNotIn("section", query)

    def test_unplaced_catalog_filter_is_not_forwarded_as_invalid_year(self):
        response = self.browse("subjects", department=self.department.pk, year_level="unplaced")
        links = RelatedAcademicLinks(response.content.decode()).links
        for namespace, name in (("workloads", "offerings"), ("timetabling", "sections"),
                ("timetabling", "requirements"), ("timetabling", "meeting-requirements")):
            with self.subTest(destination=name):
                query = links[reverse(f"{namespace}:{name}")]
                self.assertEqual(query["academic_term"], [str(self.term.pk)])
                self.assertEqual(query["department"], [str(self.department.pk)])
                self.assertNotIn("year_level", query)
                self.assertNotIn("section", query)

    def test_requirement_tables_show_readable_subject_section_and_scheduling_details(self):
        requirement = self.requirements["south"]
        requirement.room_type = self.room_type
        requirement.room_type_mandatory = True
        requirement.capacity_is_hard = True
        requirement.save()
        response = self.browse("timetabling", "requirements", department=self.department.pk,
            year_level=1, section=self.sections["south"].pk)
        self.assertContains(response, '<th scope="col">Room needs</th>', html=True)
        self.assertContains(response, self.offering.subject.title)
        self.assertContains(response, f"{self.offering.subject.code} · SOUTH")
        self.assertContains(response, "Lecture room (required) · Capacity must fit the section")
        self.assertNotContains(response, str(requirement))
        response = self.browse("timetabling", "meeting-requirements", department=self.department.pk,
            year_level=1, section=self.sections["south"].pk)
        self.assertContains(response, '<th scope="col">Weekly meeting pattern</th>', html=True)
        self.assertContains(response, self.offering.subject.title)
        self.assertContains(response, f"{self.offering.subject.code} · SOUTH · {self.faculty}")
        self.assertContains(response, "Lecture: 1 × 120 min/week")
