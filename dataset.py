import pandas as pd
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, 'mlbb_stats.csv')

def load_clean_df():
    """Loads data. Auto-generates an advanced CSV if missing."""
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        print("\n⚠️ [SYSTEM] Upgrading to Advanced Analytics CSV...\n")
        sample_data = """MatchID,PlayerName,TeamName,Role,Kills,Deaths,Assists,Points,MatchResult
1,Faker_Wannabe,T1_Esports,Mid,8,2,10,15,Win
1,JungleDiff,T1_Esports,Jungler,12,1,5,20,Win
2,Faker_Wannabe,T1_Esports,Mid,5,5,8,8,Loss
2,JungleDiff,T1_Esports,Jungler,4,6,4,5,Loss
3,TowerHugger,Blacklist,Gold,2,8,2,-2,Loss
3,SniperKing,Blacklist,Mid,10,2,4,18,Loss
4,TowerHugger,Blacklist,Gold,9,1,5,15,Win
4,SniperKing,Blacklist,Mid,12,0,8,25,Win"""
        with open(CSV_FILE, 'w') as f:
            f.write(sample_data)

    try:
        df = pd.read_csv(CSV_FILE)
        df.columns = df.columns.str.strip()
        
        # Standardize matching to avoid typos
        rename_map = {}
        for col in df.columns:
            c = col.lower().replace(" ", "").replace("_", "")
            if "matchid" in c: rename_map[col] = "MatchID"
            elif "player" in c: rename_map[col] = "PlayerName"
            elif "team" in c: rename_map[col] = "TeamName"
            elif "role" in c: rename_map[col] = "Role"
            elif "kill" in c: rename_map[col] = "Kills"
            elif "death" in c: rename_map[col] = "Deaths"
            elif "assist" in c: rename_map[col] = "Assists"
            elif "point" in c: rename_map[col] = "Points"
            elif "result" in c: rename_map[col] = "MatchResult"
        return df.rename(columns=rename_map)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return None

def get_player_leaderboard():
    """Calculates Player Points, Matches Played, and Win Rate."""
    df = load_clean_df()
    if df is None or df.empty: return []
    try:
        # Convert Win/Loss text into a 1 or 0 for easy math
        df['IsWin'] = (df['MatchResult'].str.lower() == 'win').astype(int)
        
        stats = df.groupby(['PlayerName', 'TeamName']).agg(
            Points=('Points', 'sum'),
            MatchesPlayed=('MatchID', 'nunique'),
            Wins=('IsWin', 'sum')
        ).reset_index()
        
        # Calculate Win Rate %
        stats['WinRate'] = ((stats['Wins'] / stats['MatchesPlayed']) * 100).round(1).astype(str) + '%'
        return stats.sort_values(by='Points', ascending=False).to_dict('records')
    except Exception as e:
        print(f"Player DB Error: {e}")
        return []

def get_team_leaderboard():
    """Calculates Team Points, Total Matches, Wins, and Losses."""
    df = load_clean_df()
    if df is None or df.empty: return []
    try:
        # Group by MatchID first so a 5-player team isn't counted as 5 matches
        team_matches = df.groupby(['TeamName', 'MatchID']).agg(
            MatchResult=('MatchResult', 'first'),
            Points=('Points', 'sum')
        ).reset_index()
        
        team_matches['IsWin'] = (team_matches['MatchResult'].str.lower() == 'win').astype(int)
        team_matches['IsLoss'] = (team_matches['MatchResult'].str.lower() == 'loss').astype(int)
        
        stats = team_matches.groupby('TeamName').agg(
            MatchesPlayed=('MatchID', 'nunique'),
            Wins=('IsWin', 'sum'),
            Losses=('IsLoss', 'sum'),
            Points=('Points', 'sum')
        ).reset_index()
        
        return stats.sort_values(by='Points', ascending=False).to_dict('records')
    except Exception as e:
        print(f"Team DB Error: {e}")
        return []

def get_all_stats():
    """Aggregates KDA stats for the chatbot context."""
    df = load_clean_df()
    if df is None or df.empty: return []
    try:
        stats = df.groupby(['PlayerName', 'TeamName', 'Role'])[['Kills', 'Deaths', 'Assists']].sum().reset_index()
        return stats.to_dict('records')
    except Exception:
        return []