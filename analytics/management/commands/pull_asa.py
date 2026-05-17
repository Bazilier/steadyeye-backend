from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Pull ASA daily metrics. Implemented in Phase 3."

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, help='YYYY-MM-DD')
        parser.add_argument('--days', type=int, default=1, help='Number of days back from --date')

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("pull_asa: not implemented yet (Phase 3)"))
