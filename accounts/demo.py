"""Shared provisioning for fictional development accounts only."""
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from audit.services import record_event
from core.models import College
from faculty.models import Faculty
from .models import AdminProfile


DEMO_FACULTY_ACCOUNTS = (
    ("DEMO-F1", "dev.faculty"),
    ("DEMO-F2", "dev.faculty2"),
    ("DEMO-F1B", "dev.faculty3"),
)

DEMO_STAFF_COLLEGES = ("CAS", "CCJE", "CIT", "CAF", "CBA")


@transaction.atomic
def create_college_staff_demo_accounts(*, actor=None):
    """Create restricted sample staff; never overwrite existing account decisions."""
    colleges = {college.code: college for college in
                College.objects.filter(code__in=DEMO_STAFF_COLLEGES, is_active=True)}
    missing = set(DEMO_STAFF_COLLEGES) - colleges.keys()
    if missing:
        raise ValidationError(f"Create the active sample colleges first: {', '.join(sorted(missing))}.")
    credentials = []
    for code in DEMO_STAFF_COLLEGES:
        college = colleges[code]
        username = f"dev.staff.{code.lower()}"
        existing = get_user_model().objects.select_for_update().filter(username=username).first()
        if existing:
            profile = AdminProfile.objects.select_for_update().filter(user=existing).first()
            if (existing.is_superuser or existing.is_staff or not profile
                or profile.role != AdminProfile.Role.STAFF or profile.college_id != college.pk
                or profile.department_id is not None):
                raise ValidationError(f"{username} already exists with a different role or scope; resolve it through account administration.")
            continue
        password = secrets.token_urlsafe(20)
        user = get_user_model().objects.create_user(username=username, password=password,
            first_name=code, last_name="Demo Staff", email=f"staff-{code.lower()}@example.invalid")
        AdminProfile.objects.create(user=user, role=AdminProfile.Role.STAFF, college=college)
        record_event("account.seed", actor=actor, obj=user, details={
            "college_id": college.pk, "role": AdminProfile.Role.STAFF, "source": "college_staff_demo"})
        credentials.append((username, password))
    return credentials


@transaction.atomic
def create_faculty_demo_accounts(*, actor=None):
    credentials = []
    for employee_id, username in DEMO_FACULTY_ACCOUNTS:
        person = Faculty.objects.select_for_update().filter(employee_id=employee_id, is_active=True).first()
        if not person or person.user_id:
            continue
        if get_user_model().objects.filter(username=username).exists():
            raise ValidationError(f"{username} already exists without this teaching identity; link it through account administration.")
        password = secrets.token_urlsafe(20)
        user = get_user_model().objects.create_user(username=username, password=password,
            first_name=person.first_name, last_name=person.last_name)
        person.user = user
        person.save()
        AdminProfile.objects.create(user=user, role=AdminProfile.Role.FACULTY)
        record_event("account.seed", actor=actor, obj=user, details={"faculty_id": person.pk})
        record_event("account.faculty_linked", actor=actor, obj=user, details={"faculty_id": person.pk})
        credentials.append((username, password))
    return credentials


def save_demo_credentials(credentials):
    """Save new passwords only after successful database commit; never log them."""
    if not credentials:
        return
    credential_file = settings.BASE_DIR / ".local" / "development-credentials.txt"

    def persist():
        credential_file.parent.mkdir(exist_ok=True)
        with credential_file.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(f"{username}: {password}" for username, password in credentials) + "\n")

    transaction.on_commit(persist)
