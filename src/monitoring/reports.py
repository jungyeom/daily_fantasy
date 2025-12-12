"""Performance reports and analytics."""
import logging
from datetime import datetime
from typing import Optional

from ..common.config import get_config
from ..common.models import Sport
from .history import HistoryTracker

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates performance reports and analytics."""

    def __init__(self):
        """Initialize report generator."""
        self.config = get_config()
        self.history = HistoryTracker()

    def generate_daily_summary(self, sport: Optional[Sport] = None) -> str:
        """Generate daily performance summary.

        Args:
            sport: Optional sport filter

        Returns:
            Formatted report string
        """
        stats = self.history.get_stats_by_period("daily", sport, num_periods=1)

        if not stats:
            return "No activity today."

        today = stats[0]
        lines = [
            "=" * 60,
            f"DAILY SUMMARY - {today['period']}",
            "=" * 60,
            f"Entries: {today['total_entries']}",
            f"Fees: ${today['total_fees']:.2f}",
            f"Winnings: ${today['total_winnings']:.2f}",
            f"Profit: ${today['profit']:.2f}",
            f"ROI: {today['roi_percent']:.1f}%",
            f"Best Finish: {today['best_finish']:,}" if today['best_finish'] > 0 else "Best Finish: N/A",
            "=" * 60,
        ]

        return "\n".join(lines)

    def generate_weekly_summary(self, sport: Optional[Sport] = None) -> str:
        """Generate weekly performance summary.

        Args:
            sport: Optional sport filter

        Returns:
            Formatted report string
        """
        stats = self.history.get_stats_by_period("weekly", sport, num_periods=1)

        if not stats:
            return "No activity this week."

        week = stats[0]
        lines = [
            "=" * 60,
            f"WEEKLY SUMMARY - {week['period']}",
            "=" * 60,
            f"Entries: {week['total_entries']}",
            f"Fees: ${week['total_fees']:.2f}",
            f"Winnings: ${week['total_winnings']:.2f}",
            f"Profit: ${week['profit']:.2f}",
            f"ROI: {week['roi_percent']:.1f}%",
            f"Best Finish: {week['best_finish']:,}" if week['best_finish'] > 0 else "Best Finish: N/A",
            "=" * 60,
        ]

        return "\n".join(lines)

    def generate_overall_report(self) -> str:
        """Generate comprehensive overall report.

        Returns:
            Formatted report string
        """
        lines = [
            "=" * 70,
            "OVERALL PERFORMANCE REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 70,
            "",
        ]

        # Overall stats
        overall = self.history.get_overall_stats()
        lines.extend([
            "OVERALL STATISTICS",
            "-" * 40,
            f"Contests Entered: {overall['contests_entered']}",
            f"Total Entries: {overall['total_entries']}",
            f"Total Fees: ${overall['total_fees']:.2f}",
            f"Total Winnings: ${overall['total_winnings']:.2f}",
            f"Net Profit: ${overall['profit']:.2f}",
            f"ROI: {overall['roi_percent']:.1f}%",
            f"ITM Rate: {overall['itm_rate']:.1f}%",
            f"Best Finish: {overall['best_finish']:,}" if overall['best_finish'] > 0 else "Best Finish: N/A",
            "",
        ])

        # Stats by sport
        sport_stats = self.history.get_stats_by_sport()
        if sport_stats:
            lines.extend([
                "PERFORMANCE BY SPORT",
                "-" * 40,
            ])

            for stat in sport_stats:
                profit_str = f"+${stat['profit']:.2f}" if stat['profit'] >= 0 else f"-${abs(stat['profit']):.2f}"
                lines.append(
                    f"{stat['sport']:8} | Entries: {stat['total_entries']:4} | "
                    f"Profit: {profit_str:>10} | ROI: {stat['roi_percent']:+.1f}%"
                )
            lines.append("")

        # Recent weekly trend
        weekly_stats = self.history.get_stats_by_period("weekly", num_periods=4)
        if weekly_stats:
            lines.extend([
                "LAST 4 WEEKS",
                "-" * 40,
            ])

            for week in weekly_stats:
                profit_str = f"+${week['profit']:.2f}" if week['profit'] >= 0 else f"-${abs(week['profit']):.2f}"
                lines.append(
                    f"{week['period']:20} | Entries: {week['total_entries']:4} | "
                    f"Profit: {profit_str:>10} | ROI: {week['roi_percent']:+.1f}%"
                )
            lines.append("")

        # Contest type breakdown
        type_stats = self.history.get_contest_type_stats()
        if type_stats:
            lines.extend([
                "PERFORMANCE BY ENTRY FEE",
                "-" * 40,
            ])

            for bracket, stat in type_stats.items():
                if stat['entries'] > 0:
                    profit_str = f"+${stat['profit']:.2f}" if stat['profit'] >= 0 else f"-${abs(stat['profit']):.2f}"
                    lines.append(
                        f"{bracket:10} | Entries: {stat['entries']:4} | "
                        f"Profit: {profit_str:>10} | ROI: {stat['roi_percent']:+.1f}%"
                    )
            lines.append("")

        lines.append("=" * 70)

        return "\n".join(lines)

    def generate_player_report(
        self,
        sport: Optional[Sport] = None,
        top_n: int = 20,
    ) -> str:
        """Generate player performance report.

        Args:
            sport: Optional sport filter
            top_n: Number of players to show

        Returns:
            Formatted report string
        """
        player_stats = self.history.get_player_performance(sport, min_usage=3)

        if not player_stats:
            return "No player data available."

        lines = [
            "=" * 70,
            f"PLAYER PERFORMANCE REPORT - {'All Sports' if not sport else sport.value}",
            "=" * 70,
            "",
            "TOP PERFORMERS (vs Projection)",
            "-" * 70,
            f"{'Player':<25} {'Used':>6} {'Avg Pts':>8} {'Projected':>10} {'+/-':>8}",
            "-" * 70,
        ]

        for stat in player_stats[:top_n]:
            plus_minus = stat['plus_minus']
            pm_str = f"+{plus_minus:.1f}" if plus_minus >= 0 else f"{plus_minus:.1f}"
            lines.append(
                f"{stat['player_name']:<25} {stat['times_used']:>6} "
                f"{stat['avg_actual']:>8.1f} {stat['avg_projected']:>10.1f} {pm_str:>8}"
            )

        lines.extend([
            "",
            "WORST PERFORMERS (vs Projection)",
            "-" * 70,
            f"{'Player':<25} {'Used':>6} {'Avg Pts':>8} {'Projected':>10} {'+/-':>8}",
            "-" * 70,
        ])

        for stat in player_stats[-top_n:]:
            plus_minus = stat['plus_minus']
            pm_str = f"+{plus_minus:.1f}" if plus_minus >= 0 else f"{plus_minus:.1f}"
            lines.append(
                f"{stat['player_name']:<25} {stat['times_used']:>6} "
                f"{stat['avg_actual']:>8.1f} {stat['avg_projected']:>10.1f} {pm_str:>8}"
            )

        lines.append("=" * 70)

        return "\n".join(lines)

    def generate_roi_trend(
        self,
        sport: Optional[Sport] = None,
        num_weeks: int = 12,
    ) -> str:
        """Generate ROI trend chart (ASCII).

        Args:
            sport: Optional sport filter
            num_weeks: Number of weeks to show

        Returns:
            ASCII chart string
        """
        weekly_stats = self.history.get_stats_by_period("weekly", sport, num_periods=num_weeks)

        if not weekly_stats:
            return "No data for trend analysis."

        # Reverse to show oldest first
        weekly_stats = list(reversed(weekly_stats))

        lines = [
            "=" * 60,
            f"ROI TREND - {'All Sports' if not sport else sport.value}",
            "=" * 60,
            "",
        ]

        # Find ROI range
        rois = [w['roi_percent'] for w in weekly_stats]
        max_roi = max(rois) if rois else 100
        min_roi = min(rois) if rois else -100

        # Normalize to chart height
        chart_height = 10
        roi_range = max_roi - min_roi
        if roi_range == 0:
            roi_range = 1

        # Draw chart
        for h in range(chart_height, -1, -1):
            roi_at_height = min_roi + (h / chart_height) * roi_range
            line = f"{roi_at_height:+6.0f}% |"

            for week in weekly_stats:
                normalized = (week['roi_percent'] - min_roi) / roi_range * chart_height
                if round(normalized) == h:
                    line += " * "
                else:
                    line += "   "

            lines.append(line)

        # X-axis
        lines.append("        +" + "-" * (len(weekly_stats) * 3))
        lines.append("         " + "".join(f"{i+1:^3}" for i in range(len(weekly_stats))))
        lines.append("         " + "Week" + " " * (len(weekly_stats) * 3 - 4))

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)


def generate_report(report_type: str = "overall", sport: Optional[Sport] = None) -> str:
    """Generate a report.

    Args:
        report_type: 'overall', 'daily', 'weekly', 'player', 'trend'
        sport: Optional sport filter

    Returns:
        Formatted report string
    """
    generator = ReportGenerator()

    if report_type == "daily":
        return generator.generate_daily_summary(sport)
    elif report_type == "weekly":
        return generator.generate_weekly_summary(sport)
    elif report_type == "player":
        return generator.generate_player_report(sport)
    elif report_type == "trend":
        return generator.generate_roi_trend(sport)
    else:
        return generator.generate_overall_report()
