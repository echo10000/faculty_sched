from urllib.parse import urlencode
from collections import Counter

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from accounts.permissions import require_access
from core.views import ProtectedViewMixin
from resources.services import permission_for
from .calculation import calculate_workload, calculate_workloads
from .forms import AssignmentForm, AvailabilityForm, OfferingForm, WorkloadFilterForm
from .models import FacultyAvailability, FacultySubjectAssignment, SubjectOffering
from .operations import remove_term_record, save_term_record, scoped_object
from .selectors import accessible_terms, scoped_faculty, scoped_records
from resources.browsing import academic_group_label

SPECS = {
    "availability": (FacultyAvailability, AvailabilityForm, "Faculty availability"),
    "offerings": (SubjectOffering, OfferingForm, "Classes offered"),
    "assignments": (FacultySubjectAssignment, AssignmentForm, "Teaching assignments"),
}

SECTION_HELP = {
    "monitor": ("See who is teaching, how many classes they have, and whether their workload needs attention.", "", "No faculty workload records match this selection."),
    "availability": ("Choose a faculty member to review when they can teach, cannot teach, or prefer to teach.", "Add faculty availability", "No faculty availability records match this selection."),
    "offerings": ("Review the classes offered by departments for the selected academic term.", "Add class offering", "No classes offered match this selection."),
    "assignments": ("Assign faculty members to the classes they will teach for the selected term.", "Add teaching assignment", "No teaching assignments match this selection."),
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
            self.schedule_context = None
            if request.GET.get("schedule"):
                from timetabling.queries import get_schedule
                from django.http import Http404
                try:
                    self.schedule_context = get_schedule(request.user, int(request.GET["schedule"]))
                except (ValueError, TypeError):
                    raise Http404("Invalid schedule")
                if self.schedule_context.academic_term_id != getattr(self.term, "pk", None):
                    raise Http404("Schedule does not belong to the selected term")
        return super().dispatch(request, *args, **kwargs)

    def context(self):
        context = {"section": self.section, "term": self.term, "schedule": self.schedule_context, "title": "Faculty workload" if self.section == "monitor" else SPECS[self.section][2], "list_url": term_url(f"workloads:{self.section}", self.term),
                   "description": SECTION_HELP[self.section][0], "add_label": SECTION_HELP[self.section][1], "empty_message": SECTION_HELP[self.section][2],
                   "save_label": "Save " + SECTION_HELP[self.section][1].removeprefix("Add ").lower(),
                   "edit_label": "Edit " + SECTION_HELP[self.section][1].removeprefix("Add ").lower()}
        if self.section in SPECS:
            model = SPECS[self.section][0]
            context.update(can_add=self.request.user.has_perm(permission_for(model, "add")), can_change=self.request.user.has_perm(permission_for(model, "change")), can_delete=self.section != "offerings" and self.request.user.has_perm(permission_for(model, "delete")), add_url=term_url(f"workloads:{self.section}-add", self.term))
        if self.schedule_context:
            context["list_url"] += "&" + urlencode({"schedule": self.schedule_context.pk})
            if context.get("add_url"):
                context["add_url"] += "&" + urlencode({"schedule": self.schedule_context.pk})
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
            faculty = faculty or data.get("faculty")
            people = scoped_faculty(request.user)
            for field, lookup in [("faculty", "pk"), ("college", "home_department__college"), ("department", "home_department"), ("employment_category", "employment_category"), ("academic_rank", "academic_rank")]:
                if data.get(field):
                    value = data[field].pk if field == "faculty" else data[field]
                    people = people.filter(**{lookup: value})
            if self.section == "monitor":
                if data["q"]:
                    people = people.filter(Q(first_name__icontains=data["q"]) | Q(last_name__icontains=data["q"]) | Q(employee_id__icontains=data["q"]))
                for report in calculate_workloads(people, self.term):
                    attention = request.GET.get("attention") == "1"
                    needs_attention = report["status"] in ("UNCONFIGURED", "AT_CAPACITY", "OVERLOAD") or bool(report["warnings"])
                    if (not attention or needs_attention) and (not data["workload_status"] or data["workload_status"] == report["status"]):
                        rows.append(report)
            else:
                model = SPECS[self.section][0]
                qs = scoped_records(request.user, model.objects.all())
                if self.section == "offerings":
                    qs = qs.filter(academic_term=self.term).select_related("subject", "academic_term",
                        "department__college", "scheduling_requirement__section")
                    if data.get("college"):
                        qs = qs.filter(department__college=data["college"])
                    if data.get("department"):
                        qs = qs.filter(department=data["department"])
                    if data.get("year_level"):
                        qs = qs.filter(scheduling_requirement__section__year_level=int(data["year_level"]))
                    if data.get("section"):
                        qs = qs.filter(scheduling_requirement__section=data["section"])
                    if data["q"]:
                        qs = qs.filter(Q(subject__code__icontains=data["q"]) | Q(subject__title__icontains=data["q"]) | Q(code__icontains=data["q"]))
                    qs = qs.order_by("department__college__name", "department__name",
                        "scheduling_requirement__section__year_level", "scheduling_requirement__section__code", "subject__code", "code", "pk")
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
        availability_overview = self.section == "availability" and faculty is None
        if availability_overview:
            grouped = {}
            for record in rows:
                group = grouped.setdefault(record.faculty_id, {"faculty": record.faculty,
                    "record_count": 0, "types": Counter()})
                group["record_count"] += 1
                group["types"][record.get_availability_type_display()] += 1
            for group in grouped.values():
                group["types"] = dict(group["types"])
            rows = sorted(grouped.values(), key=lambda row: (row["faculty"].last_name, row["faculty"].first_name))
        elif self.section == "assignments":
            rows = list(rows)
            for assignment in rows:
                assignment.share_percent = assignment.share * 100
        page = Paginator(rows, 20).get_page(request.GET.get("page"))
        if self.section == "offerings":
            for offering in page:
                requirement = getattr(offering, "scheduling_requirement", None)
                offering.browse_section = requirement.section if requirement else None
                offering.browse_group = academic_group_label(request.user, offering.department,
                    (offering.browse_section.year_level,) if offering.browse_section else ())
                offering.show_offering_code = offering.code != getattr(offering.browse_section, "code", None)
        query.pop("page", None)
        context = self.context()
        context["browse_year_filter"] = form.cleaned_data.get("year_level", "")
        context["reset_url"] = term_url("workloads:faculty-availability", self.term, faculty_pk=faculty.pk) if faculty_pk else context["list_url"]
        if faculty_pk and self.schedule_context:
            context["reset_url"] += "&" + urlencode({"schedule": self.schedule_context.pk})
        if faculty and self.section in ("assignments", "availability"):
            context["add_url"] = context["add_url"] + "&" + urlencode({"faculty": faculty.pk})
        primary_names = ("q", "college", "department", "academic_term", "faculty", "workload_status", "year_level", "section")
        primary_filters = [field for field in form if field.name in primary_names
                           and (field.name != "college" or request.user.has_perm("core.change_college") or field.errors)]
        secondary_filters = [field for field in form if field.name not in primary_names]
        if request.GET.get("attention") == "1" and self.section == "monitor":
            context.update(title="Workload needing attention", empty_message="No workload problems need attention in this selection.", attention_only=True)
            context["list_url"] += "&attention=1" if "?" in context["list_url"] else "?attention=1"
            context["reset_url"] = context["list_url"]
        weekly_days = []
        if faculty_pk and self.section == "availability":
            records = list(rows)
            weekly_days = [{"label": label, "entries": [entry for entry in records if entry.day_of_week == day]}
                           for day, label in FacultyAvailability.DAYS]
        template = "workloads/faculty_availability.html" if weekly_days else "workloads/list.html"
        return render(request, template, {**context, "filter_form": form,
            "primary_filters": primary_filters, "secondary_filters": secondary_filters,
            "more_filters_open": any(field.errors or field.value() for field in secondary_filters),
            "availability_overview": availability_overview, "page_obj": page,
            "page_query": query.urlencode(), "faculty_context": faculty, "weekly_days": weekly_days})


class TeachingDetailView(TeachingMixin, View):
    def get(self, request, pk):
        obj = scoped_object(request.user, SPECS[self.section][0], pk, self.term)
        fields = [(field.verbose_name.capitalize(), getattr(obj, field.name)) for field in obj._meta.fields if field.name not in ("id", "created_by")]
        context = self.context()
        if self.schedule_context and self.section in ("assignments", "availability"):
            context["list_url"] += "&" + urlencode({"faculty": obj.faculty_id})
        return render(request, "workloads/record.html", {**context, "object": obj, "details": fields})


class TeachingFormView(TeachingMixin, View):
    action = "add"

    def form_context(self, form):
        context = self.context()
        if (self.schedule_context or self.request.GET.get("faculty")) and "faculty" in form.fields and self.section in ("assignments", "availability"):
            if form.is_bound:
                person = form.cleaned_data.get("faculty")
            else:
                value = form["faculty"].value()
                person = form.fields["faculty"].queryset.filter(pk=value).first() if value else None
            if person:
                if self.section == "availability":
                    context["list_url"] = term_url("workloads:faculty-availability", self.term, faculty_pk=person.pk)
                    context["faculty_context"] = person
                    form.fields["faculty"].widget = forms.HiddenInput()
                else:
                    context["list_url"] += "&" + urlencode({"faculty": person.pk})
        return context

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
        from timetabling.workspace import guided_form_class
        form_class = guided_form_class(request, form_class)
        form = form_class(user=request.user, term=self.term, instance=obj, initial=initial)
        if not obj and getattr(request, "scheduling_workspace", None):
            for name in ("department", "subject_offering"):
                raw = request.GET.get(name)
                field = form.fields.get(name)
                if raw and field and hasattr(field, "queryset"):
                    try:
                        form.initial[name] = get_object_or_404(field.queryset, pk=int(raw)).pk
                    except ValueError:
                        raise Http404("Invalid form context")
        return render(request, "workloads/form.html", {**self.form_context(form), "form": form, "editing": bool(pk)})

    def post(self, request, pk=None):
        if not self.term:
            return HttpResponseBadRequest("Configure an academic term first.")
        model, form_class, _ = SPECS[self.section]
        preview = self.section == "assignments" and request.POST.get("intent") == "preview"
        from timetabling.workspace import guided_form_class
        form_class = guided_form_class(request, form_class)
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
            return redirect(self.form_context(form)["list_url"]) if request.user.has_perm(permission_for(model, "view")) else redirect("home")
        return render(request, "workloads/form.html", {**self.form_context(form), "form": form, "editing": bool(pk), "preview": report})


class TeachingDeleteView(TeachingMixin, View):
    action = "delete"

    def removal_context(self, obj):
        context = self.context()
        if self.section == "availability":
            context["list_url"] = term_url("workloads:faculty-availability", self.term, faculty_pk=obj.faculty_id)
        return context

    def get(self, request, pk):
        obj = scoped_object(request.user, SPECS[self.section][0], pk, self.term)
        return render(request, "workloads/delete.html", {**self.removal_context(obj), "object": obj})

    def post(self, request, pk):
        from django.db.models.deletion import ProtectedError
        original = scoped_object(request.user, SPECS[self.section][0], pk, self.term)
        try:
            remove_term_record(user=request.user, model=SPECS[self.section][0], pk=pk, term=self.term)
        except ProtectedError:
            obj = scoped_object(request.user, SPECS[self.section][0], pk, self.term)
            return render(request, "workloads/delete.html", {**self.context(), "object": obj,
                "removal_error": "This teaching record is used by class meeting requirements or a timetable. Review those dependent records before removing it."})
        messages.success(request, "Teaching record removed; its audit history is preserved.")
        return redirect(self.removal_context(original)["list_url"]) if request.user.has_perm(permission_for(SPECS[self.section][0], "view")) else redirect("home")


class FacultyWorkloadView(TeachingMixin, View):
    def get(self, request, pk):
        faculty = get_object_or_404(scoped_faculty(request.user), pk=pk)
        query = {"academic_term": self.term.pk} if self.term else {}
        form = WorkloadFilterForm(query, user=request.user, section="monitor")
        form.fields = {"academic_term": form.fields["academic_term"]}
        availability = scoped_records(request.user, FacultyAvailability.objects.filter(faculty=faculty, academic_term=self.term)) if request.user.has_perm("workloads.view_facultyavailability") else None
        report = calculate_workload(faculty, self.term) if self.term else None
        if report:
            for assignment in report["assignments"]:
                assignment.share_percent = assignment.share * 100
        return render(request, "workloads/faculty.html", {**self.context(), "faculty": faculty, "report": report, "filter_form": form, "availability": availability})
