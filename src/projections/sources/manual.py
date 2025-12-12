"""Manual projection source - load projections from local CSV files."""
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...common.config import get_config
from ...common.exceptions import ProjectionFetchError
from ...common.models import Projection, Sport
from .base import ProjectionSource

logger = logging.getLogger(__name__)


class ManualProjectionSource(ProjectionSource):
    """Load projections from manually created/edited CSV files.

    Useful for:
    - Your own projection model output
    - Manual adjustments to other sources
    - Testing with known values
    - Overriding specific players
    """

    def __init__(self, file_path: Optional[str] = None, weight: float = 0.0):
        """Initialize manual projection source.

        Args:
            file_path: Path to CSV file (default from config)
            weight: Weight for aggregation (0.0-1.0)
        """
        super().__init__(name="manual", weight=weight)
        config = get_config().projections.manual
        self.file_path = Path(file_path or config.get("file_path", "data/projections/manual.csv"))

    def fetch_projections(
        self,
        sport: Sport,
        slate_date: Optional[datetime] = None,
    ) -> list[Projection]:
        """Load projections from CSV file.

        Expected CSV columns:
        - name (required): Player name
        - team: Team abbreviation
        - position: Position (will be standardized)
        - projected_points (required): Projected fantasy points
        - projected_ownership: Projected ownership % (0-100 or 0-1)
        - floor: Projection floor
        - ceiling: Projection ceiling
        - sport: Sport filter (NFL, NBA, etc.)

        Args:
            sport: Sport to filter by (if sport column exists)
            slate_date: Not used for manual source

        Returns:
            List of Projection objects

        Raises:
            ProjectionFetchError: If file doesn't exist or is invalid
        """
        if not self.file_path.exists():
            logger.warning(f"Manual projection file not found: {self.file_path}")
            return []

        logger.info(f"Loading manual projections from {self.file_path}")

        try:
            projections = []

            with open(self.file_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)

                for row in reader:
                    try:
                        projection = self._parse_row(row, sport)
                        if projection:
                            projections.append(projection)
                    except Exception as e:
                        logger.debug(f"Failed to parse row: {e}")
                        continue

            logger.info(f"Loaded {len(projections)} manual projections")
            return projections

        except Exception as e:
            logger.error(f"Failed to load manual projections: {e}")
            raise ProjectionFetchError(f"Failed to load {self.file_path}: {e}") from e

    def _parse_row(self, row: dict, sport: Sport) -> Optional[Projection]:
        """Parse a CSV row into a Projection.

        Args:
            row: Dict from CSV reader
            sport: Sport to filter by

        Returns:
            Projection object or None if filtered out
        """
        # Filter by sport if column exists
        if "sport" in row:
            row_sport = row["sport"].upper().strip()
            if row_sport and row_sport != sport.value:
                return None

        # Required fields
        name = row.get("name", "").strip()
        if not name:
            return None

        projected_points = self._parse_float(row.get("projected_points", "0"))
        if projected_points == 0:
            # Skip players with no projection
            return None

        # Optional fields
        team = row.get("team", "").upper().strip()
        position = row.get("position", "").upper().strip()

        ownership = None
        if "projected_ownership" in row:
            ownership = self._parse_float(row["projected_ownership"])
            if ownership > 1:
                ownership = ownership / 100  # Convert percentage to decimal

        floor = None
        if "floor" in row:
            floor = self._parse_float(row["floor"])

        ceiling = None
        if "ceiling" in row:
            ceiling = self._parse_float(row["ceiling"])

        return Projection(
            name=name,
            team=team,
            position=position,
            source=self.name,
            projected_points=projected_points,
            projected_ownership=ownership,
            floor=floor,
            ceiling=ceiling,
        )

    def _parse_float(self, value: str) -> float:
        """Parse float from string.

        Args:
            value: String value

        Returns:
            Float value or 0.0
        """
        if not value:
            return 0.0
        try:
            # Remove any non-numeric characters except decimal and minus
            import re
            cleaned = re.sub(r"[^\d.\-]", "", str(value))
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    def is_available(self, sport: Sport) -> bool:
        """Check if manual file exists.

        Args:
            sport: Sport (not used - file may contain any sport)

        Returns:
            True if file exists
        """
        return self.file_path.exists()

    def create_template(self, sport: Sport, output_path: Optional[str] = None) -> Path:
        """Create a template CSV file for manual projections.

        Args:
            sport: Sport for the template
            output_path: Output path (default: self.file_path)

        Returns:
            Path to created template
        """
        output = Path(output_path) if output_path else self.file_path
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "name",
                    "team",
                    "position",
                    "projected_points",
                    "projected_ownership",
                    "floor",
                    "ceiling",
                    "sport",
                ],
            )
            writer.writeheader()

            # Add example rows
            examples = self._get_example_players(sport)
            for example in examples:
                writer.writerow(example)

        logger.info(f"Created manual projection template: {output}")
        return output

    def _get_example_players(self, sport: Sport) -> list[dict]:
        """Get example players for template.

        Args:
            sport: Sport for examples

        Returns:
            List of example player dicts
        """
        examples = {
            Sport.NFL: [
                {"name": "Patrick Mahomes", "team": "KC", "position": "QB", "projected_points": "22.5", "projected_ownership": "15", "floor": "15", "ceiling": "35", "sport": "NFL"},
                {"name": "Christian McCaffrey", "team": "SF", "position": "RB", "projected_points": "20.0", "projected_ownership": "25", "floor": "12", "ceiling": "30", "sport": "NFL"},
            ],
            Sport.NBA: [
                {"name": "Luka Doncic", "team": "DAL", "position": "PG", "projected_points": "55.0", "projected_ownership": "30", "floor": "40", "ceiling": "75", "sport": "NBA"},
                {"name": "Giannis Antetokounmpo", "team": "MIL", "position": "PF", "projected_points": "52.0", "projected_ownership": "25", "floor": "38", "ceiling": "70", "sport": "NBA"},
            ],
            Sport.MLB: [
                {"name": "Shohei Ohtani", "team": "LAD", "position": "SP", "projected_points": "18.0", "projected_ownership": "20", "floor": "8", "ceiling": "35", "sport": "MLB"},
                {"name": "Aaron Judge", "team": "NYY", "position": "OF", "projected_points": "10.0", "projected_ownership": "18", "floor": "2", "ceiling": "25", "sport": "MLB"},
            ],
            Sport.NHL: [
                {"name": "Connor McDavid", "team": "EDM", "position": "C", "projected_points": "8.5", "projected_ownership": "25", "floor": "3", "ceiling": "15", "sport": "NHL"},
                {"name": "Auston Matthews", "team": "TOR", "position": "C", "projected_points": "7.5", "projected_ownership": "20", "floor": "2", "ceiling": "14", "sport": "NHL"},
            ],
        }

        return examples.get(sport, [])

    def save_projections(self, projections: list[Projection], output_path: Optional[str] = None) -> Path:
        """Save projections to CSV file.

        Args:
            projections: List of projections to save
            output_path: Output path (default: self.file_path)

        Returns:
            Path to saved file
        """
        output = Path(output_path) if output_path else self.file_path
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "name",
                    "team",
                    "position",
                    "projected_points",
                    "projected_ownership",
                    "floor",
                    "ceiling",
                    "source",
                ],
            )
            writer.writeheader()

            for proj in projections:
                writer.writerow({
                    "name": proj.name,
                    "team": proj.team,
                    "position": proj.position,
                    "projected_points": proj.projected_points,
                    "projected_ownership": proj.projected_ownership or "",
                    "floor": proj.floor or "",
                    "ceiling": proj.ceiling or "",
                    "source": proj.source,
                })

        logger.info(f"Saved {len(projections)} projections to {output}")
        return output
