"""Calendar creation and the staff working-term policy. Existing periods are never rewritten."""
from django.db import transaction
from django.db.models import Case, When, Value, IntegerField
from django.utils import timezone
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
import logging

from accounts.permissions import is_system_admin, can_sign_in
from audit.services import record_event
from .models import AcademicCalendar, AcademicYear, AcademicTerm


def calendar_enabled():
    return AcademicCalendar.objects.filter(is_enabled=True).exists()


def ensure_calendar_periods(today=None):
    today = today or timezone.localdate()
    rule = AcademicCalendar.objects.filter(is_enabled=True).first()
    if not rule or rule.last_rollover_date == today:
        return
    with transaction.atomic():
        rule = AcademicCalendar.objects.select_for_update().get(pk=rule.pk)
        if not rule.is_enabled or rule.last_rollover_date == today:
            return
        patterns = list(rule.patterns.filter(semester__is_active=True).select_related("semester"))
        if not patterns:
            return
        current_start_year = today.year - ((today.month, today.day) < (rule.year_start_month, rule.year_start_day))
        for start_year in (current_start_year, current_start_year + 1):
            start, end = rule.year_dates(start_year)
            year = rule.academic_years.filter(start_date__year=start_year).first()
            if year is None:
                year = AcademicYear.objects.filter(start_date=start, end_date=end).first()
            if year is None:
                year = AcademicYear.objects.create(label=f"{start_year}-{start_year + 1}", start_date=start, end_date=end)
                record_event("calendar.year_created", obj=year, details={"calendar_id": rule.pk})
            rule.academic_years.add(year)
            for pattern in patterns:
                # Reuse existing demo/manual term identity and dates for this semester.
                if year.terms.filter(semester=pattern.semester).exists():
                    continue
                term_start, term_end = pattern.dates_for_year(start_year, calendar=rule)
                term = AcademicTerm.objects.create(academic_year=year, semester=pattern.semester,
                    code=pattern.code, start_date=term_start, end_date=term_end)
                record_event("calendar.term_created", obj=term, details={"calendar_id": rule.pk})
        AcademicCalendar.objects.filter(pk=rule.pk).update(last_rollover_date=today)


def current_terms():
    today = timezone.localdate()
    qs = AcademicTerm.objects.filter(is_active=True, academic_year__is_active=True,
        semester__is_active=True, start_date__lte=today, end_date__gte=today)
    rule = AcademicCalendar.objects.filter(is_enabled=True).first()
    if rule:
        qs = qs.filter(academic_year__in=rule.academic_years.all(), semester_id__in=rule.patterns.values("semester_id"))
    return qs.order_by("-start_date", "-pk")[:1]


def staff_uses_current_term(user):
    return not is_system_admin(user) and calendar_enabled()


def constrain_term_queryset(user, queryset, lookup="academic_term"):
    if staff_uses_current_term(user):
        return queryset.filter(**{f"{lookup}__in": current_terms()})
    return queryset


def prioritize_current_term(queryset):
    today = timezone.localdate()
    return queryset.annotate(calendar_priority=Case(
        When(start_date__lte=today, end_date__gte=today, is_active=True,
             academic_year__is_active=True, semester__is_active=True, then=Value(0)),
        default=Value(1), output_field=IntegerField())).order_by("calendar_priority", "-start_date", "code")


def use_current_term_field(form, user):
    """Hide a staff term selector while retaining queryset validation of submitted values."""
    if "academic_term" not in form.fields or not staff_uses_current_term(user):
        return
    field = form.fields["academic_term"]
    current = field.queryset.first()
    field.widget = forms.HiddenInput()
    form.initial["academic_term"] = current.pk if current else None
    if form.is_bound and "academic_term" not in form.data:
        form.data = form.data.copy()
        form.data["academic_term"] = current.pk if current else ""


class AcademicCalendarMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and can_sign_in(request.user):
            try:
                ensure_calendar_periods()
            except (ValidationError, IntegrityError):
                logging.getLogger(__name__).exception("Automatic calendar creation failed; existing periods were preserved.")
                if is_system_admin(request.user):
                    messages.warning(request, "Automatic calendar creation could not finish. Review the calendar settings and existing academic years and terms for conflicting dates or codes.")
        return self.get_response(request)
