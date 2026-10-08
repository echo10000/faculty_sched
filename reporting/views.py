"""One authorization and data path for screen, print, and downloads."""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from accounts.permissions import require_access
from audit.services import record_event
from .exports import render_csv, render_pdf, render_xlsx
from .services import available_catalog, build_report


@login_required
@never_cache
def index(request):
    require_access(request.user, "academics.view_academicterm")
    from workloads.selectors import accessible_terms
    term = None
    if request.GET.get("term"):
        try:
            term = get_object_or_404(accessible_terms(request.user), pk=int(request.GET["term"]))
        except (ValueError, TypeError):
            raise Http404("Invalid academic term")
    suffix = "?" + urlencode({"term": term.pk}) if term else ""
    catalog = [
        {**item, "url": reverse("reporting:detail", args=[item["key"]]) + suffix}
        for item in available_catalog(request.user)
    ]
    return render(request, "reporting/index.html", {"title": "Reports", "catalog": catalog, "term": term})


def _prepared(request, kind):
    # The service checks the same exact permissions and organizational selectors
    # for every output format, including a direct export URL.
    from core.context_processors import navigation

    context = navigation(request)
    report, filters = build_report(
        request.user, kind, request.GET,
        institution=context["institution_name"], scope=context["access_scope"],
    )
    return report, filters, context


@login_required
@never_cache
def detail(request, kind):
    report, filters, context = _prepared(request, kind)
    from workloads.selectors import accessible_terms
    term_filter = next((item for item in filters if item["name"] == "term"), None)
    term = accessible_terms(request.user).filter(pk=term_filter["selected"]).first() if term_filter and term_filter["selected"] else None
    from timetabling.models import Schedule
    from timetabling.queries import scoped
    from workloads.selectors import scoped_faculty
    selections = {item["name"]: item["selected"] for item in filters if item["selected"]}
    schedule = scoped(request.user, Schedule.objects.select_related("academic_term", "department")).filter(pk=selections["schedule"]).first() if selections.get("schedule") else None
    person = scoped_faculty(request.user).filter(pk=selections["faculty"]).first() if selections.get("faculty") else None
    query = request.GET.urlencode()
    suffix = f"?{query}" if query else ""
    return render(request, "reporting/detail.html", {
        "report": report, "filters": filters, "term": term, "schedule": schedule, "selected_faculty": person,
        "export_links": [
            {"label": label, "url": reverse("reporting:export", args=[kind, fmt]) + suffix}
            for fmt, label in (("pdf", "Export PDF"), ("xlsx", "Export XLSX"), ("csv", "Export CSV"))
        ] if request.user.has_perm("core.export_report") else [],
        "print_url": reverse("reporting:print", args=[kind]) + suffix,
        "generated_at": timezone.localtime(timezone.now()),
        "generated_by": request.user.get_full_name() or request.user.username,
        "scope_label": context["access_scope"],
    })


@login_required
@never_cache
def print_view(request, kind):
    report, _, context = _prepared(request, kind)
    return render(request, "reporting/print.html", {
        "report": report, "generated_at": timezone.localtime(timezone.now()),
        "generated_by": request.user.get_full_name() or request.user.username,
        "scope_label": context["access_scope"],
        "institution_name": context["institution_name"],
    })


@login_required
@never_cache
def export(request, kind, fmt):
    if fmt not in {"pdf", "xlsx", "csv"}:
        raise Http404("Unknown export format.")
    require_access(request.user, "core.export_report")
    report, _, _ = _prepared(request, kind)
    if fmt == "pdf":
        payload, mime = render_pdf(report), "application/pdf"
    elif fmt == "xlsx":
        payload, mime = render_xlsx(report), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        payload, mime = render_csv(report).encode("utf-8-sig"), "text/csv; charset=utf-8"
    response = HttpResponse(payload, content_type=mime)
    response["Content-Disposition"] = f'attachment; filename="{report["filename_base"]}.{fmt}"'
    record_event("report.exported", actor=request.user, details={"report": kind, "format": fmt})
    return response
