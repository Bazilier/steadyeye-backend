class RcClient:
    """Client for RevenueCat REST API. Implemented in Phase 2."""

    def fetch_subscriber(self, app_user_id):
        """Returns subscriber data for a given app_user_id."""
        raise NotImplementedError("Phase 2")

    def list_active_subscribers(self):
        """Returns list of active subscribers for snapshot."""
        raise NotImplementedError("Phase 2")
