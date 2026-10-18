"""Read-only, organizationally scoped faculty workload monitoring workspace."""
from urllib.parse import urlencode

from django import forms
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.views import View

from reporting.services import available_catalog
from timetabling.models import OfferingRequirement
from timetabling.queries import scoped
from timetabling.workspace import guided_form_class, guided_url
from .calculation import calculate_workloads
from .forms import WorkloadMonitorFilterForm
from .monitoring import CONDITIONS, decorate_report
from .selectors import scoped_faculty
from .views import TeachingMixin


class WorkloadMonitorView(TeachingMixin, View):
    def get(self, request):
        query = request.GET.copy()
        if self.term and "academic_term" not in query:
            query["academic_term"] = self.term.pk
        workspace = getattr(request, "scheduling_workspace", None)
        if workspace:
            query.setdefault("department", workspace["schedule"].department_id)
        form_class = guided_form_class(request, WorkloadMonitorFilterForm)
        form = form_class(query, user=request.user)
        context_form = form_class(query, user=request.user, auto_id="context_%s")
        if workspace:
            for name in ("academic_term", "department"):
                context_form.fields[name].widget = forms.HiddenInput()
        reports, rows = [], []
        valid = form.is_valid()
        context_form.is_valid()
        selected_department = form.cleaned_data.get("department") if valid else None
        if valid and self.term:
            data = form.cleaned_data
            people = scoped_faculty(request.user)
            for name, lookup in (("faculty", "pk"), ("college", "home_department__college"),
                                 ("department", "home_department")):
                if data.get(name):
                    people = people.filter(**{lookup: data[name].pk if name == "faculty" else data[name]})
            reports = [decorate_report(report) for report in calculate_workloads(people, self.term)]
            matching = people
            if data.get("q"):
                matching = matching.filter(Q(first_name__icontains=data["q"]) |
                    Q(last_name__icontains=data["q"]) | Q(employee_id__icontains=data["q"]))
            for name in ("employment_category", "academic_rank"):
                if data.get(name):
                    matching = matching.filter(**{name: data[name]})
            matching_ids = set(matching.values_list("pk", flat=True))
            for report in reports:
                if report["faculty"].pk not in matching_ids:
                    continue
                # Preserve existing raw-status and attention filter contracts.
                if data.get("workload_status") and report["status"] != data["workload_status"]:
                    continue
                if request.GET.get("attention") == "1" and not (
                    report["status"] in ("UNCONFIGURED", "AT_CAPACITY", "OVERLOAD") or report["warnings"]):
                    continue
                condition = data.get("condition")
                if condition == "above" and not report["monitor"]["above_target"]:
                    continue
                if condition and condition != "above" and report["monitor"]["condition"] != condition:
                    continue
                rows.append(report)

        requested_faculty = request.GET.get("selected_faculty")
        selected_index = None
        if requested_faculty is not None:
            try:
                selected_id = int(requested_faculty)
            except (TypeError, ValueError):
                raise Http404("Choose a faculty member in the displayed roster.")
            selected_index = next((index for index, report in enumerate(rows)
                                   if report["faculty"].pk == selected_id), None)
            if selected_index is None:
                raise Http404("Choose a faculty member in the displayed roster.")
        paginator = Paginator(rows, 20)
        page = paginator.get_page(selected_index // 20 + 1 if selected_index is not None else query.get("page"))
        selected_report = rows[selected_index] if selected_index is not None else next(iter(page), None)
        selected_id = selected_report["faculty"].pk if selected_report else None

        page_offerings = {assignment.subject_offering_id for report in page for assignment in report["assignments"]}
        sections = dict(scoped(request.user, OfferingRequirement.objects.filter(
            subject_offering_id__in=page_offerings)).values_list("subject_offering_id", "section__code"))
        for report in page:
            for contribution in report["monitor"]["contributions"]:
                contribution["section_code"] = sections.get(contribution["assignment"].subject_offering_id, "")

        clean_query = query.copy()
        for name in ("page", "selected_faculty"):
            clean_query.pop(name, None)
        context_query = {name: clean_query[name] for name in (
            "academic_term", "department", "college", "schedule", "workflow_schedule", "workflow_stage")
            if clean_query.get(name)}
        if self.schedule_context:
            context_query["schedule"] = self.schedule_context.pk

        def monitor_url(parameters, fragment=""):
            url = reverse("workloads:monitor") + ("?" + urlencode(parameters) if parameters else "")
            return guided_url(url + fragment, workspace)

        def task_url(route, parameters, *args):
            return guided_url(reverse(route, args=args) + ("?" + urlencode(parameters) if parameters else ""), workspace)

        assignment_permissions = ("workloads.view_facultysubjectassignment", "timetabling.view_classsection",
            "timetabling.view_offeringrequirement", "workloads.view_subjectoffering", "academics.view_subject",
            "workloads.view_workload", "academics.view_academicterm")
        can_assignments = all(request.user.has_perm(permission) for permission in assignment_permissions)
        assignment_url = task_url("workloads:section-assignments", context_query) if can_assignments else None
        can_report = any(item["key"] == "workload" for item in available_catalog(request.user))
        report_query = {"term": self.term.pk} if self.term else {}
        for name in ("department", "college"):
            if valid and form.cleaned_data.get(name):
                report_query[name] = form.cleaned_data[name].pk
        workload_report_url = task_url("reporting:detail", report_query, "workload") if can_report else None
        roster_query = clean_query.dict()
        if page.number > 1:
            roster_query["page"] = page.number
        for report in page:
            person = report["faculty"]
            report["monitor"].update(
                selection_url=monitor_url({**roster_query, "selected_faculty": person.pk}, f"#faculty-{person.pk}"),
                assignment_url=assignment_url,
                report_url=task_url("reporting:detail", {**report_query, "faculty": person.pk}, "workload") if can_report else None,
                faculty_url=task_url("workloads:faculty", context_query, person.pk))

        summary = [{"condition": key, "label": label, "tone": tone, "description": description,
                    "count": sum(report["monitor"]["condition"] == key for report in reports),
                    "active": form.cleaned_data.get("condition") == key,
                    "url": monitor_url({**context_query, "condition": key})}
                   for key, label, tone, description in CONDITIONS]
        secondary_names = ("faculty", "academic_rank", "college")
        secondary = [form[name] for name in secondary_names if name != "college" or
                     request.user.has_perm("core.change_college") or form[name].errors or form[name].value()]
        if form["workload_status"].value() or form["workload_status"].errors:
            secondary.append(form["workload_status"])
        rendered_roster = {"q", "condition", "employment_category", *[field.name for field in secondary]}
        hidden = [(name, value) for name, value in clean_query.items() if name not in rendered_roster]
        context_hidden = [(name, value) for name, value in clean_query.items() if name not in ("academic_term", "department")]
        context = self.context()
        attention = request.GET.get("attention") == "1"
        if attention:
            context.update(title="Workload needing attention", empty_message="No workload problems need attention in this selection.")
        return render(request, "workloads/monitor.html", {**context,
            "filter_form": form, "context_form": context_form, "page_obj": page,
            "page_query": clean_query.urlencode(), "context_count": len(reports),
            "condition_summary": summary, "selected_department": selected_department,
            "selected_report": selected_report, "selected_faculty_id": selected_id,
            "detail_open": requested_faculty is not None, "attention_only": attention,
            "above_target_count": sum(report["monitor"]["above_target"] for report in reports),
            "above_target_url": monitor_url({**context_query, "condition": "above"}),
            "reset_url": monitor_url(context_query), "monitor_back_url": monitor_url(roster_query, "#faculty-roster"),
            "assignment_desk_url": assignment_url, "workload_report_url": workload_report_url,
            "secondary_filters": secondary, "more_filters_open": any(field.errors or field.value() for field in secondary),
            "form_hidden_fields": hidden, "context_form_hidden_fields": context_hidden,
        })
