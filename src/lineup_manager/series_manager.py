"""Series-level lineup management.

This module handles:
1. Calculating total lineups needed for a series
2. Distributing generated lineups across contests
3. Creating contest-specific lineup files for submission
"""
import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..common.database import get_database, SeriesDB, ContestDB, LineupDB, LineupPlayerDB
from ..common.models import Series, Contest, Lineup, LineupStatus

logger = logging.getLogger(__name__)


@dataclass
class ContestAllocation:
    """Tracks lineup allocation for a single contest."""
    contest_id: str
    contest_name: str
    max_entries: int
    entry_fee: float
    lineup_start_index: int  # First lineup index (1-based)
    lineup_end_index: int    # Last lineup index (inclusive)
    lineup_count: int        # Number of lineups allocated

    @property
    def lineup_indices(self) -> range:
        """Return range of lineup indices for this contest."""
        return range(self.lineup_start_index, self.lineup_end_index + 1)


@dataclass
class SeriesAllocation:
    """Tracks lineup allocation across all contests in a series."""
    series_id: int
    sport: str
    total_lineups: int
    contest_allocations: list[ContestAllocation]

    def get_contest_for_lineup(self, lineup_index: int) -> Optional[ContestAllocation]:
        """Get the contest allocation for a specific lineup index."""
        for alloc in self.contest_allocations:
            if alloc.lineup_start_index <= lineup_index <= alloc.lineup_end_index:
                return alloc
        return None


class SeriesManager:
    """Manages lineup generation and distribution at the series level."""

    def __init__(self):
        self.db = get_database()

    def calculate_series_allocation(
        self,
        series_id: int,
        max_lineups_per_contest: Optional[int] = None,
    ) -> SeriesAllocation:
        """Calculate how many lineups to generate and how to distribute them.

        Args:
            series_id: Yahoo series ID
            max_lineups_per_contest: Optional cap on lineups per contest
                (defaults to contest's max_entries)

        Returns:
            SeriesAllocation with contest-by-contest breakdown
        """
        session = self.db.get_session()
        try:
            # Get series info
            series = session.query(SeriesDB).filter_by(id=series_id).first()
            if not series:
                raise ValueError(f"Series {series_id} not found")

            # Get all contests in this series, ordered by max_entries (largest first)
            contests = (
                session.query(ContestDB)
                .filter_by(series_id=series_id)
                .order_by(ContestDB.max_entries.desc())
                .all()
            )

            if not contests:
                raise ValueError(f"No contests found for series {series_id}")

            # Calculate allocations
            allocations = []
            current_index = 1

            for contest in contests:
                # Determine how many lineups for this contest
                lineup_count = contest.max_entries
                if max_lineups_per_contest:
                    lineup_count = min(lineup_count, max_lineups_per_contest)

                allocation = ContestAllocation(
                    contest_id=contest.id,
                    contest_name=contest.name,
                    max_entries=contest.max_entries,
                    entry_fee=contest.entry_fee,
                    lineup_start_index=current_index,
                    lineup_end_index=current_index + lineup_count - 1,
                    lineup_count=lineup_count,
                )
                allocations.append(allocation)
                current_index += lineup_count

            total_lineups = sum(a.lineup_count for a in allocations)

            return SeriesAllocation(
                series_id=series_id,
                sport=series.sport,
                total_lineups=total_lineups,
                contest_allocations=allocations,
            )

        finally:
            session.close()

    def assign_lineups_to_contests(
        self,
        series_id: int,
        allocation: Optional[SeriesAllocation] = None,
    ) -> dict[str, list[int]]:
        """Assign generated lineups to their respective contests.

        Args:
            series_id: Yahoo series ID
            allocation: Pre-calculated allocation (calculates if not provided)

        Returns:
            Dict mapping contest_id to list of lineup IDs
        """
        if allocation is None:
            allocation = self.calculate_series_allocation(series_id)

        session = self.db.get_session()
        try:
            # Get all lineups for this series
            lineups = (
                session.query(LineupDB)
                .filter_by(series_id=series_id)
                .order_by(LineupDB.lineup_index)
                .all()
            )

            if not lineups:
                logger.warning(f"No lineups found for series {series_id}")
                return {}

            # Assign lineups to contests based on their index
            assignments = defaultdict(list)

            for lineup in lineups:
                contest_alloc = allocation.get_contest_for_lineup(lineup.lineup_index)
                if contest_alloc:
                    lineup.contest_id = contest_alloc.contest_id
                    lineup.status = "assigned"
                    assignments[contest_alloc.contest_id].append(lineup.id)

            session.commit()
            logger.info(f"Assigned {len(lineups)} lineups to {len(assignments)} contests")

            return dict(assignments)

        finally:
            session.close()

    def export_contest_lineups(
        self,
        contest_id: str,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """Export lineups for a specific contest to CSV file.

        The CSV format is compatible with Yahoo DFS submission.

        Args:
            contest_id: Yahoo contest ID
            output_dir: Directory for output file (defaults to data/lineups/)

        Returns:
            Path to the generated CSV file
        """
        if output_dir is None:
            output_dir = Path("data/lineups")
        output_dir.mkdir(parents=True, exist_ok=True)

        session = self.db.get_session()
        try:
            # Get contest info
            contest = session.query(ContestDB).filter_by(id=contest_id).first()
            if not contest:
                raise ValueError(f"Contest {contest_id} not found")

            # Get lineups for this contest
            lineups = (
                session.query(LineupDB)
                .filter_by(contest_id=contest_id)
                .order_by(LineupDB.lineup_index)
                .all()
            )

            if not lineups:
                raise ValueError(f"No lineups assigned to contest {contest_id}")

            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{contest.sport}_{contest_id}_{len(lineups)}lineups_{timestamp}.csv"
            filepath = output_dir / filename

            # Get position slots from first lineup
            first_lineup = lineups[0]
            players = (
                session.query(LineupPlayerDB)
                .filter_by(lineup_id=first_lineup.id)
                .order_by(LineupPlayerDB.id)
                .all()
            )
            position_slots = [p.roster_position for p in players]

            # Write CSV
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)

                # Header row with position slots
                writer.writerow(position_slots)

                # Lineup rows
                for lineup in lineups:
                    players = (
                        session.query(LineupPlayerDB)
                        .filter_by(lineup_id=lineup.id)
                        .order_by(LineupPlayerDB.id)
                        .all()
                    )
                    # Write player IDs in position order
                    player_ids = [p.yahoo_player_id for p in players]
                    writer.writerow(player_ids)

            logger.info(f"Exported {len(lineups)} lineups to {filepath}")
            return filepath

        finally:
            session.close()

    def export_series_lineups(
        self,
        series_id: int,
        output_dir: Optional[Path] = None,
    ) -> dict[str, Path]:
        """Export lineups for all contests in a series.

        Creates one CSV file per contest.

        Args:
            series_id: Yahoo series ID
            output_dir: Directory for output files

        Returns:
            Dict mapping contest_id to filepath
        """
        session = self.db.get_session()
        try:
            # Get all contests with assigned lineups
            contests = (
                session.query(ContestDB)
                .filter_by(series_id=series_id)
                .all()
            )

            files = {}
            for contest in contests:
                # Check if contest has lineups
                lineup_count = (
                    session.query(LineupDB)
                    .filter_by(contest_id=contest.id)
                    .count()
                )
                if lineup_count > 0:
                    filepath = self.export_contest_lineups(contest.id, output_dir)
                    files[contest.id] = filepath

            return files

        finally:
            session.close()

    def get_series_summary(self, series_id: int) -> dict:
        """Get summary information about a series.

        Args:
            series_id: Yahoo series ID

        Returns:
            Dict with series statistics
        """
        session = self.db.get_session()
        try:
            series = session.query(SeriesDB).filter_by(id=series_id).first()
            if not series:
                return {}

            contests = session.query(ContestDB).filter_by(series_id=series_id).all()
            total_lineups = session.query(LineupDB).filter_by(series_id=series_id).count()
            assigned_lineups = (
                session.query(LineupDB)
                .filter(LineupDB.series_id == series_id)
                .filter(LineupDB.contest_id.isnot(None))
                .count()
            )

            return {
                "series_id": series_id,
                "sport": series.sport,
                "slate_start": series.slate_start,
                "slate_type": series.slate_type,
                "total_contests": len(contests),
                "total_entry_slots": sum(c.max_entries for c in contests),
                "lineups_generated": total_lineups,
                "lineups_assigned": assigned_lineups,
                "contests": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "max_entries": c.max_entries,
                        "entry_fee": c.entry_fee,
                    }
                    for c in contests
                ],
            }

        finally:
            session.close()


def get_series_manager() -> SeriesManager:
    """Get a SeriesManager instance."""
    return SeriesManager()
