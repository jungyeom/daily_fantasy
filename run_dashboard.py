#!/usr/bin/env python3
"""Run the DFS monitoring dashboard.

Usage:
    python run_dashboard.py
    # Then open http://localhost:8000 in your browser
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from src.common.database import init_database


def main():
    # Initialize database
    init_database()

    print("=" * 60)
    print("DFS Dashboard Server")
    print("=" * 60)
    print("Open http://localhost:8000 in your browser")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    uvicorn.run(
        "src.dashboard.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
