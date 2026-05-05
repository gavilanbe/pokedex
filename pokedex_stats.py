"""Persistence helpers for the Pokedex CLI."""

import copy
import json
import os


def fresh_stats(defaults):
    """Return an independent default stats tree."""
    return copy.deepcopy(defaults)


def coerce_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_int(value, default, lo, hi):
    return max(lo, min(hi, coerce_int(value, default)))


def normalise_int_list(value):
    if not isinstance(value, (list, tuple, set)):
        return []
    out = []
    seen = set()
    for item in value:
        try:
            num = int(item)
        except (TypeError, ValueError):
            continue
        if num not in seen:
            seen.add(num)
            out.append(num)
    return out


def normalise_stats(loaded, defaults, palette_count, sprite_style_count):
    merged = fresh_stats(defaults)
    if isinstance(loaded, dict):
        merged.update(loaded)
    for key in ("seen", "caught_safari", "shiny_seen", "gym_badges"):
        merged[key] = normalise_int_list(merged.get(key, []))
    if not isinstance(merged.get("best_quiz"), dict):
        merged["best_quiz"] = {}
    if not isinstance(merged.get("best_memory"), dict):
        merged["best_memory"] = {}
    merged["mute"] = bool(merged.get("mute", False))
    merged["palette_idx"] = clamp_int(
        merged.get("palette_idx"), 0, 0, max(0, palette_count - 1))
    merged["sprite_style"] = clamp_int(
        merged.get("sprite_style"), 0, 0, max(0, sprite_style_count - 1))
    if not isinstance(merged.get("last_open_date"), str):
        merged["last_open_date"] = ""
    return merged


def load_stats(path, defaults, palette_count, sprite_style_count):
    """Load stats from disk, falling back to normalised defaults."""
    try:
        loaded = {}
        if os.path.exists(path):
            with open(path) as f:
                loaded = json.load(f)
        return normalise_stats(loaded, defaults, palette_count, sprite_style_count)
    except (json.JSONDecodeError, OSError):
        return fresh_stats(defaults)


def save_stats(path, stats):
    """Persist stats atomically. Returns True on success."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(stats, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
