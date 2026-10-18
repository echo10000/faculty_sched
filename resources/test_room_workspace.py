"""Room workspace contracts reuse authoritative checks and scoped CRUD."""
from urllib.parse import parse_qs, urljoin, urlsplit

from django.urls import reverse
from django.test import override_settings
from django.contrib.auth.models import Permission

from audit.models import AuditLog
from resources.models import Building
from resources.test_frontend_design import Markup
from scheduling.models import Room
from timetabling.models import ScheduleGenerationRun
from timetabling.tests import TimetableFixture


class RoomWorkspaceTests(TimetableFixture):
    def setUp(self):
        self.client.force_login(self.staff)
        self.query = {"academic_term": self.term.pk, "schedule": self.schedule.pk,
                      "subject_offering": self.offering.pk}

    def test_directory_search_filters_and_scope(self):
        Room.objects.filter(pk=self.room.pk).update(building=self.building)
        for filters, expected in [
            ({"q": self.room.code}, 1), ({"q": self.building.name}, 1),
            ({"category": self.room_type.pk, "building": self.building.pk}, 1),
            ({"status": "inactive"}, 0), ({"department": self.department.pk}, 1),
        ]:
            with self.subTest(filters=filters):
                page = self.client.get(reverse("rooms:list"), filters)
                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.context["paginator"].count, expected)
                self.assertNotContains(page, self.records[self.external.pk]["rooms"].name)
        page = self.client.get(reverse("rooms:list"), {"department": self.external.pk})
        self.assertContains(page, "The selected filters could not be applied.")
        self.assertEqual(page.context["paginator"].count, 0)

    def test_directory_empty_and_invalid_filters_have_recovery(self):
        page = self.client.get(reverse("rooms:list"), {"q": "no-room-matches"})
        self.assertContains(page, "No rooms match the current filters.")
        self.assertContains(page, "Reset filters")
        page = self.client.get(reverse("rooms:list"), {"status": "forged"})
        markup = Markup(page.content.decode())
        self.assertEqual(markup.by_id("id_status")["aria-invalid"], "true")
        self.assertIn("id_status_error", markup.by_id("id_status")["aria-describedby"])
        self.room.delete()
        self.assertContains(self.client.get(reverse("rooms:list")), "No rooms are available in your scope.")

    def test_configuration_never_claims_time_availability(self):
        for route, args in [("rooms:list", []), ("rooms:detail", [self.room.pk])]:
            page = self.client.get(reverse(route, args=args))
            self.assertContains(page, "Not checked")
            self.assertContains(page, "Room schedule" if not args else "View room schedule")
        building = Building.objects.create(code="CLOSED-UX", name="Closed hall", is_active=False)
        Room.objects.filter(pk=self.room.pk).update(building=building, is_active=False)
        for route, args in [("rooms:list", []), ("rooms:detail", [self.room.pk])]:
            page = self.client.get(reverse(route, args=args))
            self.assertContains(page, "Building is inactive")
            self.assertContains(page, "Room is inactive")

    def test_class_and_directory_context_survives_detail_edit_save(self):
        page = self.client.get(reverse("rooms:list"), {**self.query, "q": self.room.code})
        self.assertEqual(page.status_code, 200)
        markup = Markup(page.content.decode())
        detail = next(attrs["href"] for tag, attrs in markup.elements
                      if tag == "a" and urlsplit(attrs.get("href", "")).path == reverse("rooms:detail", args=[self.room.pk]))
        detail_page = self.client.get(detail)
        self.assertEqual(detail_page.status_code, 200)
        self.assertEqual(detail_page.context["room_offering"], self.offering)
        back_query = parse_qs(urlsplit(detail_page.context["list_url"]).query)
        self.assertEqual(back_query["q"], [self.room.code])
        edit = reverse("rooms:edit", args=[self.room.pk]) + "?" + urlsplit(detail).query
        response = self.client.post(edit, self.payload("rooms", identifier=self.room.code))
        self.assertEqual(response.status_code, 302)
        redirect_query = parse_qs(urlsplit(response.url).query)
        for name, value in self.query.items():
            self.assertEqual(redirect_query[name], [str(value)])
        self.assertIn("directory", redirect_query)
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_status_redirect_preserves_context(self):
        from urllib.parse import urlencode
        url = reverse("rooms:status", args=[self.room.pk]) + "?" + urlencode(self.query)
        response = self.client.post(url, {"active": "false"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query)["subject_offering"], [str(self.offering.pk)])
        self.room.refresh_from_db()
        self.assertFalse(self.room.is_active)

    def test_forged_context_is_rejected_before_writes(self):
        from urllib.parse import urlencode
        before = AuditLog.objects.count()
        queries = [{**self.query, "subject_offering": self.offerings[self.external.pk].pk},
                   {**self.query, "academic_term": self.later.pk},
                   {**self.query, "schedule": "invalid"}]
        for query in queries:
            url = reverse("rooms:edit", args=[self.room.pk]) + "?" + urlencode(query)
            response = self.client.post(url, self.payload("rooms", identifier="FORGED-UX"))
            self.assertEqual(response.status_code, 404)
        self.room.refresh_from_db()
        self.assertNotEqual(self.room.code, "FORGED-UX")
        self.assertEqual(AuditLog.objects.count(), before)

    def test_out_of_scope_room_stays_inaccessible(self):
        external = self.records[self.external.pk]["rooms"]
        for action in ("detail", "edit", "status"):
            self.assertEqual(self.client.get(reverse(f"rooms:{action}", args=[external.pk])).status_code, 404)
        before = AuditLog.objects.count()
        self.assertEqual(self.client.post(reverse("rooms:status", args=[external.pk]), {"active": "false"}).status_code, 404)
        external.refresh_from_db()
        self.assertTrue(external.is_active)
        self.assertEqual(AuditLog.objects.count(), before)

    def test_inactive_room_remains_accessible_and_can_be_reactivated_with_context(self):
        from urllib.parse import urlencode
        Room.objects.filter(pk=self.room.pk).update(is_active=False)
        for action in ("detail", "edit", "status"):
            url = reverse(f"rooms:{action}", args=[self.room.pk]) + "?" + urlencode(self.query)
            self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"active": "true"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(response.url).context["room_offering"], self.offering)
        self.room.refresh_from_db()
        self.assertTrue(self.room.is_active)

    @override_settings(AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"])
    def test_read_only_room_access_does_not_grant_edit_or_status_permissions(self):
        reader = self.make_user("room-workspace-reader", "staff", department=self.department)
        reader.groups.clear()
        reader.user_permissions.clear()
        reader.user_permissions.add(Permission.objects.get(content_type__app_label="scheduling", codename="view_room"))
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse("rooms:list")).status_code, 200)
        page = self.client.get(reverse("rooms:detail", args=[self.room.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Edit room")
        self.assertNotContains(page, "Deactivate room")
        self.assertEqual(self.client.get(reverse("rooms:edit", args=[self.room.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("rooms:status", args=[self.room.pk]), {"active": "false"}).status_code, 403)
        self.room.refresh_from_db()
        self.assertTrue(self.room.is_active)

    def test_room_schedule_links_retain_authorized_room_term_and_version(self):
        page = self.client.get(reverse("rooms:detail", args=[self.room.pk]), self.query)
        self.assertEqual(page.status_code, 200)
        markup = Markup(page.content.decode())
        link = next(attrs["href"] for tag, attrs in markup.elements
                    if tag == "a" and urlsplit(attrs.get("href", "")).path == reverse("reporting:detail", args=["room-schedule"]))
        query = parse_qs(urlsplit(link).query)
        self.assertEqual(query["room"], [str(self.room.pk)])
        self.assertEqual(query["term"], [str(self.term.pk)])
        self.assertEqual(query["schedule"], [str(self.schedule.pk)])
        self.assertEqual(self.client.get(link).status_code, 200)

    def test_invalid_room_form_preserves_values_and_accessible_errors(self):
        payload = self.payload("rooms", identifier="UX-NEW")
        payload["capacity"] = "-1"
        page = self.client.post(reverse("rooms:add"), payload)
        self.assertEqual(page.status_code, 200)
        markup = Markup(page.content.decode())
        self.assertEqual(markup.by_id("id_capacity")["aria-invalid"], "true")
        self.assertEqual(markup.by_id("id_code")["value"], "UX-NEW")
        self.assertIn("id_capacity_error", markup.by_id("id_capacity")["aria-describedby"])
        self.assertFalse(Room.objects.filter(code="UX-NEW").exists())

    def test_preview_room_link_retains_class_and_schedule_without_guide(self):
        page = self.client.get(reverse("timetabling:room-eligibility"), {**self.query, "department": self.department.pk})
        markup = Markup(page.content.decode())
        link = next(attrs["href"] for tag, attrs in markup.elements
                    if tag == "a" and urlsplit(attrs.get("href", "")).path == reverse("rooms:detail", args=[self.room.pk]))
        for name, value in self.query.items():
            self.assertEqual(parse_qs(urlsplit(link).query)[name], [str(value)])
        room_page = self.client.get(link)
        self.assertEqual(room_page.status_code, 200)
        markup = Markup(room_page.content.decode())
        comparison = next(attrs["href"] for tag, attrs in markup.elements
                          if tag == "a" and urlsplit(attrs.get("href", "")).path == reverse("timetabling:room-eligibility"))
        response = self.client.get(comparison)
        self.assertEqual(response.context["preview"]["offering"], self.offering)
        self.assertEqual(response.context["schedule"], self.schedule)

    def test_preview_explanations_are_read_only_and_mobile_labeled(self):
        before = ScheduleGenerationRun.objects.count(), AuditLog.objects.count()
        page = self.client.get(reverse("timetabling:room-eligibility"), {**self.query, "department": self.department.pk})
        self.assertContains(page, 'data-label="Checks and explanations"')
        self.assertContains(page, "Mandatory specialization excludes rooms")
        self.assertContains(page, "advisory capacity warns without excluding")
        self.assertContains(page, "Not checked")
        self.assertEqual(before, (ScheduleGenerationRun.objects.count(), AuditLog.objects.count()))

    def test_directory_pagination_preserves_filters_and_context(self):
        for index in range(21):
            Room.objects.create(code=f"UX-PAGE-{index}", name=f"UX pagination {index}", capacity=35,
                                category=self.room_type, owner_department=self.department)
        page = self.client.get(reverse("rooms:list"), {**self.query, "q": "UX pagination"})
        self.assertEqual(page.status_code, 200)
        markup = Markup(page.content.decode())
        link = next(attrs["href"] for tag, attrs in markup.elements if tag == "a" and "page=2" in attrs.get("href", ""))
        query = parse_qs(urlsplit(link).query)
        self.assertEqual(query["q"], ["UX pagination"])
        self.assertEqual(query["subject_offering"], [str(self.offering.pk)])
        # The shared paginator emits a query-relative href. Resolve it as a
        # browser does; Django's test client otherwise requests the site root.
        next_page = self.client.get(urljoin(reverse("rooms:list"), link))
        self.assertEqual(next_page.status_code, 200)
        self.assertEqual(next_page.context["page_obj"].number, 2)
