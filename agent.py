import os
import requests
from datetime import datetime
from typing import TypedDict, Annotated

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

# Import working backend tools
from predictor import predict_match
from rag_indexer import setup_qdrant, COLLECTION_NAME, get_embedding_model
from qdrant_client.models import Filter, FieldCondition, MatchValue

# --------------------------------------------------------------------------- #
# Team Name Normalization Dictionary
# --------------------------------------------------------------------------- #
TEAM_ALIASES = {
    "manchester city": "Man City",
    "man city": "Man City",
    "mancity": "Man City",
    "manchester united": "Man Utd",
    "man united": "Man Utd",
    "man utd": "Man Utd",
    "tottenham": "Spurs",
    "tottenham hotspur": "Spurs",
    "spurs": "Spurs",
    "nottingham forest": "Nott'm Forest",
    "nottm forest": "Nott'm Forest",
    "nott'm forest": "Nott'm Forest",
    "wolverhampton": "Wolves",
    "wolves": "Wolves",
    "newcastle united": "Newcastle",
    "newcastle": "Newcastle",
    "leicester city": "Leicester",
    "leicester": "Leicester",
}

def normalize_team(team_name: str) -> str:
    """Converts user or LLM input into the strict canonical name expected by our APIs."""
    clean_name = team_name.lower().strip()
    return TEAM_ALIASES.get(clean_name, team_name.title())  # Default to Title Case if not in dict

# --------------------------------------------------------------------------- #
# 1. Define the Tools
# --------------------------------------------------------------------------- #
@tool
def predict_match_tool(home_team: str, away_team: str) -> str:
    """Use this to predict the statistical probability of a football match outcome.
    Requires the home team and away team names."""
    try:
        # Normalize the names
        home = normalize_team(home_team)
        away = normalize_team(away_team)

        # Quick patch: predictor.py expects "Man United", but FPL expects "Man Utd"
        if home == "Man Utd": home = "Man United"
        if away == "Man Utd": away = "Man United"

        result = predict_match(home, away)
        probs = result['probabilities']
        fav = result['most_likely']
        reasons = "\n".join([f"- {r['factor']}: {r['value']} (pushes {r['pushes']} {fav})" for r in result[f"why_{fav}"]])

        return (
            f"Prediction for {result['home']} vs {result['away']}:\n"
            f"Home Win: {probs['home_win'] * 100:.1f}%\n"
            f"Draw: {probs['draw'] * 100:.1f}%\n"
            f"Away Win: {probs['away_win'] * 100:.1f}%\n"
            f"Key statistical factors:\n{reasons}"
        )
    except Exception as e:
        return f"Could not generate prediction: {str(e)}"

@tool
def search_football_news(team: str, query: str) -> str:
    """Use this to find qualitative context, injuries, or tactical news for a specific team.
    Requires the team name and a search query."""
    try:
        qdrant = setup_qdrant()
        embedder = get_embedding_model()
        query_vector = embedder.embed_query(query)
        
        search_result = qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=Filter(
                must=[FieldCondition(key="team", match=MatchValue(value=team))]
            ),
            limit=2,
            with_payload=True
        )
        qdrant.close()
        
        if not search_result.points:
            return f"No news found for {team} regarding '{query}'."
            
        articles = "\n\n".join([f"Date: {p.payload['date']}\nSource: {p.payload['source']}\n{p.payload['text']}" for p in search_result.points])
        return f"Here is the latest news for {team}:\n\n{articles}"
    except Exception as e:
        return f"Error retrieving news: {str(e)}"

@tool
def get_upcoming_fixtures() -> str:
    """Use this to find the schedule of upcoming Premier League matches for this weekend or the near future.
    It returns a list of the next 10 scheduled fixtures."""
    try:
        bootstrap_url = "https://fantasy.premierleague.com/api/bootstrap-static/"
        bootstrap_data = requests.get(bootstrap_url, timeout=5).json()
        team_map = {team['id']: team['name'] for team in bootstrap_data['teams']}

        fixtures_url = "https://fantasy.premierleague.com/api/fixtures/"
        response = requests.get(fixtures_url, timeout=5).json()
        
        upcoming = [m for m in response if m.get('finished') is False]
        
        if not upcoming:
            return "No upcoming fixtures found."
            
        schedule = []
        for match in upcoming[:10]:
            home = team_map.get(match['team_h'], f"Team {match['team_h']}")
            away = team_map.get(match['team_a'], f"Team {match['team_a']}")
            
            raw_date = match['kickoff_time']
            parsed_date = datetime.strptime(raw_date, "%Y-%m-%dT%H:%M:%SZ")
            formatted_date = parsed_date.strftime("%A, %b %d at %H:%M UTC")
            
            schedule.append(f"- {formatted_date}: {home} vs {away}")
            
        return "Upcoming Premier League Fixtures:\n" + "\n".join(schedule)
    except Exception as e:
        return f"Error fetching schedule: {str(e)}"

@tool
def get_team_roster(team_name: str) -> str:
    """Use this to get the CURRENT, up-to-date real roster of forwards and midfielders for a specific Premier League team.
    Call this to avoid hallucinating players who no longer play for the club."""
    try:
        norm_team = normalize_team(team_name)  # Fix the name first!

        url = "https://fantasy.premierleague.com/api/bootstrap-static/"
        data = requests.get(url, timeout=5).json()

        team_id = None
        for t in data['teams']:
            # Compare the normalized name to the FPL name
            if norm_team.lower() in t['name'].lower() or t['name'].lower() in norm_team.lower():
                team_id = t['id']
                break

        if not team_id:
            return f"Could not find a current Premier League team matching '{team_name}'."

        attackers = []
        for player in data['elements']:
            if player['team'] == team_id and player['element_type'] in [3, 4]:
                attackers.append(player['web_name'])

        if not attackers:
            return f"No attacking players found for {team_name}."

        return f"Current Attackers/Midfielders for {norm_team}: " + ", ".join(attackers)
    except Exception as e:
        return f"Error fetching roster: {str(e)}"

# --------------------------------------------------------------------------- #
# 2. Build the LangGraph Agent
# --------------------------------------------------------------------------- #
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

SYSTEM_PROMPT = """You are PitchIQ, an elite AI football analyst.
Provide concise, data-driven analysis using your tools.

RULES:
1. Tool Usage: Call a tool only if you need missing data. NEVER call the same tool twice in one turn.
2. If the user asks for fixtures/schedule, call `get_upcoming_fixtures` once, then present the list.
3. If the user asks for match predictions, call `predict_match_tool(home_team, away_team)` once.
4. If the user asks for current players/scorers, call `get_team_roster(team_name)` once.
5. STOPPING RULE: Once you receive tool output containing the requested information, DO NOT call any more tools. Summarize the findings clearly in plain text for the user.
"""

def create_pitchiq_agent():
    tools = [predict_match_tool, search_football_news, get_upcoming_fixtures, get_team_roster]
    
    llm = ChatOllama(model="llama3.2", temperature=0)
    llm_with_tools = llm.bind_tools(tools)
    
    def agent_node(state: AgentState):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}
        
    tool_node = ToolNode(tools)
    
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", tools_condition)
    workflow.add_edge("tools", "agent")
    
    return workflow.compile()

# --------------------------------------------------------------------------- #
# 3. Interactive CLI Chat Loop
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    app = create_pitchiq_agent()
    print("\n" + "=" * 60)
    print(" PitchIQ Agent Online (Type 'quit' or 'exit' to stop)")
    print("=" * 60 + "\n")
    
    while True:
        try:
            query = input("PitchIQ > ").strip()
            if not query:
                continue
            if query.lower() in ("quit", "exit", "q"):
                print("Shutting down PitchIQ.")
                break

            user_input = {"messages": [HumanMessage(content=query)]}
            
            # recursion_limit=8 prevents any runaway execution loops
            for chunk in app.stream(user_input, config={"recursion_limit": 8}):
                if "tools" in chunk:
                    tool_data = chunk['tools']['messages'][-1].content
                    print(f"\n[Executing Tool...]\n{tool_data}\n")
                elif "agent" in chunk:
                    msg = chunk["agent"]["messages"][-1]
                    if msg.content:
                        print(f"\n[PitchIQ Analysis]:\n{msg.content}\n")

        except KeyboardInterrupt:
            print("\nShutting down PitchIQ.")
            break
        except Exception as e:
            print(f"\n[Error]: {e}\n")