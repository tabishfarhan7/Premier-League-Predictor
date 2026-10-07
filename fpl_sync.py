import requests
import sqlite3
import pandas as pd

def sync_fpl_data():
    print("Fetching real-time player data from FPL API...")
    
    url = "https://fantasy.premierleague.com/api/bootstrap-static/"
    response = requests.get(url).json()
    
    # 2. Extract the arrays we care about
    players = pd.DataFrame(response['elements'])
    teams = pd.DataFrame(response['teams'])
    
    # 3. Filter only the columns we want for our ML model
    # 'form' is their recent performance score.
    # 'status' is their availability: 'a' (available), 'i' (injured), 's' (suspended)
    players_clean = players[['id', 'team', 'first_name', 'second_name', 'form', 'status', 'total_points']].copy()
    
    # FPL stores 'form' as a string, let's cast it to a float for our math
    players_clean['form'] = pd.to_numeric(players_clean['form'], errors='coerce').fillna(0)
    
    # 4. Save this directly into our local SQLite database as a cache
    conn = sqlite3.connect("pitchiq.db")
    
    # We use if_exists="replace" because we always want this table to represent the EXACT present moment
    players_clean.to_sql("fpl_players", conn, if_exists="replace", index=False)
    teams[['id', 'name', 'strength']].to_sql("fpl_teams", conn, if_exists="replace", index=False)
    
    conn.close()
    print("Successfully cached real-time player data into SQLite.")

if __name__ == "__main__":
    sync_fpl_data()