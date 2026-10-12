"""News search index (RAG): Qdrant vector database + local embedding model.

Same public functions as before (setup_qdrant, get_embedding_model, ingest_documents,
COLLECTION_NAME), so agent.py and news_ingester.py keep working.

Storage modes (pick with environment variables):
    default                   local folder ./qdrant_storage  (simple, but ONE process at a time)
    QDRANT_URL=http://...     a real Qdrant server, e.g. `docker run -p 6333:6333 qdrant/qdrant`
                              (needed once the API and the news job run at the same time)
    PITCHIQ_QDRANT_PATH=:memory:   throwaway index, used by tests

    python rag_indexer.py --demo     load 3 SYNTHETIC demo articles (never use these for real answers)
"""
import argparse
import hashlib
import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams

from teams import canonical

BASE_DIR = Path(os.getenv("PITCHIQ_HOME", Path(__file__).resolve().parent))
DB_PATH = Path(os.getenv("PITCHIQ_DB", BASE_DIR / "pitchiq.db"))
QDRANT_PATH = BASE_DIR / "qdrant_storage"
COLLECTION_NAME = "football_news"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"    # 384-dimensional vectors
EMBEDDING_SIZE = 384

log = logging.getLogger("pitchiq.rag")


# --------------------------------------------------------------------------- #
# Small helpers shared with news_ingester.py and agent.py
# --------------------------------------------------------------------------- #
def to_iso_utc(value: Optional[str]) -> Optional[str]:
    """'Wed, 08 Oct 2026 09:30:00 GMT' or '2026-10-08T09:30:00Z' -> '2026-10-08T09:30:00+00:00'.
    One date format everywhere, so 'newest first' and 'older than 30 days' just work."""
    if not value:
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)          # RSS style dates
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def point_id(*parts: object) -> str:
    """Same input -> same ID, so re-running an import UPDATES points instead of duplicating them."""
    return str(uuid5(NAMESPACE_URL, "|".join(str(p) for p in parts)))


# --------------------------------------------------------------------------- #
# 1. Qdrant + embedding model
# --------------------------------------------------------------------------- #
def setup_qdrant() -> QdrantClient:
    """Connect to Qdrant (server if QDRANT_URL is set, otherwise a local folder) and make
    sure the collection exists."""
    url = os.getenv("QDRANT_URL")
    where = os.getenv("PITCHIQ_QDRANT_PATH")
    if url:
        client = QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY"))
    elif where == ":memory:":
        client = QdrantClient(":memory:")
    else:
        path = Path(where) if where else QDRANT_PATH
        path.mkdir(parents=True, exist_ok=True)
        client = QdrantClient(path=str(path))

    if not client.collection_exists(COLLECTION_NAME):
        log.info("Creating Qdrant collection %s", COLLECTION_NAME)
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_SIZE, distance=Distance.COSINE),
        )
        for field, schema in (("team", PayloadSchemaType.KEYWORD), ("date", PayloadSchemaType.DATETIME),
                              ("synthetic", PayloadSchemaType.BOOL)):
            try:   # indexes speed up filters on a server; local mode just ignores them
                client.create_payload_index(COLLECTION_NAME, field, schema)
            except Exception as exc:  # noqa: BLE001
                log.debug("payload index on %s skipped: %s", field, exc)
    return client


@lru_cache(maxsize=1)
def get_embedding_model():
    """Load the embedding model ONCE per process (it takes seconds and ~100 MB), not per question."""
    from langchain_huggingface import HuggingFaceEmbeddings   # heavy import, so only when needed
    log.info("Loading embedding model %s ...", EMBEDDING_MODEL)
    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)


# --------------------------------------------------------------------------- #
# 2. Ingestion
# --------------------------------------------------------------------------- #
def ingest_documents(documents: list[dict], client: QdrantClient, embeddings_model) -> int:
    """Split documents into chunks, embed them, store them with metadata. Returns chunks stored.

    Each document: {"text", "team", "date", "source", optional "url", optional "synthetic"}.
    Safe to run repeatedly: IDs are derived from the content, so nothing is duplicated and
    nothing is overwritten by accident."""
    if not documents:
        log.info("No documents to ingest.")
        return 0
    from langchain_text_splitters import RecursiveCharacterTextSplitter   # light, but lazy for fast imports

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50, length_function=len)
    points = []
    for doc in documents:
        team = canonical(doc["team"]) or doc["team"]
        date = to_iso_utc(doc.get("date")) or datetime.now(timezone.utc).isoformat(timespec="seconds")
        chunks = splitter.split_text(doc["text"])
        for i, (chunk, vector) in enumerate(zip(chunks, embeddings_model.embed_documents(chunks))):
            digest = hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:12]
            points.append(PointStruct(
                id=point_id(doc.get("url") or doc.get("source", ""), team, date, i, digest),
                vector=vector,
                payload={"text": chunk, "team": team, "date": date, "chunk_index": i,
                         "source": doc.get("source", "Unknown"), "url": doc.get("url"),
                         "synthetic": bool(doc.get("synthetic", False))},
            ))
    client.upsert(collection_name=COLLECTION_NAME, points=points)
    log.info("Stored %d chunks.", len(points))
    return len(points)


# --------------------------------------------------------------------------- #
# 3. Demo data. FLAGGED as synthetic: the agent never searches these.
# --------------------------------------------------------------------------- #
def get_mock_news() -> list[dict]:
    src = "SYNTHETIC DEMO DATA"
    return [
        {"text": "Arsenal defender William Saliba was substituted in the 60th minute against Liverpool after "
                 "complaining of tightness in his hamstring. Manager Mikel Arteta said it was precautionary.",
         "team": "Arsenal", "date": "2024-03-15T18:00:00Z", "source": src, "synthetic": True},
        {"text": "Manchester City's midfield continues to struggle without Rodri, leading to a 1-1 draw against Aston Villa.",
         "team": "Man City", "date": "2024-03-10T21:30:00Z", "source": src, "synthetic": True},
        {"text": "Chelsea confirmed that Reece James will undergo surgery on a recurring hamstring injury.",
         "team": "Chelsea", "date": "2023-12-20T10:00:00Z", "source": src, "synthetic": True},
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description="PitchIQ news index")
    parser.add_argument("--demo", action="store_true", help="load 3 synthetic demo articles (for testing only)")
    args = parser.parse_args()
    qdrant = setup_qdrant()
    try:
        if args.demo:
            ingest_documents(get_mock_news(), qdrant, get_embedding_model())
        total = qdrant.count(COLLECTION_NAME).count
        print(f"Index '{COLLECTION_NAME}' holds {total} chunks. Load real news with: python news_ingester.py")
    finally:
        qdrant.close()