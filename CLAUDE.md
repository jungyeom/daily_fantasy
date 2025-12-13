# Daily Fantasy Automation - Claude Code Instructions

This document provides instructions for Claude Code when working on this project, especially during automated error debugging.

## Project Overview

This is a Daily Fantasy Sports (DFS) automation system that:
- Fetches contests from Yahoo DFS
- Scrapes player projections from DailyFantasyFuel
- Generates optimized lineups using pydfs-lineup-optimizer
- Submits lineups to Yahoo via Selenium browser automation
- Monitors for injuries and performs late swaps
- Sends email notifications via SendGrid

## Key Files and Directories

```
src/
├── common/
│   ├── auto_debug.py      # Auto-debug system (invokes Claude)
│   ├── config.py          # Configuration management
│   ├── database.py        # SQLite database
│   ├── models.py          # Data models (Sport, Player, etc.)
│   └── notifications.py   # SendGrid email notifications
├── optimizer/
│   └── lineup_generator.py # pydfs-lineup-optimizer wrapper
├── projections/
│   └── sources/
│       └── dailyfantasyfuel.py # Projection scraping
├── scheduler/
│   ├── runner.py          # APScheduler automation runner
│   ├── job_functions.py   # Individual job implementations
│   └── jobs/              # Job class modules
└── yahoo/
    ├── auth.py            # Yahoo authentication
    ├── browser.py         # Selenium browser management
    ├── contests.py        # Contest fetching
    └── submission.py      # Lineup submission

config/
├── settings.yaml          # Main configuration
└── sports/                # Sport-specific configs

data/
├── daily_fantasy.db       # SQLite database
├── errors/                # Error context files for debugging
└── screenshots/           # Browser screenshots on error
```

## Automated Error Debugging

When a scheduled job fails, the auto-debug system (`src/common/auto_debug.py`) will:
1. Capture full error context (stack trace, job args, etc.)
2. Write context to `data/errors/error_<job>_<timestamp>.json`
3. Invoke Claude Code with `--dangerously-skip-permissions`

### When Invoked for Error Debugging

If you are invoked automatically to debug an error:

1. **Read the error context** from the prompt or `data/errors/` directory
2. **Create a feature branch**: `git checkout -b fix/<job-name>-<timestamp>`
3. **Analyze the error**:
   - Read the relevant source files mentioned in the stack trace
   - Check recent changes with `git log --oneline -10`
   - Look for similar patterns in the codebase
4. **Implement a fix**:
   - Make minimal, focused changes
   - Don't over-engineer - fix the specific issue
5. **Test if possible**:
   - Run `uv run python -c "from src.<module> import <function>; ..."`
   - Check imports work: `uv run python -c "from src.scheduler.runner import AutomationRunner"`
6. **Commit and merge**:
   - Commit with clear message explaining the fix
   - Merge to main: `git checkout main && git merge fix/<branch>`
   - Push: `git push origin main`
7. **Send notification**:
   ```python
   from src.common.notifications import get_notifier
   notifier = get_notifier()
   notifier.notify_error(
       error_type="AutoFix",
       error_message="Successfully fixed <issue>",
       context={"job": "<job_name>", "fix": "<description>", "branch": "<branch>"}
   )
   ```

### Common Error Patterns

#### 1. Selenium/Browser Errors
- **Stale element**: Element changed after finding it - add explicit waits
- **Element not found**: Selector changed - check Yahoo page structure
- **Timeout**: Page slow - increase timeout in config
- **Session expired**: Re-authenticate - call `context.close_driver()` then `context.get_driver()`

#### 2. Projection Scraping Errors
- **Table structure changed**: DailyFantasyFuel updated their HTML - check cell indices
- **No projections found**: Sport not in season or no games today - not an error

#### 3. Optimizer Errors
- **No valid lineups**: Constraints too tight - check exposure limits
- **Salary cap issues**: Check player salaries are being parsed correctly

#### 4. Database Errors
- **Locked database**: Another process has the DB - check for stale processes
- **Missing tables**: Run migrations or check schema

### Do NOT Auto-Fix

Some issues require human intervention:
- **Authentication failures**: Credentials may have changed
- **Yahoo site redesign**: Major scraping changes need review
- **Configuration issues**: User preferences shouldn't be auto-changed
- **Data corruption**: Need to understand scope before fixing

For these, send a detailed error notification and document findings.

## Running the Scheduler

```bash
# Start scheduler in foreground
uv run python -m src.scheduler.main

# Or run specific pipeline manually
uv run python -c "
from src.scheduler.runner import run_full_pipeline
from src.common.models import Sport
run_full_pipeline(Sport.NBA, 'contest_id', 'Contest Name')
"
```

## Testing

```bash
# Test imports
uv run python -c "from src.scheduler.runner import AutomationRunner; print('OK')"

# Test notifications
uv run python -c "
from src.common.notifications import get_notifier
get_notifier().notify_error('Test', 'Test message', {'key': 'value'})
"

# Test projections
uv run python -c "
from src.projections.sources.dailyfantasyfuel import DailyFantasyFuelSource
from src.common.models import Sport
source = DailyFantasyFuelSource()
projs = source.fetch_projections(Sport.NBA)
print(f'Fetched {len(projs)} projections')
"
```

## Git Workflow

- **main**: Production branch, always deployable
- **fix/***: Bug fix branches (auto-created by error debugger)
- **feature/***: New features (manual development)

Always create branches for changes, then merge to main after testing.
