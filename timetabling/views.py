from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from core.views import ProtectedViewMixin
from workloads.calculation import calculate_workload
from workloads.models import FacultyAvailability
from workloads.selectors import accessible_terms
from .models import Schedule, ScheduleEntry, ClassSection, OfferingRequirement, RoomUnavailability
from .forms import ScheduleForm, SectionForm, RequirementForm, ClosureForm, EntryForm, TimetableFilter
from .queries import authorized, scoped, get_schedule, entry_queryset
from .mutations import save_record, save_entry, remove_entry, remove_closure, validate_schedule, effective_status
from .conflicts import get_schedule_conflicts, summarize
from .timetable import filtered_entries, week_columns

SPECS = {
    "schedules": (Schedule, ScheduleForm, "Manual schedules"),
    "sections": (ClassSection, SectionForm, "Class sections"),
    "requirements": (OfferingRequirement, RequirementForm, "Offering requirements"),
    "closures": (RoomUnavailability, ClosureForm, "Room unavailability"),
}


class TimetableMixin(ProtectedViewMixin):
    section = "schedules"
    action = "view"

    def dispatch(self, request, *args, **kwargs):
        self.permission = f"timetabling.{self.action}_{SPECS[self.section][0]._meta.model_name}"
        if request.user.is_authenticated:
            authorized(request.user, SPECS[self.section][0], self.action)
        return super().dispatch(request, *args, **kwargs)

    def context(self):
        model, _, title = SPECS[self.section]
        return {"title": title, "section": self.section, "list_url": reverse(f"timetabling:{self.section}"),
            "can_add": self.request.user.has_perm(f"timetabling.add_{model._meta.model_name}"),
            "can_change": self.request.user.has_perm(f"timetabling.change_{model._meta.model_name}")}


class RecordList(TimetableMixin, View):
    def get(self, request):
        model = SPECS[self.section][0]
        form = TimetableFilter(request.GET, user=request.user)
        keep = {"academic_term", "department", "q"}
        if self.section == "closures":
            keep = {"academic_term", "room", "day_of_week"}
        form.fields = {k: v for k, v in form.fields.items() if k in keep}
        qs = scoped(request.user, model.objects.all())
        if form.is_valid():
            data = form.cleaned_data
            prefix = "subject_offering__" if self.section == "requirements" else ""
            for field in ("academic_term", "department", "room", "day_of_week"):
                if data.get(field):
                    qs = qs.filter(**{prefix + field: data[field]})
            if data.get("q"):
                lookup = {"schedules": "name", "sections": "code", "requirements": "subject_offering__subject__code"}[self.section]
                qs = qs.filter(**{lookup + "__icontains": data["q"]})
        else:
            qs = qs.none()
        if not qs.ordered:
            qs = qs.order_by("pk")
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        for obj in page:
            obj.owner_label = getattr(obj, "department", None) or getattr(obj, "college", None) or "Institution"
            obj.detail_url = reverse("timetabling:schedules-detail", args=[obj.pk]) if self.section == "schedules" else reverse(f"timetabling:{self.section}-edit", args=[obj.pk])
            if isinstance(obj, Schedule):
                obj.display_status = effective_status(obj)
        query = request.GET.copy()
        query.pop("page", None)
        return render(request, "timetabling/list.html", {**self.context(), "page_obj": page, "page_query": query.urlencode(), "filter_form": form})


class RecordForm(TimetableMixin, View):
    action = "add"

    def get_original(self, request, pk):
        model = SPECS[self.section][0]
        return get_object_or_404(scoped(request.user, model.objects.all()), pk=pk) if pk else None

    def selected_term(self, request, obj):
        if obj:
            return obj.academic_term
        if request.GET.get("academic_term"):
            try:
                return get_object_or_404(accessible_terms(request.user), pk=int(request.GET["academic_term"]))
            except ValueError:
                raise Http404("Invalid term")
        return None

    def get(self, request, pk=None):
        obj = self.get_original(request, pk)
        form = SPECS[self.section][1](user=request.user, instance=obj, term=self.selected_term(request, obj))
        return render(request, "timetabling/form.html", {**self.context(), "form": form, "editing": bool(pk)})

    def post(self, request, pk=None):
        original = self.get_original(request, pk)
        term = self.selected_term(request, original)
        form_class = SPECS[self.section][1]
        try:
            obj, form = save_record(user=request.user, form_class=form_class, data=request.POST, pk=pk, term=term)
        except (ValidationError, IntegrityError) as error:
            form = form_class(request.POST, user=request.user, instance=original, term=term)
            form.is_valid()
            form.add_error(None, "; ".join(error.messages) if isinstance(error, ValidationError) else "This record conflicts with existing data. Check its identifier and time range.")
            obj = None
        if obj:
            messages.success(request, "Record saved. Revalidate affected schedules after changing scheduling requirements or availability.")
            return redirect("timetabling:schedules-detail", obj.pk) if self.section == "schedules" else redirect(f"timetabling:{self.section}")
        return render(request, "timetabling/form.html", {**self.context(), "form": form, "editing": bool(pk)})


class ScheduleDetail(TimetableMixin, View):
    mode = "detail"

    def get(self, request, pk):
        schedule = get_schedule(request.user, pk)
        authorized(request.user, ScheduleEntry)
        form = TimetableFilter(request.GET, user=request.user)
        entries = filtered_entries(request.user, schedule, form)
        report = summarize(get_schedule_conflicts(schedule, user=request.user), schedule.entries.count()) if self.mode == "conflicts" else None
        return render(request, "timetabling/detail.html", {**self.context(), "schedule": schedule, "status": effective_status(schedule),
            "filter_form": form, "entries": entries, "week": week_columns(entries), "mode": self.mode, "report": report})


class EntryEditor(TimetableMixin, View):
    action = "view"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            authorized(request.user, ScheduleEntry, "change" if kwargs.get("pk") else "add")
            self.schedule = get_schedule(request.user, kwargs["schedule_id"])
        return super().dispatch(request, *args, **kwargs)

    def original(self, request, pk):
        return get_object_or_404(scoped(request.user, entry_queryset()).filter(schedule=self.schedule), pk=pk) if pk else None

    def context_entry(self, form, conflicts=None, preview=False, editing=False):
        context = {**self.context(), "title": "Manual meeting", "schedule": self.schedule, "form": form, "is_entry": True,
            "conflicts": conflicts or [], "previewed": preview, "editing": editing,
            "list_url": reverse("timetabling:schedules-detail", args=[self.schedule.pk])}
        assignment = form.cleaned_data.get("assignment") if hasattr(form, "cleaned_data") else form.instance.assignment if form.instance.assignment_id else None
        if assignment:
            context["assignment_context"] = assignment
            requirement = OfferingRequirement.objects.filter(subject_offering=assignment.subject_offering).select_related("section").first()
            context["requirement"] = requirement
            if self.request.user.has_perm("workloads.view_workload"):
                context["workload"] = calculate_workload(assignment.faculty, self.schedule.academic_term)
            if self.request.user.has_perm("workloads.view_facultyavailability"):
                context["availability"] = FacultyAvailability.objects.filter(faculty=assignment.faculty, academic_term=self.schedule.academic_term)
        return context

    def get(self, request, schedule_id, pk=None):
        form = EntryForm(user=request.user, schedule=self.schedule, instance=self.original(request, pk))
        return render(request, "timetabling/form.html", self.context_entry(form, editing=bool(pk)))

    def post(self, request, schedule_id, pk=None):
        preview = request.POST.get("intent") == "preview"
        try:
            obj, form, conflicts = save_entry(user=request.user, schedule_id=schedule_id, pk=pk, data=request.POST, preview=preview)
        except (ValidationError, IntegrityError) as error:
            form = EntryForm(request.POST, user=request.user, schedule=self.schedule, instance=self.original(request, pk))
            form.is_valid()
            form.add_error(None, "; ".join(error.messages) if isinstance(error, ValidationError) else "Meeting could not be saved because its data conflicts. Refresh and check again.")
            obj, conflicts = None, []
        if obj and not preview:
            messages.success(request, "Meeting saved. The schedule is now a draft.")
            for conflict in conflicts:
                messages.warning(request, conflict.message)
            return redirect("timetabling:schedules-detail", schedule_id)
        return render(request, "timetabling/form.html", self.context_entry(form, conflicts, preview=preview and bool(obj), editing=bool(pk)))


class EntryDelete(TimetableMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            authorized(request.user, ScheduleEntry, "delete")
            self.schedule = get_schedule(request.user, kwargs["schedule_id"])
            self.entry = get_object_or_404(scoped(request.user, entry_queryset()).filter(schedule=self.schedule), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, schedule_id, pk):
        return render(request, "timetabling/delete.html", {"object": self.entry, "list_url": reverse("timetabling:schedules-detail", args=[schedule_id])})

    def post(self, request, schedule_id, pk):
        remove_entry(user=request.user, schedule_id=schedule_id, pk=pk)
        messages.success(request, "Meeting removed; audit history retained.")
        return redirect("timetabling:schedules-detail", schedule_id)


class ValidateView(TimetableMixin, View):
    action = "validate"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            get_schedule(request.user, kwargs["pk"], "validate")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        report = validate_schedule(user=request.user, schedule_id=pk)
        messages.info(request, f"Validation finished: {report['errors']} errors, {report['warnings']} warnings. Validated is not approved.")
        return redirect("timetabling:conflicts", pk)


class ClosureDelete(TimetableMixin, View):
    section = "closures"
    action = "delete"

    def get(self, request, pk):
        obj = get_object_or_404(scoped(request.user, RoomUnavailability.objects.all()), pk=pk)
        return render(request, "timetabling/delete.html", {"object": obj, "list_url": reverse("timetabling:closures")})

    def post(self, request, pk):
        remove_closure(user=request.user, pk=pk)
        messages.success(request, "Room restriction removed. Revalidate affected schedules.")
        return redirect("timetabling:closures")
