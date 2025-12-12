"""Pydantic models for type-safe data handling."""
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Sport(str, Enum):
    """Supported sports."""
    NFL = "NFL"
    NBA = "NBA"
    MLB = "MLB"
    NHL = "NHL"
    PGA = "PGA"
    NASCAR = "NASCAR"
    SOCCER = "SOCCER"


class LineupStatus(str, Enum):
    """Lineup lifecycle states."""
    GENERATED = "generated"
    SUBMITTED = "submitted"
    SWAPPED = "swapped"
    FAILED = "failed"


class ContestStatus(str, Enum):
    """Contest lifecycle states."""
    UPCOMING = "upcoming"
    LIVE = "live"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Player(BaseModel):
    """Player data from Yahoo player pool."""
    yahoo_player_id: str  # e.g., "nfl.p.32723"
    player_game_code: Optional[str] = None  # Full ID for CSV upload: "nfl.p.32723$nfl.g.13553497"
    name: str
    team: str
    position: str
    salary: int
    game_time: Optional[datetime] = None
    game_code: Optional[str] = None  # e.g., "nfl.g.13553497"
    opponent: Optional[str] = None

    # Extended position info (from API)
    eligible_positions: list[str] = []

    # Projection data (from Yahoo API or merged from projection sources)
    projected_points: Optional[float] = None
    projected_ownership: Optional[float] = None

    # Yahoo API extended stats
    fppg: Optional[float] = None  # Fantasy points per game
    fpts_std_dev: Optional[float] = None  # Standard deviation of fantasy points
    fpts_history: list[float] = []  # Recent game fantasy point history

    # Game/matchup info (from API)
    game_status: Optional[str] = None  # e.g., "1:00 pm ET @ NYG"
    spread: Optional[float] = None  # Point spread for player's team
    over_under: Optional[float] = None  # Game total

    # Weather info (from API, relevant for outdoor sports)
    weather: Optional[str] = None  # e.g., "CLEAR", "RAIN_AND_SNOW"
    temperature: Optional[int] = None  # Temperature in Fahrenheit

    # Injury info (from API)
    # status: INJ (injured), O (out), GTD (game time decision), N/A (available)
    status: Optional[str] = None
    injury_status: Optional[str] = None  # e.g., "Q" (questionable), "O" (out)
    injury_note: Optional[str] = None

    # Optimizer constraints
    min_exposure: Optional[float] = None
    max_exposure: Optional[float] = None
    is_locked: bool = False
    is_excluded: bool = False

    class Config:
        frozen = False


class Series(BaseModel):
    """A series/slate represents a shared player pool across multiple contests.

    All contests in a series share the same:
    - Player pool (same players, same salaries)
    - Game times (same slate start)
    - Salary cap

    Lineups are generated at the series level and then distributed to contests.
    """
    id: int  # Yahoo seriesId
    sport: Sport
    slate_start: datetime
    slate_type: Optional[str] = None  # 'MULTI_GAME', 'SINGLE_GAME'
    salary_cap: int = 200

    # Aggregated from contests
    total_contests: int = 0
    total_entry_slots: int = 0  # Sum of max_entries across all contests

    # Lineup generation tracking
    lineups_generated: int = 0

    class Config:
        frozen = False


class Contest(BaseModel):
    """Yahoo Daily Fantasy contest."""
    id: str
    series_id: Optional[int] = None  # Links to series/slate
    sport: Sport
    name: str
    entry_fee: Decimal
    max_entries: int  # Max entries per user (multipleEntryLimit)
    total_entries: Optional[int] = None  # Current number of entries
    entry_limit: Optional[int] = None  # Total entry cap for contest
    prize_pool: Optional[Decimal] = None
    slate_start: datetime
    slate_end: Optional[datetime] = None
    status: ContestStatus = ContestStatus.UPCOMING

    # Contest type info (from API)
    contest_type: Optional[str] = None  # e.g., "league", "tournament"
    slate_type: Optional[str] = None  # e.g., "MULTI_GAME", "SINGLE_GAME"
    is_guaranteed: bool = False  # GPP flag
    is_multi_entry: bool = False
    salary_cap: int = 200  # Default Yahoo salary cap

    # Entries tracking
    entries_submitted: int = 0

    class Config:
        frozen = False


class LineupPlayer(BaseModel):
    """Player slot in a lineup."""
    yahoo_player_id: str  # e.g., "nfl.p.32723"
    player_game_code: str  # Full ID for CSV upload: "nfl.p.32723$nfl.g.13553497"
    name: str
    roster_position: str  # Position slot (e.g., FLEX, UTIL, SUPERSTAR)
    actual_position: str  # Player's actual position
    salary: int
    projected_points: float
    actual_points: Optional[float] = None


class Lineup(BaseModel):
    """Generated or submitted lineup.

    Lineups are generated at the series level and assigned to contests for submission.
    """
    id: Optional[int] = None
    series_id: int  # Generated for this series (required)
    contest_id: Optional[str] = None  # Assigned contest (null until submission)
    entry_id: Optional[str] = None  # Yahoo entry ID (assigned after submission, needed for edits)
    lineup_index: Optional[int] = None  # Index within series (1, 2, 3, ... N)
    players: list[LineupPlayer]
    total_salary: int
    projected_points: float
    actual_points: Optional[float] = None
    status: LineupStatus = LineupStatus.GENERATED
    lineup_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    submitted_at: Optional[datetime] = None

    def calculate_hash(self) -> str:
        """Generate unique hash from player IDs for deduplication."""
        import hashlib
        player_ids = sorted([p.yahoo_player_id for p in self.players])
        return hashlib.md5("|".join(player_ids).encode()).hexdigest()

    class Config:
        frozen = False


class Projection(BaseModel):
    """Player projection from a source."""
    yahoo_player_id: Optional[str] = None  # Matched after transformation
    source_player_id: Optional[str] = None  # ID from projection source
    name: str
    team: str
    position: str
    source: str  # 'dailyfantasyfuel', 'manual', etc.
    projected_points: float
    projected_ownership: Optional[float] = None
    floor: Optional[float] = None
    ceiling: Optional[float] = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        frozen = False


class ContestResult(BaseModel):
    """Result for a lineup in a completed contest."""
    lineup_id: int
    contest_id: str
    actual_points: float
    finish_position: int
    entries_count: int
    winnings: Decimal
    roi_pct: Optional[float] = None  # Calculated: (winnings - entry_fee) / entry_fee * 100
    recorded_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        frozen = False


class SlateInfo(BaseModel):
    """Information about a game slate."""
    sport: Sport
    slate_date: datetime
    first_lock: datetime
    last_start: Optional[datetime] = None
    games: list[dict] = []  # List of game matchups

    class Config:
        frozen = False
