from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from accounts.permissions import require_access
from core.views import ProtectedViewMixin
from resources.services import permission_for
from .calculation import calculate_workload
from .forms import AssignmentForm, AvailabilityForm, OfferingForm, WorkloadFilterForm
from .models import FacultyAvailability, FacultySubjectAssignment, SubjectOffering
from .operations import remove_term_record, save_term_record, scoped_object
from .selectors import accessible_terms, scoped_faculty, scoped_records

SPECS = {
    "availability": (FacultyAvailability, AvailabilityForm, "Faculty availability"),
    "offerings": (SubjectOffering, OfferingForm, "Subject offerings"),
    "assignments": (FacultySubjectAssignment, AssignmentForm, "Faculty assignments"),
}


def term_url(name, term, **kwargs):
    return reverse(name, kwargs=kwargs) + ("?" + urlencode({"academic_term": term.pk}) if term else "")


class TeachingMixin(ProtectedViewMixin):
    section = "monitor"
    action = "view"

    def dispatch(self, request, *args, **kwargs):
        self.permission = "workloads.view_workload" if self.section == "monitor" else permission_for(SPECS[self.section][0], self.action)
        if request.user.is_authenticated:
            require_access(request.user, self.permission)
            terms = accessible_terms(request.user)
            selected = request.GET.get("academic_term")
            if selected is not None:
                try:
                    self.term = get_object_or_404(terms, pk=int(selected))
                except (ValueError, TypeError):
                    return HttpResponseBadRequest("Select a valid academic term.")
            else:
                self.term = accessible_terms(request.user, active=True).first() or terms.first()
        return super().dispatch(request, *args, **kwargs)

    def context(self):
        context = {"section": self.section, "term": self.term, "title": "Workload monitoring" if self.section == "monitor" else SPECS[self.section][2], "list_url": term_url(f"workloads:{self.section}", self.term)}
        if self.section in SPECS:
            model = SPECS[self.section][0]
            context.update(can_add=self.request.user.has_perm(permission_for(model, "add")), can_change=self.request.user.has_perm(permission_for(model, "change")), can_delete=self.section != "offerings" and self.request.user.has_perm(permission_for(model, "delete")), add_url=term_url(f"workloads:{self.section}-add", self.term))
        return context


class TeachingListView(TeachingMixin, View):
    def get(self, request, faculty_pk=None):
        query = request.GET.copy()
        if self.term and "academic_term" not in query:
            query["academic_term"] = self.term.pk
        faculty = get_object_or_404(scoped_faculty(request.user), pk=faculty_pk) if faculty_pk else None
        if faculty:
            query["faculty"] = faculty.pk
        form = WorkloadFilterForm(query, user=request.user, section=self.section)
        rows = []
        if form.is_valid():
            data = form.cleaned_data
            people = scoped_faculty(request.user)
            for field, lookup in [("faculty", "pk"), ("college", "home_department__college"), ("department", "home_department"), ("employment_category", "employment_category"), ("academic_rank", "academic_rank")]:
                if data.get(field):
                    value = data[field].pk if field == "faculty" else data[field]
                    people = people.filter(**{lookup: value})
            if self.section == "monitor":
                if data["q"]:
                    people = people.filter(Q(first_name__icontains=data["q"]) | Q(last_name__icontains=data["q"]) | Q(employee_id__icontains=data["q"]))
                for person in people:
                    report = calculate_workload(person, self.term)
                    if not data["workload_status"] or data["workload_status"] == report["status"]:
                        rows.append(report)
            else:
                model = SPECS[self.section][0]
                qs = scoped_records(request.user, model.objects.all())
                if self.section == "offerings":
                    qs = qs.filter(academic_term=self.term).select_related("subject", "academic_term", "department")
                    if data.get("college"):
                        qs = qs.filter(department__college=data["college"])
                    if data.get("department"):
                        qs = qs.filter(department=data["department"])
                    if data["q"]:
                        qs = qs.filter(Q(subject__code__icontains=data["q"]) | Q(subject__title__icontains=data["q"]) | Q(code__icontains=data["q"]))
                else:
                    qs = qs.filter(faculty__in=people).select_related("faculty__home_department")
                    if self.section == "assignments":
                        qs = qs.filter(subject_offering__academic_term=self.term).select_related("subject_offering__subject", "subject_offering__academic_term")
                    else:
                        qs = qs.filter(academic_term=self.term).select_related("academic_term")
                        for field in ("day_of_week", "availability_type"):
                            if data.get(field):
                                qs = qs.filter(**{field: data[field]})
                    if data["q"]:
                        search = Q(faculty__first_name__icontains=data["q"]) | Q(faculty__last_name__icontains=data["q"]) | Q(faculty__employee_id__icontains=data["q"])
                        if self.section == "assignments":
                            search |= Q(subject_offering__subject__code__icontains=data["q"])
                        qs = qs.filter(search)
                rows = qs
        page = Paginator(rows, 20).get_page(request.GET.get("page"))
        query.pop("page", None)
        return render(request, "workloads/list.html", {**self.context(), "filter_form": form, "page_obj": page, "page_query": query.urlencode(), "faculty_context": faculty})


class TeachingDetailView(TeachingMixin, View):
    def get(self, request, pk):
        obj = scoped_object(request.user, SPECS[self.section][0], pk, self.term)
        fields = [(field.verbose_name.capitalize(), getattr(obj, field.name)) for field in obj._meta.fields if field.name not in ("id", "created_by")]
        return render(request, "workloads/record.html", {**self.context(), "object": obj, "details": fields})


class TeachingFormView(TeachingMixin, View):
    action = "add"

    def get(self, request, pk=None):
        if not self.term:
            return render(request, "workloads/form.html", self.context())
        model, form_class, _ = SPECS[self.section]
        obj = scoped_object(request.user, model, pk, self.term) if pk else None
        initial = {}
        if request.GET.get("faculty"):
            try:
                initial["faculty"] = get_object_or_404(scoped_faculty(request.user), pk=int(request.GET["faculty"])).pk
            except ValueError:
                return HttpResponseBadRequest("Invalid faculty.")
        form = form_class(user=request.user, term=self.term, instance=obj, initial=initial)
        return render(request, "workloads/form.html", {**self.context(), "form": form, "editing": bool(pk)})

    def post(self, request, pk=None):
        if not self.term:
            return HttpResponseBadRequest("Configure an academic term first.")
        model, form_class, _ = SPECS[self.section]
        preview = self.section == "assignments" and request.POST.get("intent") == "preview"
        try:
            obj, form, report = save_term_record(user=request.user, form_class=form_class, data=request.POST, term=self.term, pk=pk, preview=preview)
        except (ValidationError, IntegrityError) as error:
            original = scoped_object(request.user, model, pk, self.term) if pk else None
            form = form_class(request.POST, user=request.user, term=self.term, instance=original)
            form.is_valid()
            form.add_error(None, "; ".join(error.messages) if isinstance(error, ValidationError) else "The record conflicts with existing data. Refresh and check duplicates, time ranges or teaching shares.")
            obj, report = None, None
        if obj and not preview:
            messages.success(request, "Teaching record saved.")
            for warning in report["warnings"] if report else []:
                messages.warning(request, warning)
            return redirect(term_url(f"workloads:{self.section}", self.term)) if request.user.has_perm(permission_for(model, "view")) else redirect("home")
        return render(request, "workloads/form.html", {**self.context(), "form": form, "editing": bool(pk), "preview": report})


class TeachingDeleteView(TeachingMixin, View):
    action = "delete"

    def get(self, request, pk):
        obj = scoped_object(request.user, SPECS[self.section][0], pk, self.term)
        return render(request, "workloads/delete.html", {**self.context(), "object": obj})

    def post(self, request, pk):
        from django.db.models.deletion import ProtectedError
        try:
            remove_term_record(user=request.user, model=SPECS[self.section][0], pk=pk, term=self.term)
        except ProtectedError:
            obj = scoped_object(request.user, SPECS[self.section][0], pk, self.term)
            return render(request, "workloads/delete.html", {**self.context(), "object": obj,
                "removal_error": "This teaching record is used by a timetable. Remove its timetable meetings first."})
        messages.success(request, "Teaching record removed; its audit history is preserved.")
        return redirect(term_url(f"workloads:{self.section}", self.term)) if request.user.has_perm(permission_for(SPECS[self.section][0], "view")) else redirect("home")


class FacultyWorkloadView(TeachingMixin, View):
    def get(self, request, pk):
        faculty = get_object_or_404(scoped_faculty(request.user), pk=pk)
        query = {"academic_term": self.term.pk} if self.term else {}
        form = WorkloadFilterForm(query, user=request.user, section="monitor")
        form.fields = {"academic_term": form.fields["academic_term"]}
        availability = scoped_records(request.user, FacultyAvailability.objects.filter(faculty=faculty, academic_term=self.term)) if request.user.has_perm("workloads.view_facultyavailability") else None
        report = calculate_workload(faculty, self.term) if self.term else None
        return render(request, "workloads/faculty.html", {**self.context(), "faculty": faculty, "report": report, "filter_form": form, "availability": availability})
