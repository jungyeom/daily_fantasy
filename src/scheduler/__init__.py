"""Scheduler module for automated DFS operations.

This module provides:
- Contest filtering based on entry fee and multi-entry rules
- Fill rate monitoring for submission timing
- Injury monitoring and player swapping
- Email alerts via SendGrid
"""

from .alerts import AlertSeverity, EmailAlerter, get_alerter, send_alert
from .contest_filter import ContestFilter, ContestFilterConfig
from .fill_monitor import FillMonitor, FillMonitorConfig
from .player_swapper import PlayerSwapper, check_and_swap_injuries

__all__ = [
    "AlertSeverity",
    "ContestFilter",
    "ContestFilterConfig",
    "EmailAlerter",
    "FillMonitor",
    "FillMonitorConfig",
    "PlayerSwapper",
    "check_and_swap_injuries",
    "get_alerter",
    "send_alert",
]
