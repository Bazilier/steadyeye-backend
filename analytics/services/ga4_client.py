class Ga4Client:
    """Client for Google Analytics 4 Data API. Implemented in Phase 4."""

    def fetch_daily_events(self, date):
        """Returns a list of dicts with event metrics for a given date."""
        raise NotImplementedError("Phase 4")

    def fetch_daily_funnel(self, date):
        """Returns a list of dicts with funnel metrics for a given date."""
        raise NotImplementedError("Phase 4")
