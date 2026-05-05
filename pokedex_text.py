"""Text normalisation, accepted answer keys, and Pokemon search."""

import difflib
import unicodedata


def strip_accents(s):
    """Remove accents/diacritics for flexible name matching."""
    return "".join(
        c for c in unicodedata.normalize("NFD", str(s))
        if unicodedata.category(c) != "Mn"
    )


def lookup_key(s):
    """Normalize free-text pokemon input for search and quiz answers."""
    return strip_accents(str(s).strip().lower())


def lookup_compact_key(s, slug_func):
    """Normalize text and remove punctuation/spaces for forgiving matches."""
    return slug_func(lookup_key(s))


def answer_keys(name, display_name_func, slug_func, aliases):
    """All accepted normalized answer keys for a pokemon name."""
    keys = set()
    candidates = [name, display_name_func(name)] + aliases.get(name, [])
    for candidate in candidates:
        keys.add(lookup_key(candidate))
        keys.add(lookup_compact_key(candidate, slug_func))
    return {k for k in keys if k}


def search_pokemon(q, pokemon, real_count, display_name_func, slug_func, aliases):
    q = str(q).strip().lower()
    if not q:
        return None
    try:
        n = int(q)
        if n == 0:
            return len(pokemon) - 1
        return n - 1 if 1 <= n <= real_count else None
    except ValueError:
        pass

    qn = lookup_key(q)
    qc = lookup_compact_key(q, slug_func)

    for i, (_, nm) in enumerate(pokemon):
        name_keys = (lookup_key(nm), lookup_key(display_name_func(nm)))
        compact_keys = (
            lookup_compact_key(nm, slug_func),
            lookup_compact_key(display_name_func(nm), slug_func),
        )
        if qn in name_keys or qc in compact_keys:
            return i

    for i, (_, nm) in enumerate(pokemon):
        keys = answer_keys(nm, display_name_func, slug_func, aliases)
        if qn in keys or qc in keys:
            return i

    for i, (_, nm) in enumerate(pokemon):
        if lookup_key(nm).startswith(qn) \
                or lookup_compact_key(nm, slug_func).startswith(qc):
            return i

    for i, (_, nm) in enumerate(pokemon):
        if qn in lookup_key(nm) or qc in lookup_compact_key(nm, slug_func):
            return i

    names = [lookup_key(nm) for _, nm in pokemon]
    matches = difflib.get_close_matches(qn, names, n=1, cutoff=0.7)
    if matches:
        return names.index(matches[0])
    return None
