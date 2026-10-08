from datetime import time

from .models import FacultyAvailability
from .tests import TeachingFixture


class StaffWorkloadClarityTests(TeachingFixture):
    def test_weekly_times_and_editing_return_to_the_same_instructor(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.url("availability-add") + f"&faculty={self.faculty.pk}", self.availability_data())
        self.assertRedirects(response, self.url("faculty-availability", self.faculty.pk))
        page = self.client.get(response.url)
        self.assertEqual(len(page.context["weekly_days"]), 7)
        self.assertContains(page, "No times recorded")
        record = FacultyAvailability.objects.get()
        response = self.client.post(self.url("availability-edit", record.pk) + f"&faculty={self.faculty.pk}", self.availability_data(end_time="11:00"))
        self.assertRedirects(response, self.url("faculty-availability", self.faculty.pk))
        response = self.client.post(self.url("availability-delete", record.pk))
        self.assertRedirects(response, self.url("faculty-availability", self.faculty.pk))

    def test_availability_overview_groups_records_by_faculty_and_keeps_filters_scoped(self):
        for day in (1, 2):
            FacultyAvailability.objects.create(faculty=self.faculty, academic_term=self.term,
                day_of_week=day, start_time=time(8), end_time=time(12), availability_type="available")
        external = self.records[self.external.pk]["faculty-management"]
        FacultyAvailability.objects.create(faculty=external, academic_term=self.term,
            day_of_week=1, start_time=time(8), end_time=time(12), availability_type="available")
        self.client.force_login(self.chair)
        response = self.client.get(self.url("availability"))
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        row = response.context["page_obj"][0]
        self.assertEqual(row["faculty"], self.faculty)
        self.assertEqual(row["record_count"], 2)
        self.assertNotContains(response, str(external))
        response = self.client.get(self.url("availability") + "&day_of_week=2")
        self.assertEqual(response.context["page_obj"][0]["record_count"], 1)
        response = self.client.get(self.url("faculty-availability", self.faculty.pk))
        self.assertEqual(response.context["page_obj"].paginator.count, 2)
        self.assertContains(response, "08:00–12:00")

    def test_invalid_secondary_filters_stay_visible_and_do_not_expose_foreign_records(self):
        self.client.force_login(self.chair)
        response = self.client.get(self.url("availability") + f"&college={self.other_college.pk}")
        self.assertTrue(response.context["filter_form"].errors)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertContains(response, "Select a valid choice")

    def test_assignment_percentage_and_workload_totals_keep_the_existing_values(self):
        self.save_assignment()
        self.policy(recommended_load=18, maximum_load=24)
        self.client.force_login(self.chair)
        response = self.client.get(self.url("assignments"))
        self.assertContains(response, "100%")
        response = self.client.get(self.url("faculty", self.faculty.pk))
        self.assertEqual(response.context["report"]["assigned_load"], 3.5)
        self.assertContains(response, "5.00")
        self.assertContains(response, "How workload is calculated")
