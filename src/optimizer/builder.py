"""Main lineup builder - orchestrates optimization process."""
import logging
from datetime import datetime
from typing import Optional

from ..common.config import get_config
from ..common.database import get_database, LineupDB, LineupPlayerDB, ContestDB
from ..common.models import Lineup, LineupPlayer, LineupStatus, Player, Sport
from .yahoo_optimizer import create_optimizer, is_single_game_contest
from .strategies import GPPStrategy, StrategyBuilder, apply_strategy_to_optimizer

logger = logging.getLogger(__name__)


class LineupBuilder:
    """Builds optimized lineups for Yahoo DFS contests."""

    def __init__(
        self,
        sport: Sport,
        single_game: bool = False,
        salary_cap: Optional[int] = None,
    ):
        """Initialize lineup builder.

        Args:
            sport: Sport to build lineups for
            single_game: If True, use single-game optimizer (SUPERSTAR + FLEX)
            salary_cap: Salary cap (used for single-game, varies by seriesId)
        """
        self.sport = sport
        self.single_game = single_game
        self.salary_cap = salary_cap
        self.config = get_config()
        self.db = get_database()
        self.optimizer = create_optimizer(sport, single_game=single_game, salary_cap=salary_cap)
        self.strategy_builder = StrategyBuilder(sport)
        self._current_strategy: Optional[GPPStrategy] = None

    def build_lineups(
        self,
        players: list[Player],
        num_lineups: int,
        contest_id: str,
        strategy: Optional[GPPStrategy] = None,
        save_to_db: bool = True,
    ) -> list[Lineup]:
        """Build optimized lineups for a contest.

        Args:
            players: Player pool with projections
            num_lineups: Number of lineups to generate
            contest_id: Contest ID for tracking
            strategy: Optional GPP strategy (uses default if None)
            save_to_db: Whether to save lineups to database

        Returns:
            List of Lineup objects
        """
        if not players:
            logger.warning("No players provided for lineup building")
            return []

        logger.info(f"Building {num_lineups} lineups for contest {contest_id}")

        # Use default strategy if none provided
        if strategy is None:
            strategy = self.strategy_builder.build_default_strategy()
        self._current_strategy = strategy

        # Filter players with valid projections
        valid_players = [p for p in players if p.projected_points and p.projected_points > 0]
        logger.info(f"Using {len(valid_players)} players with valid projections")

        # Filter out injured players (INJ, O are excluded; GTD players are kept)
        excluded_statuses = {"INJ", "O"}
        healthy_players = [
            p for p in valid_players
            if not p.injury_status or p.injury_status not in excluded_statuses
        ]
        injured_count = len(valid_players) - len(healthy_players)
        if injured_count > 0:
            logger.info(f"Excluded {injured_count} injured/out players (keeping GTD)")
        valid_players = healthy_players

        # Single-game requires fewer players (5 per lineup vs 8-10 for classic)
        min_players = 5 if self.single_game else 8
        if len(valid_players) < min_players:
            error_msg = f"Not enough players with projections ({len(valid_players)}, need {min_players}) for contest {contest_id}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Load players into optimizer
        self.optimizer.load_players(valid_players)

        # Apply strategy
        apply_strategy_to_optimizer(self.optimizer, strategy, valid_players)

        # Generate lineups
        try:
            pdfs_lineups = self.optimizer.optimize(
                num_lineups=num_lineups,
                randomness=strategy.randomness,
            )
        except Exception as e:
            logger.error(f"Optimization failed: {e}")
            return []

        # Convert to our Lineup model
        lineups = []
        for pdfs_lineup in pdfs_lineups:
            lineup = self._convert_pdfs_lineup(pdfs_lineup, contest_id, valid_players)
            if lineup:
                lineups.append(lineup)

        logger.info(f"Generated {len(lineups)} valid lineups")

        # Save to database
        if save_to_db and lineups:
            self._save_lineups(lineups, contest_id)

        return lineups

    def _convert_pdfs_lineup(
        self,
        pdfs_lineup,
        contest_id: str,
        players: list[Player],
    ) -> Optional[Lineup]:
        """Convert pydfs-lineup-optimizer lineup to our model.

        Preserves player_game_code from Yahoo API for CSV upload.
        For single-game lineups, handles SUPERSTAR/FLEX suffixed player IDs.

        Args:
            pdfs_lineup: Lineup from pydfs optimizer
            contest_id: Contest ID
            players: Player pool for additional info

        Returns:
            Lineup object or None if conversion fails
        """
        try:
            # Create player lookup by yahoo_player_id
            player_lookup = {p.yahoo_player_id: p for p in players}

            lineup_players = []
            total_salary = 0
            projected_points = 0.0

            for pdfs_player in pdfs_lineup.players:
                # For single-game, player IDs have _SUPERSTAR or _FLEX suffix
                # Extract the original yahoo_player_id
                player_id = pdfs_player.id
                is_superstar = False

                if self.single_game:
                    if player_id.endswith("_SUPERSTAR"):
                        player_id = player_id.replace("_SUPERSTAR", "")
                        is_superstar = True
                    elif player_id.endswith("_FLEX"):
                        player_id = player_id.replace("_FLEX", "")

                # Get our Player object to retrieve player_game_code
                original_player = player_lookup.get(player_id)

                # player_game_code is required for CSV upload
                player_game_code = ""
                if original_player and original_player.player_game_code:
                    player_game_code = original_player.player_game_code
                else:
                    logger.warning(f"Missing player_game_code for {pdfs_player.full_name}")

                # For single-game, determine roster position from optimizer result
                if self.single_game:
                    roster_position = "SUPERSTAR" if is_superstar else "FLEX"
                    # For projected points, use original (non-boosted) value
                    if is_superstar and original_player:
                        fppg = original_player.projected_points or pdfs_player.fppg
                    else:
                        fppg = pdfs_player.fppg
                else:
                    roster_position = pdfs_player.lineup_position
                    fppg = pdfs_player.fppg

                # Determine actual position
                if self.single_game and original_player:
                    actual_position = original_player.position
                else:
                    actual_position = pdfs_player.positions[0] if pdfs_player.positions else ""

                lineup_player = LineupPlayer(
                    yahoo_player_id=player_id,  # Use clean ID without suffix
                    player_game_code=player_game_code,
                    name=pdfs_player.full_name,
                    roster_position=roster_position,
                    actual_position=actual_position,
                    salary=pdfs_player.salary,
                    projected_points=fppg,
                )

                lineup_players.append(lineup_player)
                total_salary += pdfs_player.salary
                projected_points += fppg

            # Create lineup - series_id will be set when assigned to a series
            lineup = Lineup(
                series_id=0,  # Will be updated when saved
                contest_id=contest_id,
                players=lineup_players,
                total_salary=total_salary,
                projected_points=projected_points,
                status=LineupStatus.GENERATED,
            )

            # Calculate hash for deduplication
            lineup.lineup_hash = lineup.calculate_hash()

            return lineup

        except Exception as e:
            logger.error(f"Failed to convert lineup: {e}")
            return None

    def _save_lineups(self, lineups: list[Lineup], contest_id: str) -> None:
        """Save lineups to database.

        Args:
            lineups: List of lineups to save
            contest_id: Contest ID
        """
        session = self.db.get_session()
        try:
            saved_count = 0
            for lineup in lineups:
                # Check for duplicate
                existing = (
                    session.query(LineupDB)
                    .filter_by(contest_id=contest_id, lineup_hash=lineup.lineup_hash)
                    .first()
                )

                if existing:
                    logger.debug(f"Skipping duplicate lineup: {lineup.lineup_hash}")
                    lineup.id = existing.id
                    continue

                # Create lineup record
                db_lineup = LineupDB(
                    contest_id=contest_id,
                    lineup_hash=lineup.lineup_hash,
                    total_salary=lineup.total_salary,
                    projected_points=lineup.projected_points,
                    status=LineupStatus.GENERATED.value,
                )
                session.add(db_lineup)
                session.flush()  # Get ID

                # Create player records
                for player in lineup.players:
                    db_player = LineupPlayerDB(
                        lineup_id=db_lineup.id,
                        yahoo_player_id=player.yahoo_player_id,
                        player_game_code=player.player_game_code,
                        name=player.name,
                        roster_position=player.roster_position,
                        actual_position=player.actual_position,
                        salary=player.salary,
                        projected_points=player.projected_points,
                    )
                    session.add(db_player)

                lineup.id = db_lineup.id
                saved_count += 1

            session.commit()
            logger.info(f"Saved {saved_count} new lineups to database")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save lineups: {e}")
        finally:
            session.close()

    def get_max_entries(self, contest_id: str) -> int:
        """Get maximum entries allowed for a contest.

        Args:
            contest_id: Contest ID

        Returns:
            Maximum entries, or default from config
        """
        session = self.db.get_session()
        try:
            contest = session.query(ContestDB).filter_by(id=contest_id).first()
            if contest and contest.max_entries:
                return contest.max_entries
        finally:
            session.close()

        # Default from config
        return 150  # Common max for GPPs

    def build_lineups_for_contest(
        self,
        players: list[Player],
        contest_id: str,
        strategy: Optional[GPPStrategy] = None,
    ) -> list[Lineup]:
        """Build max lineups for a contest based on entry limits.

        Args:
            players: Player pool with projections
            contest_id: Contest ID
            strategy: Optional GPP strategy

        Returns:
            List of Lineup objects
        """
        max_entries = self.get_max_entries(contest_id)
        return self.build_lineups(
            players=players,
            num_lineups=max_entries,
            contest_id=contest_id,
            strategy=strategy,
        )

    def print_lineup(self, lineup: Lineup) -> str:
        """Format lineup for display.

        Args:
            lineup: Lineup to format

        Returns:
            Formatted string
        """
        lines = [
            f"Lineup (ID: {lineup.id or 'N/A'})",
            f"Projected: {lineup.projected_points:.1f} pts | Salary: ${lineup.total_salary}",
            "-" * 50,
        ]

        for player in lineup.players:
            lines.append(
                f"{player.roster_position:5} {player.name:25} ${player.salary:5} {player.projected_points:.1f}"
            )

        lines.append("-" * 50)
        return "\n".join(lines)


def build_lineups(
    sport: Sport,
    players: list[Player],
    contest_id: str,
    num_lineups: int = 1,
    single_game: bool = False,
    salary_cap: Optional[int] = None,
) -> list[Lineup]:
    """Convenience function to build lineups.

    Args:
        sport: Sport
        players: Player pool
        contest_id: Contest ID
        num_lineups: Number of lineups
        single_game: If True, use single-game optimizer (SUPERSTAR + FLEX)
        salary_cap: Salary cap (for single-game, varies by seriesId)

    Returns:
        List of optimized Lineup objects
    """
    builder = LineupBuilder(sport, single_game=single_game, salary_cap=salary_cap)
    return builder.build_lineups(players, num_lineups, contest_id)
