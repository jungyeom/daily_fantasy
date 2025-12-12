"""Custom exceptions for the daily fantasy application."""


class DailyFantasyError(Exception):
    """Base exception for all daily fantasy errors."""
    pass


# Yahoo-related exceptions
class YahooError(DailyFantasyError):
    """Base exception for Yahoo-related errors."""
    pass


class YahooAuthError(YahooError):
    """Failed to authenticate with Yahoo."""
    pass


class YahooSessionExpiredError(YahooError):
    """Yahoo session has expired and needs re-authentication."""
    pass


class YahooContestNotFoundError(YahooError):
    """Contest not found or no longer available."""
    pass


class YahooRateLimitError(YahooError):
    """Too many requests to Yahoo."""
    pass


class YahooSubmissionError(YahooError):
    """Failed to submit lineup to Yahoo."""
    pass


class YahooPlayerPoolError(YahooError):
    """Failed to fetch player pool from Yahoo."""
    pass


class YahooAPIError(YahooError):
    """Failed to make API request to Yahoo DFS API."""
    pass


# Projection-related exceptions
class ProjectionError(DailyFantasyError):
    """Base exception for projection-related errors."""
    pass


class ProjectionFetchError(ProjectionError):
    """Failed to fetch projections from source."""
    pass


class ProjectionParseError(ProjectionError):
    """Failed to parse projection data."""
    pass


class PlayerMatchError(ProjectionError):
    """Failed to match projection player to Yahoo player pool."""
    pass


# Optimizer-related exceptions
class OptimizerError(DailyFantasyError):
    """Base exception for optimizer-related errors."""
    pass


class NoValidLineupError(OptimizerError):
    """Optimizer could not generate a valid lineup."""
    pass


class InsufficientPlayersError(OptimizerError):
    """Not enough players available to fill roster."""
    pass


class SalaryCapError(OptimizerError):
    """Cannot create valid lineup within salary cap."""
    pass


class ExposureConstraintError(OptimizerError):
    """Cannot satisfy exposure constraints."""
    pass


# Lineup manager exceptions
class LineupError(DailyFantasyError):
    """Base exception for lineup management errors."""
    pass


class LineupNotFoundError(LineupError):
    """Lineup not found in database."""
    pass


class LineupAlreadySubmittedError(LineupError):
    """Lineup has already been submitted."""
    pass


class LateSwapError(LineupError):
    """Failed to perform late swap."""
    pass


class GameAlreadyStartedError(LineupError):
    """Cannot modify lineup - game has already started."""
    pass


# Database exceptions
class DatabaseError(DailyFantasyError):
    """Base exception for database-related errors."""
    pass


class DuplicateEntryError(DatabaseError):
    """Attempted to insert duplicate record."""
    pass


# Configuration exceptions
class ConfigError(DailyFantasyError):
    """Base exception for configuration errors."""
    pass


class InvalidConfigError(ConfigError):
    """Configuration is invalid or missing required fields."""
    pass


# Notification exceptions
class NotificationError(DailyFantasyError):
    """Base exception for notification errors."""
    pass


class EmailSendError(NotificationError):
    """Failed to send email notification."""
    pass
