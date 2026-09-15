from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from academics.models import AcademicTerm, Subject
from core.views import ProtectedViewMixin
from faculty.models import Faculty
from scheduling.models import Room
from workloads.services import resolve_capacity
from .forms import FacultyForm, ResourceFilterForm, RoomForm, SubjectForm
from .selectors import scope_resources
from .services import permission_for, save_resource, set_resource_status


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

    def get_queryset(self):
        qs = self.queryset()
        self.filter_form = ResourceFilterForm(self.request.GET, user=self.request.user, kind=self.kind)
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
        return qs.order_by(*self.model._meta.ordering, "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.base_context())
        query = self.request.GET.copy()
        query.pop("page", None)
        context.update(filter_form=self.filter_form, page_query=query.urlencode(), kind=self.kind)
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
        if self.kind == "subjects":
            fields.append(("Total units", obj.total_units))
        context.update(object=obj, details=fields, kind=self.kind)
        if self.kind == "faculty" and request.user.has_perm("academics.view_academicterm"):
            context["show_capacities"] = True
            capacities = []
            for term in AcademicTerm.objects.filter(is_active=True).select_related("academic_year", "semester"):
                try:
                    capacities.append({"term": term, **resolve_capacity(obj, term)})
                except ValidationError as error:
                    capacities.append({"term": term, "error": "; ".join(error.messages)})
            context["capacities"] = capacities
        return render(request, "resources/detail.html", context)


class ResourceFormView(ResourceMixin, View):
    action = "add"

    def get(self, request, pk=None):
        instance = get_object_or_404(self.queryset(), pk=pk) if pk else None
        form = self.spec["form"](instance=instance, user=request.user)
        return self.render_form(form, pk)

    def render_form(self, form, pk):
        return render(self.request, "resources/form.html", {**self.base_context(), "form": form, "editing": bool(pk)})

    def post(self, request, pk=None):
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
