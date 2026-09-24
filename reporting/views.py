"""One authorization and data path for screen, print, and downloads."""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import require_access
from audit.services import record_event
from .exports import render_csv, render_pdf, render_xlsx
from .services import available_catalog, build_report


@login_required
def index(request):
    require_access(request.user, "academics.view_academicterm")
    catalog = [
        {**item, "url": reverse("reporting:detail", args=[item["key"]])}
        for item in available_catalog(request.user)
    ]
    return render(request, "reporting/index.html", {"title": "Reports", "catalog": catalog})


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
def detail(request, kind):
    report, filters, context = _prepared(request, kind)
    query = request.GET.urlencode()
    suffix = f"?{query}" if query else ""
    return render(request, "reporting/detail.html", {
        "report": report, "filters": filters,
        "export_links": [
            {"label": label, "url": reverse("reporting:export", args=[kind, fmt]) + suffix}
            for fmt, label in (("pdf", "Export PDF"), ("xlsx", "Export XLSX"), ("csv", "Export CSV"))
        ],
        "print_url": reverse("reporting:print", args=[kind]) + suffix,
        "generated_at": timezone.localtime(timezone.now()),
        "generated_by": request.user.get_full_name() or request.user.username,
        "scope_label": context["access_scope"],
    })


@login_required
def print_view(request, kind):
    report, _, context = _prepared(request, kind)
    return render(request, "reporting/print.html", {
        "report": report, "generated_at": timezone.localtime(timezone.now()),
        "generated_by": request.user.get_full_name() or request.user.username,
        "scope_label": context["access_scope"],
        "institution_name": context["institution_name"],
    })


@login_required
def export(request, kind, fmt):
    if fmt not in {"pdf", "xlsx", "csv"}:
        raise Http404("Unknown export format.")
    report, _, _ = _prepared(request, kind)
    if fmt == "pdf":
        payload, mime = render_pdf(report), "application/pdf"
    elif fmt == "xlsx":
        payload, mime = render_xlsx(report), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        payload, mime = render_csv(report).encode("utf-8-sig"), "text/csv; charset=utf-8"
    response = HttpResponse(payload, content_type=mime)
    response["Content-Disposition"] = f'attachment; filename="{report["filename_base"]}.{fmt}"'
    if kind in {"official", "historical", "approvals", "workload"}:
        record_event("report.exported", actor=request.user, details={"report": kind, "format": fmt})
    return response
