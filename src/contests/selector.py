"""Contest selection and filtering for FanDuel DFS.

Provides two approaches for selecting worthy contests:
1. Hard Filter: Pass/fail criteria for minimum requirements
2. Scoring System: 0-100 score based on multiple dimensions

Usage:
    from src.contests.selector import ContestSelector

    selector = ContestSelector()

    # Hard filter approach
    worthy_contests = selector.filter_contests(contests)

    # Scoring approach
    scored_contests = selector.score_contests(contests)
    top_contests = scored_contests[:10]  # Top 10 by score
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ContestCriteria:
    """Configurable criteria for contest filtering."""

    # Hard filter thresholds
    max_entry_fee: Decimal = Decimal("3.00")
    min_entries_per_user: int = 50  # Minimum entries we want to submit
    min_contest_size: int = 50
    min_exposure_ratio: float = 0.02  # 2%

    # Scoring weights (must sum to 100)
    weight_exposure: int = 25
    weight_entry_fee: int = 20
    weight_contest_size: int = 15
    weight_shark_avoidance: int = 25
    weight_multi_entry: int = 15

    # Shark avoidance thresholds
    shark_prize_pool_threshold: Decimal = Decimal("5000")
    shark_fill_rate_threshold: float = 0.80


class ContestSelector:
    """Select and score contests based on configurable criteria."""

    # Contest types/names to exclude
    EXCLUDED_TYPES = {"satellite", "qualifier"}
    EXCLUDED_NAME_KEYWORDS = {"satellite", "qualifier", "ticket", "seat"}

    # Featured/main contest indicators (slight penalty)
    FEATURED_KEYWORDS = {"featured", "main event", "flagship"}

    def __init__(self, criteria: Optional[ContestCriteria] = None):
        """Initialize selector with criteria.

        Args:
            criteria: Custom criteria, or None for defaults
        """
        self.criteria = criteria or ContestCriteria()

    # =========================================================================
    # Approach 1: Hard Filter
    # =========================================================================

    def filter_contests(self, contests: list[dict]) -> list[dict]:
        """Filter contests using hard pass/fail criteria.

        Args:
            contests: List of parsed contest dicts

        Returns:
            List of contests that pass all criteria
        """
        worthy = []

        for contest in contests:
            passed, reason = self._passes_hard_filter(contest)
            if passed:
                worthy.append(contest)
            else:
                logger.debug(f"Contest {contest.get('id')} filtered: {reason}")

        logger.info(f"Hard filter: {len(worthy)}/{len(contests)} contests passed")
        return worthy

    def _passes_hard_filter(self, contest: dict) -> tuple[bool, str]:
        """Check if contest passes all hard filter criteria.

        Returns:
            Tuple of (passed, reason_if_failed)
        """
        c = self.criteria

        # 1. Entry fee < max
        entry_fee = contest.get("entry_fee", Decimal("999"))
        if entry_fee >= c.max_entry_fee:
            return False, f"entry_fee ${entry_fee} >= ${c.max_entry_fee}"

        # 2. Multi-entry (max_entries > 1)
        max_entries = contest.get("max_entries", 1)
        if max_entries < c.min_entries_per_user:
            return False, f"max_entries {max_entries} < {c.min_entries_per_user}"

        # 3. Minimum contest size
        size = contest.get("size", 0)
        if size < c.min_contest_size:
            return False, f"size {size} < {c.min_contest_size}"

        # 4. Exposure ratio >= 2%
        if size > 0:
            exposure = max_entries / size
            if exposure < c.min_exposure_ratio:
                return False, f"exposure {exposure:.1%} < {c.min_exposure_ratio:.1%}"

        # 5. Not a satellite/qualifier
        contest_type = (contest.get("contest_type") or "").lower()
        name = (contest.get("name") or "").lower()

        if contest_type in self.EXCLUDED_TYPES:
            return False, f"excluded type: {contest_type}"

        for keyword in self.EXCLUDED_NAME_KEYWORDS:
            if keyword in name:
                return False, f"excluded keyword in name: {keyword}"

        return True, ""

    # =========================================================================
    # Approach 2: Scoring System
    # =========================================================================

    def score_contests(
        self,
        contests: list[dict],
        min_score: int = 0,
    ) -> list[dict]:
        """Score contests and return sorted by score descending.

        Args:
            contests: List of parsed contest dicts
            min_score: Minimum score to include (0-100)

        Returns:
            List of contests with 'score' and 'score_breakdown' added,
            sorted by score descending
        """
        scored = []

        for contest in contests:
            score, breakdown = self._calculate_score(contest)

            if score >= min_score:
                contest_copy = contest.copy()
                contest_copy["score"] = score
                contest_copy["score_breakdown"] = breakdown
                scored.append(contest_copy)

        # Sort by score descending
        scored.sort(key=lambda x: x["score"], reverse=True)

        logger.info(
            f"Scoring: {len(scored)}/{len(contests)} contests scored >= {min_score}"
        )

        return scored

    def _calculate_score(self, contest: dict) -> tuple[int, dict]:
        """Calculate contest score (0-100) with breakdown.

        Returns:
            Tuple of (total_score, breakdown_dict)
        """
        breakdown = {}

        # Check disqualifying criteria first
        if not self._passes_hard_filter(contest)[0]:
            return 0, {"disqualified": True}

        # 1. Exposure Ratio Score (25 pts)
        breakdown["exposure"] = self._score_exposure(contest)

        # 2. Entry Fee Score (20 pts)
        breakdown["entry_fee"] = self._score_entry_fee(contest)

        # 3. Contest Size Score (15 pts)
        breakdown["contest_size"] = self._score_contest_size(contest)

        # 4. Shark Avoidance Score (25 pts)
        breakdown["shark_avoidance"] = self._score_shark_avoidance(contest)

        # 5. Multi-Entry Depth Score (15 pts)
        breakdown["multi_entry"] = self._score_multi_entry(contest)

        total = sum(breakdown.values())
        breakdown["total"] = total

        return total, breakdown

    def _score_exposure(self, contest: dict) -> int:
        """Score exposure ratio (0-25 pts)."""
        max_entries = contest.get("max_entries", 1)
        size = contest.get("size", 1)

        if size == 0:
            return 0

        exposure = max_entries / size

        if exposure >= 0.05:
            return 25
        elif exposure >= 0.03:
            return 20
        elif exposure >= 0.02:
            return 15
        elif exposure >= 0.01:
            return 10
        else:
            return 0

    def _score_entry_fee(self, contest: dict) -> int:
        """Score entry fee - lower is better (0-20 pts)."""
        entry_fee = contest.get("entry_fee", Decimal("999"))

        if entry_fee == 0:
            return 20  # Freeroll
        elif entry_fee <= 1:
            return 18
        elif entry_fee <= 2:
            return 15
        elif entry_fee < 3:
            return 10
        else:
            return 0

    def _score_contest_size(self, contest: dict) -> int:
        """Score contest size - sweet spot is 50-200 (0-15 pts)."""
        size = contest.get("size", 0)

        if 50 <= size <= 200:
            return 15  # Sweet spot
        elif 200 < size <= 500:
            return 12
        elif 500 < size <= 1000:
            return 8
        elif size > 1000:
            return 5  # Large = more sharks
        else:
            return 0  # Too small

    def _score_shark_avoidance(self, contest: dict) -> int:
        """Score shark avoidance heuristics (0-25 pts)."""
        score = 0

        # Prize pool component (0-10 pts) - lower is better
        prize_pool = contest.get("prize_pool", Decimal("0"))
        if prize_pool < 500:
            score += 10
        elif prize_pool < 2000:
            score += 8
        elif prize_pool < 5000:
            score += 5
        else:
            score += 2

        # Fill rate component (0-10 pts) - lower is better
        entry_count = contest.get("entry_count", 0)
        size = contest.get("size", 1)
        fill_rate = entry_count / size if size > 0 else 1

        if fill_rate < 0.50:
            score += 10
        elif fill_rate < 0.70:
            score += 7
        elif fill_rate < 0.90:
            score += 4
        else:
            score += 2

        # Name/type component (0-5 pts)
        name = (contest.get("name") or "").lower()

        is_featured = any(kw in name for kw in self.FEATURED_KEYWORDS)
        if is_featured:
            score += 2
        else:
            score += 5

        return score

    def _score_multi_entry(self, contest: dict) -> int:
        """Score multi-entry depth - more is better (0-15 pts).

        We want to submit 50+ lineups, so contests allowing more entries score higher.
        """
        max_entries = contest.get("max_entries", 1)

        if max_entries >= 150:
            return 15
        elif max_entries >= 100:
            return 13
        elif max_entries >= 50:
            return 10  # Minimum acceptable
        else:
            return 0  # Below our 50-entry requirement

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def print_contest_summary(
        self,
        contests: list[dict],
        show_breakdown: bool = False,
        limit: int = 20,
    ):
        """Print formatted contest summary.

        Args:
            contests: List of scored contest dicts
            show_breakdown: Show score breakdown details
            limit: Max contests to display
        """
        print("\n" + "=" * 90)
        print("CONTEST SELECTION SUMMARY")
        print("=" * 90)

        if not contests:
            print("No contests found matching criteria")
            return

        # Header
        print(
            f"{'Score':>5} | {'Name':<35} | {'Fee':>5} | "
            f"{'Size':>6} | {'Entries':>7} | {'Exposure':>8} | {'Prize':>8}"
        )
        print("-" * 90)

        for contest in contests[:limit]:
            score = contest.get("score", "N/A")
            name = contest.get("name", "")[:35]
            fee = contest.get("entry_fee", 0)
            size = contest.get("size", 0)
            max_entries = contest.get("max_entries", 0)
            entry_count = contest.get("entry_count", 0)
            prize = contest.get("prize_pool", 0)

            exposure = f"{max_entries/size:.1%}" if size > 0 else "N/A"
            fill = f"{entry_count}/{size}"

            print(
                f"{score:>5} | {name:<35} | ${fee:>4} | "
                f"{size:>6} | {fill:>7} | {exposure:>8} | ${prize:>7}"
            )

            if show_breakdown and "score_breakdown" in contest:
                bd = contest["score_breakdown"]
                print(
                    f"       └─ exposure:{bd.get('exposure',0):>2} "
                    f"fee:{bd.get('entry_fee',0):>2} "
                    f"size:{bd.get('contest_size',0):>2} "
                    f"shark:{bd.get('shark_avoidance',0):>2} "
                    f"multi:{bd.get('multi_entry',0):>2}"
                )

        print("=" * 90)

        if len(contests) > limit:
            print(f"... and {len(contests) - limit} more contests")

        # Stats
        if contests and "score" in contests[0]:
            scores = [c["score"] for c in contests]
            avg_score = sum(scores) / len(scores)
            print(f"\nTotal: {len(contests)} contests | Avg Score: {avg_score:.1f}")


def select_best_contests(
    contests: list[dict],
    approach: str = "score",
    top_n: int = 10,
    min_score: int = 50,
    criteria: Optional[ContestCriteria] = None,
) -> list[dict]:
    """Convenience function to select best contests.

    Args:
        contests: List of parsed contest dicts
        approach: "filter" for hard filter, "score" for scoring
        top_n: Number of top contests to return
        min_score: Minimum score threshold (for scoring approach)
        criteria: Custom criteria or None for defaults

    Returns:
        List of selected contests
    """
    selector = ContestSelector(criteria)

    if approach == "filter":
        return selector.filter_contests(contests)[:top_n]
    else:
        return selector.score_contests(contests, min_score=min_score)[:top_n]
