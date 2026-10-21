"""Section-based presentation of the existing assignment operation."""
from decimal import Decimal
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View

from accounts.permissions import is_system_admin, require_access
from timetabling.forms import SectionGenerationForm
from timetabling.models import OfferingRequirement
from timetabling.queries import scoped
from .calculation import calculate_workloads
from .forms import AssignmentForm
from .models import FacultySubjectAssignment
from .operations import save_term_record
from .selectors import accessible_terms, scoped_faculty, scoped_records
from .views import TeachingMixin


def assignment_preview(report):
    """Display current totals from the existing create-assignment preview only."""
    if report is None:
        return None
    return {**report,
        "current_teaching_units": report["teaching_units"] - report["assignment_teaching_units"],
        "current_teaching_hours": report["teaching_hours"] - report["assignment_teaching_hours"],
    }


class SectionAssignmentsView(TeachingMixin, View):
    section = "assignments"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            for permission in ("timetabling.view_classsection", "timetabling.view_offeringrequirement",
                               "workloads.view_subjectoffering", "academics.view_subject", "workloads.view_workload"):
                require_access(request.user, permission)
        return super().dispatch(request, *args, **kwargs)

    def selection_form(self, request):
        data = (request.POST if request.method == "POST" else request.GET).copy()
        if request.method == "POST" and data.get("academic_term"):
            try:
                self.term = get_object_or_404(accessible_terms(request.user, active=True), pk=int(data["academic_term"]))
            except (ValueError, TypeError):
                pass  # The choice field reports the malformed value.
        data.setdefault("academic_term", self.term.pk if self.term else "")
        data.setdefault("year_level", 1)
        bound = request.method == "POST" or bool(data.get("department") or data.get("section"))
        form = SectionGenerationForm(data if bound else None, user=request.user, initial=data)
        form.fields.pop("schedule")
        form.fields.pop("strategy")
        if not is_system_admin(request.user):
            form.fields["department"].label_from_instance = lambda department: department.name
        if not data.get("department"):
            form.fields["section"].queryset = form.fields["section"].queryset.none()
        return form

    def requirements(self, request, section):
        return scoped(request.user, OfferingRequirement.objects.filter(section=section,
            subject_offering__is_active=True, subject_offering__subject__is_active=True)
        ).select_related("subject_offering__subject", "subject_offering__academic_term")

    def row_form(self, request, offering, remaining, data=None):
        form = AssignmentForm(data, user=request.user, term=self.term,
                              offering=offering, initial={"subject_offering": offering.pk, "share": remaining})
        self.style_row_form(form, offering)
        return form

    def style_row_form(self, form, offering):
        form.auto_id = f"id_%s_{offering.pk}"
        form.fields["academic_term"].widget = forms.HiddenInput()
        form.fields["subject_offering"].widget = forms.HiddenInput()
        # Bound choices remain available in HTML after an invalid submission.
        form.fields["faculty"].widget.attrs["aria-label"] = f"Instructor for {offering.subject.title}"
        form.fields["share"].widget.attrs["aria-label"] = f"Teaching share for {offering.subject.title}"
        share_id = f"id_share_{offering.pk}"
        form.fields["share"].widget.attrs["aria-describedby"] = f"{share_id}_helptext {share_id}_errors"
        form.fields.pop("notes", None)

    def render_workspace(self, request, selection, *, submitted=None, preview=None):
        selected = selection.cleaned_data.get("section") if selection.is_bound and selection.is_valid() else None
        rows = []
        if selected:
            requirements = list(self.requirements(request, selected))
            assignments = list(scoped_records(request.user, FacultySubjectAssignment.objects.filter(
                subject_offering_id__in=[item.subject_offering_id for item in requirements]
            )).select_related("faculty__home_department", "subject_offering__subject"))
            reports = {item["faculty"].pk: item for item in calculate_workloads(
                scoped_faculty(request.user).filter(pk__in={item.faculty_id for item in assignments}), self.term)}
            by_offering = {}
            for assignment in assignments:
                assignment.workload = reports[assignment.faculty_id]
                assignment.share_percent = assignment.share * 100
                by_offering.setdefault(assignment.subject_offering_id, []).append(assignment)
            instructors = list(scoped_faculty(request.user).filter(home_department=selected.department, is_active=True,
                home_department__is_active=True, home_department__college__is_active=True))
            instructor_choices = [("", "Choose an instructor"), *[(person.pk, str(person)) for person in instructors]]
            for requirement in requirements:
                offering = requirement.subject_offering
                existing = by_offering.get(offering.pk, [])
                remaining = Decimal("1") - sum((item.share for item in existing), Decimal("0"))
                matching = submitted is not None and str(submitted["subject_offering"].value()) == str(offering.pk)
                form = submitted if matching else self.row_form(request, offering, remaining) if remaining > 0 else None
                if form is not None:
                    form.fields["faculty"].choices = instructor_choices
                rows.append({"offering": offering, "assignments": existing, "remaining": remaining,
                    "remaining_percent": remaining * 100,
                    "assignment_state": "full" if remaining <= 0 else "partial" if existing else "needed",
                    "form": form, "faculty_value": str(form["faculty"].value() or "") if form else "",
                    "submitted": matching,
                    "preview": assignment_preview(preview) if matching else None})
        else:
            instructors = []
        selection_values = {name: getattr(selected, name + "_id") if name in ("academic_term", "department")
                            else getattr(selected, name) for name in ("academic_term", "department", "year_level")} if selected else {}
        if selected:
            selection_values["section"] = selected.pk
        url = reverse("workloads:section-assignments") + ("?" + urlencode({"academic_term": self.term.pk}) if self.term else "")
        return render(request, "workloads/section_assignments.html", {
            "term": self.term, "filter_form": selection, "selected_section": selected, "rows": rows,
            "instructors": instructors,
            "assignment_counts": {state: sum(row["assignment_state"] == state for row in rows)
                                  for state in ("full", "partial", "needed")},
            "selection_values": selection_values, "workspace_url": url,
            "unassigned_count": sum(row["remaining"] > 0 for row in rows),
            "generator_url": reverse("timetabling:section-generator") + "?" + urlencode(selection_values),
        })

    def get(self, request):
        return self.render_workspace(request, self.selection_form(request))

    def post(self, request):
        selection = self.selection_form(request)
        if not selection.is_valid() or not selection.cleaned_data.get("section"):
            return self.render_workspace(request, selection)
        selected = selection.cleaned_data["section"]
        if selected.academic_term_id != getattr(self.term, "pk", None):
            return HttpResponseBadRequest("Choose the displayed academic term.")
        try:
            offering_id = int(request.POST.get("subject_offering", ""))
        except (ValueError, TypeError):
            return HttpResponseBadRequest("Choose a subject in this section.")
        requirement = get_object_or_404(self.requirements(request, selected), subject_offering_id=offering_id)
        intent = request.POST.get("intent")
        if intent not in ("preview", "save"):
            return HttpResponseBadRequest("Choose Check workload or Assign instructor.")
        data = request.POST.copy()
        data["academic_term"] = self.term.pk
        # This workspace creates an additional share, never overwrites an assignment.
        try:
            obj, form, report = save_term_record(user=request.user, form_class=AssignmentForm,
                data=data, term=self.term, preview=intent == "preview")
        except (ValidationError, IntegrityError) as error:
            form = self.row_form(request, requirement.subject_offering, Decimal("1"), data)
            form.is_valid()
            form.add_error(None, error if isinstance(error, ValidationError)
                           else "This instructor is already assigned to this subject. Reload the section to review its teaching shares.")
            obj, report = None, None
        self.style_row_form(form, requirement.subject_offering)
        if intent == "preview" and "application/json" in request.headers.get("Accept", ""):
            payload = None if report is None else {
                "current_load": str(report["current_load"]) if report["current_load"] is not None else None,
                "assigned_load": str(report["assigned_load"]) if report["assigned_load"] is not None else None,
                "maximum_load": str(report["policy"]["maximum_load"]) if report["policy"]["maximum_load"] is not None else None,
                "status": report["status"].replace("_", " ").title(), "warnings": report["warnings"],
                "summary_html": render_to_string("workloads/_assignment_preview.html",
                    {"report": assignment_preview(report)}, request=request),
            }
            return JsonResponse({"preview": payload, "errors": form.errors.get_json_data()})
        if obj and intent == "save":
            messages.success(request, f"{obj.faculty} assigned to {obj.subject_offering.subject.title}.")
            for warning in report["warnings"]:
                messages.warning(request, warning)
            return redirect(reverse("workloads:section-assignments") + "?" + urlencode({
                "academic_term": selected.academic_term_id, "department": selected.department_id,
                "year_level": selected.year_level, "section": selected.pk}) + f"#assignment-{obj.subject_offering_id}")
        return self.render_workspace(request, selection, submitted=form, preview=report)
