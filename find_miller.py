from src.yahoo.api import get_api_client

api = get_api_client()
players = api.get_contest_players('15251987')

# Find Jordan Miller
for p in players:
    name = p.get('playerDetails', {}).get('fullName', '') or p.get('name', '')
    if 'Miller' in name:
        player_id = p.get('playerId', '')
        positions = p.get('eligiblePositions', [])
        salary = p.get('salary', 0)
        code = p.get('playerGameCode', '')
        print(f'Player: {name}, ID: {player_id}, Positions: {positions}, Salary: {salary}, Code: {code}')
