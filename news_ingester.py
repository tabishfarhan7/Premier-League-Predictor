"""Fetch Premier League news from RSS feeds and index it for the agent.

    python news_ingester.py                  # fetch, index, delete articles older than 60 days
    python news_ingester.py --dry-run        # show what WOULD be indexed
    python news_ingester.py --prune-days 30  # keep only the last 30 days (0 = never delete)

Safe to run every few hours: each article gets a stable ID, so re-running updates it
instead of adding a copy. Exit code 1 if any feed failed.

Only add feeds whose terms allow this use. BBC's RSS is offered for personal,
non-commercial use. Read the terms of any feed before adding it to FEEDS.
"""
import argparse
import html
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from qdrant_client.models import DatetimeRange, FieldCondition, Filter, FilterSelector

try:                                    # defusedxml protects against malicious XML; optional
    from defusedxml import ElementTree as ET
except ImportError:                     # pragma: no cover
    import xml.etree.ElementTree as ET

from rag_indexer import COLLECTION_NAME, get_embedding_model, ingest_documents, setup_qdrant, to_iso_utc
from teams import find_teams_in_text

FEEDS = [
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml"),
    # Add more as (name, url) after checking their terms, for example a club or newspaper RSS.
]
HEADERS = {"User-Agent": "PitchIQ/1.0 (learning project)"}

log = logging.getLogger("pitchiq.news")


def clean_text(raw: Optional[str]) -> str:
    """Remove HTML tags and entities (&amp; &#39; ...) and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))
    return re.sub(r"\s+", " ", text).strip()


def _child_text(item, tag: str) -> str:
    """All the text inside a tag, including text after nested markup (<title>Hi <b>there</b></title>)."""
    node = item.find(tag)
    return "".join(node.itertext()).strip() if node is not None else ""


def parse_feed(xml_bytes: bytes, source: str) -> list[dict]:
    """RSS XML -> list of {title, description, url, date}. Items missing a title are skipped."""
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall("./channel/item"):
        title = clean_text(_child_text(item, "title"))
        if not title:
            continue
        items.append({
            "title": title,
            "description": clean_text(_child_text(item, "description")),
            "url": _child_text(item, "link") or None,
            "date": to_iso_utc(_child_text(item, "pubDate")) or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source,
        })
    return items


def to_documents(items: list[dict]) -> list[dict]:
    """One document per (article, mentioned club), so a search for a club finds every article
    that mentions it. Articles that mention no known club are skipped."""
    docs = []
    for it in items:
        text = f"{it['title']}. {it['description']}".strip()
        for team in sorted(find_teams_in_text(text)):
            docs.append({"text": text, "team": team, "date": it["date"],
                         "source": it["source"], "url": it["url"]})
    return docs


def prune_old(client, days: int) -> None:
    """Delete articles older than `days`. Old injury news is worse than no news."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(filter=Filter(must=[FieldCondition(key="date", range=DatetimeRange(lt=cutoff))])),
    )
    log.info("Pruned articles older than %d days", days)


def run(feeds=FEEDS, prune_days: int = 60, dry_run: bool = False) -> int:
    session = requests.Session()
    session.headers.update(HEADERS)
    docs, failed = [], []
    for name, url in feeds:
        try:
            resp = session.get(url, timeout=(5, 20))
            resp.raise_for_status()
            items = parse_feed(resp.content, name)
            found = to_documents(items)
            log.info("[%s] %d articles, %d tagged to a club", name, len(items), len(found))
            docs += found
        except Exception as exc:  # noqa: BLE001 - one broken feed must not stop the others
            failed.append(name)
            log.error("[%s] FAILED: %s: %s", name, type(exc).__name__, exc)

    if dry_run:
        for d in docs[:15]:
            print(f"{d['date'][:10]}  {d['team']:<14} {d['text'][:90]}")
        print(f"(dry run) {len(docs)} club-tagged documents would be indexed")
        return 1 if failed else 0

    client = setup_qdrant()
    try:
        if docs:
            ingest_documents(docs, client, get_embedding_model())
        else:
            log.info("Nothing new to index.")
        if prune_days > 0:
            prune_old(client, prune_days)
        log.info("Index now holds %d chunks", client.count(COLLECTION_NAME).count)
    finally:
        client.close()                  # always release the local storage lock
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PitchIQ news ingester")
    parser.add_argument("--prune-days", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    return run(prune_days=args.prune_days, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())