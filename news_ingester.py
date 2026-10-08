import requests
import xml.etree.ElementTree as ET
from uuid import uuid4
from qdrant_client.models import PointStruct

# Import your existing local embedding model and Qdrant setup
from rag_indexer import setup_qdrant, COLLECTION_NAME, get_embedding_model

# We use the same aliases from agent.py to automatically tag articles
TEAM_KEYWORDS = {
    "arsenal": "Arsenal", "aston villa": "Aston Villa", "bournemouth": "Bournemouth",
    "brentford": "Brentford", "brighton": "Brighton", "chelsea": "Chelsea",
    "crystal palace": "Crystal Palace", "everton": "Everton", "fulham": "Fulham",
    "leicester": "Leicester", "liverpool": "Liverpool", "manchester city": "Man City",
    "man city": "Man City", "manchester united": "Man Utd", "man united": "Man Utd",
    "man utd": "Man Utd", "newcastle": "Newcastle", "nottingham forest": "Nott'm Forest",
    "nott'm forest": "Nott'm Forest", "southampton": "Southampton", "spurs": "Spurs",
    "tottenham": "Spurs", "west ham": "West Ham", "wolves": "Wolves"
}

def fetch_and_index_news():
    print("Fetching live RSS feed from BBC Sport...")
    # The official BBC Sport Premier League RSS Feed
    url = "http://feeds.bbci.co.uk/sport/football/premier-league/rss.xml"
    
    try:
        response = requests.get(url, timeout=10)
        root = ET.fromstring(response.content)
    except Exception as e:
        print(f"Failed to fetch RSS: {e}")
        return

    qdrant = setup_qdrant()
    embedder = get_embedding_model()
    
    points = []
    
    # Parse the XML feed for news items
    for item in root.findall('./channel/item'):
        title = item.find('title').text or ""
        desc = item.find('description').text or ""
        date = item.find('pubDate').text or ""
        link = item.find('link').text or "BBC Sport"
        
        full_text = f"{title}. {desc}"
        text_lower = full_text.lower()
        
        # Scan the article text for any team names
        tagged_teams = set()
        for keyword, canonical_name in TEAM_KEYWORDS.items():
            if keyword in text_lower:
                tagged_teams.add(canonical_name)
        
        # If the article mentions a team, generate an embedding and prep it for Qdrant
        if tagged_teams:
            vector = embedder.embed_query(full_text)
            
            # If an article mentions multiple teams (e.g., "Arsenal beats Chelsea"),
            # we index it for both teams so the agent can find it easily.
            for team in tagged_teams:
                point_id = str(uuid4())
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "team": team,
                            "date": date,
                            "source": link,
                            "text": full_text
                        }
                    )
                )

    if points:
        print(f"Found {len(points)} team-specific articles. Indexing into Qdrant...")
        qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )
        print("Live news successfully ingested!")
    else:
        print("No team-specific news found in the latest RSS feed.")
        
    qdrant.close()

if __name__ == "__main__":
    fetch_and_index_news()