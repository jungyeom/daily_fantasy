from src.yahoo.api import get_api_client

api = get_api_client()

# Check contest 15251988
print("Contest 15251988 players:")
players_88 = api.get_contest_players('15251988')
print(f"Total players: {len(players_88)}")

# Print first 3 players raw to see format
print("\nFirst 3 players raw data:")
for p in players_88[:3]:
    print(f"  Keys: {p.keys()}")
    print(f"  playerId: {p.get('playerId')}")
    print(f"  name: {p.get('name')}")
    print(f"  playerDetails: {p.get('playerDetails')}")
    print(f"  eligiblePositions: {p.get('eligiblePositions')}")
    print()

# Build lookup by player ID
player_lookup = {}
for p in players_88:
    player_id = str(p.get('playerId', ''))
    name = p.get('playerDetails', {}).get('fullName', '') or p.get('name', '')
    positions = p.get('eligiblePositions', [])
    player_lookup[f'nba.p.{player_id}'] = {
        'name': name,
        'positions': positions
    }
    # Find Jordan Miller
    if 'Miller' in name:
        print(f"Found {name}: ID=nba.p.{player_id}, Positions={positions}")

print()

# Check the players in line 2 of the CSV for 15251988
csv_players = [
    'nba.p.6218', 'nba.p.10337', 'nba.p.10084', 'nba.p.5482',
    'nba.p.6036', 'nba.p.5636', 'nba.p.5643', 'nba.p.6734'
]
csv_positions = ['PG', 'SG', 'SF', 'PF', 'C', 'G', 'F', 'UTIL']

print("CSV 15251988 Line 2 Analysis:")
print("="*60)
for i, (pid, roster_pos) in enumerate(zip(csv_players, csv_positions)):
    info = player_lookup.get(pid, {'name': 'UNKNOWN', 'positions': []})
    eligible = info['positions']
    name = info['name']
    is_valid = roster_pos in eligible or roster_pos == 'UTIL'

    # Special flex position handling
    if roster_pos == 'G' and any(p in eligible for p in ['PG', 'SG']):
        is_valid = True
    if roster_pos == 'F' and any(p in eligible for p in ['SF', 'PF']):
        is_valid = True

    status = "✓" if is_valid else "✗ INVALID"
    print(f"{roster_pos}: {name} (ID: {pid})")
    print(f"   Eligible: {eligible} {status}")
    print()
