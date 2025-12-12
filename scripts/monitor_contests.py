#!/usr/bin/env python3
"""Monitor contest performance and view results."""
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
    """Monitor contests and results."""
    parser = argparse.ArgumentParser(description="Monitor Contest Performance")

    parser.add_argument(
        "--mode",
        type=str,
        choices=["live", "results", "history", "report"],
        default="report",
        help="Mode: live scores, fetch results, view history, or generate report",
    )

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA", "all"],
        default="all",
        help="Sport filter",
    )

    parser.add_argument(
        "--contest-id",
        type=str,
        help="Specific contest ID",
    )

    parser.add_argument(
        "--report-type",
        type=str,
        choices=["overall", "daily", "weekly", "player", "trend"],
        default="overall",
        help="Report type (for --mode report)",
    )

    args = parser.parse_args()

    # Initialize
    config = get_config()
    db = init_database()

    sport = Sport(args.sport) if args.sport != "all" else None

    if args.mode == "live":
        show_live_scores(sport, args.contest_id)

    elif args.mode == "results":
        fetch_results(sport, args.contest_id)

    elif args.mode == "history":
        show_history(sport)

    elif args.mode == "report":
        show_report(args.report_type, sport)


def show_live_scores(sport: Sport = None, contest_id: str = None):
    """Show live scores for active contests.

    Args:
        sport: Optional sport filter
        contest_id: Optional specific contest
    """
    from src.yahoo.browser import get_browser_manager
    from src.yahoo.auth import YahooAuth
    from src.monitoring.live_scoring import LiveScoring
    from src.lineup_manager.tracker import LineupTracker

    tracker = LineupTracker()
    scoring = LiveScoring()

    # Get active contests
    if contest_id:
        contests = [{"id": contest_id, "name": contest_id}]
    else:
        contests = tracker.get_active_contests(sport)

    if not contests:
        print("No active contests found")
        return

    print(f"\n{'='*60}")
    print("LIVE SCORES")
    print(f"{'='*60}")

    # Initialize browser
    browser = get_browser_manager()
    driver = browser.create_driver()

    try:
        auth = YahooAuth()
        auth.login(driver)

        for contest in contests:
            cid = contest["id"]
            cname = contest.get("name", cid)

            print(f"\n{cname}")
            print("-" * 40)

            scores = scoring.get_live_scores(driver, cid)

            if scores:
                for i, score in enumerate(scores, 1):
                    print(
                        f"  Lineup {i}: {score.get('current_points', 0):.1f} pts "
                        f"(Rank: {score.get('current_rank', 'N/A'):,})"
                    )
            else:
                print("  No scores available")

    finally:
        browser.close_driver()

    print(f"\n{'='*60}")


def fetch_results(sport: Sport = None, contest_id: str = None):
    """Fetch results for completed contests.

    Args:
        sport: Optional sport filter
        contest_id: Optional specific contest
    """
    from src.yahoo.browser import get_browser_manager
    from src.yahoo.auth import YahooAuth
    from src.yahoo.results import ResultsFetcher
    from src.common.notifications import get_notifier

    # Initialize browser
    browser = get_browser_manager()
    driver = browser.create_driver()
    fetcher = ResultsFetcher()
    notifier = get_notifier()

    try:
        auth = YahooAuth()
        auth.login(driver)

        if contest_id:
            contest_ids = [contest_id]
        else:
            contest_ids = fetcher.get_completed_contests(driver)

        print(f"\n{'='*60}")
        print(f"FETCHING RESULTS FOR {len(contest_ids)} CONTESTS")
        print(f"{'='*60}")

        total_winnings = 0
        total_fees = 0

        for cid in contest_ids:
            print(f"\nContest {cid}:")
            results = fetcher.fetch_contest_results(driver, cid)

            for result in results:
                print(
                    f"  Lineup {result.lineup_id}: {result.actual_points:.1f} pts, "
                    f"Rank {result.finish_position:,}, "
                    f"Won ${float(result.winnings):.2f}"
                )
                total_winnings += float(result.winnings)

        print(f"\n{'='*60}")
        print(f"Total Winnings: ${total_winnings:.2f}")
        print(f"{'='*60}")

    finally:
        browser.close_driver()


def show_history(sport: Sport = None):
    """Show historical performance.

    Args:
        sport: Optional sport filter
    """
    from src.monitoring.history import HistoryTracker

    tracker = HistoryTracker()

    # Overall stats
    stats = tracker.get_overall_stats(sport)

    print(f"\n{'='*60}")
    print(f"HISTORICAL PERFORMANCE - {sport.value if sport else 'ALL SPORTS'}")
    print(f"{'='*60}")

    print(f"Contests Entered: {stats['contests_entered']}")
    print(f"Total Entries: {stats['total_entries']}")
    print(f"Total Fees: ${stats['total_fees']:.2f}")
    print(f"Total Winnings: ${stats['total_winnings']:.2f}")
    print(f"Net Profit: ${stats['profit']:.2f}")
    print(f"ROI: {stats['roi_percent']:.1f}%")
    print(f"ITM Rate: {stats['itm_rate']:.1f}%")
    print(f"Best Finish: {stats['best_finish']:,}" if stats['best_finish'] > 0 else "Best Finish: N/A")

    # By sport breakdown
    if not sport:
        print(f"\n{'='*60}")
        print("BY SPORT")
        print("-" * 60)

        sport_stats = tracker.get_stats_by_sport()
        for stat in sport_stats:
            profit_str = f"+${stat['profit']:.2f}" if stat['profit'] >= 0 else f"-${abs(stat['profit']):.2f}"
            print(
                f"{stat['sport']:8} | Entries: {stat['total_entries']:4} | "
                f"Profit: {profit_str:>10} | ROI: {stat['roi_percent']:+.1f}%"
            )

    print(f"{'='*60}")


def show_report(report_type: str, sport: Sport = None):
    """Generate and display a report.

    Args:
        report_type: Type of report
        sport: Optional sport filter
    """
    from src.monitoring.reports import generate_report

    report = generate_report(report_type, sport)
    print(report)


if __name__ == "__main__":
    main()
