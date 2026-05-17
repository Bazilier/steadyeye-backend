class AsaClient:
    """Client for Apple Search Ads Reporting API. Implemented in Phase 3."""

    def fetch_campaign_metrics(self, date):
        """Returns a list of dicts with campaign-level metrics for a given date."""
        raise NotImplementedError("Phase 3")

    def fetch_search_term_metrics(self, date):
        """Returns a list of dicts with search-term-level metrics."""
        raise NotImplementedError("Phase 3")
