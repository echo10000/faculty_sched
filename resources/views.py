from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import F, OuterRef, Q, Subquery
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from academics.models import AcademicTerm, Subject
from core.views import ProtectedViewMixin
from accounts.permissions import is_system_admin
from faculty.models import Faculty
from scheduling.models import Room
from workloads.services import resolve_capacity
from .forms import FacultyForm, ResourceFilterForm, RoomForm, SubjectForm
from .selectors import scope_resources
from .services import permission_for, save_resource, set_resource_status
from .browsing import academic_group_label


SPECS = {
    "faculty": {"model": Faculty, "form": FacultyForm, "title": "Faculty", "singular": "faculty member", "namespace": "faculty-management", "search": ["employee_id", "first_name", "middle_name", "last_name", "email"], "fields": ["employee_id", "first_name", "middle_name", "last_name", "suffix", "email", "contact_number", "home_department", "employment_category", "academic_rank", "recommended_load", "maximum_load", "notes"]},
    "subjects": {"model": Subject, "form": SubjectForm, "title": "Subjects", "singular": "subject", "namespace": "subjects", "search": ["code", "title"], "fields": ["code", "title", "description", "owning_department", "lecture_units", "laboratory_units", "lecture_hours", "laboratory_hours"]},
    "rooms": {"model": Room, "form": RoomForm, "title": "Rooms", "singular": "room", "namespace": "rooms", "search": ["code", "name", "building__name"], "fields": ["code", "name", "building", "category", "capacity", "owner_college", "owner_department"]},
}


class ResourceMixin(ProtectedViewMixin):
    kind = None
    action = "view"

    def dispatch(self, request, *args, **kwargs):
        self.spec = SPECS[self.kind]
        self.model = self.spec["model"]
        self.permission = permission_for(self.model, self.action)
        return super().dispatch(request, *args, **kwargs)

    def queryset(self):
        qs = scope_resources(self.request.user, self.model.objects.all())
        relations = {"faculty": ["home_department__college", "employment_category", "academic_rank"], "subjects": ["owning_department__college"], "rooms": ["owner_college", "owner_department__college", "category", "building"]}
        return qs.select_related(*relations[self.kind])

    def base_context(self):
        namespace = self.spec["namespace"]
        return {
            "title": self.spec["title"], "singular": self.spec["singular"], "namespace": namespace,
            "list_url": reverse(f"{namespace}:list"), "add_url": reverse(f"{namespace}:add"),
            "can_add": self.request.user.has_perm(permission_for(self.model, "add")),
            "can_change": self.request.user.has_perm(permission_for(self.model, "change")),
            "can_activate": self.request.user.has_perm(permission_for(self.model, "activate")),
        }


class ResourceListView(ResourceMixin, ListView):
    template_name = "resources/list.html"
    paginate_by = 20

    def get_template_names(self):
        if self.kind == "subjects":
            return ["resources/subject_directory.html"]
        if self.kind == "faculty" and not is_system_admin(self.request.user):
            return ["workloads/faculty_directory.html"]
        return super().get_template_names()

    def get_queryset(self):
        qs = self.queryset()
        self.filter_form = ResourceFilterForm(self.request.GET, user=self.request.user, kind=self.kind)
        if self.kind in ("faculty", "subjects") and not is_system_admin(self.request.user):
            self.filter_form.fields["department"].label_from_instance = lambda department: department.name
        if not self.filter_form.is_valid():
            return qs.none()
        data = self.filter_form.cleaned_data
        if data["q"]:
            condition = Q()
            for field in self.spec["search"]:
                condition |= Q(**{field + "__icontains": data["q"]})
            qs = qs.filter(condition)
        if data["status"]:
            qs = qs.filter(is_active=data["status"] == "active")
        if self.kind == "rooms":
            if data["college"]:
                qs = qs.filter(Q(owner_college=data["college"]) | Q(owner_department__college=data["college"]))
            if data["department"]:
                qs = qs.filter(owner_department=data["department"])
        else:
            field = "home_department" if self.kind == "faculty" else "owning_department"
            if data["college"]:
                qs = qs.filter(**{field + "__college": data["college"]})
            if data["department"]:
                qs = qs.filter(**{field: data["department"]})
        for field in ("employment_category", "academic_rank", "category", "building"):
            if data.get(field):
                qs = qs.filter(**{field: data[field]})
        self.subject_term = None
        self.subject_placements = None
        if self.kind == "subjects" and "academic_term" in self.filter_form.fields:
            from workloads.selectors import accessible_terms
            from timetabling.models import OfferingRequirement
            from timetabling.queries import scoped
            self.subject_term = data.get("academic_term") or accessible_terms(self.request.user, active=True).first()
            self.subject_placements = scoped(self.request.user, OfferingRequirement.objects.filter(
                subject_offering__academic_term=self.subject_term, section__year_level__isnull=False,
            ))
            placement = self.subject_placements.filter(subject_offering__subject_id=OuterRef("pk"))
            qs = qs.annotate(browse_year=Subquery(placement.order_by("section__year_level").values("section__year_level")[:1]))
            year = data.get("year_level")
            if year == "unplaced":
                qs = qs.filter(browse_year__isnull=True)
            elif year:
                qs = qs.filter(pk__in=self.subject_placements.filter(section__year_level=int(year)).values("subject_offering__subject_id"))
            return qs.order_by("owning_department__college__name", "owning_department__name", F("browse_year").asc(nulls_last=True), "code", "pk")
        return qs.order_by(*self.model._meta.ordering, "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.base_context())
        query = self.request.GET.copy()
        query.pop("page", None)
        context.update(filter_form=self.filter_form, page_query=query.urlencode(), kind=self.kind)
        if self.kind == "subjects":
            context["subject_filters_active"] = any(self.request.GET.get(name) for name in
                ("q", "college", "department", "year_level", "status"))
            context["primary_filters"] = [field for field in self.filter_form if field.name != "status"
                and (field.name != "college" or is_system_admin(self.request.user) or field.errors)]
            context["secondary_filters"] = [self.filter_form["status"]]
            context["more_filters_open"] = bool(self.filter_form["status"].value() or self.filter_form["status"].errors)
            context["subject_term"] = getattr(self, "subject_term", None)
            context["term"] = context["subject_term"]
            if context["subject_term"]:
                query["academic_term"] = context["subject_term"].pk
                context["page_query"] = query.urlencode()
            year_filter = self.filter_form.cleaned_data.get("year_level", "")
            context["browse_year_filter"] = year_filter if year_filter != "unplaced" else ""
            people = list(context["object_list"])
            years = {}
            placements = getattr(self, "subject_placements", None)
            if placements is not None:
                for subject_id, year in placements.filter(subject_offering__subject_id__in=[obj.pk for obj in people]).values_list(
                        "subject_offering__subject_id", "section__year_level").distinct():
                    years.setdefault(subject_id, set()).add(year)
            for obj in people:
                obj.browse_group = academic_group_label(self.request.user, obj.owning_department, years.get(obj.pk, ()))
                if "year_level" not in self.filter_form.fields:
                    obj.browse_group = obj.owning_department.name if obj.owning_department else "No program assigned"
            context["object_list"] = people
        if self.kind == "faculty":
            context["primary_filters"] = [field for field in self.filter_form if field.name in ("q", "department", "academic_term")]
            context["secondary_filters"] = [field for field in self.filter_form if field.name not in ("q", "department", "academic_term")
                                             and (field.name != "college" or field.errors)]
            context["more_filters_open"] = any(field.value() or field.errors for field in context["secondary_filters"])
        if self.kind == "faculty" and "academic_term" in self.filter_form.fields and self.filter_form.is_valid():
            from workloads.selectors import accessible_terms, scoped_records
            from workloads.calculation import calculate_workloads
            from workloads.models import FacultyAvailability
            term = self.filter_form.cleaned_data.get("academic_term") or accessible_terms(self.request.user, active=True).first()
            context["workload_term"] = term
            if term:
                people = list(context["object_list"])
                reports = {row["faculty"].pk: row for row in calculate_workloads(people, term)}
                recorded = None
                if self.request.user.has_perm("workloads.view_facultyavailability"):
                    recorded = set(scoped_records(self.request.user, FacultyAvailability.objects.filter(
                        academic_term=term, faculty_id__in=[person.pk for person in people],
                    )).values_list("faculty_id", flat=True))
                for person in people:
                    person.workload = reports.get(person.pk)
                    person.availability_recorded = person.pk in recorded if recorded is not None else None
                context["object_list"] = people
        return context


class ResourceDetailView(ResourceMixin, View):
    def get(self, request, pk):
        obj = get_object_or_404(self.queryset(), pk=pk)
        context = self.base_context()
        fields = [(self.model._meta.get_field(name).verbose_name.capitalize(), getattr(obj, name)) for name in self.spec["fields"] if name != "owner_college"]
        department = getattr(obj, "home_department", None) or getattr(obj, "owning_department", None)
        if department:
            fields.append(("College", department.college))
        if self.kind == "rooms":
            fields.append(("College", obj.college or "Institution-owned"))
            fields = [("Room specialization / type" if label == "Category" else "Seating capacity" if label == "Capacity" else label, value) for label, value in fields]
            from timetabling.room_rules import room_resource_failures
            failures = room_resource_failures(obj)
            if not obj.is_active:
                failures.append("Room is inactive")
            if (obj.owner_department_id and (not obj.owner_department.is_active or not obj.owner_department.college.is_active)) or (obj.owner_college_id and not obj.owner_college.is_active):
                failures.append("Owning department or college is inactive")
            context["room_configuration_failures"] = failures
        if self.kind == "subjects":
            fields.append(("Total units", obj.total_units))
        context.update(object=obj, details=fields, kind=self.kind)
        if self.kind in ("faculty", "rooms") and request.user.has_perm("academics.view_academicterm"):
            from workloads.selectors import accessible_terms
            raw_term = request.GET.get("academic_term")
            if raw_term:
                try:
                    context["term"] = get_object_or_404(accessible_terms(request.user), pk=int(raw_term))
                except ValueError:
                    return HttpResponseBadRequest("Select a valid academic term.")
            else:
                context["term"] = accessible_terms(request.user, active=True).first() or accessible_terms(request.user).first()
        if self.kind == "faculty" and request.user.has_perm("academics.view_academicterm"):
            context["show_capacities"] = True
            capacities = []
            for term in AcademicTerm.objects.filter(is_active=True).select_related("academic_year", "semester"):
                try:
                    capacities.append({"term": term, **resolve_capacity(obj, term)})
                except ValidationError as error:
                    capacities.append({"term": term, "error": "; ".join(error.messages)})
            context["capacities"] = capacities
        if self.kind == "faculty":
            if request.GET.get("schedule"):
                from timetabling.queries import get_schedule
                from django.http import Http404
                try:
                    context["schedule"] = get_schedule(request.user, int(request.GET["schedule"]))
                except (ValueError, TypeError):
                    raise Http404("Invalid schedule")
                if context["schedule"].academic_term_id != getattr(context.get("term"), "pk", None):
                    raise Http404("Schedule does not belong to the selected term")
            tab = request.GET.get("tab", "overview")
            if tab not in ("overview", "qualifications"):
                return HttpResponseBadRequest("Choose a valid faculty tab.")
            context["faculty_tab"] = tab
            if tab == "qualifications":
                from accounts.permissions import require_access
                require_access(request.user, "faculty.view_facultyqualification")
                require_access(request.user, "academics.view_subject")
                context["qualifications"] = obj.qualifications.filter(
                    subject__in=scope_resources(request.user, Subject.objects.all())
                ).select_related("subject")
        return render(request, "resources/detail.html", context)


class ResourceFormView(ResourceMixin, View):
    action = "add"

    def workload_context(self, pk):
        if self.kind != "faculty" or not pk or self.request.GET.get("workload") != "1":
            return {}
        from accounts.permissions import require_access
        from workloads.calculation import calculate_workload
        from workloads.selectors import accessible_terms
        from workloads.views import term_url
        from django.http import Http404
        require_access(self.request.user, "workloads.view_workload")
        faculty = get_object_or_404(self.queryset().select_related("designation"), pk=pk)
        terms = accessible_terms(self.request.user)
        raw = self.request.GET.get("academic_term")
        if raw is not None:
            try:
                term = get_object_or_404(terms, pk=int(raw))
            except (ValueError, TypeError):
                raise Http404("Invalid workload term")
        else:
            term = accessible_terms(self.request.user, active=True).first() or terms.first()
        schedule = None
        if self.request.GET.get("schedule"):
            from timetabling.queries import get_schedule
            try:
                schedule = get_schedule(self.request.user, int(self.request.GET["schedule"]))
            except (ValueError, TypeError):
                raise Http404("Invalid schedule")
            if schedule.academic_term_id != getattr(term, "pk", None):
                raise Http404("Schedule does not belong to the selected term")
        return_url = term_url("workloads:faculty", term, pk=pk)
        if schedule:
            return_url += "&" + urlencode({"schedule": schedule.pk})
        return {"workload_configuration": True, "term": term, "schedule": schedule,
                "report": calculate_workload(faculty, term) if term else None,
                "workload_return_url": return_url, "list_url": return_url}

    def get(self, request, pk=None):
        self.load_context = self.workload_context(pk)
        instance = get_object_or_404(self.queryset(), pk=pk) if pk else None
        form = self.spec["form"](instance=instance, user=request.user)
        return self.render_form(form, pk)

    def render_form(self, form, pk):
        return render(self.request, "resources/form.html", {**self.base_context(), **self.load_context, "form": form, "editing": bool(pk)})

    def post(self, request, pk=None):
        self.load_context = self.workload_context(pk)
        try:
            obj, form = save_resource(user=request.user, form_class=self.spec["form"], data=request.POST, pk=pk)
        except (ValidationError, IntegrityError) as error:
            # The atomic service has rolled back before rebuilding the error page.
            instance = get_object_or_404(self.queryset(), pk=pk) if pk else None
            form = self.spec["form"](request.POST, instance=instance, user=request.user)
            form.is_valid()
            form.add_error(None, "; ".join(error.messages) if isinstance(error, ValidationError) else "This change conflicts with an existing record. Check the identifier and try again.")
            return self.render_form(form, pk)
        if obj:
            messages.success(request, "Record updated." if pk else "Record created.")
            if self.load_context:
                return redirect(self.load_context["workload_return_url"])
            # A write grant need not include a read grant.
            return redirect(f"{self.spec['namespace']}:detail", pk=obj.pk) if request.user.has_perm(permission_for(self.model, "view")) else redirect("home")
        return self.render_form(form, pk)


class ResourceStatusView(ResourceMixin, View):
    action = "activate"

    def get(self, request, pk):
        obj = get_object_or_404(self.queryset(), pk=pk)
        return render(request, "resources/status.html", {**self.base_context(), "object": obj})

    def post(self, request, pk):
        if request.POST.get("active") not in ("true", "false"):
            return HttpResponseBadRequest("A valid status is required.")
        try:
            obj = set_resource_status(user=request.user, model=self.model, pk=pk, active=request.POST["active"] == "true")
        except ValidationError as error:
            obj = get_object_or_404(self.queryset(), pk=pk)
            return render(request, "resources/status.html", {**self.base_context(), "object": obj, "error": "; ".join(error.messages)})
        messages.success(request, "Record activated." if obj.is_active else "Record deactivated.")
        return redirect(f"{self.spec['namespace']}:detail", pk=pk) if request.user.has_perm(permission_for(self.model, "view")) else redirect("home")
