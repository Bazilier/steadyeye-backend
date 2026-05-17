"""Pull Apple Search Ads daily metrics into AsaDailyMetrics.

Fetches reports from the Apple Ads Campaign Management API v5 at the
requested granularity levels and upserts one row per
day × level × campaign × country × search_term × keyword × match_type.
"""

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from analytics.alerts import send_alert
from analytics.models import AsaDailyMetrics
from analytics.services.asa_client import AsaClient, AsaError

VALID_LEVELS = ('campaign', 'ad_group', 'keyword', 'search_term')


def _parse_date(value: str, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise CommandError(f"{label} must be YYYY-MM-DD, got: {value!r}")


def resolve_date_range(options: dict, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Resolve the (start, end) report range from command arguments.

    Priority: --start/--end > --backfill-since > --date > --days.
    For --days=N the range is the N days ending today (N=1 → today only).
    """
    today = today or timezone.now().date()
    start = options.get('start')
    end = options.get('end')
    backfill = options.get('backfill_since')
    date = options.get('date')
    days = options.get('days') or 1

    if start and end:
        return _parse_date(start, '--start'), _parse_date(end, '--end')
    if start or end:
        raise CommandError("--start and --end must be given together")
    if backfill:
        return _parse_date(backfill, '--backfill-since'), today
    if date:
        single = _parse_date(date, '--date')
        return single, single
    if days < 1:
        raise CommandError("--days must be >= 1")
    return today - dt.timedelta(days=days - 1), today


def _money(value) -> Decimal | None:
    """Parse an ASA Money object ({amount, currency}) into a Decimal."""
    if isinstance(value, dict):
        amount = value.get('amount')
    else:
        amount = value
    if amount in (None, ''):
        return None
    try:
        return Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class Command(BaseCommand):
    help = "Pull ASA daily metrics into the database."

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, help='YYYY-MM-DD, single day')
        parser.add_argument('--days', type=int, default=1,
                            help='Number of days back from --date or today')
        parser.add_argument('--start', type=str, help='YYYY-MM-DD start of date range')
        parser.add_argument('--end', type=str, help='YYYY-MM-DD end of date range')
        parser.add_argument('--backfill-since', type=str,
                            help='YYYY-MM-DD: pull from this date until today')
        parser.add_argument('--levels', type=str, default='campaign,search_term',
                            help='Comma-separated: campaign,ad_group,keyword,search_term')

    def handle(self, *args, **options):
        # 1. Resolve the date range.
        start_date, end_date = resolve_date_range(options)
        if start_date > end_date:
            raise CommandError(f"start ({start_date}) is after end ({end_date})")

        # 2. Parse and validate levels.
        levels = [lv.strip() for lv in (options.get('levels') or '').split(',') if lv.strip()]
        invalid = [lv for lv in levels if lv not in VALID_LEVELS]
        if invalid:
            raise CommandError(f"Invalid level(s): {', '.join(invalid)}")
        if not levels:
            raise CommandError("No levels requested")

        self.stdout.write(
            f"pull_asa: {start_date} → {end_date}, levels={','.join(levels)}"
        )

        try:
            client = AsaClient()
        except ValueError as exc:
            raise CommandError(str(exc))

        self.fetched = 0   # rows upserted
        self.created = 0   # rows newly inserted
        self.errors = 0    # report fetches that failed
        self.by_level = {lv: 0 for lv in VALID_LEVELS}

        # 3a. Org-wide campaign report (a single call covers all campaigns).
        if 'campaign' in levels:
            try:
                rows = client.fetch_campaign_report(start_date, end_date)
                for row in rows:
                    self._upsert_row(row, level='campaign')
            except AsaError as exc:
                self.errors += 1
                self.stderr.write(self.style.ERROR(f"  campaign report failed: {exc}"))

        # 3b. Per-campaign reports for the remaining levels.
        per_campaign_levels = [lv for lv in levels if lv != 'campaign']
        if per_campaign_levels:
            try:
                campaigns = client.list_campaigns()
            except AsaError as exc:
                raise CommandError(f"list_campaigns failed: {exc}")

            self.stdout.write(f"  {len(campaigns)} campaign(s) to scan")
            fetchers = {
                'ad_group': client.fetch_ad_group_report,
                'keyword': client.fetch_keyword_report,
                'search_term': client.fetch_search_term_report,
            }
            for campaign in campaigns:
                campaign_id = campaign.get('id')
                campaign_name = campaign.get('name') or ''
                for level in per_campaign_levels:
                    try:
                        rows = fetchers[level](campaign_id, start_date, end_date)
                    except AsaError as exc:
                        self.errors += 1
                        self.stderr.write(self.style.ERROR(
                            f"  {level} report for campaign {campaign_id} failed: {exc}"
                        ))
                        continue
                    for row in rows:
                        self._upsert_row(row, level=level, campaign_name=campaign_name)

        # 4-5. Summary + alerts.
        summary = (
            f"pull_asa complete: {self.fetched} rows upserted "
            f"({self.created} new), {self.errors} errors. "
            + ", ".join(f"{lv}={self.by_level[lv]}" for lv in VALID_LEVELS if lv in levels)
        )
        self.stdout.write(self.style.SUCCESS(summary))

        if self.errors > 0:
            send_alert(f"pull_asa had {self.errors} report errors", severity="warning")
        if self.created > 0:
            send_alert(
                f"pull_asa: {self.created} new ASA metric rows for "
                f"{start_date}–{end_date}",
                severity="info",
            )

    def _upsert_row(self, row, level, campaign_name=None):
        """Parse one report row (which spans multiple days) and upsert each day."""
        meta = row.get('metadata') or {}
        name = campaign_name or meta.get('campaignName') or ''
        country = (meta.get('countryOrRegion') or '')[:2].upper()
        match_type = (meta.get('matchType') or '').upper()

        # The model has no ad-group column; for ad_group level the ad group
        # name is stored in the `keyword` column so rows stay distinct.
        if level == 'campaign':
            keyword, search_term = '', ''
        elif level == 'ad_group':
            keyword, search_term = (meta.get('adGroupName') or ''), ''
        elif level == 'keyword':
            keyword, search_term = (meta.get('keyword') or ''), ''
        else:  # search_term
            keyword = meta.get('keyword') or ''
            search_term = meta.get('searchTermText') or ''

        for g in row.get('granularity') or []:
            raw_date = g.get('date')
            if not raw_date:
                continue
            try:
                day = dt.date.fromisoformat(raw_date)
            except (ValueError, TypeError):
                continue

            tap_installs = _int(g.get('tapInstalls'))
            view_installs = _int(g.get('viewInstalls'))
            tap_nd = _int(g.get('tapNewDownloads'))
            view_nd = _int(g.get('viewNewDownloads'))
            tap_rd = _int(g.get('tapRedownloads'))
            view_rd = _int(g.get('viewRedownloads'))
            impressions = _int(g.get('impressions'))
            taps = _int(g.get('taps'))

            spend = _money(g.get('localSpend')) or Decimal('0')
            # ttr derived as a 0–1 ratio so it fits DecimalField(5, 4).
            ttr = None
            if impressions > 0:
                ttr = (Decimal(taps) / Decimal(impressions)).quantize(Decimal('0.0001'))

            defaults = {
                'spend': spend.quantize(Decimal('0.01')),
                'impressions': impressions,
                'taps': taps,
                'installs': tap_installs + view_installs,
                'installs_tap_through': tap_installs,
                'installs_view_through': view_installs,
                'new_downloads_tap_through': tap_nd,
                'new_downloads_view_through': view_nd,
                'new_downloads_total': _int(g.get('totalNewDownloads')) or (tap_nd + view_nd),
                'redownloads_tap_through': tap_rd,
                'redownloads_view_through': view_rd,
                'redownloads_total': _int(g.get('totalRedownloads')) or (tap_rd + view_rd),
                'ttr': ttr,
                'cpt': _quantize4(_money(g.get('avgCPT'))),
                'cpa': _quantize4(_money(g.get('avgCPA'))),
            }

            with transaction.atomic():
                _, created = AsaDailyMetrics.objects.update_or_create(
                    date=day,
                    level=level,
                    campaign_name=name,
                    country=country,
                    search_term=search_term,
                    keyword=keyword,
                    match_type=match_type,
                    defaults=defaults,
                )
            self.fetched += 1
            self.by_level[level] += 1
            if created:
                self.created += 1


def _quantize4(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal('0.0001'))
