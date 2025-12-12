#!/usr/bin/env python3
"""Generate optimized lineups for a contest."""
import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import get_config
from src.common.database import init_database
from src.common.models import Sport

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Generate lineups."""
    parser = argparse.ArgumentParser(description="Generate Optimized Lineups")

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA"],
        required=True,
        help="Sport",
    )

    parser.add_argument(
        "--contest-id",
        type=str,
        required=True,
        help="Contest ID",
    )

    parser.add_argument(
        "--num-lineups",
        type=int,
        default=None,
        help="Number of lineups (default: max entries for contest)",
    )

    parser.add_argument(
        "--export",
        type=str,
        choices=["upload", "detailed", "summary"],
        default="upload",
        help="Export format",
    )

    parser.add_argument(
        "--output",
        type=str,
        help="Output file path",
    )

    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_lineups",
        help="Print lineups to console",
    )

    args = parser.parse_args()

    # Initialize
    config = get_config()
    db = init_database()

    sport = Sport(args.sport)

    # Import modules
    from src.optimizer.builder import LineupBuilder
    from src.optimizer.exporter import LineupExporter
    from src.yahoo.players import PlayerPoolFetcher
    from src.projections.aggregator import ProjectionAggregator

    # Get player pool
    logger.info(f"Loading player pool for contest {args.contest_id}")
    fetcher = PlayerPoolFetcher()
    players = fetcher.get_player_pool_from_db(args.contest_id)

    if not players:
        logger.error(f"No player pool found for contest {args.contest_id}")
        logger.info("Run 'python scripts/fetch_contests.py' first to fetch player pools")
        sys.exit(1)

    logger.info(f"Loaded {len(players)} players")

    # Get projections
    logger.info("Fetching projections...")
    aggregator = ProjectionAggregator()
    players = aggregator.get_projections_for_contest(sport, players)

    with_proj = sum(1 for p in players if p.projected_points and p.projected_points > 0)
    logger.info(f"{with_proj} players have projections")

    if with_proj < 10:
        logger.error("Not enough players with projections")
        sys.exit(1)

    # Build lineups
    logger.info("Generating lineups...")
    builder = LineupBuilder(sport)

    if args.num_lineups:
        lineups = builder.build_lineups(players, args.num_lineups, args.contest_id)
    else:
        lineups = builder.build_lineups_for_contest(players, args.contest_id)

    logger.info(f"Generated {len(lineups)} lineups")

    if not lineups:
        logger.error("No lineups generated")
        sys.exit(1)

    # Export
    exporter = LineupExporter(sport)

    if args.export == "upload":
        output_path = exporter.export_for_upload(lineups, args.contest_id, args.output)
    elif args.export == "detailed":
        output_path = exporter.export_detailed(lineups, args.contest_id, args.output)
    else:
        output_path = exporter.export_summary(lineups, args.contest_id, args.output)

    logger.info(f"Exported lineups to {output_path}")

    # Print if requested
    if args.print_lineups:
        print(exporter.format_for_display(lineups))

    # Summary
    print(f"\n{'='*60}")
    print(f"Generated {len(lineups)} lineups for {sport.value}")
    print(f"Contest ID: {args.contest_id}")
    print(f"Avg Projected: {sum(l.projected_points for l in lineups) / len(lineups):.1f} pts")
    print(f"Exported to: {output_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
