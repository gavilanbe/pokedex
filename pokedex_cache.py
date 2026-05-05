"""Cache status and resumable prefetch helpers for the Pokedex CLI."""

from dataclasses import dataclass
import datetime
import json
import os


@dataclass(frozen=True)
class CacheStatus:
    """Count cached assets for a concrete Pokemon set."""

    pokemon_count: int
    sprites: int
    cries: int
    data: int

    @property
    def total_assets(self):
        return self.pokemon_count * 3

    @property
    def cached_assets(self):
        return self.sprites + self.cries + self.data

    @property
    def missing_assets(self):
        return max(0, self.total_assets - self.cached_assets)

    @property
    def complete(self):
        return self.missing_assets == 0


def atomic_write_bytes(path, data):
    """Write bytes atomically. Returns True when the file landed on disk."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except OSError:
        _remove_tmp(path)
        return False


def atomic_write_json(path, data):
    """Write JSON atomically. Returns True when the file landed on disk."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        _remove_tmp(path)
        return False


def _remove_tmp(path):
    try:
        os.remove(path + ".tmp")
    except OSError:
        pass


def cache_status(pokemon, sprite_cached, cry_cached, data_cached):
    """Return aggregate cache counts using injected per-asset predicates."""
    sprites = 0
    cries = 0
    data = 0
    for num, name in pokemon:
        if sprite_cached(name):
            sprites += 1
        if cry_cached(name):
            cries += 1
        if data_cached(num):
            data += 1
    return CacheStatus(len(pokemon), sprites, cries, data)


def empty_progress():
    return {"completed": [], "failed": {}, "updated_at": ""}


def load_prefetch_progress(path):
    """Load and normalise prefetch progress metadata."""
    if not os.path.exists(path):
        return empty_progress()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return empty_progress()
    if not isinstance(raw, dict):
        return empty_progress()
    completed = raw.get("completed", [])
    failed = raw.get("failed", {})
    return {
        "completed": sorted({str(k) for k in completed})
        if isinstance(completed, list) else [],
        "failed": {
            str(k): str(v)
            for k, v in failed.items()
        } if isinstance(failed, dict) else {},
        "updated_at": raw.get("updated_at", "")
        if isinstance(raw.get("updated_at", ""), str) else "",
    }


def save_prefetch_progress(path, progress):
    clean = load_prefetch_progress_from_data(progress)
    clean["updated_at"] = datetime.datetime.now(
        datetime.timezone.utc).replace(microsecond=0).isoformat()
    return atomic_write_json(path, clean)


def load_prefetch_progress_from_data(raw):
    if not isinstance(raw, dict):
        return empty_progress()
    completed = raw.get("completed", [])
    failed = raw.get("failed", {})
    return {
        "completed": sorted({str(k) for k in completed})
        if isinstance(completed, (list, tuple, set)) else [],
        "failed": {
            str(k): str(v)
            for k, v in failed.items()
        } if isinstance(failed, dict) else {},
        "updated_at": raw.get("updated_at", "")
        if isinstance(raw.get("updated_at", ""), str) else "",
    }


def mark_done(progress, key):
    clean = load_prefetch_progress_from_data(progress)
    done = set(clean["completed"])
    done.add(str(key))
    clean["completed"] = sorted(done)
    clean["failed"].pop(str(key), None)
    progress.clear()
    progress.update(clean)


def mark_failed(progress, key, reason):
    clean = load_prefetch_progress_from_data(progress)
    clean["failed"][str(key)] = str(reason)
    progress.clear()
    progress.update(clean)
