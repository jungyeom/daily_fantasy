"""Configuration management using YAML files and environment variables."""
import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class YahooConfig(BaseModel):
    """Yahoo authentication and browser settings."""
    username: str = ""
    password: str = ""
    headless: bool = True  # Run browser in headless mode
    timeout: int = 30  # Page load timeout in seconds
    screenshot_on_error: bool = True
    user_agent: Optional[str] = None


class EmailConfig(BaseModel):
    """Email notification settings."""
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    username: str = ""
    password: str = ""  # App password for Gmail
    from_address: str = ""
    to_addresses: list[str] = []

    # Notification triggers
    notify_on_submission: bool = True
    notify_on_late_swap: bool = True
    notify_on_results: bool = True
    notify_on_error: bool = True


class DatabaseConfig(BaseModel):
    """Database settings."""
    path: str = "data/daily_fantasy.db"
    backup_enabled: bool = True
    backup_retention_days: int = 30


class OptimizerConfig(BaseModel):
    """Lineup optimizer settings."""
    default_max_exposure: float = 0.5  # 50% max exposure
    default_min_exposure: float = 0.0
    randomness: float = 0.1  # Projection randomization for lineup diversity
    min_salary_usage: float = 0.95  # Use at least 95% of salary cap


class ContestFilterConfig(BaseModel):
    """Contest filtering criteria."""
    max_entry_fee: float = 1.0  # Only enter contests with fee <= $1
    min_prize_pool: float = 0.0
    multi_entry_only: bool = True
    gpp_only: bool = True  # GPP (tournament) contests only


class SportConfig(BaseModel):
    """Sport-specific settings."""
    enabled: bool = True
    positions: list[str] = []
    salary_cap: int = 50000
    roster_size: int = 9
    max_exposure: dict[str, float] = {}  # Position-specific exposure limits
    stacking_rules: dict[str, Any] = {}  # Sport-specific stacking config
    slate_times: list[Any] = []  # Typical slate start times (can be strings or dicts)
    late_swap: dict[str, Any] = {}  # Late swap settings


class ProjectionSourceConfig(BaseModel):
    """Projection source settings."""
    dailyfantasyfuel: dict[str, Any] = Field(default_factory=lambda: {
        "enabled": True,
        "weight": 1.0,
        "base_url": "https://www.dailyfantasyfuel.com",
    })
    manual: dict[str, Any] = Field(default_factory=lambda: {
        "enabled": False,
        "weight": 0.0,
        "file_path": "data/projections/manual_overrides.csv",
    })


class SchedulerConfig(BaseModel):
    """Automation scheduler settings."""
    enabled: bool = True
    fetch_contests_hours_before: float = 4.0  # Hours before slate lock
    generate_lineups_hours_before: float = 3.0
    submit_lineups_hours_before: float = 2.0
    edit_lineups_minutes_before: int = 30  # Minutes before lock to edit/replace injured players
    stop_editing_minutes: int = 5  # Stop making edits X minutes before lock
    late_swap_check_interval_minutes: int = 15
    results_check_delay_hours: float = 4.0  # Hours after slate ends
    # Fill rate monitoring
    fill_rate_threshold: float = 0.70  # Submit when contest is >= 70% full
    fill_rate_check_interval: int = 10  # Check fill rates every X minutes
    # Injury monitoring
    injury_check_interval: int = 10  # Check injuries every X minutes


class Config(BaseModel):
    """Main application configuration."""
    yahoo: YahooConfig = Field(default_factory=YahooConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)
    contest_filter: ContestFilterConfig = Field(default_factory=ContestFilterConfig)
    projections: ProjectionSourceConfig = Field(default_factory=ProjectionSourceConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    sports: dict[str, SportConfig] = Field(default_factory=dict)

    # General settings
    log_level: str = "INFO"
    data_dir: str = "data"

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Config":
        """Load configuration from YAML file and environment variables.

        Args:
            config_path: Path to settings.yaml. Defaults to config/settings.yaml

        Returns:
            Loaded Config instance
        """
        if config_path is None:
            config_path = str(Path(__file__).parent.parent.parent / "config" / "settings.yaml")

        config_data = {}

        # Load from YAML if exists
        if Path(config_path).exists():
            with open(config_path) as f:
                config_data = yaml.safe_load(f) or {}
            logger.info(f"Loaded config from {config_path}")
        else:
            logger.warning(f"Config file not found: {config_path}, using defaults")

        # Override with environment variables
        config_data = cls._apply_env_overrides(config_data)

        # Load sport-specific configs
        config_data["sports"] = cls._load_sport_configs(config_path)

        return cls(**config_data)

    @classmethod
    def _apply_env_overrides(cls, config_data: dict) -> dict:
        """Override config values with environment variables.

        Environment variable format: DFS_SECTION_KEY (e.g., DFS_YAHOO_USERNAME)
        """
        env_mappings = {
            "DFS_YAHOO_USERNAME": ("yahoo", "username"),
            "DFS_YAHOO_PASSWORD": ("yahoo", "password"),
            "DFS_EMAIL_USERNAME": ("email", "username"),
            "DFS_EMAIL_PASSWORD": ("email", "password"),
            "DFS_EMAIL_TO": ("email", "to_addresses"),
            "DFS_LOG_LEVEL": ("log_level",),
        }

        for env_var, path in env_mappings.items():
            value = os.environ.get(env_var)
            if value is not None:
                # Navigate to nested key and set value
                current = config_data
                for key in path[:-1]:
                    current = current.setdefault(key, {})

                # Handle list values (comma-separated)
                if env_var == "DFS_EMAIL_TO":
                    value = [v.strip() for v in value.split(",")]

                current[path[-1]] = value
                logger.debug(f"Config override from {env_var}")

        return config_data

    @classmethod
    def _load_sport_configs(cls, main_config_path: str) -> dict[str, SportConfig]:
        """Load sport-specific configuration files."""
        sports = {}
        sports_dir = Path(main_config_path).parent / "sports"

        if sports_dir.exists():
            for sport_file in sports_dir.glob("*.yaml"):
                sport_name = sport_file.stem.upper()
                with open(sport_file) as f:
                    sport_data = yaml.safe_load(f) or {}
                sports[sport_name] = SportConfig(**sport_data)
                logger.debug(f"Loaded {sport_name} config from {sport_file}")

        return sports

    def save(self, config_path: Optional[str] = None) -> None:
        """Save current configuration to YAML file.

        Args:
            config_path: Destination path. Defaults to config/settings.yaml
        """
        if config_path is None:
            config_path = str(Path(__file__).parent.parent.parent / "config" / "settings.yaml")

        # Don't save sensitive data
        config_dict = self.model_dump()
        config_dict["yahoo"]["password"] = ""
        config_dict["email"]["password"] = ""

        # Remove sports (saved in separate files)
        config_dict.pop("sports", None)

        with open(config_path, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Config saved to {config_path}")

    def get_sport_config(self, sport: str) -> SportConfig:
        """Get configuration for a specific sport.

        Args:
            sport: Sport name (NFL, NBA, MLB, NHL)

        Returns:
            SportConfig for the sport, or default if not found
        """
        return self.sports.get(sport.upper(), SportConfig())


# Singleton instance
_config_instance: Optional[Config] = None


def get_config(config_path: Optional[str] = None) -> Config:
    """Get or load the configuration singleton.

    Args:
        config_path: Path to config file (only used on first call)

    Returns:
        Config instance
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = Config.load(config_path)
    return _config_instance


def reload_config(config_path: Optional[str] = None) -> Config:
    """Force reload configuration from file.

    Args:
        config_path: Path to config file

    Returns:
        Newly loaded Config instance
    """
    global _config_instance
    _config_instance = Config.load(config_path)
    return _config_instance
