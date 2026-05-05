"""Minimal tests for pokedex_gen1 helpers. Run with: python3 -m pytest test_pokedex.py

Tests stay library-only: no terminal I/O, no subprocess calls.
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path

import pokedex_audio
import pokedex_cache
import pokedex_stats
import pokedex_text


def _load_module():
    path = Path(__file__).parent / "pokedex_gen1.py"
    spec = importlib.util.spec_from_file_location("pokedex_gen1", str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


P = _load_module()


# ── Data invariants ──────────────────────────────────────────────────────────

def test_pokemon_list_has_gen5_national_entries_plus_missingno():
    assert P.POKE_COUNT == 650
    assert P.REAL_POKE_COUNT == 649
    numbers = [n for n, _ in P.POKEMON[:649]]
    assert numbers == list(range(1, 650))
    assert P.POKEMON[-1] == (0, "missingno")


def test_pokemon_names_unique():
    names = [nm for _, nm in P.POKEMON]
    assert len(set(names)) == len(names)


# ── Search ───────────────────────────────────────────────────────────────────

def test_search_exact_name():
    assert P.search("pikachu") == 24


def test_search_by_number():
    assert P.search("25") == 24
    assert P.search("1") == 0
    assert P.search("151") == 150
    assert P.search("649") == 648
    assert P.search("999") is None


def test_search_missingno():
    assert P.search("0") == P.POKE_COUNT - 1


def test_search_prefix_match():
    assert P.search("pik") == 24


def test_search_substring_match():
    # "chan" appears in chansey (idx 112) and hitmonchan (idx 106).
    # search() walks indices in order, so the first match is chansey.
    assert P.search("chan") == 112
    # something only appearing mid-name
    assert P.search("tortl") == 7  # wartortle


def test_search_accents_insensitive():
    # Display is Pokédex but search input shouldn't need accents
    idx = P.search("kakuna")
    assert idx == 13


def test_search_fuzzy_typos():
    # Fuzzy fallback should catch common typos
    assert P.search("charzard") == 5
    assert P.search("piakchu") == 24


def test_search_empty_returns_none():
    assert P.search("") is None
    assert P.search("   ") is None


def test_search_nidoran_disambiguation():
    # "nidoranf" is index 28, "nidoranm" is 31; plain "nidoran" should pick one
    assert P.search("nidoranf") == 28
    assert P.search("nidoranm") == 31


def test_search_uses_human_aliases():
    assert P.search("nidoran hembra") == 28
    assert P.search("nidoran female") == 28
    assert P.search("nidoran macho") == 31
    assert P.search("nidoran male") == 31
    assert P.search("mr mime") == 121
    assert P.search("mime jr") == 438
    assert P.search("ho oh") == 249
    assert P.search("porygon z") == 473
    assert P.search("farfetch d") == 82


# ── Helpers ──────────────────────────────────────────────────────────────────

def test_strip_accents():
    assert P._strip_accents("Pokédex") == "Pokedex"
    assert P._strip_accents("niño") == "nino"


def test_dn_display_names():
    assert P._dn("nidoranf").endswith("♀")
    assert P._dn("nidoranm").endswith("♂")
    assert P._dn("mr. mime") == "Mr. Mime"
    assert P._dn("mr-mime") == "Mr. Mime"
    assert P._dn("mime-jr") == "Mime Jr."
    assert P._dn("porygon-z") == "Porygon-Z"
    assert P._dn("farfetchd") == "Farfetch'd"
    assert P._dn("missingno") == "MissingNo."


def test_sn_strips_punctuation():
    assert P._sn("mr. mime") == "mrmime"
    assert P._sn("mr-mime") == "mrmime"
    assert P._sn("ho-oh") == "hooh"
    assert P._sn("farfetchd") == "farfetchd"


def test_answer_keys_include_aliases_and_compact_forms():
    assert "nidoran hembra" in P._answer_keys("nidoranf")
    assert "nidoranf" in P._answer_keys("nidoranf")
    assert "nidoran macho" in P._answer_keys("nidoranm")
    assert "mrmime" in P._answer_keys("mr. mime")


def test_text_module_search_matches_facade():
    for query in ("pikachu", "nidoran hembra", "nidoran macho", "charzard"):
        assert pokedex_text.search_pokemon(
            query, P.POKEMON, P.REAL_POKE_COUNT,
            P._dn, P._sn, P.QUIZ_ALIASES,
        ) == P.search(query)


def test_vl_counts_visible_characters():
    # ANSI escapes should not count
    s = f"{P.BOLD}Hello{P.RST}"
    assert P._vl(s) == 5


# ── Colors / stat bar ────────────────────────────────────────────────────────

def test_stat_bar_width_matches():
    for val in (0, 50, 100, 200, 255, 300):
        bar = P._stat_bar(val, 255, 10)
        assert P._vl(bar) == 10


def test_type_colors_cover_gen1_types():
    for t in ("fire", "water", "grass", "electric", "psychic",
              "fighting", "rock", "ground", "normal", "flying",
              "bug", "poison", "ghost", "dragon", "ice"):
        assert t in P.TYPE_COLORS
        r, g, b = P.TYPE_COLORS[t]
        assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255


# ── Persistence ──────────────────────────────────────────────────────────────

def test_stats_roundtrip():
    original = P.STATS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        P.STATS_FILE = os.path.join(tmp, "stats.json")
        P.STATS = P._fresh_stats()
        P._mark_seen(25)
        P._mark_caught(1)
        P._mark_shiny(6)
        P._set_best_quiz("silueta", 10, 7)
        # Reload
        P.STATS = {}
        P._load_stats()
        assert 25 in P.STATS["seen"]
        assert 1 in P.STATS["caught_safari"]
        assert 6 in P.STATS["shiny_seen"]
        assert P.STATS["best_quiz"]["silueta-10"] == 7
    P.STATS_FILE = original
    P.STATS = P._fresh_stats()


def test_best_quiz_only_increases():
    original = P.STATS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        P.STATS_FILE = os.path.join(tmp, "stats.json")
        P.STATS = P._fresh_stats()
        P.STATS["best_quiz"] = {}
        assert P._set_best_quiz("cry", 10, 5) is True
        assert P._set_best_quiz("cry", 10, 3) is False  # lower, ignored
        assert P._set_best_quiz("cry", 10, 9) is True
        assert P._get_best_quiz("cry", 10) == 9
    P.STATS_FILE = original
    P.STATS = P._fresh_stats()


def test_fresh_stats_does_not_share_nested_defaults():
    stats = P._fresh_stats()
    stats["seen"].append(25)
    stats["best_quiz"]["cry-10"] = 3
    assert P.STATS_DEFAULT["seen"] == []
    assert P.STATS_DEFAULT["best_quiz"] == {}


def test_load_stats_normalises_bad_shapes_and_indices():
    original = P.STATS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        P.STATS_FILE = os.path.join(tmp, "stats.json")
        with open(P.STATS_FILE, "w") as f:
            json.dump({
                "seen": ["25", "bad", 25],
                "caught_safari": "not-a-list",
                "best_quiz": [],
                "palette_idx": 999,
                "sprite_style": -10,
                "last_open_date": 123,
            }, f)
        P._load_stats()
        assert P.STATS["seen"] == [25]
        assert P.STATS["caught_safari"] == []
        assert P.STATS["best_quiz"] == {}
        assert P.STATS["gym_badges"] == []
        assert P.STATS["palette_idx"] == len(P.PALETTES) - 1
        assert P.STATS["sprite_style"] == 0
        assert P.STATS["last_open_date"] == ""
    P.STATS_FILE = original
    P.STATS = P._fresh_stats()


def test_stats_module_loads_bad_json_as_fresh_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "stats.json")
        with open(path, "w") as f:
            f.write("{bad json")
        loaded = pokedex_stats.load_stats(
            path, P.STATS_DEFAULT, len(P.PALETTES), len(P.SPRITE_STYLES))
        assert loaded == P._fresh_stats()


def test_stats_module_normalises_gym_badges():
    loaded = pokedex_stats.normalise_stats(
        {"gym_badges": ["2", "bad", 2]},
        P.STATS_DEFAULT,
        len(P.PALETTES),
        len(P.SPRITE_STYLES),
    )
    assert loaded["gym_badges"] == [2]


# ── Cache / CLI helpers ──────────────────────────────────────────────────────

def test_cache_status_counts_assets():
    pokemon = [(1, "bulbasaur"), (4, "charmander")]
    status = pokedex_cache.cache_status(
        pokemon,
        sprite_cached=lambda name: name == "bulbasaur",
        cry_cached=lambda name: True,
        data_cached=lambda num: num == 4,
    )
    assert status.pokemon_count == 2
    assert status.sprites == 1
    assert status.cries == 2
    assert status.data == 1
    assert status.cached_assets == 4
    assert status.total_assets == 6
    assert status.missing_assets == 2
    assert status.complete is False


def test_prefetch_progress_roundtrip_and_failure_cleanup():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "progress.json")
        progress = pokedex_cache.empty_progress()
        pokedex_cache.mark_failed(progress, "cry:025", "download failed")
        pokedex_cache.save_prefetch_progress(path, progress)

        loaded = pokedex_cache.load_prefetch_progress(path)
        assert loaded["failed"] == {"cry:025": "download failed"}

        pokedex_cache.mark_done(loaded, "cry:025")
        pokedex_cache.save_prefetch_progress(path, loaded)
        loaded = pokedex_cache.load_prefetch_progress(path)
        assert "cry:025" in loaded["completed"]
        assert loaded["failed"] == {}


def test_cli_list_palettes_returns_without_sys_exit():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = P.cli(["--list-palettes"])
    assert code == 0
    assert "DMG Green" in buf.getvalue()


def test_cli_delegates_interactive_args_to_main():
    calls = []
    original_main = P.main
    original_stop_cry = P._stop_cry
    original_kill_tts = P._kill_tts
    try:
        P.main = lambda args=None: calls.append(args)
        P._stop_cry = lambda: None
        P._kill_tts = lambda: None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = P.cli(["--pokemon", "pikachu", "--no-audio"])
    finally:
        P.main = original_main
        P._stop_cry = original_stop_cry
        P._kill_tts = original_kill_tts

    assert code == 0
    assert len(calls) == 1
    assert calls[0].pokemon == "pikachu"
    assert calls[0].no_audio is True


def test_print_cache_status_reports_partial_offline_cache():
    original_cache_status = P._cache_status
    original_progress_path = P._prefetch_progress_path
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "prefetch-progress.json")
        pokedex_cache.save_prefetch_progress(
            path,
            {"completed": ["sprite:001"], "failed": {"cry:001": "download failed"}},
        )
        try:
            P._cache_status = lambda: pokedex_cache.CacheStatus(
                pokemon_count=2, sprites=1, cries=0, data=1)
            P._prefetch_progress_path = lambda: path
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                P._print_cache_status()
        finally:
            P._cache_status = original_cache_status
            P._prefetch_progress_path = original_progress_path

    out = buf.getvalue()
    assert "Offline:   parcial" in out
    assert "cry:001" in out


def test_prefetch_all_is_resumable_and_records_failures():
    original_pokemon = P.POKEMON
    original_real_count = P.REAL_POKE_COUNT
    original_load_stats = P._load_stats
    original_sprite_cached = P._sprite_disk_cached
    original_cry_cached = P._cry_disk_cached
    original_data_cached = P._data_disk_cached
    original_sprite = P._prefetch_sprite
    original_cry = P._prefetch_cry
    original_data = P._prefetch_data
    original_progress_path = P._prefetch_progress_path

    with tempfile.TemporaryDirectory() as tmp:
        try:
            P.POKEMON = [(1, "bulbasaur")]
            P.REAL_POKE_COUNT = 1
            P._load_stats = lambda: None
            P._sprite_disk_cached = lambda name: False
            P._cry_disk_cached = lambda name: False
            P._data_disk_cached = lambda num: False
            P._prefetch_sprite = lambda name, force=False: True
            P._prefetch_cry = lambda name, force=False: False
            P._prefetch_data = lambda num, force=False: True
            P._prefetch_progress_path = lambda: os.path.join(
                tmp, "prefetch-progress.json")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = P._prefetch_all()

            progress = pokedex_cache.load_prefetch_progress(
                P._prefetch_progress_path())
        finally:
            P.POKEMON = original_pokemon
            P.REAL_POKE_COUNT = original_real_count
            P._load_stats = original_load_stats
            P._sprite_disk_cached = original_sprite_cached
            P._cry_disk_cached = original_cry_cached
            P._data_disk_cached = original_data_cached
            P._prefetch_sprite = original_sprite
            P._prefetch_cry = original_cry
            P._prefetch_data = original_data
            P._prefetch_progress_path = original_progress_path

    assert ok is False
    assert "sprite:001" in progress["completed"]
    assert "data:001" in progress["completed"]
    assert progress["failed"] == {"cry:001": "download failed"}


def test_audio_helpers_are_safe_when_nothing_is_running():
    assert pokedex_audio.play_sfx("NoSuchSound", muted=True) is None
    pokedex_audio.kill_process(None)


def test_custom_sfx_generates_cached_wav():
    old_cache = os.environ.get("XDG_CACHE_HOME")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["XDG_CACHE_HOME"] = tmp
            path = pokedex_audio._sfx_path("ui_nav")
            assert path.endswith("ui_nav.wav")
            with open(path, "rb") as f:
                assert f.read(4) == b"RIFF"
            assert os.path.exists(pokedex_audio._sfx_path("ui_scan"))
            assert os.path.exists(pokedex_audio._sfx_path("dex_latch"))
        finally:
            if old_cache is None:
                os.environ.pop("XDG_CACHE_HOME", None)
            else:
                os.environ["XDG_CACHE_HOME"] = old_cache


def test_ui_sfx_helpers_delegate_to_central_sfx():
    calls = []
    original_sfx = P._sfx
    try:
        P._sfx = lambda name, rate=1.0, volume=1.0: calls.append(
            (name, rate, volume))
        P._sfx_nav()
        P._sfx_select()
        P._sfx_back()
        P._sfx_scan()
    finally:
        P._sfx = original_sfx

    assert [name for name, _rate, _volume in calls] == [
        "ui_nav", "ui_select", "ui_back", "ui_scan"]


def test_intro_boot_renders_national_dex_without_audio_or_delay():
    original_sfx = P._sfx
    original_sleep = P.time.sleep
    sleep_calls = []
    try:
        P._sfx = lambda *_args, **_kwargs: None
        P.time.sleep = lambda seconds: sleep_calls.append(seconds)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            P.play_intro(20, 80)
    finally:
        P._sfx = original_sfx
        P.time.sleep = original_sleep

    out = buf.getvalue()
    assert "POK\u00c9DEX" in out
    assert "NACIONAL I-V" in out
    assert "649 ESPECIES" in out
    assert 2.2 in sleep_calls


def test_tts_speak_text_respects_mute_without_starting_thread():
    player = pokedex_audio.TTSPlayer("Daniel", "Jorge")
    player.speak_text("descripcion secreta", muted=True)
    assert player._thread is None


def test_speak_text_es_uses_spanish_voice_without_name():
    class FakeTTS:
        def __init__(self):
            self.calls = []

        def speak_text(self, text, voice=None, muted=False):
            self.calls.append((text, voice, muted))

    original_player = P._tts_player
    original_muted = P.AUDIO_MUTED
    fake = FakeTTS()
    try:
        P._tts_player = fake
        P.AUDIO_MUTED = False
        P.speak_text_es("Vive cerca del agua.")
        assert fake.calls == [("Vive cerca del agua.", P.TTS_ES, False)]
    finally:
        P._tts_player = original_player
        P.AUDIO_MUTED = original_muted


# ── Gym Challenge ────────────────────────────────────────────────────────────

def test_gym_type_multiplier_handles_double_weakness_and_immunity():
    assert P._gym_type_multiplier("water", ["rock", "ground"]) == 4.0
    assert P._gym_type_multiplier("electric", ["ground"]) == 0.0
    assert P._gym_effect_text(4.0) == "Es muy eficaz!"


def test_gym_damage_uses_stab_and_immunity():
    attacker = {
        "level": 20,
        "types": ["electric"],
        "stats": {"attack": 40, "defense": 40,
                  "special_attack": 80, "special_defense": 40},
    }
    defender = {
        "types": ["water"],
        "stats": {"attack": 40, "defense": 40,
                  "special_attack": 40, "special_defense": 40},
    }
    damage, mult = P._gym_damage(
        attacker, defender, {"name": "Rayo", "type": "electric", "power": 80})
    assert mult == 2.0
    assert damage > 0

    defender["types"] = ["ground"]
    damage, mult = P._gym_damage(
        attacker, defender, {"name": "Rayo", "type": "electric", "power": 80})
    assert (damage, mult) == (0, 0.0)


def test_gym_take_turn_awards_fast_knockout_before_counterattack():
    class FixedRng:
        def uniform(self, _lo, _hi):
            return 1.0

    player = {
        "dname": "Squirtle",
        "level": 30,
        "types": ["water"],
        "hp": 80,
        "max_hp": 80,
        "stats": {"attack": 40, "defense": 40, "special_attack": 90,
                  "special_defense": 40, "speed": 99},
        "moves": [{"name": "Surf", "type": "water", "power": 90}],
    }
    enemy = {
        "dname": "Onix",
        "level": 14,
        "types": ["rock", "ground"],
        "hp": 20,
        "max_hp": 20,
        "stats": {"attack": 40, "defense": 40, "special_attack": 25,
                  "special_defense": 30, "speed": 10},
        "moves": [{"name": "Placaje", "type": "normal", "power": 40}],
    }
    phase, log = P._gym_take_turn(player, enemy, 0, rng=FixedRng())
    assert phase == "win"
    assert enemy["hp"] == 0
    assert player["hp"] == 80
    assert any("Onix no puede continuar" in line for line in log)


def test_gym_leaders_have_real_teams_and_ace_last():
    brock = P.GYM_LEADERS[0]
    team = P._gym_leader_team_defs(brock)
    assert [member["idx"] for member in team] == [73, 94]
    assert team[-1]["idx"] == P._gym_ace_idx(brock)


def test_gym_player_team_indices_start_at_cursor_without_duplicates():
    roster = [24, 5, 8, 24]
    assert P._gym_player_team_indices(roster, 1, 3) == [5, 8, 24]


def test_gym_next_alive_skips_fainted_team_members():
    team = [{"hp": 0}, {"hp": 12}, {"hp": 0}]
    assert P._gym_next_alive(team, 0) == 1
    assert P._gym_next_alive(team, 1) is None


def test_mark_gym_badge_only_adds_once():
    original_stats = P.STATS
    original_file = P.STATS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        P.STATS_FILE = os.path.join(tmp, "stats.json")
        P.STATS = P._fresh_stats()
        try:
            assert P._mark_gym_badge(0) is True
            assert P._mark_gym_badge(0) is False
            assert P.STATS["gym_badges"] == [0]
        finally:
            P.STATS = original_stats
            P.STATS_FILE = original_file


# ── Daily Pokemon ────────────────────────────────────────────────────────────

def test_daily_pokemon_stable_within_day():
    a = P._daily_pokemon_idx()
    b = P._daily_pokemon_idx()
    assert a == b
    assert 0 <= a < P.REAL_POKE_COUNT


# ── Sprite helpers ───────────────────────────────────────────────────────────

def test_shiny_tint_changes_colours():
    from PIL import Image
    img = Image.new("RGBA", (3, 3), (255, 0, 0, 255))
    shiny = P._shiny_tint(img)
    original = img.getpixel((1, 1))
    shifted = shiny.getpixel((1, 1))
    assert original != shifted


def test_silhouette_respects_color_argument():
    from PIL import Image
    img = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
    out = P._silhouette(img, color=(100, 100, 100))
    r, g, b, a = out.getpixel((0, 0))
    assert (r, g, b) == (100, 100, 100)
    assert a == 255


def test_trim_handles_empty_image():
    from PIL import Image
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))  # fully transparent
    out = P._trim(img)
    assert out is img  # returns original when nothing to crop


def test_sprite_styles_include_gen4():
    keys = [key for key, _url, _label in P.SPRITE_STYLES]
    assert keys == ["gen1", "gen2", "gen3", "gen4", "gen5"]


def test_dl_sprite_skips_generation_that_cannot_contain_pokemon():
    from io import BytesIO
    from PIL import Image

    original_dir = P.CACHE_DIR
    original_get = P._get
    original_style = P.SPRITE_STYLE_IDX
    with tempfile.TemporaryDirectory() as tmp:
        icon = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
        buf = BytesIO()
        icon.save(buf, format="PNG")
        calls = []

        def fake_get(url, timeout=10):
            calls.append(url)
            if "/gen5/" in url:
                return buf.getvalue()
            return None

        try:
            P.CACHE_DIR = tmp
            P._get = fake_get
            P.SPRITE_STYLE_IDX = 0  # gen1 selected; genesect only exists later.
            out = P.dl_sprite("genesect")
            assert out.size == (4, 4)
            assert not any("/gen1rb/" in url for url in calls)
            assert any("/gen5/" in url for url in calls)
        finally:
            P.CACHE_DIR = original_dir
            P._get = original_get
            P.SPRITE_STYLE_IDX = original_style


def test_dl_sprite_keeps_selected_generation_when_possible():
    from io import BytesIO
    from PIL import Image

    original_dir = P.CACHE_DIR
    original_get = P._get
    original_style = P.SPRITE_STYLE_IDX
    with tempfile.TemporaryDirectory() as tmp:
        icon = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
        buf = BytesIO()
        icon.save(buf, format="PNG")
        calls = []

        def fake_get(url, timeout=10):
            calls.append(url)
            return buf.getvalue()

        try:
            P.CACHE_DIR = tmp
            P._get = fake_get
            P.SPRITE_STYLE_IDX = 1  # gen2 selected; celebi belongs to gen2.
            out = P.dl_sprite("celebi")
            assert out.size == (4, 4)
            assert any("/gen2/" in url for url in calls)
            assert not any("/gen5/" in url for url in calls)
        finally:
            P.CACHE_DIR = original_dir
            P._get = original_get
            P.SPRITE_STYLE_IDX = original_style


def test_memory_icon_download_and_cache():
    from io import BytesIO
    from PIL import Image

    original_dir = P.MEMORY_ICON_DIR
    original_get = P._get
    with tempfile.TemporaryDirectory() as tmp:
        P.MEMORY_ICON_DIR = tmp
        icon = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
        buf = BytesIO()
        icon.save(buf, format="PNG")
        calls = []

        def fake_get(url):
            calls.append(url)
            return buf.getvalue()

        P._get = fake_get
        img = P.dl_memory_icon(25)
        assert img.size == (8, 8)
        assert os.path.exists(os.path.join(tmp, "25.png"))

        P._get = lambda url: None
        cached = P.dl_memory_icon(25)
        assert cached.size == (8, 8)
        assert len(calls) == 1
    P.MEMORY_ICON_DIR = original_dir
    P._get = original_get


def test_memory_card_slice_uses_large_icon_inside_card():
    from PIL import Image

    original_dl = P.dl_memory_icon
    P._memory_icon_render_cache.clear()
    P.dl_memory_icon = lambda num: Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    line = P._memory_card_slice(
        sub_row=1,
        card_w=24,
        card_h=12,
        idx=0,
        cards=[24],
        flipped=[0],
        matched=set(),
        cursor=0,
    )
    assert P._vl(line) == 24
    assert "?" not in line
    assert "#025" not in line
    P.dl_memory_icon = original_dl
    P._memory_icon_render_cache.clear()


def test_memory_card_slice_compact_cards_use_text_not_tiny_icon():
    original_dl = P.dl_memory_icon
    P._memory_icon_render_cache.clear()
    P.dl_memory_icon = lambda num: None
    line = P._memory_card_slice(
        sub_row=1,
        card_w=8,
        card_h=4,
        idx=0,
        cards=[24],
        flipped=[0],
        matched=set(),
        cursor=0,
    )
    assert P._vl(line) == 8
    assert "#025" in line
    P.dl_memory_icon = original_dl
    P._memory_icon_render_cache.clear()


def test_memory_layout_prefers_large_cards_and_scrolls_pairs():
    layout = P._memory_layout_for_screen(sw=80, scr_h=27, desired_pairs=10)
    assert layout["card_w"] == 24
    assert layout["card_h"] == 12
    assert layout["pairs"] == 10
    assert layout["visible_rows"] < layout["rows"]
    assert layout["grid_w"] <= 80
    assert layout["visible_grid_h"] <= 26


def test_memory_scroll_keeps_cursor_visible():
    assert P._memory_scroll_for_cursor(
        scroll_row=0, cursor=12, rows=7, cols=3, visible_rows=2) == 3
    assert P._memory_scroll_for_cursor(
        scroll_row=3, cursor=3, rows=7, cols=3, visible_rows=2) == 1


def test_memory_face_down_cards_show_slot_number():
    line = P._memory_card_slice(
        sub_row=1,
        card_w=24,
        card_h=12,
        idx=6,
        cards=[24] * 8,
        flipped=[],
        matched=set(),
        cursor=0,
    )
    assert P._vl(line) == 24
    assert "07" in line


def test_memory_scroll_marker_shows_direction_and_thumb():
    assert P._memory_scroll_marker(
        screen_row=1, grid_top=1, grid_h=25,
        scroll_row=0, rows=7, visible_rows=2) == "│"
    assert P._memory_scroll_marker(
        screen_row=25, grid_top=1, grid_h=25,
        scroll_row=0, rows=7, visible_rows=2) == "↓"
    assert P._memory_scroll_marker(
        screen_row=1, grid_top=1, grid_h=25,
        scroll_row=3, rows=7, visible_rows=2) == "↑"
    markers = [
        P._memory_scroll_marker(i, 1, 25, 3, 7, 2)
        for i in range(1, 26)
    ]
    assert "┃" in markers


# ── Smoothstep / utilities ───────────────────────────────────────────────────

def test_smoothstep_bounds():
    assert P._smoothstep(0.0) == 0.0
    assert P._smoothstep(1.0) == 1.0
    mid = P._smoothstep(0.5)
    assert 0.4 < mid < 0.6


def test_safari_modifiers():
    assert P._safari_modifiers(0, 0) == (1.0, 1.0)
    c, f = P._safari_modifiers(2, 0)  # anger doubles both
    assert c == 2.0 and f == 2.0
    c, f = P._safari_modifiers(0, 2)  # eating halves both
    assert c == 0.5 and f == 0.5


# ── Rendered-line clipping (safari animations) ──────────────────────────────

def _sample_cell_line(n_cells):
    """Three-cell-per-colour rendered line, like render_sprite output."""
    cell = "\033[38;2;255;0;0m\033[48;2;0;255;0m▀"
    return cell * n_cells


def test_clip_inside():
    line = _sample_cell_line(3)
    clipped, start = P._clip_rendered_line(line, 10, 5, 20)
    assert start == 10
    assert P._vl(clipped) == 3


def test_clip_right_edge():
    line = _sample_cell_line(3)
    # sprite starts at col 18, screen ends at col 20 → only 2 cells fit
    clipped, start = P._clip_rendered_line(line, 18, 5, 20)
    assert start == 18
    assert P._vl(clipped) == 2


def test_clip_fully_off_right():
    line = _sample_cell_line(3)
    clipped, start = P._clip_rendered_line(line, 25, 5, 20)
    assert clipped is None and start is None


def test_clip_left_edge():
    line = _sample_cell_line(3)
    # sprite starts at col 3, screen starts at col 5 → only last cell kept
    clipped, start = P._clip_rendered_line(line, 3, 5, 20)
    assert start == 5
    assert P._vl(clipped) == 1
