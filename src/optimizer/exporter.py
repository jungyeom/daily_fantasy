"""Export lineups to Yahoo CSV format for upload."""
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..common.config import get_config
from ..common.models import Lineup, Sport

logger = logging.getLogger(__name__)


# Yahoo roster position order by sport (multi-game classic format)
YAHOO_POSITION_ORDER = {
    Sport.NFL: ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DEF"],
    Sport.NBA: ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL"],
    Sport.MLB: ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"],
    Sport.NHL: ["G", "G", "C", "C", "W", "W", "W", "D", "D"],
    Sport.PGA: ["G", "G", "G", "G", "G", "G"],
}

# Single-game (showdown) roster position order
YAHOO_SINGLE_GAME_POSITIONS = ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"]


class LineupExporter:
    """Exports lineups to various formats for Yahoo upload."""

    def __init__(self, sport: Sport):
        """Initialize exporter.

        Args:
            sport: Sport for position ordering
        """
        self.sport = sport
        self.config = get_config()
        self.output_dir = Path(self.config.data_dir) / "lineups"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_for_upload(
        self,
        lineups: list[Lineup],
        contest_id: str,
        output_path: Optional[str] = None,
    ) -> Path:
        """Export lineups to Yahoo upload CSV format.

        Yahoo expects CSV with columns matching roster positions.
        Each row is one lineup with player IDs in position columns.

        Args:
            lineups: List of lineups to export
            contest_id: Contest ID for filename
            output_path: Optional custom output path

        Returns:
            Path to exported CSV
        """
        if not lineups:
            raise ValueError("No lineups to export")

        # Determine output path
        if output_path:
            filepath = Path(output_path)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"yahoo_upload_{contest_id}_{timestamp}.csv"
            filepath = self.output_dir / filename

        # Check if this is a single-game lineup (has SUPERSTAR position)
        first_lineup_positions = [p.roster_position for p in lineups[0].players]
        is_single_game = "SUPERSTAR" in first_lineup_positions

        # Get position order for sport
        if is_single_game:
            positions = YAHOO_SINGLE_GAME_POSITIONS
        else:
            positions = YAHOO_POSITION_ORDER.get(self.sport, [])
            if not positions:
                # Use positions from first lineup
                positions = first_lineup_positions

        # Write CSV using list-based approach to handle duplicate position names
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(positions)  # Header

            for lineup in lineups:
                row = self._lineup_to_row_list(lineup, positions)
                writer.writerow(row)

        logger.info(f"Exported {len(lineups)} lineups to {filepath}")
        return filepath

    def _lineup_to_row_list(self, lineup: Lineup, positions: list[str]) -> list[str]:
        """Convert lineup to CSV row list.

        Args:
            lineup: Lineup to convert
            positions: Position column order

        Returns:
            List of player IDs matching position order
        """
        # Initialize row with empty strings
        row = [""] * len(positions)

        # Group players by position, maintaining order
        players_by_pos = {}
        for player in lineup.players:
            pos = player.roster_position
            if pos not in players_by_pos:
                players_by_pos[pos] = []
            player_id = player.player_game_code or player.yahoo_player_id
            players_by_pos[pos].append(player_id)

        # Fill in the row by position
        position_usage = {}
        for i, pos in enumerate(positions):
            if pos not in position_usage:
                position_usage[pos] = 0

            if pos in players_by_pos and position_usage[pos] < len(players_by_pos[pos]):
                row[i] = players_by_pos[pos][position_usage[pos]]
                position_usage[pos] += 1

        return row

    def export_detailed(
        self,
        lineups: list[Lineup],
        contest_id: str,
        output_path: Optional[str] = None,
    ) -> Path:
        """Export lineups with detailed player information.

        Useful for review before submission.

        Args:
            lineups: List of lineups to export
            contest_id: Contest ID for filename
            output_path: Optional custom output path

        Returns:
            Path to exported CSV
        """
        if not lineups:
            raise ValueError("No lineups to export")

        # Determine output path
        if output_path:
            filepath = Path(output_path)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"lineups_detailed_{contest_id}_{timestamp}.csv"
            filepath = self.output_dir / filename

        # Write CSV with one row per player
        fieldnames = [
            "lineup_num",
            "lineup_id",
            "position",
            "player_id",
            "player_name",
            "team",
            "salary",
            "projected_points",
            "total_salary",
            "total_projected",
        ]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for i, lineup in enumerate(lineups, 1):
                for player in lineup.players:
                    writer.writerow({
                        "lineup_num": i,
                        "lineup_id": lineup.id or "",
                        "position": player.roster_position,
                        "player_id": player.yahoo_player_id,
                        "player_name": player.name,
                        "team": "",  # Would need to look up
                        "salary": player.salary,
                        "projected_points": f"{player.projected_points:.2f}",
                        "total_salary": lineup.total_salary,
                        "total_projected": f"{lineup.projected_points:.2f}",
                    })

        logger.info(f"Exported detailed lineups to {filepath}")
        return filepath

    def export_summary(
        self,
        lineups: list[Lineup],
        contest_id: str,
        output_path: Optional[str] = None,
    ) -> Path:
        """Export lineup summary statistics.

        Args:
            lineups: List of lineups
            contest_id: Contest ID
            output_path: Optional output path

        Returns:
            Path to exported CSV
        """
        if not lineups:
            raise ValueError("No lineups to export")

        # Determine output path
        if output_path:
            filepath = Path(output_path)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"lineups_summary_{contest_id}_{timestamp}.csv"
            filepath = self.output_dir / filename

        fieldnames = [
            "lineup_num",
            "lineup_id",
            "lineup_hash",
            "total_salary",
            "projected_points",
            "status",
            "player_1", "player_2", "player_3", "player_4",
            "player_5", "player_6", "player_7", "player_8", "player_9",
        ]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for i, lineup in enumerate(lineups, 1):
                row = {
                    "lineup_num": i,
                    "lineup_id": lineup.id or "",
                    "lineup_hash": lineup.lineup_hash or "",
                    "total_salary": lineup.total_salary,
                    "projected_points": f"{lineup.projected_points:.2f}",
                    "status": lineup.status.value,
                }

                # Add player names
                for j, player in enumerate(lineup.players, 1):
                    if j <= 9:
                        row[f"player_{j}"] = player.name

                writer.writerow(row)

        logger.info(f"Exported lineup summary to {filepath}")
        return filepath

    def format_for_display(self, lineups: list[Lineup]) -> str:
        """Format lineups for console display.

        Args:
            lineups: List of lineups

        Returns:
            Formatted string
        """
        lines = []

        for i, lineup in enumerate(lineups, 1):
            lines.append(f"\n{'='*60}")
            lines.append(f"LINEUP {i} (ID: {lineup.id or 'N/A'})")
            lines.append(f"Projected: {lineup.projected_points:.1f} pts | Salary: ${lineup.total_salary}")
            lines.append("-" * 60)

            for player in lineup.players:
                lines.append(
                    f"{player.roster_position:5} | {player.name:28} | ${player.salary:5} | {player.projected_points:.1f} pts"
                )

            lines.append("=" * 60)

        # Summary
        if lineups:
            avg_proj = sum(l.projected_points for l in lineups) / len(lineups)
            avg_sal = sum(l.total_salary for l in lineups) / len(lineups)
            lines.append(f"\nTotal Lineups: {len(lineups)}")
            lines.append(f"Avg Projected: {avg_proj:.1f} pts")
            lines.append(f"Avg Salary: ${avg_sal:.0f}")

        return "\n".join(lines)


def export_lineups(
    lineups: list[Lineup],
    sport: Sport,
    contest_id: str,
    format: str = "upload",
) -> Path:
    """Convenience function to export lineups.

    Args:
        lineups: Lineups to export
        sport: Sport
        contest_id: Contest ID
        format: 'upload', 'detailed', or 'summary'

    Returns:
        Path to exported file
    """
    exporter = LineupExporter(sport)

    if format == "detailed":
        return exporter.export_detailed(lineups, contest_id)
    elif format == "summary":
        return exporter.export_summary(lineups, contest_id)
    else:
        return exporter.export_for_upload(lineups, contest_id)
