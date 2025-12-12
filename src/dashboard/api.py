"""FastAPI backend for contest monitoring dashboard."""
import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..common.database import get_database, ContestEntryDB, ContestDB, LineupDB

logger = logging.getLogger(__name__)

app = FastAPI(title="DFS Dashboard", version="1.0.0")

# Allow CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Your Yahoo user ID
YAHOO_USER_ID = "37022364"
YAHOO_API_BASE = "https://dfyql-ro.sports.yahoo.com/v2"

# Additional contest IDs to monitor (not in database)
# These are contests you've entered manually or before the system tracked them
ADDITIONAL_CONTEST_IDS = [
    "15237128",  # NFL contest with $87.88 winnings
]


class ContestEntry(BaseModel):
    """Single contest entry from Yahoo API."""
    entry_id: str
    contest_id: str
    rank: int
    percentile: float
    score: float
    winnings: float
    paid_winnings: float
    live_projected_points: Optional[float] = None
    periods_remaining: Optional[int] = None


class ContestSummary(BaseModel):
    """Summary for a single contest."""
    contest_id: str
    contest_name: Optional[str] = None
    sport: Optional[str] = None
    entry_fee: float
    total_entries: int
    user_entries: int
    user_entry_details: list[ContestEntry]
    total_score: float
    total_winnings: float
    entries_winning: int
    best_rank: int
    worst_rank: int
    avg_percentile: float
    status: str  # 'live', 'completed', 'upcoming'


class DashboardData(BaseModel):
    """Full dashboard data response."""
    timestamp: str
    overall: dict
    contests: list[ContestSummary]


async def fetch_user_entries_fast(contest_id: str, expected_entries: int = 70) -> tuple[list[dict], int]:
    """Fetch user's entries efficiently using parallel requests.

    Instead of fetching all entries sequentially, we:
    1. First fetch page 0 to get total count
    2. Then fetch multiple pages in parallel
    3. Stop once we've found all expected user entries

    Args:
        contest_id: Yahoo contest ID
        expected_entries: Expected number of user entries (default 70 for max multi-entry)

    Returns:
        Tuple of (user_entries list, total_entries count)
    """
    user_entries = []
    total_count = 0
    limit = 50

    async with httpx.AsyncClient(timeout=30.0) as client:
        # First request to get total count
        url = f"{YAHOO_API_BASE}/contestEntries?contestId={contest_id}&start=0&limit={limit}"
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            if "entries" in data and isinstance(data["entries"], dict):
                entries = data["entries"].get("result", [])
                pagination = data.get("pagination", {}).get("result", {})
                total_count = pagination.get("totalCount", len(entries))
            else:
                entries = data.get("contestEntries", [])
                total_count = data.get("totalCount", len(entries))

            # Filter first batch
            for e in entries:
                if str(e.get("userId")) == YAHOO_USER_ID:
                    user_entries.append(e)

            # If we found all expected entries in first page, we're done
            if len(user_entries) >= expected_entries:
                return user_entries, total_count

            # Calculate remaining pages needed
            # Fetch in parallel batches of 10 requests
            remaining_pages = (total_count - limit) // limit + 1
            batch_size = 10

            for batch_start in range(0, remaining_pages, batch_size):
                # Check if we've found enough entries
                if len(user_entries) >= expected_entries:
                    break

                # Create batch of requests
                tasks = []
                for i in range(batch_start, min(batch_start + batch_size, remaining_pages)):
                    start = (i + 1) * limit  # +1 because we already fetched page 0
                    if start >= total_count:
                        break
                    page_url = f"{YAHOO_API_BASE}/contestEntries?contestId={contest_id}&start={start}&limit={limit}"
                    tasks.append(client.get(page_url))

                if not tasks:
                    break

                # Execute batch in parallel
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                for resp in responses:
                    if isinstance(resp, Exception):
                        continue
                    try:
                        page_data = resp.json()
                        if "entries" in page_data and isinstance(page_data["entries"], dict):
                            page_entries = page_data["entries"].get("result", [])
                        else:
                            page_entries = page_data.get("contestEntries", [])

                        for e in page_entries:
                            if str(e.get("userId")) == YAHOO_USER_ID:
                                user_entries.append(e)
                    except Exception:
                        continue

        except Exception as e:
            logger.error(f"Failed to fetch entries for contest {contest_id}: {e}")

    return user_entries, total_count


async def fetch_user_entries(contest_id: str) -> list[dict]:
    """Fetch only the user's entries for a contest.

    Args:
        contest_id: Yahoo contest ID

    Returns:
        List of user's entry dicts
    """
    entries, _ = await fetch_user_entries_fast(contest_id)
    return entries


async def fetch_contest_entries(contest_id: str) -> list[dict]:
    """Fetch all entries for a contest - only used when total count is needed.

    For dashboard, we use fetch_user_entries_fast instead which is much faster.
    """
    # This is now only called for total_entries count in the response
    # We'll get total from fetch_user_entries_fast instead
    return []


def get_submitted_contest_ids() -> list[str]:
    """Get list of contest IDs we've submitted to from database.

    Uses LineupDB to find contests where we have submitted lineups,
    since ContestEntryDB may show 'locked' after submission.
    """
    db = get_database()
    session = db.get_session()
    try:
        from sqlalchemy import distinct
        # Get unique contest IDs from lineups with submitted status
        contest_ids = session.query(distinct(LineupDB.contest_id)).filter(
            LineupDB.status == "submitted",
            LineupDB.contest_id.isnot(None)
        ).all()
        return [c[0] for c in contest_ids if c[0]]
    finally:
        session.close()


async def fetch_contest_info_from_yahoo(contest_id: str) -> Optional[dict]:
    """Fetch contest info from Yahoo API.

    Args:
        contest_id: Yahoo contest ID

    Returns:
        Contest info dict or None
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{YAHOO_API_BASE}/contest/{contest_id}"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            contests = data.get("contests", {}).get("result", [])
            if contests:
                contest = contests[0]
                return {
                    "name": contest.get("title"),
                    "sport": contest.get("sportCode", "").upper(),
                    "entry_fee": float(contest.get("entryFee", 0) or 0),
                    "status": contest.get("state", "unknown"),
                }
    except Exception as e:
        logger.error(f"Failed to fetch contest info from Yahoo: {e}")

    return None


def get_contest_info(contest_id: str) -> Optional[dict]:
    """Get contest info from database."""
    db = get_database()
    session = db.get_session()
    try:
        # Try ContestDB first
        contest = session.query(ContestDB).filter_by(id=contest_id).first()
        if contest:
            return {
                "name": contest.name,
                "sport": contest.sport,
                "entry_fee": contest.entry_fee,
                "status": contest.status,
            }

        # Fall back to ContestEntryDB
        entry = session.query(ContestEntryDB).filter_by(contest_id=contest_id).first()
        if entry:
            return {
                "name": f"Contest {contest_id}",
                "sport": entry.sport,
                "entry_fee": 0.0,  # Not stored in ContestEntryDB
                "status": entry.status,
            }

        return None
    finally:
        session.close()


@app.get("/api/dashboard", response_model=DashboardData)
async def get_dashboard_data():
    """Get full dashboard data for all submitted contests."""
    # Get contest IDs from database
    db_contest_ids = get_submitted_contest_ids()

    # Combine with additional manually-tracked contests
    contest_ids = list(set(db_contest_ids + ADDITIONAL_CONTEST_IDS))

    if not contest_ids:
        return DashboardData(
            timestamp=datetime.now().isoformat(),
            overall={
                "total_entry_fees": 0.0,
                "total_entries": 0,
                "total_winnings": 0.0,
                "net_profit": 0.0,
                "entries_winning": 0,
                "contests_count": 0,
            },
            contests=[],
        )

    contests_data = []
    overall_fees = 0.0
    overall_entries = 0
    overall_winnings = 0.0
    overall_entries_winning = 0

    for contest_id in contest_ids:
        try:
            # Get contest info from DB first, then Yahoo API as fallback
            contest_info = get_contest_info(contest_id)
            # Always fetch from Yahoo API for live status
            yahoo_info = await fetch_contest_info_from_yahoo(contest_id)
            if yahoo_info:
                # Merge Yahoo info with DB info (Yahoo takes precedence for missing fields)
                contest_info = {**(contest_info or {}), **yahoo_info}
            contest_info = contest_info or {}

            # Skip completed contests - only show live ones
            contest_status = contest_info.get("status", "").lower()
            if contest_status == "completed":
                logger.debug(f"Skipping completed contest {contest_id}")
                continue

            entry_fee = contest_info.get("entry_fee", 0.0)

            # Fetch live data from Yahoo
            user_entries = await fetch_user_entries(contest_id)

            if not user_entries:
                continue

            # Parse entries
            entry_details = []
            total_score = 0.0
            total_winnings = 0.0
            entries_winning = 0
            ranks = []
            percentiles = []

            for entry in user_entries:
                winnings = float(entry.get("winnings", 0) or 0)
                score = float(entry.get("score", 0) or 0)
                rank = int(entry.get("rank", 0) or 0)
                percentile = float(entry.get("percentile", 0) or 0)

                # Handle paidWinnings which can be a dict or a number
                paid_winnings_raw = entry.get("paidWinnings", 0)
                if isinstance(paid_winnings_raw, dict):
                    paid_winnings = float(paid_winnings_raw.get("value", 0) or 0)
                else:
                    paid_winnings = float(paid_winnings_raw or 0)

                entry_details.append(ContestEntry(
                    entry_id=str(entry.get("id", "")),
                    contest_id=contest_id,
                    rank=rank,
                    percentile=percentile,
                    score=score,
                    winnings=winnings,
                    paid_winnings=paid_winnings,
                    live_projected_points=entry.get("liveProjectedPoints"),
                    periods_remaining=entry.get("periodsRemaining"),
                ))

                total_score += score
                total_winnings += winnings
                if winnings > 0:
                    entries_winning += 1
                if rank > 0:
                    ranks.append(rank)
                if percentile > 0:
                    percentiles.append(percentile)

            # Determine status based on periodsRemaining
            periods_remaining = user_entries[0].get("periodsRemaining", 0)
            if periods_remaining is None or periods_remaining == 0:
                status = "completed"
            else:
                status = "live"

            # Calculate contest totals
            user_entry_count = len(user_entries)
            contest_entry_fee = entry_fee * user_entry_count

            contests_data.append(ContestSummary(
                contest_id=contest_id,
                contest_name=contest_info.get("name"),
                sport=contest_info.get("sport"),
                entry_fee=entry_fee,
                total_entries=len(await fetch_contest_entries(contest_id)),  # Total in contest
                user_entries=user_entry_count,
                user_entry_details=entry_details,
                total_score=total_score,
                total_winnings=total_winnings,
                entries_winning=entries_winning,
                best_rank=min(ranks) if ranks else 0,
                worst_rank=max(ranks) if ranks else 0,
                avg_percentile=sum(percentiles) / len(percentiles) if percentiles else 0,
                status=status,
            ))

            # Accumulate overall totals
            overall_fees += contest_entry_fee
            overall_entries += user_entry_count
            overall_winnings += total_winnings
            overall_entries_winning += entries_winning

        except Exception as e:
            logger.error(f"Error processing contest {contest_id}: {e}")
            continue

    return DashboardData(
        timestamp=datetime.now().isoformat(),
        overall={
            "total_entry_fees": overall_fees,
            "total_entries": overall_entries,
            "total_winnings": overall_winnings,
            "net_profit": overall_winnings - overall_fees,
            "entries_winning": overall_entries_winning,
            "contests_count": len(contests_data),
        },
        contests=contests_data,
    )


@app.get("/api/contest/{contest_id}")
async def get_contest_data(contest_id: str):
    """Get detailed data for a single contest."""
    user_entries = await fetch_user_entries(contest_id)
    all_entries = await fetch_contest_entries(contest_id)
    contest_info = get_contest_info(contest_id) or {}

    return {
        "contest_id": contest_id,
        "contest_info": contest_info,
        "total_entries": len(all_entries),
        "user_entries": user_entries,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    """Serve the dashboard HTML page."""
    return get_dashboard_html()


def get_dashboard_html() -> str:
    """Return the dashboard HTML."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DFS Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            padding: 20px;
            min-height: 100vh;
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
        }

        h1 {
            color: #00d4ff;
            font-size: 28px;
        }

        .refresh-btn {
            background: #00d4ff;
            color: #1a1a2e;
            border: none;
            padding: 12px 24px;
            font-size: 16px;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }

        .refresh-btn:hover {
            background: #00b8e6;
            transform: translateY(-2px);
        }

        .refresh-btn:disabled {
            background: #555;
            cursor: not-allowed;
            transform: none;
        }

        .timestamp {
            color: #888;
            font-size: 14px;
            margin-top: 5px;
        }

        .overall-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }

        .stat-card {
            background: #16213e;
            padding: 20px;
            border-radius: 12px;
            border: 1px solid #0f3460;
        }

        .stat-label {
            color: #888;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }

        .stat-value {
            font-size: 28px;
            font-weight: 700;
        }

        .positive { color: #4ade80; }
        .negative { color: #f87171; }
        .neutral { color: #00d4ff; }

        .contests-section h2 {
            color: #00d4ff;
            margin-bottom: 20px;
            font-size: 20px;
        }

        .contest-card {
            background: #16213e;
            border-radius: 12px;
            border: 1px solid #0f3460;
            margin-bottom: 20px;
            overflow: hidden;
        }

        .contest-header {
            background: #0f3460;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .contest-name {
            font-weight: 600;
            font-size: 16px;
        }

        .contest-status {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
        }

        .status-live {
            background: #22c55e;
            color: #000;
        }

        .status-completed {
            background: #6b7280;
            color: #fff;
        }

        .contest-body {
            padding: 20px;
        }

        .contest-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .contest-stat {
            text-align: center;
        }

        .contest-stat-label {
            color: #888;
            font-size: 11px;
            text-transform: uppercase;
            margin-bottom: 4px;
        }

        .contest-stat-value {
            font-size: 20px;
            font-weight: 600;
        }

        .entries-table {
            width: 100%;
            border-collapse: collapse;
        }

        .entries-table th,
        .entries-table td {
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid #0f3460;
        }

        .entries-table th {
            color: #888;
            font-size: 11px;
            text-transform: uppercase;
            font-weight: 600;
        }

        .entries-table tr:last-child td {
            border-bottom: none;
        }

        .loading {
            text-align: center;
            padding: 60px;
            color: #888;
        }

        .spinner {
            width: 40px;
            height: 40px;
            border: 4px solid #333;
            border-top-color: #00d4ff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        .error {
            background: #7f1d1d;
            color: #fca5a5;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
        }

        .no-data {
            text-align: center;
            padding: 60px;
            color: #888;
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>DFS Live Dashboard</h1>
            <div class="timestamp" id="timestamp">Last updated: --</div>
        </div>
        <button class="refresh-btn" id="refreshBtn" onclick="refreshData()">
            Refresh
        </button>
    </div>

    <div id="content">
        <div class="loading">
            <div class="spinner"></div>
            <p>Loading dashboard data...</p>
        </div>
    </div>

    <script>
        async function refreshData() {
            const btn = document.getElementById('refreshBtn');
            const content = document.getElementById('content');

            btn.disabled = true;
            btn.textContent = 'Loading...';

            // Show loading message with explanation
            content.innerHTML = `
                <div class="loading">
                    <div class="spinner"></div>
                    <p>Fetching contest data from Yahoo...</p>
                    <p style="margin-top: 10px; font-size: 12px; color: #666;">
                        This may take 30-60 seconds for large contests.
                    </p>
                </div>
            `;

            try {
                // Use AbortController for timeout (2 minutes)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 120000);

                const response = await fetch('/api/dashboard', {
                    signal: controller.signal
                });
                clearTimeout(timeoutId);

                if (!response.ok) throw new Error('Failed to fetch data');

                const data = await response.json();
                renderDashboard(data);

            } catch (error) {
                const errorMsg = error.name === 'AbortError'
                    ? 'Request timed out. The server is still processing. Try again in a moment.'
                    : error.message;
                content.innerHTML = `
                    <div class="error">
                        <strong>Error:</strong> ${errorMsg}
                    </div>
                `;
            } finally {
                btn.disabled = false;
                btn.textContent = 'Refresh';
            }
        }

        function formatCurrency(amount) {
            return '$' + amount.toFixed(2);
        }

        function formatNumber(num) {
            return num.toLocaleString();
        }

        function renderDashboard(data) {
            const content = document.getElementById('content');
            const timestamp = document.getElementById('timestamp');

            const ts = new Date(data.timestamp);
            timestamp.textContent = `Last updated: ${ts.toLocaleTimeString()}`;

            if (data.contests.length === 0) {
                content.innerHTML = `
                    <div class="no-data">
                        <p>No submitted contests found.</p>
                        <p style="margin-top: 10px; font-size: 14px;">
                            Submit lineups to see live data here.
                        </p>
                    </div>
                `;
                return;
            }

            const overall = data.overall;
            const profitClass = overall.net_profit >= 0 ? 'positive' : 'negative';

            let html = `
                <div class="overall-stats">
                    <div class="stat-card">
                        <div class="stat-label">Contests</div>
                        <div class="stat-value neutral">${overall.contests_count}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Total Entries</div>
                        <div class="stat-value neutral">${formatNumber(overall.total_entries)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Entry Fees</div>
                        <div class="stat-value neutral">${formatCurrency(overall.total_entry_fees)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Total Winnings</div>
                        <div class="stat-value positive">${formatCurrency(overall.total_winnings)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Net Profit</div>
                        <div class="stat-value ${profitClass}">${formatCurrency(overall.net_profit)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Entries Winning</div>
                        <div class="stat-value positive">${overall.entries_winning}</div>
                    </div>
                </div>

                <div class="contests-section">
                    <h2>Contest Details</h2>
            `;

            for (const contest of data.contests) {
                const statusClass = contest.status === 'live' ? 'status-live' : 'status-completed';
                const contestProfitClass = (contest.total_winnings - (contest.entry_fee * contest.user_entries)) >= 0 ? 'positive' : 'negative';

                html += `
                    <div class="contest-card">
                        <div class="contest-header">
                            <div>
                                <div class="contest-name">${contest.contest_name || 'Contest ' + contest.contest_id}</div>
                                <div style="color: #888; font-size: 12px; margin-top: 4px;">
                                    ${contest.sport || ''} | ID: ${contest.contest_id}
                                </div>
                            </div>
                            <span class="contest-status ${statusClass}">${contest.status}</span>
                        </div>
                        <div class="contest-body">
                            <div class="contest-stats">
                                <div class="contest-stat">
                                    <div class="contest-stat-label">Your Entries</div>
                                    <div class="contest-stat-value neutral">${contest.user_entries}</div>
                                </div>
                                <div class="contest-stat">
                                    <div class="contest-stat-label">Total Entries</div>
                                    <div class="contest-stat-value">${formatNumber(contest.total_entries)}</div>
                                </div>
                                <div class="contest-stat">
                                    <div class="contest-stat-label">Entry Fee</div>
                                    <div class="contest-stat-value">${formatCurrency(contest.entry_fee)}</div>
                                </div>
                                <div class="contest-stat">
                                    <div class="contest-stat-label">Total Winnings</div>
                                    <div class="contest-stat-value positive">${formatCurrency(contest.total_winnings)}</div>
                                </div>
                                <div class="contest-stat">
                                    <div class="contest-stat-label">Best Rank</div>
                                    <div class="contest-stat-value">${formatNumber(contest.best_rank)}</div>
                                </div>
                                <div class="contest-stat">
                                    <div class="contest-stat-label">Avg Percentile</div>
                                    <div class="contest-stat-value">${contest.avg_percentile.toFixed(1)}%</div>
                                </div>
                            </div>

                            <table class="entries-table">
                                <thead>
                                    <tr>
                                        <th>Rank</th>
                                        <th>Percentile</th>
                                        <th>Score</th>
                                        <th>Winnings</th>
                                    </tr>
                                </thead>
                                <tbody>
                `;

                // Sort entries by rank
                const sortedEntries = [...contest.user_entry_details].sort((a, b) => a.rank - b.rank);

                for (const entry of sortedEntries) {
                    const winClass = entry.winnings > 0 ? 'positive' : '';
                    html += `
                        <tr>
                            <td>${formatNumber(entry.rank)}</td>
                            <td>${entry.percentile.toFixed(1)}%</td>
                            <td>${entry.score.toFixed(2)}</td>
                            <td class="${winClass}">${formatCurrency(entry.winnings)}</td>
                        </tr>
                    `;
                }

                html += `
                                </tbody>
                            </table>
                        </div>
                    </div>
                `;
            }

            html += '</div>';
            content.innerHTML = html;
        }

        // Load data on page load
        refreshData();
    </script>
</body>
</html>
'''
