"""Room suitability is available with read permissions, independently of generation."""
from django.http import Http404
from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import reverse
from django.views import View

from accounts.permissions import require_access
from core.views import ProtectedViewMixin
from .forms import RoomEligibilityForm
from .models import OfferingRequirement
from .queries import scoped, get_schedule
from .room_eligibility import PREVIEW_PERMISSIONS, room_eligibility_preview
from .workspace import guided_form_class


class RoomEligibilityView(ProtectedViewMixin, View):
    permission = "scheduling.view_room"

    def get(self, request):
        for permission in PREVIEW_PERMISSIONS:
            require_access(request.user, permission)
        workspace = getattr(request, "scheduling_workspace", None)
        schedule = workspace["schedule"] if workspace else None
        if not schedule and request.GET.get("schedule"):
            try:
                schedule = get_schedule(request.user, int(request.GET["schedule"]))
            except (ValueError, TypeError):
                raise Http404("Invalid schedule")
        data = request.GET.copy()
        if schedule:
            for name, value in (("academic_term", schedule.academic_term_id), ("department", schedule.department_id)):
                if data.get(name) and data[name] != str(value):
                    raise Http404("The room preview context does not match this schedule")
                data[name] = value
        form = guided_form_class(request, RoomEligibilityForm)(data, user=request.user)
        preview = None
        if form.is_valid() and form.cleaned_data["subject_offering"]:
            offering = form.cleaned_data["subject_offering"]
            requirement = scoped(request.user, OfferingRequirement.objects.filter(subject_offering=offering)).select_related(
                "room_type", "section__program", "section__department", "section__academic_term").first()
            preview = room_eligibility_preview(request.user, offering, requirement)
        page = Paginator(preview["rooms"], 20).get_page(request.GET.get("page")) if preview else None
        query = data.copy()
        query.pop("page", None)
        return render(request, "timetabling/room_eligibility.html", {
            "filter_form": form, "preview": preview, "schedule": schedule,
            "term": form.cleaned_data.get("academic_term"),
            "list_url": reverse("timetabling:room-eligibility"),
            "page_obj": page, "page_query": query.urlencode(),
            "department": form.cleaned_data.get("department"),
        })
