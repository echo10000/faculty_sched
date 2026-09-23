from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

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
                     SchedulingConfiguration, ScheduleGenerationRun)
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
    "schedules": (Schedule, ScheduleForm, "Manual schedules"),
    "sections": (ClassSection, SectionForm, "Class sections"),
    "requirements": (OfferingRequirement, RequirementForm, "Offering requirements"),
    "closures": (RoomUnavailability, ClosureForm, "Room unavailability"),
    "meeting-requirements": (AssignmentMeetingRequirement, MeetingRequirementForm, "Assignment meeting requirements"),
    "configurations": (SchedulingConfiguration, SchedulingConfigurationForm, "Scheduling configurations"),
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
        if self.section == "meeting-requirements":
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
            "filter_form": form, "entries": entries, "week": week_columns(entries), "mode": self.mode, "report": report,
            "can_generate": _can_generate(request.user), "can_view_generation_runs": _can_view_runs(request.user)})


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
        raw_schedule = (request.POST if request.method == "POST" else request.GET).get("schedule")
        if raw_schedule:
            try:
                selected = get_schedule(request.user, int(raw_schedule), action="change")
            except (TypeError, ValueError):
                raise Http404("Invalid schedule")
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
                    counts["remaining_demands"] = len(prepared.input_summary.get("remaining_demands", []))
                    counts["candidate_positions"] = prepared.input_summary.get("candidate_count", 0)
        return {"form": form, "selected_schedule": selected,
                "configuration": configuration, "counts": counts, "readiness_issues": readiness_issues,
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
    "ACCEPTED": "Generated meetings were written to the draft schedule; the schedule is not approved.",
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


class GenerationRunDetail(ProtectedViewMixin, View):
    permission = "timetabling.view_schedulegenerationrun"

    def get(self, request, pk):
        run = get_generation_run(request.user, pk)
        week, display_warnings = _proposal_week(request.user, run)
        diagnostics = [d for d in run.diagnostics if type(d) is dict and
                       type(d.get("message")) is str and d.get("severity") in ("ERROR", "WARNING")]
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
        config_rows = [(label, snapshot.get(key, "Unavailable")) for key, label in (
            ("earliest_start", "Earliest start"), ("latest_end", "Latest end"),
            ("slot_increment_minutes", "Slot minutes"), ("solver_time_limit_seconds", "Time limit (seconds)"),
            ("random_seed", "Random seed"), ("worker_count", "Workers"))]
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
        })


class GenerationTerminalView(ProtectedViewMixin, View):
    permission = "timetabling.generate_schedule"
    http_method_names = ["post"]

    def operation(self, *, user, run_id):
        raise NotImplementedError

    def post(self, request, pk):
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
