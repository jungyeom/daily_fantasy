"""SQLite database setup with SQLAlchemy ORM."""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

logger = logging.getLogger(__name__)

Base = declarative_base()


class SeriesDB(Base):
    """Series table - represents a slate (shared player pool across contests)."""
    __tablename__ = "series"

    id = Column(Integer, primary_key=True)  # Yahoo seriesId
    sport = Column(String(10), nullable=False)
    slate_start = Column(DateTime, nullable=False)
    slate_type = Column(String(50))  # 'MULTI_GAME', 'SINGLE_GAME'
    salary_cap = Column(Integer, default=200)

    # Aggregated info from contests in this series
    total_contests = Column(Integer, default=0)
    total_entry_slots = Column(Integer, default=0)  # Sum of max_entries across contests

    # Lineup generation tracking
    lineups_generated = Column(Integer, default=0)
    generation_strategy = Column(String(50))  # 'max_exposure', 'balanced', etc.

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    contests = relationship("ContestDB", back_populates="series")
    lineups = relationship("LineupDB", back_populates="series")

    __table_args__ = (
        Index("idx_series_sport_date", "sport", "slate_start"),
    )


class ContestDB(Base):
    """Contests table - stores Yahoo DFS contest metadata."""
    __tablename__ = "contests"

    id = Column(String, primary_key=True)  # Yahoo contest ID
    series_id = Column(Integer, ForeignKey("series.id"))  # Links to series/slate
    sport = Column(String(10), nullable=False)
    name = Column(String(255))
    entry_fee = Column(Float, nullable=False)
    max_entries = Column(Integer)  # Max entries per user (multipleEntryLimit)
    total_entries = Column(Integer)  # Current entry count
    entry_limit = Column(Integer)  # Total entry cap for contest
    prize_pool = Column(Float)
    first_place_prize = Column(Float)  # First place payout
    slate_start = Column(DateTime, nullable=False)
    slate_end = Column(DateTime)
    status = Column(String(20), default="upcoming")
    entries_submitted = Column(Integer, default=0)

    # Contest type info (from API)
    contest_type = Column(String(50))  # 'league', 'tournament', etc.
    slate_type = Column(String(50))  # 'MULTI_GAME', 'SINGLE_GAME'
    is_guaranteed = Column(Boolean, default=False)  # GPP flag
    is_multi_entry = Column(Boolean, default=False)
    salary_cap = Column(Integer, default=200)  # Yahoo salary cap

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    series = relationship("SeriesDB", back_populates="contests")
    lineups = relationship("LineupDB", back_populates="contest")
    player_pools = relationship("PlayerPoolDB", back_populates="contest")
    projections = relationship("ProjectionDB", back_populates="contest")

    __table_args__ = (
        Index("idx_contests_series", "series_id"),
        Index("idx_contests_sport_date", "sport", "slate_start"),
        Index("idx_contests_status", "status"),
        Index("idx_contests_entry_fee", "entry_fee"),
        Index("idx_contests_guaranteed", "is_guaranteed"),
    )


class PlayerPoolDB(Base):
    """Player pools table - Yahoo player data for each contest."""
    __tablename__ = "player_pools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contest_id = Column(String, ForeignKey("contests.id"), nullable=False)
    yahoo_player_id = Column(String(50), nullable=False)  # e.g., "nfl.p.32723"
    player_game_code = Column(String(100))  # Full ID for CSV upload: "nfl.p.32723$nfl.g.13553497"
    name = Column(String(100), nullable=False)
    team = Column(String(10))
    team_name = Column(String(50))  # Full team name
    position = Column(String(20))
    eligible_positions = Column(String(100))  # Comma-separated list
    salary = Column(Integer, nullable=False)
    game_time = Column(DateTime)
    game_code = Column(String(50))  # e.g., "nfl.g.13553497"
    opponent = Column(String(10))
    game_status = Column(String(100))  # e.g., "1:00 pm ET @ NYG"
    is_active = Column(Boolean, default=True)  # False if injured/out

    # Yahoo API projection data
    yahoo_projected_points = Column(Float)  # Yahoo's built-in projections
    fppg = Column(Float)  # Fantasy points per game (historical average)
    fpts_std_dev = Column(Float)  # Standard deviation of fantasy points

    # Game/matchup info (from API)
    spread = Column(Float)  # Point spread for player's team
    over_under = Column(Float)  # Game total

    # Weather info (from API, relevant for outdoor sports)
    weather = Column(String(50))  # e.g., "CLEAR", "RAIN_AND_SNOW"
    temperature = Column(Integer)  # Temperature in Fahrenheit

    # Injury info (from API)
    injury_status = Column(String(20))  # e.g., "Q" (questionable), "O" (out)
    injury_note = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    contest = relationship("ContestDB", back_populates="player_pools")

    __table_args__ = (
        Index("idx_player_pools_contest", "contest_id"),
        Index("idx_player_pools_yahoo_id", "yahoo_player_id"),
        Index("idx_player_pools_unique", "contest_id", "yahoo_player_id", unique=True),
        Index("idx_player_pools_team", "team"),
        Index("idx_player_pools_position", "position"),
    )


class ProjectionDB(Base):
    """Projections table - player projections from various sources."""
    __tablename__ = "projections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contest_id = Column(String, ForeignKey("contests.id"), nullable=False)
    yahoo_player_id = Column(String(50))  # Nullable until matched
    source_player_name = Column(String(100), nullable=False)  # Original name from source
    team = Column(String(10))  # Player's team for matching
    position = Column(String(20))  # Player's position for matching
    source = Column(String(50), nullable=False)  # 'yahoo', 'dailyfantasyfuel', 'manual'
    projected_points = Column(Float, nullable=False)
    projected_ownership = Column(Float)
    floor = Column(Float)
    ceiling = Column(Float)
    value = Column(Float)  # Points per $1000 salary
    is_matched = Column(Boolean, default=False)  # Whether matched to Yahoo player
    fetched_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    contest = relationship("ContestDB", back_populates="projections")

    __table_args__ = (
        Index("idx_projections_contest", "contest_id"),
        Index("idx_projections_source", "source"),
        Index("idx_projections_fetched", "fetched_at"),
        Index("idx_projections_yahoo_id", "yahoo_player_id"),
    )


class LineupDB(Base):
    """Lineups table - generated and submitted lineups.

    Lineups are generated at the series level (shared player pool) and then
    assigned to specific contests for submission.
    """
    __tablename__ = "lineups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    series_id = Column(Integer, ForeignKey("series.id"), nullable=False)  # Generated for this series
    contest_id = Column(String, ForeignKey("contests.id"))  # Assigned contest (null until submission)
    entry_id = Column(String(100))  # Yahoo entry ID (assigned after submission, needed for edits)
    lineup_hash = Column(String(32), nullable=False)  # MD5 hash for deduplication
    lineup_index = Column(Integer)  # Index within series (1, 2, 3, ... N)
    total_salary = Column(Integer, nullable=False)
    projected_points = Column(Float, nullable=False)
    actual_points = Column(Float)
    status = Column(String(20), default="generated")  # generated, assigned, submitted, swapped, edited, failed
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    series = relationship("SeriesDB", back_populates="lineups")
    contest = relationship("ContestDB", back_populates="lineups")
    players = relationship("LineupPlayerDB", back_populates="lineup", cascade="all, delete-orphan")
    result = relationship("ResultDB", back_populates="lineup", uselist=False)

    __table_args__ = (
        Index("idx_lineups_series", "series_id"),
        Index("idx_lineups_contest", "contest_id"),
        Index("idx_lineups_status", "status"),
        Index("idx_lineups_series_hash", "series_id", "lineup_hash", unique=True),
    )


class LineupPlayerDB(Base):
    """Lineup players table - many-to-many relationship for lineup composition."""
    __tablename__ = "lineup_players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lineup_id = Column(Integer, ForeignKey("lineups.id"), nullable=False)
    yahoo_player_id = Column(String(50), nullable=False)  # e.g., "nfl.p.32723"
    player_game_code = Column(String(100), nullable=False)  # Full ID for CSV: "nfl.p.32723$nfl.g.13553497"
    name = Column(String(100), nullable=False)
    roster_position = Column(String(20), nullable=False)  # Position slot (FLEX, UTIL, SUPERSTAR)
    actual_position = Column(String(20), nullable=False)  # Player's real position
    salary = Column(Integer, nullable=False)
    projected_points = Column(Float, nullable=False)
    actual_points = Column(Float)

    # Relationships
    lineup = relationship("LineupDB", back_populates="players")

    __table_args__ = (
        Index("idx_lineup_players_lineup", "lineup_id"),
        Index("idx_lineup_players_yahoo_id", "yahoo_player_id"),
    )


class ResultDB(Base):
    """Results table - contest outcomes for submitted lineups."""
    __tablename__ = "results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lineup_id = Column(Integer, ForeignKey("lineups.id"), nullable=False, unique=True)
    contest_id = Column(String, ForeignKey("contests.id"), nullable=False)
    actual_points = Column(Float, nullable=False)
    finish_position = Column(Integer, nullable=False)
    entries_count = Column(Integer, nullable=False)
    percentile = Column(Float)  # Finish percentile (0-100)
    winnings = Column(Float, default=0.0)
    recorded_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    lineup = relationship("LineupDB", back_populates="result")

    __table_args__ = (
        Index("idx_results_contest", "contest_id"),
        Index("idx_results_recorded", "recorded_at"),
    )


class SwapLogDB(Base):
    """Swap log table - tracks late swap history."""
    __tablename__ = "swap_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lineup_id = Column(Integer, ForeignKey("lineups.id"), nullable=False)
    old_player_id = Column(String(50), nullable=False)
    old_player_name = Column(String(100))
    new_player_id = Column(String(50), nullable=False)
    new_player_name = Column(String(100))
    reason = Column(Text)  # 'projection_drop', 'injury', 'inactive'
    old_projection = Column(Float)
    new_projection = Column(Float)
    swapped_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_swap_logs_lineup", "lineup_id"),
        Index("idx_swap_logs_date", "swapped_at"),
    )


class ContestEntryDB(Base):
    """Tracks our entry/submission status per contest.

    This is separate from ContestDB which stores contest metadata.
    ContestEntryDB tracks our actual participation and submission state.

    Note: No foreign key to contests table since we may track contests
    before they're fully stored in ContestDB.
    """
    __tablename__ = "contest_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contest_id = Column(String, unique=True, nullable=False)  # No FK - standalone tracking
    sport = Column(String(10), nullable=False)

    # Contest type info (needed for lineup generation)
    slate_type = Column(String(50), default="MULTI_GAME")  # 'MULTI_GAME', 'SINGLE_GAME'
    salary_cap = Column(Integer, default=200)  # Yahoo salary cap (varies per series)

    # Entry status
    status = Column(String(20), default="eligible")  # eligible, pending, submitted, locked, skipped

    # Submission tracking
    max_entries_allowed = Column(Integer)  # From contest multipleEntryLimit
    lineups_submitted = Column(Integer, default=0)
    fill_rate_at_submit = Column(Float)  # Fill rate when we submitted
    submitted_at = Column(DateTime)

    # Timing
    lock_time = Column(DateTime, nullable=False)

    # Audit
    skip_reason = Column(String(255))  # If skipped, why (e.g., "fill_rate_too_low", "no_projections")
    last_checked_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def is_single_game(self) -> bool:
        """Check if this is a single-game contest."""
        return self.slate_type == "SINGLE_GAME"

    __table_args__ = (
        Index("idx_contest_entries_status", "status"),
        Index("idx_contest_entries_sport", "sport"),
        Index("idx_contest_entries_lock_time", "lock_time"),
        Index("idx_contest_entries_slate_type", "slate_type"),
    )


class FanDuelFixtureListDB(Base):
    """FanDuel fixture lists (slates) - equivalent to Yahoo series."""
    __tablename__ = "fanduel_fixture_lists"

    id = Column(Integer, primary_key=True)  # FanDuel fixture list ID
    sport = Column(String(10), nullable=False)
    label = Column(String(255))  # Slate name/description
    slate_start = Column(DateTime, nullable=False)
    salary_cap = Column(Integer, default=60000)  # FanDuel default is $60,000

    # Aggregated info
    contest_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    contests = relationship("FanDuelContestDB", back_populates="fixture_list")
    players = relationship("FanDuelPlayerPoolDB", back_populates="fixture_list")

    __table_args__ = (
        Index("idx_fd_fixture_lists_sport_date", "sport", "slate_start"),
    )


class FanDuelContestDB(Base):
    """FanDuel contests table - stores contest metadata."""
    __tablename__ = "fanduel_contests"

    id = Column(String, primary_key=True)  # FanDuel contest ID
    fixture_list_id = Column(Integer, ForeignKey("fanduel_fixture_lists.id"))
    sport = Column(String(10), nullable=False)
    name = Column(String(255))
    entry_fee = Column(Float, nullable=False)
    max_entries = Column(Integer)  # Max entries per user
    entry_count = Column(Integer)  # Current entry count
    size = Column(Integer)  # Max total entries for contest
    prize_pool = Column(Float)
    slate_start = Column(DateTime, nullable=False)
    status = Column(String(20), default="upcoming")

    # Contest type info
    contest_type = Column(String(50))  # 'tournament', 'h2h', '50-50', etc.
    is_guaranteed = Column(Boolean, default=False)
    salary_cap = Column(Integer, default=60000)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    fixture_list = relationship("FanDuelFixtureListDB", back_populates="contests")

    __table_args__ = (
        Index("idx_fd_contests_fixture_list", "fixture_list_id"),
        Index("idx_fd_contests_sport_date", "sport", "slate_start"),
        Index("idx_fd_contests_status", "status"),
        Index("idx_fd_contests_entry_fee", "entry_fee"),
        Index("idx_fd_contests_guaranteed", "is_guaranteed"),
    )


class FanDuelPlayerPoolDB(Base):
    """FanDuel player pools - player data for each fixture list (slate)."""
    __tablename__ = "fanduel_player_pools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fixture_list_id = Column(Integer, ForeignKey("fanduel_fixture_lists.id"), nullable=False)
    fanduel_player_id = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    first_name = Column(String(50))
    last_name = Column(String(50))
    team = Column(String(10))
    team_name = Column(String(50))
    position = Column(String(20))
    salary = Column(Integer, nullable=False)

    # FanDuel projection data
    fppg = Column(Float)  # Fantasy points per game

    # Game info
    game_id = Column(String(50))
    opponent = Column(String(10))

    # Injury info
    injury_status = Column(String(20))
    injury_details = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    fixture_list = relationship("FanDuelFixtureListDB", back_populates="players")

    __table_args__ = (
        Index("idx_fd_player_pools_fixture_list", "fixture_list_id"),
        Index("idx_fd_player_pools_player_id", "fanduel_player_id"),
        Index("idx_fd_player_pools_unique", "fixture_list_id", "fanduel_player_id", unique=True),
        Index("idx_fd_player_pools_team", "team"),
        Index("idx_fd_player_pools_position", "position"),
    )


class SchedulerRunDB(Base):
    """Tracks scheduler job executions for debugging and monitoring."""
    __tablename__ = "scheduler_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String(50), nullable=False)  # 'contest_sync', 'projection_sync', 'submission', 'injury_check'
    sport = Column(String(10))  # Optional, some jobs are sport-specific
    status = Column(String(20), nullable=False)  # 'started', 'completed', 'failed'

    # Execution details
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime)
    duration_seconds = Column(Float)

    # Results
    items_processed = Column(Integer, default=0)  # Contests synced, lineups submitted, etc.
    error_message = Column(Text)
    details = Column(Text)  # JSON blob with additional info

    __table_args__ = (
        Index("idx_scheduler_runs_job", "job_name"),
        Index("idx_scheduler_runs_status", "status"),
        Index("idx_scheduler_runs_started", "started_at"),
    )


class Database:
    """Database manager for SQLite operations."""

    def __init__(self, db_path: Optional[str] = None):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to data/daily_fantasy.db
        """
        if db_path is None:
            db_path = str(Path(__file__).parent.parent.parent / "data" / "daily_fantasy.db")

        self.db_path = db_path
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            future=True,
        )

        # Enable foreign keys for SQLite
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
        )

        logger.info(f"Database initialized at {db_path}")

    def create_tables(self) -> None:
        """Create all tables if they don't exist."""
        Base.metadata.create_all(self.engine)
        logger.info("Database tables created successfully")

    def drop_tables(self) -> None:
        """Drop all tables. Use with caution!"""
        Base.metadata.drop_all(self.engine)
        logger.warning("All database tables dropped")

    def get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()

    def backup(self, backup_path: Optional[str] = None) -> str:
        """Create a backup of the database.

        Args:
            backup_path: Destination path. Defaults to data/backups/daily_fantasy_YYYYMMDD_HHMMSS.db

        Returns:
            Path to backup file
        """
        import shutil

        if backup_path is None:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_dir = Path(self.db_path).parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            backup_path = str(backup_dir / f"daily_fantasy_{timestamp}.db")

        shutil.copy2(self.db_path, backup_path)
        logger.info(f"Database backed up to {backup_path}")
        return backup_path


# Singleton instance
_db_instance: Optional[Database] = None


def get_database(db_path: Optional[str] = None) -> Database:
    """Get or create the database singleton.

    Args:
        db_path: Path to database file (only used on first call)

    Returns:
        Database instance
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
    return _db_instance


def init_database(db_path: Optional[str] = None) -> Database:
    """Initialize database and create tables.

    Args:
        db_path: Path to database file

    Returns:
        Database instance with tables created
    """
    db = get_database(db_path)
    db.create_tables()
    return db
