"""PitchIQ API settings – a plain dataclass built from os.getenv.

No pydantic-settings, no heavy imports, no side-effects at module level.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # CORS
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip()
        for o in os.getenv(
            "PITCHIQ_CORS_ORIGINS",
            "http://localhost:5173,http://localhost:3000",
        ).split(",")
        if o.strip()
    ])

    # Auth
    api_key: str | None = field(
        default_factory=lambda: os.getenv("PITCHIQ_API_KEY") or None
    )

    # Rate limiting  (requests per minute per IP on /chat endpoints)
    rate_limit_per_min: int = field(
        default_factory=lambda: int(os.getenv("PITCHIQ_RATE_LIMIT_PER_MIN", "30"))
    )

    # Concurrency cap for simultaneous LLM calls
    max_concurrent_chats: int = field(
        default_factory=lambda: int(os.getenv("PITCHIQ_MAX_CONCURRENT_CHATS", "2"))
    )

    # SSE / streaming overall wall-clock timeout in seconds
    chat_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("PITCHIQ_CHAT_TIMEOUT_S", "90"))
    )

    # LLM spec string, e.g. "ollama:llama3.2" or "openai:gpt-4o-mini"
    llm_spec: str = field(
        default_factory=lambda: os.getenv("PITCHIQ_LLM", "ollama:llama3.2")
    )

    # Optional OpenAI key (for /ready check)
    openai_api_key: str | None = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY") or None
    )

    # Ollama host (used by /ready)
    ollama_host: str = field(
        default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434")
    )


# Singleton – imported once per process (no side effects at construction)
settings = Settings()
