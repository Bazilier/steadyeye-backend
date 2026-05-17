from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run all pull commands in sequence."

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, help='YYYY-MM-DD')
        parser.add_argument('--days', type=int, default=1)

    def handle(self, *args, **options):
        for cmd in ['pull_asa', 'pull_rc', 'pull_ga4']:
            self.stdout.write(self.style.NOTICE(f"Running {cmd}..."))
            try:
                call_command(cmd, **{k: v for k, v in options.items() if k in ('date', 'days') and v is not None})
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"{cmd} failed: {e}"))
        self.stdout.write(self.style.SUCCESS("pull_all complete"))
