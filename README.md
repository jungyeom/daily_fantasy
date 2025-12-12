# Daily Fantasy Lineup Optimizer

Automated lineup optimization and submission system for Yahoo Daily Fantasy Sports.

## Features

- **Contest Discovery** - Automatically fetches and filters eligible contests from Yahoo DFS API
- **Projection Integration** - Pulls player projections from DailyFantasyFuel
- **Lineup Optimization** - Generates optimized lineups using pydfs-lineup-optimizer
- **Automated Submission** - Browser-based lineup submission to Yahoo
- **Multi-Sport Support** - NFL, NBA, NHL, MLB
- **Single & Multi-Game** - Supports both classic and single-game contest formats

## Architecture

```
src/
├── yahoo/           # Yahoo DFS API & browser automation
├── optimizer/       # Lineup generation with pydfs-lineup-optimizer
├── projections/     # Projection sources (DailyFantasyFuel)
├── scheduler/       # Job orchestration (APScheduler)
├── lineup_manager/  # Lineup tracking & late swap
├── monitoring/      # Live scoring & reports
└── common/          # Shared models, config, database
```

## Quick Start

### Prerequisites

- Python 3.11+
- Chrome browser (for Selenium automation)
- [uv](https://github.com/astral-sh/uv) package manager

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/daily_fantasy.git
cd daily_fantasy

# Install dependencies
uv sync

# Create config file
cp config/settings.yaml.example config/settings.yaml
# Edit config/settings.yaml with your settings
```

### Yahoo Authentication

The system uses cookie-based authentication for Yahoo:

```bash
# Login to Yahoo and save cookies (opens browser)
uv run python scripts/yahoo_login.py
```

Cookies are valid for ~7 days before requiring refresh.

### Running the Pipeline

#### Manual End-to-End Test

```bash
# Run full E2E test suite
uv run python scripts/test_e2e.py --sport nba

# Test specific modules
uv run python scripts/test_e2e.py --module contest_sync
uv run python scripts/test_e2e.py --module lineup_gen
```

#### Generate Lineups

```bash
# Generate lineups for a specific contest
uv run python scripts/generate_lineups.py --contest-id 12345678 --sport nba

# Generate max lineups for submission
uv run python scripts/submit_max_lineups.py --contest 12345678 --sport nba
```

#### Run Scheduler (Automated)

```bash
# Start the automated scheduler
uv run python run_scheduler.py
```

## Configuration

Edit `config/settings.yaml`:

```yaml
yahoo:
  cookie_timeout_hours: 168  # 7 days

optimizer:
  salary_cap: 200
  min_projection: 5.0

scheduler:
  contest_sync_interval: 30  # minutes
  projection_sync_interval: 60
```

## Database

SQLite database stores:
- Contests and entries
- Player pools and projections
- Generated lineups
- Submission history

Located at `data/daily_fantasy.db`

## Key Scripts

| Script | Description |
|--------|-------------|
| `scripts/yahoo_login.py` | Authenticate with Yahoo |
| `scripts/fetch_contests.py` | List available contests |
| `scripts/generate_lineups.py` | Generate optimized lineups |
| `scripts/submit_max_lineups.py` | Generate & submit lineups |
| `scripts/test_e2e.py` | End-to-end test suite |

## How It Works

1. **Contest Sync** - Fetches contests from Yahoo API, filters by criteria (entry fee, multi-entry, etc.)
2. **Player Pool** - Retrieves player salaries and game info for each contest
3. **Projections** - Fetches fantasy point projections from DailyFantasyFuel
4. **Optimization** - Generates lineups maximizing projected points within salary cap
5. **Submission** - Uploads lineups via CSV to Yahoo (browser automation)
6. **Monitoring** - Tracks fill rates, triggers submission at optimal times

## License

MIT
