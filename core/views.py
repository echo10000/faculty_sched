from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView, ListView, TemplateView

from academics.models import AcademicTerm
from accounts.permissions import department_scoped_queryset, require_access, scoped_colleges
from .dashboard_data import build_monitoring
from .models import College, Department


class ProtectedViewMixin(LoginRequiredMixin):
    permission = "core.view_dashboard"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        require_access(request.user, self.permission)
        return super().dispatch(request, *args, **kwargs)


class DashboardView(ProtectedViewMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get(self, request, *args, **kwargs):
        self.selected_term = None
        self.term_options = []
        if request.user.has_perm("academics.view_academicterm"):
            from workloads.selectors import accessible_terms

            self.term_options = accessible_terms(request.user)
            selected = request.GET.get("academic_term")
            if selected is not None:
                try:
                    term_id = int(selected)
                except (ValueError, TypeError):
                    return HttpResponseBadRequest("Select a valid academic term.")
                self.selected_term = get_object_or_404(self.term_options, pk=term_id)
            else:
                self.selected_term = accessible_terms(request.user, active=True).first() or self.term_options.first()
        elif "academic_term" in request.GET:
            raise PermissionDenied("Academic calendar access is required.")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        from faculty.models import Faculty
        from academics.models import Subject
        from scheduling.models import Room
        from resources.selectors import scope_resources
        context["selected_term"] = self.selected_term
        context["term_options"] = self.term_options
        context["monitoring"] = build_monitoring(user, self.selected_term)
        context["resource_metrics"] = []
        if user.has_perm("workloads.view_workload") and user.has_perm("academics.view_academicterm"):
            from workloads.models import FacultySubjectAssignment
            from workloads.selectors import scoped_records
            teaching_term = self.selected_term
            context["teaching_term"] = teaching_term
            context["teaching_assignment_count"] = scoped_records(user, FacultySubjectAssignment.objects.filter(subject_offering__academic_term=teaching_term)).count() if teaching_term else None
        for model, route, label in [(Faculty, "faculty-management:list", "Faculty"), (Subject, "subjects:list", "Subjects"), (Room, "rooms:list", "Rooms")]:
            if user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}"):
                records = scope_resources(user, model.objects.all())
                count = records.count()
                context[f"{model._meta.model_name}_count"] = count
                active_count = records.filter(is_active=True).count()
                context[f"active_{model._meta.model_name}_count"] = active_count
                context["resource_metrics"].append({"label": label, "route": route, "count": count, "active": active_count})
        context["college_count"] = scoped_colleges(user, College.objects.all()).count() if user.has_perm("core.view_college") else None
        context["department_count"] = department_scoped_queryset(user, Department.objects.all(), "pk").count() if user.has_perm("core.view_department") else None
        if user.has_perm("academics.view_academicterm"):
            context["terms"] = AcademicTerm.objects.filter(is_active=True).select_related("academic_year", "semester")[:5]
            context["term_count"] = AcademicTerm.objects.filter(is_active=True).count()
        return context


class SearchListMixin:
    paginate_by = 20
    template_name = "core/organization_list.html"

    def get_queryset(self):
        queryset = super().get_queryset()
        query = self.request.GET.get("q", "").strip()[:100]
        if query:
            queryset = queryset.filter(Q(name__icontains=query) | Q(code__icontains=query))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(title=self.title, detail_route=self.detail_route, query=self.request.GET.get("q", "")[:100])
        return context


class CollegeListView(ProtectedViewMixin, SearchListMixin, ListView):
    model = College
    permission = "core.view_college"
    title = "Colleges"
    detail_route = "college-detail"

    def get_queryset(self):
        return scoped_colleges(self.request.user, super().get_queryset())


class DepartmentListView(ProtectedViewMixin, SearchListMixin, ListView):
    model = Department
    permission = "core.view_department"
    title = "Departments"
    detail_route = "department-detail"

    def get_queryset(self):
        return department_scoped_queryset(self.request.user, super().get_queryset().select_related("college"), "pk")


class CollegeDetailView(ProtectedViewMixin, DetailView):
    model = College
    permission = "core.view_college"
    template_name = "core/organization_detail.html"

    def get_queryset(self):
        return scoped_colleges(self.request.user, super().get_queryset())


class DepartmentDetailView(ProtectedViewMixin, DetailView):
    model = Department
    permission = "core.view_department"
    template_name = "core/organization_detail.html"

    def get_queryset(self):
        return department_scoped_queryset(self.request.user, super().get_queryset().select_related("college"), "pk")


class CalendarView(ProtectedViewMixin, ListView):
    model = AcademicTerm
    permission = "academics.view_academicterm"
    template_name = "core/calendar.html"
    paginate_by = 20

    def get_queryset(self):
        # Academic calendars are intentionally institution-wide reference data.
        return super().get_queryset().select_related("academic_year", "semester")
