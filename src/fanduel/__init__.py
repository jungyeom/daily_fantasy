"""FanDuel DFS integration module.

This module provides read-only API access to FanDuel's DFS platform for
fetching contest and player data. No automated entry submission is supported.

Authentication requires manual extraction of auth tokens from browser dev tools.
"""

from .api import FanDuelApiClient, get_api_client

__all__ = ["FanDuelApiClient", "get_api_client"]
