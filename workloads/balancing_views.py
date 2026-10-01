"""Review-only workload recommendation pages and explicit terminal actions."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from accounts.permissions import require_access
from core.views import ProtectedViewMixin

from .balancing_forms import BalancingRequestForm
from .balancing_service import (
    BalancingError, accept_balancing_run, discard_balancing_run,
    request_balancing_run, scoped_balancing_run, scoped_balancing_runs,
)
from .models import WorkloadRecommendationRun


GENERATE_PERMISSION = "workloads.generate_workloadrecommendation"
VIEW_PERMISSION = "workloads.view_workloadrecommendationrun"

STATUS_EXPLANATIONS = {
    "PENDING": "The request is recorded; no assignments have changed.",
    "PROPOSAL_READY": "A complete proposal is ready for review; no assignments have changed.",
    "ACCEPTED": "The proposed faculty assignments were applied. Timetables were not regenerated.",
    "DISCARDED": "The proposal was discarded without changing assignments.",
    "INPUT_INVALID": "The source data was not ready, so no recommendation was applied.",
    "INFEASIBLE": "No complete recommendation satisfied the captured hard constraints.",
    "STALE": "Source data changed after generation. Request a new recommendation before accepting.",
    "VALIDATION_FAILED": "The proposal failed acceptance validation and was not applied.",
    "FAILED": "Generation failed and no assignments changed.",
}

SOLVER_EXPLANATIONS = {
    "OPTIMAL": "The solver proved the complete recommendation optimal for this model.",
    "FEASIBLE": "The solver found a complete recommendation within its time limit; optimality was not proven.",
    "INFEASIBLE": "No complete recommendation satisfied the hard constraints.",
    "MODEL_INVALID": "The solver rejected its model.",
    "UNKNOWN": "The solver did not return a complete recommendation within its time limit.",
}


def _can_generate(user):
    try:
        require_access(user, GENERATE_PERMISSION)
        require_access(user, "academics.view_academicterm")
    except PermissionDenied:
        return False
    return True


def _can_view_history(user):
    try:
        require_access(user, VIEW_PERMISSION)
        require_access(user, "academics.view_academicterm")
    except PermissionDenied:
        return False
    return True


class BalancingRequestView(ProtectedViewMixin, View):
    permission = GENERATE_PERMISSION

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            require_access(request.user, "academics.view_academicterm")
        return super().dispatch(request, *args, **kwargs)

    def _context(self, request, form):
        return {"form": form, "can_view_history": _can_view_history(request.user)}

    def get(self, request):
        initial = {name: request.GET[name] for name in ("academic_term", "department") if request.GET.get(name)}
        form = BalancingRequestForm(user=request.user, initial=initial)
        return render(request, "workloads/balancing_request.html", self._context(request, form))

    def post(self, request):
        form = BalancingRequestForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, "workloads/balancing_request.html", self._context(request, form))
        try:
            run = request_balancing_run(
                user=request.user,
                academic_term=form.cleaned_data["academic_term"],
                department=form.cleaned_data["department"],
            )
        except BalancingError as error:
            form.add_error(None, str(error))
            return render(request, "workloads/balancing_request.html", self._context(request, form))
        return redirect("workloads:balancing-run-detail", pk=run.pk)


class BalancingRunListView(ProtectedViewMixin, View):
    permission = VIEW_PERMISSION

    def get(self, request):
        runs = scoped_balancing_runs(request.user).order_by("-created_at", "-pk")
        page = Paginator(runs, 20).get_page(request.GET.get("page"))
        return render(request, "workloads/balancing_run_list.html", {
            "page_obj": page, "can_generate": _can_generate(request.user),
        })


class BalancingRunDetailView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not _can_view_history(request.user) and not _can_generate(request.user):
            raise PermissionDenied("Workload recommendation access is required.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        run = scoped_balancing_run(request.user, pk)
        raw_proposal = run.proposed_assignments if type(run.proposed_assignments) is list else []
        proposal = [row for row in raw_proposal if type(row) is dict]
        raw_comparison = run.comparison if type(run.comparison) is dict else {}
        raw_faculty = raw_comparison.get("faculty", [])
        faculty_rows = [row for row in raw_faculty if type(row) is dict] if type(raw_faculty) is list else []
        summary = raw_comparison.get("summary", {})
        summary = summary if type(summary) is dict else {}
        raw_diagnostics = run.diagnostics if type(run.diagnostics) is list else []
        diagnostics = [row for row in raw_diagnostics if type(row) is dict and
                       row.get("severity") in ("ERROR", "WARNING") and type(row.get("message")) is str]
        source = run.input_summary if type(run.input_summary) is dict else {}
        statistics = run.solver_stats if type(run.solver_stats) is dict else {}
        return render(request, "workloads/balancing_run_detail.html", {
            "run": run,
            "status_explanation": STATUS_EXPLANATIONS.get(run.status, "Status unavailable."),
            "solver_explanation": SOLVER_EXPLANATIONS.get(run.raw_solver_status, "The solver has not started."),
            "proposal": proposal,
            "faculty_rows": faculty_rows,
            "summary": summary,
            "source": source,
            "statistics": statistics,
            "diagnostic_errors": [item["message"] for item in diagnostics if item["severity"] == "ERROR"],
            "diagnostic_warnings": [item["message"] for item in diagnostics if item["severity"] == "WARNING"],
            "can_finalize": run.status == WorkloadRecommendationRun.Status.PROPOSAL_READY and _can_generate(request.user),
            "can_view_history": _can_view_history(request.user),
        })


class BalancingTerminalView(ProtectedViewMixin, View):
    permission = GENERATE_PERMISSION
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            require_access(request.user, "academics.view_academicterm")
        return super().dispatch(request, *args, **kwargs)

    def operation(self, *, user, run):
        raise NotImplementedError

    def post(self, request, pk):
        run = scoped_balancing_run(request.user, pk)
        try:
            run = self.operation(user=request.user, run=run)
        except BalancingError as error:
            return HttpResponse(str(error), status=409)
        if run.status == WorkloadRecommendationRun.Status.ACCEPTED:
            messages.success(request, "The recommendation was accepted; assignments changed and timetables were not regenerated.")
        elif run.status == WorkloadRecommendationRun.Status.DISCARDED:
            messages.info(request, "The recommendation was discarded without changing assignments.")
        else:
            messages.warning(request, STATUS_EXPLANATIONS.get(run.status, "The recommendation was not applied."))
        return redirect("workloads:balancing-run-detail", pk=run.pk)


class BalancingAcceptView(BalancingTerminalView):
    def operation(self, *, user, run):
        return accept_balancing_run(user=user, run=run)


class BalancingDiscardView(BalancingTerminalView):
    def operation(self, *, user, run):
        return discard_balancing_run(user=user, run=run)
