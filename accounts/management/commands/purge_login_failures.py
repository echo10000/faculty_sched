from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import LoginFailureBucket


class Command(BaseCommand):
    help = "Remove expired login failure counters. Run periodically in production."

    def handle(self, *args, **options):
        count, _ = LoginFailureBucket.objects.filter(expires_at__lte=timezone.now()).delete()
        self.stdout.write(f"Removed {count} expired login failure counters.")
