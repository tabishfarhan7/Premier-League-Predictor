import os
import sqlite3
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, PayloadSchemaType
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(os.getenv("PITCHIQ_HOME", Path(__file__).resolve().parent))
DB_PATH = Path(os.getenv("PITCHIQ_DB", BASE_DIR / "pitchiq.db"))
QDRANT_PATH = BASE_DIR / "qdrant_storage"
COLLECTION_NAME = "football_news"

# --------------------------------------------------------------------------- #
# 1. Initialize Qdrant and Embedding Model
# --------------------------------------------------------------------------- #
def setup_qdrant() -> QdrantClient:
    """Initialize a local Qdrant database."""
    QDRANT_PATH.mkdir(exist_ok=True)
    # Using local persistent storage
    client = QdrantClient(path=str(QDRANT_PATH))
    
    # Check if collection exists, create if it doesn't
    if not client.collection_exists(COLLECTION_NAME):
        print(f"Creating Qdrant collection: {COLLECTION_NAME}")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE),
            # Enable sparse vectors for BM25 (Hybrid Search) later if needed natively,
            # or rely on metadata filtering as the primary hard constraint.
        )
        
        # Create metadata indices for fast filtering
        client.create_payload_index(COLLECTION_NAME, "team", PayloadSchemaType.KEYWORD)
        client.create_payload_index(COLLECTION_NAME, "date", PayloadSchemaType.DATETIME)
    
    return client

def get_embedding_model() -> HuggingFaceEmbeddings:
    """Load a fast, local embedding model."""
    print("Loading embedding model (this may take a moment the first time)...")
    # This model produces 384-dimensional vectors
    return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

# --------------------------------------------------------------------------- #
# 2. Ingestion (Chunking and Vectorizing)
# --------------------------------------------------------------------------- #
def ingest_documents(documents: list[dict], client: QdrantClient, embeddings_model: HuggingFaceEmbeddings):
    """
    Process raw documents, split them into manageable chunks, embed them, 
    and store them in Qdrant with metadata.
    """
    if not documents:
        print("No documents to ingest.")
        return

    print(f"Processing {len(documents)} raw documents...")
    
    # We chunk text because LLMs have context limits, and vector meaning dilutes 
    # if the text is too long (e.g., embedding a whole book loses the detail).
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,       # ~100 words per chunk
        chunk_overlap=50,     # Overlap ensures sentences aren't cut awkwardly in half
        length_function=len,
    )
    
    points = []
    point_id = 1
    
    for doc in documents:
        chunks = text_splitter.split_text(doc["text"])
        
        # Embed all chunks for this document
        vectors = embeddings_model.embed_documents(chunks)
        
        for i, chunk in enumerate(chunks):
            # Create a unique payload for each chunk
            payload = {
                "text": chunk,
                "team": doc["team"],
                "date": doc["date"],
                "source": doc.get("source", "Unknown"),
                "chunk_index": i
            }
            
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vectors[i],
                    payload=payload
                )
            )
            point_id += 1

    print(f"Uploading {len(points)} vectorized chunks to Qdrant...")
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=points
    )
    print("Upload complete.")

# --------------------------------------------------------------------------- #
# 3. Mock Data Generation for Testing
# --------------------------------------------------------------------------- #
def get_mock_news() -> list[dict]:
    """Generate synthetic news articles since we can't scrape the web easily in the sandbox."""
    return [
        {
            "text": "Arsenal defender William Saliba was substituted in the 60th minute against Liverpool after complaining of tightness in his hamstring. Manager Mikel Arteta stated it was a precautionary measure, but he is a major doubt for next week's crucial clash.",
            "team": "Arsenal",
            "date": "2024-03-15T18:00:00Z",
            "source": "Synthetic Sports Network"
        },
        {
            "text": "Manchester City's midfield continues to struggle without Rodri. The team looks disjointed in transition, leading to a surprise 1-1 draw against Aston Villa.",
            "team": "Man City",
            "date": "2024-03-10T21:30:00Z",
            "source": "Synthetic Sports Network"
        },
        {
            "text": "Chelsea confirmed today that Reece James will undergo surgery on his recurring hamstring injury, ruling him out for the remainder of the season.",
            "team": "Chelsea",
            "date": "2023-12-20T10:00:00Z",
            "source": "Synthetic Sports Network"
        }
    ]

# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    qdrant = setup_qdrant()
    embedder = get_embedding_model()
    
    mock_docs = get_mock_news()
    ingest_documents(mock_docs, qdrant, embedder)
    
    # Quick Test Query
    test_query = "Who is injured in Arsenal's defense?"
    print(f"\nTesting Query: '{test_query}'")
    
    query_vector = embedder.embed_query(test_query)
    
    # We use a FieldCondition to ensure we ONLY get Arsenal news
    from qdrant_client.models import Filter, FieldCondition, MatchValue, Query
    
    search_result = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=Filter(
            must=[
                FieldCondition(
                    key="team",
                    match=MatchValue(value="Arsenal")
                )
            ]
        ),
        limit=2,
        with_payload=True,
    )
    
    for result in search_result.points:
        print(f"\n- Score: {result.score:.3f}")
        print(f"  Date: {result.payload['date']}")
        print(f"  Text: {result.payload['text']}")