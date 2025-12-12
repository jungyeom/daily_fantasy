"""Historical performance tracking and ROI analysis."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from ..common.database import get_database, ContestDB, LineupDB, ResultDB
from ..common.models import Sport

logger = logging.getLogger(__name__)


@dataclass
class PerformanceSummary:
    """Summary of performance over a period."""
    period: str
    sport: Optional[str]
    contests_entered: int
    total_entries: int
    total_fees: Decimal
    total_winnings: Decimal
    profit: Decimal
    roi_percent: float
    itm_rate: float  # In the money rate
    best_finish: int
    avg_finish_percentile: float


class HistoryTracker:
    """Tracks historical performance and calculates statistics."""

    def __init__(self):
        """Initialize history tracker."""
        self.db = get_database()

    def get_overall_stats(self, sport: Optional[Sport] = None) -> dict:
        """Get overall performance statistics.

        Args:
            sport: Optional sport filter

        Returns:
            Dict with overall stats
        """
        session = self.db.get_session()
        try:
            # Base query
            query = (
                session.query(
                    func.count(ResultDB.id).label("total_entries"),
                    func.sum(ResultDB.winnings).label("total_winnings"),
                    func.min(ResultDB.finish_position).label("best_finish"),
                    func.avg(ResultDB.percentile).label("avg_percentile"),
                )
                .join(LineupDB)
                .join(ContestDB)
            )

            if sport:
                query = query.filter(ContestDB.sport == sport.value)

            result = query.first()

            # Get total fees
            fees_query = (
                session.query(func.sum(ContestDB.entry_fee))
                .join(LineupDB)
                .join(ResultDB)
            )
            if sport:
                fees_query = fees_query.filter(ContestDB.sport == sport.value)
            total_fees = fees_query.scalar() or 0

            # Get contests count
            contests_query = (
                session.query(func.count(func.distinct(ContestDB.id)))
                .join(LineupDB)
                .join(ResultDB)
            )
            if sport:
                contests_query = contests_query.filter(ContestDB.sport == sport.value)
            contests_count = contests_query.scalar() or 0

            # Get ITM count (winnings > 0)
            itm_query = (
                session.query(func.count(ResultDB.id))
                .filter(ResultDB.winnings > 0)
            )
            if sport:
                itm_query = itm_query.join(LineupDB).join(ContestDB).filter(ContestDB.sport == sport.value)
            itm_count = itm_query.scalar() or 0

            total_entries = result.total_entries or 0
            total_winnings = Decimal(str(result.total_winnings or 0))
            profit = total_winnings - Decimal(str(total_fees))
            roi = float(profit / Decimal(str(total_fees)) * 100) if total_fees > 0 else 0
            itm_rate = (itm_count / total_entries * 100) if total_entries > 0 else 0

            return {
                "sport": sport.value if sport else "All",
                "contests_entered": contests_count,
                "total_entries": total_entries,
                "total_fees": float(total_fees),
                "total_winnings": float(total_winnings),
                "profit": float(profit),
                "roi_percent": roi,
                "itm_count": itm_count,
                "itm_rate": itm_rate,
                "best_finish": result.best_finish or 0,
                "avg_percentile": result.avg_percentile or 0,
            }

        finally:
            session.close()

    def get_stats_by_sport(self) -> list[dict]:
        """Get performance statistics broken down by sport.

        Returns:
            List of stats dicts for each sport
        """
        stats = []
        for sport in Sport:
            sport_stats = self.get_overall_stats(sport)
            if sport_stats["total_entries"] > 0:
                stats.append(sport_stats)

        # Sort by profit
        stats.sort(key=lambda x: x["profit"], reverse=True)
        return stats

    def get_stats_by_period(
        self,
        period: str = "weekly",
        sport: Optional[Sport] = None,
        num_periods: int = 10,
    ) -> list[dict]:
        """Get performance statistics by time period.

        Args:
            period: 'daily', 'weekly', or 'monthly'
            sport: Optional sport filter
            num_periods: Number of periods to return

        Returns:
            List of stats dicts for each period
        """
        session = self.db.get_session()
        try:
            # Determine period start dates
            now = datetime.utcnow()
            periods = []

            for i in range(num_periods):
                if period == "daily":
                    start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                    end = start + timedelta(days=1)
                    label = start.strftime("%Y-%m-%d")
                elif period == "weekly":
                    start = (now - timedelta(weeks=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                    start = start - timedelta(days=start.weekday())  # Start of week
                    end = start + timedelta(weeks=1)
                    label = f"Week of {start.strftime('%Y-%m-%d')}"
                else:  # monthly
                    start = (now.replace(day=1) - timedelta(days=i*30)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    if start.month == 12:
                        end = start.replace(year=start.year+1, month=1)
                    else:
                        end = start.replace(month=start.month+1)
                    label = start.strftime("%Y-%m")

                periods.append((label, start, end))

            # Calculate stats for each period
            stats = []
            for label, start, end in periods:
                period_stats = self._get_period_stats(session, start, end, sport)
                period_stats["period"] = label
                if period_stats["total_entries"] > 0:
                    stats.append(period_stats)

            return stats

        finally:
            session.close()

    def _get_period_stats(
        self,
        session,
        start: datetime,
        end: datetime,
        sport: Optional[Sport] = None,
    ) -> dict:
        """Get stats for a specific time period.

        Args:
            session: Database session
            start: Period start
            end: Period end
            sport: Optional sport filter

        Returns:
            Stats dict
        """
        query = (
            session.query(
                func.count(ResultDB.id).label("total_entries"),
                func.sum(ResultDB.winnings).label("total_winnings"),
                func.min(ResultDB.finish_position).label("best_finish"),
            )
            .join(LineupDB)
            .join(ContestDB)
            .filter(ContestDB.slate_start >= start)
            .filter(ContestDB.slate_start < end)
        )

        if sport:
            query = query.filter(ContestDB.sport == sport.value)

        result = query.first()

        # Get fees for period
        fees_query = (
            session.query(func.sum(ContestDB.entry_fee))
            .join(LineupDB)
            .join(ResultDB)
            .filter(ContestDB.slate_start >= start)
            .filter(ContestDB.slate_start < end)
        )
        if sport:
            fees_query = fees_query.filter(ContestDB.sport == sport.value)
        total_fees = fees_query.scalar() or 0

        total_entries = result.total_entries or 0
        total_winnings = float(result.total_winnings or 0)
        profit = total_winnings - total_fees
        roi = (profit / total_fees * 100) if total_fees > 0 else 0

        return {
            "total_entries": total_entries,
            "total_fees": total_fees,
            "total_winnings": total_winnings,
            "profit": profit,
            "roi_percent": roi,
            "best_finish": result.best_finish or 0,
        }

    def get_player_performance(
        self,
        sport: Optional[Sport] = None,
        min_usage: int = 5,
    ) -> list[dict]:
        """Get performance statistics by player.

        Args:
            sport: Optional sport filter
            min_usage: Minimum times used to include

        Returns:
            List of player performance dicts
        """
        session = self.db.get_session()
        try:
            from ..common.database import LineupPlayerDB

            query = (
                session.query(
                    LineupPlayerDB.yahoo_player_id,
                    LineupPlayerDB.name,
                    func.count(LineupPlayerDB.id).label("times_used"),
                    func.avg(LineupPlayerDB.actual_points).label("avg_actual"),
                    func.avg(LineupPlayerDB.projected_points).label("avg_projected"),
                )
                .join(LineupDB)
                .join(ResultDB)
                .group_by(LineupPlayerDB.yahoo_player_id, LineupPlayerDB.name)
                .having(func.count(LineupPlayerDB.id) >= min_usage)
            )

            if sport:
                query = query.join(ContestDB).filter(ContestDB.sport == sport.value)

            results = []
            for row in query.all():
                avg_actual = row.avg_actual or 0
                avg_projected = row.avg_projected or 0
                plus_minus = avg_actual - avg_projected

                results.append({
                    "player_id": row.yahoo_player_id,
                    "player_name": row.name,
                    "times_used": row.times_used,
                    "avg_actual": avg_actual,
                    "avg_projected": avg_projected,
                    "plus_minus": plus_minus,
                })

            # Sort by plus/minus
            results.sort(key=lambda x: x["plus_minus"], reverse=True)
            return results

        finally:
            session.close()

    def get_contest_type_stats(self, sport: Optional[Sport] = None) -> dict:
        """Get stats broken down by entry fee bracket.

        Args:
            sport: Optional sport filter

        Returns:
            Dict with stats by entry fee bracket
        """
        session = self.db.get_session()
        try:
            # Define fee brackets
            brackets = [
                ("Free", 0, 0),
                ("$0.25", 0.01, 0.25),
                ("$1", 0.26, 1.00),
                ("$3-$5", 1.01, 5.00),
                ("$10+", 5.01, 1000),
            ]

            stats = {}
            for name, min_fee, max_fee in brackets:
                query = (
                    session.query(
                        func.count(ResultDB.id).label("entries"),
                        func.sum(ResultDB.winnings).label("winnings"),
                    )
                    .join(LineupDB)
                    .join(ContestDB)
                    .filter(ContestDB.entry_fee >= min_fee)
                    .filter(ContestDB.entry_fee <= max_fee)
                )

                if sport:
                    query = query.filter(ContestDB.sport == sport.value)

                result = query.first()
                entries = result.entries or 0
                winnings = float(result.winnings or 0)

                # Calculate fees
                fees_result = (
                    session.query(func.sum(ContestDB.entry_fee))
                    .join(LineupDB)
                    .join(ResultDB)
                    .filter(ContestDB.entry_fee >= min_fee)
                    .filter(ContestDB.entry_fee <= max_fee)
                )
                if sport:
                    fees_result = fees_result.filter(ContestDB.sport == sport.value)
                fees = fees_result.scalar() or 0

                profit = winnings - fees
                roi = (profit / fees * 100) if fees > 0 else 0

                stats[name] = {
                    "entries": entries,
                    "fees": fees,
                    "winnings": winnings,
                    "profit": profit,
                    "roi_percent": roi,
                }

            return stats

        finally:
            session.close()


def get_history_tracker() -> HistoryTracker:
    """Get history tracker instance."""
    return HistoryTracker()
