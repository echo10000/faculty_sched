from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from core.views import ProtectedViewMixin
from accounts.permissions import require_access
from workloads.calculation import calculate_workload
from workloads.models import FacultyAvailability
from workloads.selectors import accessible_terms
from workloads.models import FacultySubjectAssignment
from workloads.selectors import scoped_records
from resources.selectors import available_resources
from scheduling.models import Room
from .models import (Schedule, ScheduleEntry, ClassSection, OfferingRequirement,
                     RoomUnavailability, AssignmentMeetingRequirement,
                     SchedulingConfiguration, ScheduleGenerationRun, ActiveSchedule)
from .forms import (ScheduleForm, SectionForm, RequirementForm, ClosureForm, EntryForm,
                    TimetableFilter, MeetingRequirementForm, SchedulingConfigurationForm,
                    GenerationRequestForm)
from .queries import authorized, scoped, get_schedule, entry_queryset
from .generation_inputs import (GenerationOverrides, generation_run_queryset,
                                get_generation_run, prepare_generation_input,
                                require_generation_access)
from .generation import (InvalidRunTransition, request_generation,
                         accept_generation, discard_generation)
from .locking import scheduling_lock
from .mutations import save_record, save_entry, remove_entry, remove_closure, validate_schedule, effective_status
from .conflicts import get_schedule_conflicts, summarize
from .timetable import filtered_entries, week_columns

SPECS = {
    "schedules": (Schedule, ScheduleForm, "Manage schedules"),
    "sections": (ClassSection, SectionForm, "Class sections"),
    "requirements": (OfferingRequirement, RequirementForm, "Class scheduling requirements"),
    "closures": (RoomUnavailability, ClosureForm, "Blocked room times"),
    "meeting-requirements": (AssignmentMeetingRequirement, MeetingRequirementForm, "Class meeting requirements"),
    "configurations": (SchedulingConfiguration, SchedulingConfigurationForm, "Schedule generation settings"),
}

SECTION_HELP = {
    "schedules": ("View and manage schedule versions for the selected term and department.", "Create schedule", "No schedules match this selection."),
    "sections": ("Organize the student groups that need classes this term.", "Add class section", "No class sections match this selection."),
    "requirements": ("Connect offered classes with sections and the room needs used in scheduling.", "Add class scheduling requirement", "No class scheduling requirements match this selection."),
    "closures": ("Mark periods when rooms cannot be assigned to classes.", "Add blocked room time", "No blocked room times match this selection."),
    "meeting-requirements": ("Specify how often and how long each assigned class should meet. CampusLoad uses this when building a timetable.", "Add meeting requirement", "No class meeting requirements match this selection."),
    "configurations": ("Choose the days, hours, and rules used when CampusLoad generates a schedule.", "Add generation settings", "No schedule generation settings match this selection."),
}


class PrepareScheduleView(ProtectedViewMixin, TemplateView):
    """A permission-filtered guide to existing scheduling screens."""

    permission = "timetabling.view_schedule"
    template_name = "timetabling/prepare.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            require_access(request.user, "academics.view_academicterm")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        terms = accessible_terms(user)
        raw_term = self.request.GET.get("academic_term")
        if raw_term:
            try:
                term = get_object_or_404(terms, pk=int(raw_term))
            except ValueError:
                raise Http404("Invalid term")
        else:
            term = accessible_terms(user, active=True).first() or terms.first()
        schedules = scoped(user, Schedule.objects.filter(academic_term=term).select_related("department", "academic_term")) if term else Schedule.objects.none()
        raw_schedule = self.request.GET.get("schedule")
        if raw_schedule:
            try:
                schedule = get_object_or_404(schedules, pk=int(raw_schedule))
            except ValueError:
                raise Http404("Invalid schedule")
        else:
            schedule = schedules.order_by("-updated_at", "-pk").first()
        editable = schedule and schedule.status in ("draft", "validated", "needs_revision")
        display_status = effective_status(schedule) if schedule and schedule.status in ("draft", "validated") else schedule.status if schedule else None
        context.update(terms=terms, term=term, schedules=schedules.order_by("department__name", "name"), schedule=schedule,
            editable=editable, display_status=display_status,
            display_status_label=dict(Schedule.Status.choices).get(display_status, "Unknown"),
            can_generate=bool(editable and _can_generate(user)),
            can_check=bool(editable and user.has_perm("timetabling.validate_schedule")),
            can_submit=bool(editable and user.has_perm("timetabling.finalize_schedule") and user.has_perm("timetabling.change_schedule")),
            can_add_meeting=bool(editable and user.has_perm("timetabling.add_scheduleentry")))
        return context


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
        item_name = SECTION_HELP[self.section][1].removeprefix("Add ").removeprefix("Create ")
        return {"title": title, "section": self.section, "list_url": reverse(f"timetabling:{self.section}"),
            "description": SECTION_HELP[self.section][0], "add_label": SECTION_HELP[self.section][1],
            "empty_message": SECTION_HELP[self.section][2],
            "save_label": "Save " + item_name.lower(), "edit_label": "Edit " + item_name.lower(),
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
        if self.section == "schedules":
            form.fields["version_status"] = forms.ChoiceField(required=False, label="Version status", choices=[
                ("", "All versions"), ("editable", "Drafts and revisions"), ("under_review", "Under review"), ("approved", "Published"),
            ], widget=forms.Select(attrs={"class": "form-select"}))
        qs = scoped(request.user, model.objects.all())
        if self.section == "schedules":
            qs = qs.select_related("family", "academic_term", "department")
        elif self.section == "meeting-requirements":
            qs = qs.select_related("assignment__faculty", "assignment__subject_offering__subject",
                                   "assignment__subject_offering__academic_term", "assignment__subject_offering__department")
        elif self.section == "configurations":
            qs = qs.select_related("academic_term", "department")
        if form.is_valid():
            data = form.cleaned_data
            prefix = {"requirements": "subject_offering__",
                      "meeting-requirements": "assignment__subject_offering__"}.get(self.section, "")
            for field in ("academic_term", "department", "room", "day_of_week"):
                if data.get(field):
                    qs = qs.filter(**{prefix + field: data[field]})
            if self.section == "schedules" and data.get("version_status"):
                statuses = ("draft", "validated", "needs_revision") if data["version_status"] == "editable" else (data["version_status"],)
                qs = qs.filter(status__in=statuses)
            if data.get("q"):
                lookup = {"schedules": "name", "sections": "code", "requirements": "subject_offering__subject__code",
                          "meeting-requirements": "assignment__subject_offering__subject__code",
                          "configurations": "department__name"}.get(self.section)
                qs = qs.filter(**{lookup + "__icontains": data["q"]}) if lookup else qs.none()
        else:
            qs = qs.none()
        if not qs.ordered:
            qs = qs.order_by("pk")
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        official_ids = set(ActiveSchedule.objects.filter(
            schedule_id__in=[obj.pk for obj in page if isinstance(obj, Schedule)],
        ).values_list("schedule_id", flat=True)) if self.section == "schedules" else set()
        for obj in page:
            owner = getattr(obj, "department", None) or getattr(obj, "college", None)
            obj.owner_label = owner.name if owner else "Institution"
            obj.detail_url = reverse("timetabling:schedules-detail", args=[obj.pk]) if self.section == "schedules" else reverse(f"timetabling:{self.section}-edit", args=[obj.pk])
            if isinstance(obj, Schedule):
                obj.display_status = effective_status(obj) if obj.status in ("draft", "validated") else obj.status
                obj.is_official = obj.pk in official_ids
        query = request.GET.copy()
        query.pop("page", None)
        return render(request, "timetabling/list.html", {**self.context(), "page_obj": page, "page_query": query.urlencode(), "filter_form": form})


class RecordForm(TimetableMixin, View):
    action = "add"

    def get_original(self, request, pk):
        model = SPECS[self.section][0]
        original = get_object_or_404(scoped(request.user, model.objects.all()), pk=pk) if pk else None
        if isinstance(original, Schedule) and original.status not in ("draft", "validated", "needs_revision"):
            raise PermissionDenied("This schedule version is read-only. Create a revision to edit it.")
        return original

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
        editable = schedule.status in ("draft", "validated", "needs_revision")
        active = ActiveSchedule.objects.filter(
            academic_term_id=schedule.academic_term_id,
            department_id=schedule.department_id,
        ).first()
        is_official = bool(active and active.schedule_id == schedule.pk)
        form = TimetableFilter(request.GET, user=request.user)
        entries = filtered_entries(request.user, schedule, form)
        report = summarize(get_schedule_conflicts(
            schedule, user=request.user,
            excluded_schedule_ids=(active.schedule_id,) if active and not is_official else (),
        ), schedule.entries.count()) if self.mode in ("detail", "conflicts", "unscheduled") else None
        # Presentation reads reuse the canonical validator and scoped records.
        all_entries = scoped(request.user, entry_queryset()).filter(schedule=schedule)
        summary = {"meetings": all_entries.count(), "scheduled_assignments": all_entries.values("assignment_id").distinct().count()}
        unscheduled = None
        if request.user.has_perm("workloads.view_facultysubjectassignment"):
            assignments = scoped_records(request.user, FacultySubjectAssignment.objects.filter(
                subject_offering__academic_term=schedule.academic_term,
                subject_offering__department=schedule.department,
            )).select_related("faculty", "subject_offering__subject")
            unscheduled = assignments.exclude(pk__in=all_entries.values("assignment_id"))
            summary["without_meetings"] = unscheduled.count()
        requirement_findings = [item for item in report["conflicts"] if item.code in (
            "MEETING_REQUIREMENT_MISSING", "MEETING_REQUIREMENT_COUNT", "MEETING_REQUIREMENT_DURATION", "MEETING_HOURS_WARNING",
        )] if report else []
        conflict_groups = []
        if report:
            for severity, label in (("ERROR", "Blocking errors"), ("WARNING", "Warnings"), ("INFO", "Information")):
                findings = [item for item in report["conflicts"] if item.severity == severity]
                if findings:
                    conflict_groups.append({"label": label, "findings": sorted(findings, key=lambda item: item.code)})
        display_status = effective_status(schedule) if editable and schedule.status in ("draft", "validated") else schedule.status
        return render(request, "timetabling/detail.html", {**self.context(), "schedule": schedule, "status": display_status,
            "filter_form": form, "entries": entries, "week": week_columns(entries), "mode": self.mode, "report": report,
            "can_generate": editable and _can_generate(request.user), "can_view_generation_runs": _can_view_runs(request.user),
            "can_edit_version": editable, "can_submit_action": editable and request.user.has_perm("timetabling.finalize_schedule") and request.user.has_perm("timetabling.change_schedule"),
            "is_official": is_official, "summary": summary, "unscheduled_assignments": unscheduled,
            "requirement_findings": requirement_findings, "conflict_groups": conflict_groups})


class EntryEditor(TimetableMixin, View):
    action = "view"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            authorized(request.user, ScheduleEntry, "change" if kwargs.get("pk") else "add")
            self.schedule = get_schedule(request.user, kwargs["schedule_id"])
            if self.schedule.status not in ("draft", "validated", "needs_revision"):
                raise PermissionDenied("This schedule version is read-only. Create a revision to edit it.")
        return super().dispatch(request, *args, **kwargs)

    def original(self, request, pk):
        return get_object_or_404(scoped(request.user, entry_queryset()).filter(schedule=self.schedule), pk=pk) if pk else None

    def context_entry(self, form, conflicts=None, preview=False, editing=False):
        context = {**self.context(), "title": "Manual meeting", "schedule": self.schedule, "form": form, "is_entry": True,
            "description": "Add a class meeting to this schedule and check it for conflicts before saving.",
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
            if self.schedule.status not in ("draft", "validated", "needs_revision"):
                raise PermissionDenied("This schedule version is read-only. Create a revision to edit it.")
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
        messages.info(request, f"Validation finished: {report['errors']} errors, {report['warnings']} warnings. Validation does not publish a schedule.")
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


def _can_generate(user, strategy=ScheduleGenerationRun.Strategy.FILL_GAPS):
    try:
        require_generation_access(user, strategy)
    except PermissionDenied:
        return False
    return True


def _can_view_runs(user):
    try:
        require_access(user, "timetabling.view_schedulegenerationrun")
        require_access(user, "academics.view_academicterm")
    except PermissionDenied:
        return False
    return True


class GeneratorView(ProtectedViewMixin, View):
    permission = "timetabling.generate_schedule"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            require_generation_access(request.user, ScheduleGenerationRun.Strategy.FILL_GAPS)
        return super().dispatch(request, *args, **kwargs)

    def _context(self, request, form):
        selected = None
        configuration = None
        counts = None
        readiness_issues = None
        configuration_rows = []
        input_rows = []
        raw_schedule = (request.POST if request.method == "POST" else request.GET).get("schedule")
        if raw_schedule:
            try:
                selected = get_schedule(request.user, int(raw_schedule), action="change")
            except (TypeError, ValueError):
                raise Http404("Invalid schedule")
            if selected.status not in ("draft", "validated", "needs_revision"):
                raise PermissionDenied("This schedule version is read-only. Create a revision before generating.")
            configuration = scoped(request.user, SchedulingConfiguration.objects.filter(
                academic_term_id=selected.academic_term_id, department_id=selected.department_id,
            )).first()
            counts = {
                "requirements": scoped(request.user, AssignmentMeetingRequirement.objects.filter(
                    assignment__subject_offering__academic_term_id=selected.academic_term_id,
                    assignment__subject_offering__department_id=selected.department_id,
                )).count(),
                "entries": scoped(request.user, ScheduleEntry.objects.filter(schedule=selected)).count(),
            }
            if request.method == "GET" and configuration:
                strategy = request.GET.get("strategy") or ScheduleGenerationRun.Strategy.FILL_GAPS
                if strategy in ScheduleGenerationRun.Strategy.values and _can_generate(request.user, strategy):
                    with transaction.atomic():
                        scheduling_lock()
                        prepared = prepare_generation_input(
                            user=request.user, schedule_id=selected.pk,
                            strategy=strategy, overrides=GenerationOverrides(),
                        )
                    readiness_issues = prepared.issues
                    summary = prepared.input_summary
                    snapshot = prepared.configuration_snapshot
                    input_rows = [(label, len(summary.get(key, []))) for key, label in (
                        ("offering_ids", "Offerings"), ("assignment_ids", "Assignments"),
                        ("meeting_requirement_ids", "Meeting requirements"),
                        ("section_ids", "Sections"), ("eligible_room_ids", "Eligible rooms"),
                        ("remaining_demands", "Remaining demands"))]
                    input_rows += [(label, summary.get(key, 0)) for key, label in (
                        ("retained_entry_count", "Retained meetings"),
                        ("replace_entry_count", "Replaceable meetings"),
                        ("availability_count", "Availability records"),
                        ("closure_count", "Room closures"),
                        ("peer_occupancy_count", "Protected peer occupancy"),
                        ("candidate_count", "Candidate positions"))]
                    configuration_rows = _configuration_rows(snapshot)
        return {"form": form, "selected_schedule": selected,
                "configuration": configuration, "counts": counts, "readiness_issues": readiness_issues,
                "configuration_rows": configuration_rows, "input_rows": input_rows,
                "can_view_generation_runs": _can_view_runs(request.user),
                "can_replace": _can_generate(request.user, ScheduleGenerationRun.Strategy.REPLACE_UNLOCKED)}

    def get(self, request):
        initial = {name: request.GET.get(name) for name in ("academic_term", "schedule", "strategy")
                   if request.GET.get(name)}
        initial.setdefault("strategy", ScheduleGenerationRun.Strategy.FILL_GAPS)
        form = GenerationRequestForm(user=request.user, initial=initial)
        return render(request, "timetabling/generator.html", self._context(request, form))

    def post(self, request):
        form = GenerationRequestForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, "timetabling/generator.html", self._context(request, form))
        try:
            run = request_generation(user=request.user, schedule_id=form.cleaned_data["schedule"].pk,
                                     strategy=form.cleaned_data["strategy"],
                                     overrides=form.cleaned_data["overrides"])
        except InvalidRunTransition:
            return HttpResponse("Generation request changed state. Refresh and try again.", status=409)
        return redirect("timetabling:generation-run-detail", pk=run.pk)


class GenerationRunList(ProtectedViewMixin, View):
    permission = "timetabling.view_schedulegenerationrun"

    def get(self, request):
        runs = generation_run_queryset(request.user).order_by("-requested_at", "-pk")
        page = Paginator(runs, 20).get_page(request.GET.get("page"))
        return render(request, "timetabling/generation_run_list.html", {
            "page_obj": page, "can_generate": _can_generate(request.user),
        })


STATUS_EXPLANATIONS = {
    "PENDING": "The request is recorded; solving has not started.",
    "RUNNING": "The solver is working within the configured bound.",
    "PROPOSAL_READY": "A complete validated proposal is ready; no schedule meetings have changed.",
    "ACCEPTED": "Generated meetings were written to the draft schedule; the schedule is not published.",
    "DISCARDED": "The proposal was discarded and did not change the schedule.",
    "INPUT_INVALID": "Source or configuration data was not ready, so the solver did not run.",
    "INFEASIBLE": "No complete timetable was found under the captured constraints.",
    "STALE": "Source data changed after generation; regenerate before accepting.",
    "VALIDATION_FAILED": "The result failed validation and was not applied.",
    "FAILED": "Generation failed and no proposal was applied.",
}

SOLVER_EXPLANATIONS = {
    "OPTIMAL": "A complete solution was found and proven optimal.",
    "FEASIBLE": "A complete solution was found within the time bound; optimality was not proven.",
    "INFEASIBLE": "No complete solution satisfied the constraints.",
    "MODEL_INVALID": "The solver rejected its model.",
    "UNKNOWN": "No complete solution was returned within the time bound.",
}


def _proposal_week(user, run):
    from .intervals import DAYS
    rows = [row for row in run.proposed_meetings if type(row) is dict] if type(run.proposed_meetings) is list else []
    assignment_ids = {row.get("assignment_id") for row in rows if type(row.get("assignment_id")) is int}
    room_ids = {row.get("room_id") for row in rows if type(row.get("room_id")) is int}
    assignments = {obj.pk: obj for obj in scoped_records(
        user, FacultySubjectAssignment.objects.filter(pk__in=assignment_ids,
            subject_offering__department_id=run.department_id,
            subject_offering__academic_term_id=run.academic_term_id,
        )).select_related("faculty", "subject_offering__subject")}
    rooms = {obj.pk: obj for obj in available_resources(user, Room).filter(pk__in=room_ids)}
    requirements = {obj.subject_offering_id: obj.section.code for obj in scoped(
        user, OfferingRequirement.objects.filter(
            subject_offering_id__in=[a.subject_offering_id for a in assignments.values()],
        )).select_related("section")}
    days = {number: [] for number, _ in DAYS}
    warnings = []
    for row in rows:
        assignment_id = row.get("assignment_id")
        room_id = row.get("room_id")
        assignment = assignments.get(assignment_id) if type(assignment_id) is int else None
        room = rooms.get(room_id) if type(room_id) is int else None
        day = row.get("day_of_week")
        start, end = row.get("start_time"), row.get("end_time")
        if not assignment or not room or type(day) is not int or day not in days or not all(
            type(value) is str and len(value) == 5 and value[2] == ":" and value[:2].isdigit() and value[3:].isdigit()
            for value in (start, end)
        ):
            warnings.append("One proposal meeting could not be displayed because its source record is unavailable.")
            continue
        days[day].append({
            "start_time": start, "end_time": end,
            "subject": assignment.subject_offering.subject.code,
            "offering": assignment.subject_offering.code,
            "section": requirements.get(assignment.subject_offering_id, "Section unavailable"),
            "faculty": str(assignment.faculty), "room": room.code,
            "meeting_type": row.get("meeting_type") if row.get("meeting_type") in ("lecture", "laboratory") else "Meeting",
            "occurrence": row.get("occurrence_index") if type(row.get("occurrence_index")) is int else None,
        })
    for meetings in days.values():
        meetings.sort(key=lambda item: (item["start_time"], item["end_time"], item["subject"], item["occurrence"] or 0))
    return [{"number": number, "day": label, "meetings": days[number]} for number, label in DAYS], warnings


def _configuration_rows(snapshot):
    if type(snapshot) is not dict:
        return []
    rows = [(label, snapshot.get(key, "Unavailable")) for key, label in (
        ("allowed_weekdays", "Allowed weekdays"),
        ("earliest_start", "Earliest start"), ("latest_end", "Latest end"),
        ("slot_increment_minutes", "Slot minutes"),
        ("solver_time_limit_seconds", "Time limit (seconds)"),
        ("random_seed", "Random seed"), ("worker_count", "Workers"),
        ("faculty_preference_weight", "Faculty preference weight"),
        ("faculty_gap_weight", "Faculty gap weight"),
        ("section_gap_weight", "Section gap weight"),
        ("meeting_distribution_weight", "Meeting distribution weight"),
        ("room_fit_weight", "Room fit weight"),
    )]
    return [(label, ", ".join(str(item) for item in value) if type(value) is list
             else value) for label, value in rows]


def _visible_run(user, pk):
    if _can_view_runs(user):
        return get_generation_run(user, pk)
    require_generation_access(user, ScheduleGenerationRun.Strategy.FILL_GAPS)
    queryset = scoped(user, ScheduleGenerationRun.objects.select_related(
        "schedule", "academic_term", "department", "requested_by",
    )).filter(requested_by=user)
    return get_object_or_404(queryset, pk=pk)


class GenerationRunDetail(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not _can_view_runs(request.user) and not _can_generate(request.user):
            raise PermissionDenied("Generation run access is required.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        run = _visible_run(request.user, pk)
        week, display_warnings = _proposal_week(request.user, run)
        raw_diagnostics = run.diagnostics if type(run.diagnostics) is list else []
        diagnostics = [d for d in raw_diagnostics if type(d) is dict and
                       type(d.get("code")) is str and type(d.get("message")) is str
                       and type(d.get("severity")) is str and d["severity"] in ("ERROR", "WARNING")]
        penalties = run.penalty_breakdown if type(run.penalty_breakdown) is dict else {}
        statistics = run.solver_statistics if type(run.solver_statistics) is dict else {}
        summary = run.input_summary if type(run.input_summary) is dict else {}
        snapshot = run.configuration_snapshot if type(run.configuration_snapshot) is dict else {}
        penalty_rows = [(label, penalties.get(key, 0)) for key, label in (
            ("faculty_preference", "Faculty preference"), ("faculty_gap", "Faculty gaps"),
            ("section_gap", "Section gaps"), ("meeting_distribution", "Meeting distribution"),
            ("room_fit", "Room fit"))]
        source_rows = [(label, summary.get(key, 0)) for key, label in (
            ("current_entry_count", "Current meetings"), ("retained_entry_count", "Retained meetings"),
            ("replace_entry_count", "Replaceable meetings"), ("peer_occupancy_count", "Protected peer occupancy"),
            ("candidate_count", "Candidate positions"))]
        config_rows = _configuration_rows(snapshot)
        stat_rows = [(label, statistics.get(key, "Unavailable")) for key, label in (
            ("wall_time_seconds", "Solver wall time (seconds)"), ("branches", "Branches"),
            ("conflicts", "Conflicts"), ("candidate_count", "Candidates"), ("variable_count", "Variables"))]
        return render(request, "timetabling/generation_run_detail.html", {
            "run": run, "status_explanation": STATUS_EXPLANATIONS.get(run.status, "Status unavailable."),
            "solver_explanation": SOLVER_EXPLANATIONS.get(run.solver_status, "The solver has not started."),
            "proposal_week": week, "proposal_display_warnings": display_warnings,
            "diagnostic_errors": [d["message"] for d in diagnostics if d["severity"] == "ERROR"],
            "diagnostic_warnings": [d["message"] for d in diagnostics if d["severity"] == "WARNING"],
            "penalty_rows": penalty_rows, "source_rows": source_rows,
            "configuration_rows": config_rows, "statistic_rows": stat_rows,
            "objective_available": run.objective_value is not None,
            "bound_available": run.best_bound is not None,
            "runtime_available": run.runtime_seconds is not None,
            "can_finalize": run.status == "PROPOSAL_READY" and _can_generate(request.user, run.strategy),
            "can_view_generation_runs": _can_view_runs(request.user),
        })


class GenerationTerminalView(ProtectedViewMixin, View):
    permission = "timetabling.generate_schedule"
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            require_generation_access(request.user, ScheduleGenerationRun.Strategy.FILL_GAPS)
        return super().dispatch(request, *args, **kwargs)

    def operation(self, *, user, run_id):
        raise NotImplementedError

    def post(self, request, pk):
        if not _can_view_runs(request.user):
            _visible_run(request.user, pk)
        try:
            run = self.operation(user=request.user, run_id=pk)
        except InvalidRunTransition:
            return HttpResponse("This proposal is no longer ready. Refresh the run before acting.", status=409)
        if run.status == ScheduleGenerationRun.Status.ACCEPTED:
            messages.success(request, "Generated meetings were accepted into the draft schedule.")
        elif run.status == ScheduleGenerationRun.Status.DISCARDED:
            messages.info(request, "The proposal was discarded.")
        else:
            messages.warning(request, STATUS_EXPLANATIONS.get(run.status, "The proposal was not applied."))
        return redirect("timetabling:generation-run-detail", pk=run.pk)


class GenerationAcceptView(GenerationTerminalView):
    def operation(self, *, user, run_id):
        return accept_generation(user=user, run_id=run_id)


class GenerationDiscardView(GenerationTerminalView):
    def operation(self, *, user, run_id):
        return discard_generation(user=user, run_id=run_id)
