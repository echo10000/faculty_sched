from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.views.generic import TemplateView

from scheduling.models import Term
from scheduling.views import build_timetable_grid

from .models import Faculty
from .services import compute_faculty_load


class FacultyDashboardView(LoginRequiredMixin, TemplateView):
    """Read-only self-service timetable and load summary for a faculty user."""

    template_name = "faculty/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            faculty = self.request.user.faculty
        except Faculty.DoesNotExist as exc:
            raise PermissionDenied("This account is not linked to a faculty record.") from exc
        active_term = Term.objects.filter(is_active=True).first()
        if active_term is None:
            raise Http404("There is no active term.")
        assignments = faculty.assignments.filter(term=active_term)
        context.update({
            "faculty": faculty,
            "term": active_term,
            "load": compute_faculty_load(faculty, active_term),
            "days": ("MON", "TUE", "WED", "THU", "FRI", "SAT"),
            "grid_rows": build_timetable_grid(assignments),
        })
        return context
