#!/usr/bin/env python3
"""Test script to find the user entries API endpoint."""

import json
import pickle
import requests
from pathlib import Path

# Load cookies
cookies_file = Path("data/.yahoo_cookies.pkl")
if not cookies_file.exists():
    print("No cookies file found")
    exit(1)

with open(cookies_file, "rb") as f:
    data = pickle.load(f)
    cookies = data["cookies"]

# Create session with cookies
session = requests.Session()
for cookie in cookies:
    session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ".yahoo.com"))

session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html",
})

# Contest ID to check
contest_id = "15255304"

# First, try to fetch the "My Contests" page to see user's contest participation
print("Trying to fetch My Contests page...")
my_contests_url = "https://sports.yahoo.com/dailyfantasy/mycontests"
resp = session.get(my_contests_url, timeout=30)
print(f"My Contests page status: {resp.status_code}")

# Check if we're logged in by looking at the response
if resp.status_code == 200:
    if "Sign In" in resp.text or "log in" in resp.text.lower():
        print("NOT logged in - need to re-authenticate")
    else:
        # Look for contest ID in the page
        if contest_id in resp.text:
            print(f"Found contest {contest_id} in My Contests page!")
        else:
            print(f"Contest {contest_id} NOT found in My Contests page")

        # Save page for debugging
        with open(f"data/debug/my_contests_page.html", "w") as f:
            f.write(resp.text)
        print("Saved My Contests page to data/debug/my_contests_page.html")

# Now let's try to fetch the user's own contest data with the right RW API
print("\n" + "="*60)
print("Trying authenticated API endpoints...")

# Try dfyql-rw with various endpoints that might need auth
auth_endpoints = [
    f"https://dfyql-rw.sports.yahoo.com/v2/user/contestEntries?contestId={contest_id}",
    f"https://dfyql-rw.sports.yahoo.com/v2/myEntries?contestId={contest_id}",
    f"https://dfyql-rw.sports.yahoo.com/v2/user/entries?sport=nba",
    f"https://dfyql-rw.sports.yahoo.com/v2/upcoming/entries",
    f"https://dfyql-rw.sports.yahoo.com/v2/user/upcoming",
]

for endpoint in auth_endpoints:
    print(f"\nTrying: {endpoint}")
    try:
        resp = session.get(endpoint, timeout=10)
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            try:
                data = resp.json()
                print(f"  Keys: {list(data.keys())}")
            except:
                print(f"  Response (first 200): {resp.text[:200]}")
    except Exception as e:
        print(f"  Error: {e}")
