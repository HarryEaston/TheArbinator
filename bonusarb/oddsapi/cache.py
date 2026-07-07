"""On-disk JSON cache for Odds API responses."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from bonusarb.config import CACHE_DIR, DEFAULT_CACHE_TTL_SECONDS


class OddsCache:
    def __init__(self, cache_dir: Path | None = None, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        self.cache_dir = cache_dir or CACHE_DIR
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(f"{namespace}:{key}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{namespace}_{digest}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._key_path(namespace, key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - payload["saved_at"] > self.ttl_seconds:
            return None
        return payload["data"]

    def set(self, namespace: str, key: str, data: Any) -> None:
        path = self._key_path(namespace, key)
        payload = {"saved_at": time.time(), "data": data}
        path.write_text(json.dumps(payload), encoding="utf-8")

    def has_fresh(self, namespace: str, key: str) -> bool:
        return self.get(namespace, key) is not None
