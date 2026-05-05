#!/usr/bin/env python3
"""National Pokedex I-V - Interactive CLI with sprites and minigames."""

import argparse
import atexit
import datetime
import json
import math
import os
import random
import re
import select
import signal
import sys
import termios
import textwrap
import threading
import time
import tty
from io import BytesIO

import pokedex_audio
import pokedex_cache
import pokedex_network
import pokedex_stats
import pokedex_text

try:
    from PIL import Image
except ImportError:
    print("Pillow is required: pip3 install Pillow")
    sys.exit(1)

# ── Paths & URLs ─────────────────────────────────────────────────────────────

def _cache_root():
    """Return a persistent cache directory (survives reboots)."""
    base = os.environ.get("XDG_CACHE_HOME")
    if not base:
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Caches")
        else:
            base = os.path.expanduser("~/.cache")
    return os.path.join(base, "pokedex")


CACHE_DIR = os.path.join(_cache_root(), "sprites")
CRIES_DIR = os.path.join(_cache_root(), "cries")
DATA_DIR = os.path.join(_cache_root(), "data")
SPRITE_STYLES = [
    # (key, url, label) — first is the default
    ("gen1", "https://play.pokemonshowdown.com/sprites/gen1rb",      "Gen 1 (RB)"),
    ("gen2", "https://play.pokemonshowdown.com/sprites/gen2",         "Gen 2"),
    ("gen3", "https://play.pokemonshowdown.com/sprites/gen3",         "Gen 3"),
    ("gen4", "https://play.pokemonshowdown.com/sprites/gen4",         "Gen 4 (DPP)"),
    ("gen5", "https://play.pokemonshowdown.com/sprites/gen5",         "Gen 5 (BW)"),
]
SPRITE_STYLE_DEX_LIMITS = {
    "gen1": 151,
    "gen2": 251,
    "gen3": 386,
    "gen4": 493,
    "gen5": 649,
}
SPRITE_STYLE_IDX = 0  # index into SPRITE_STYLES, mutated via `G` key or CLI flag

# Kept for back-compat with anything that still reads SPRITE_BASE directly.
SPRITE_BASE = SPRITE_STYLES[0][1]


def _sprite_base():
    return SPRITE_STYLES[SPRITE_STYLE_IDX][1]


def _sprite_style_key():
    return SPRITE_STYLES[SPRITE_STYLE_IDX][0]


def _sprite_base_for_key(style_key):
    for key, base, _label in SPRITE_STYLES:
        if key == style_key:
            return base
    return SPRITE_STYLES[0][1]


CRIES_BASE = "https://play.pokemonshowdown.com/audio/cries"
MEMORY_ICON_BASE = "https://play.pokemonshowdown.com/sprites/bwicons"
MEMORY_ICON_DIR = os.path.join(_cache_root(), "memory-icons", "bwicons")
POKEAPI = "https://pokeapi.co/api/v2/pokemon-species"

# ── ANSI true-color palette ──────────────────────────────────────────────────
RST = "\033[0m"
BOLD = "\033[1m"
BG_RED = "\033[48;2;200;38;32m"
BG_DKRED = "\033[48;2;155;25;20m"
BG_SCR = "\033[48;2;155;188;15m"
FG_WHITE = "\033[38;2;255;255;255m"
FG_GRAY = "\033[38;2;170;170;170m"
FG_DKGRAY = "\033[38;2;75;75;85m"
FG_SCRTXT = "\033[38;2;15;56;15m"
FG_SCRHI = "\033[38;2;48;98;48m"
FG_CYAN = "\033[38;2;55;195;255m"
FG_RLED = "\033[38;2;255;65;65m"
FG_YLED = "\033[38;2;255;215;45m"
FG_GLED = "\033[38;2;75;225;75m"
SCR_RGB = (155, 188, 15)

# ── GBC color palettes ───────────────────────────────────────────────────────
# Each entry: (name, bg_rgb, text_rgb, highlight_rgb)
# Authentic Game Boy Color boot-screen palettes + DMG/Pocket classics.
# fmt: off
PALETTES = [
    ("DMG Green",    (155, 188, 15),  (15, 56, 15),    (48, 98, 48)),
    ("Pocket",       (192, 192, 176), (32, 32, 32),    (104, 104, 96)),
    ("GBC Brown",    (248, 224, 168), (80, 48, 16),    (160, 120, 48)),
    ("GBC Red",      (248, 176, 160), (104, 16, 16),   (176, 72, 56)),
    ("GBC Blue",     (160, 200, 248), (16, 48, 104),   (64, 112, 168)),
    ("GBC Pastel",   (248, 200, 216), (96, 40, 72),    (168, 104, 136)),
    ("GBC Yellow",   (248, 240, 152), (72, 56, 8),     (152, 136, 48)),
    ("GBC Dark",     (72, 80, 64),    (8, 16, 8),      (40, 48, 36)),
]
# fmt: on
_palette_idx = 0


def _apply_palette(idx):
    """Switch the screen palette and return its name."""
    global SCR_RGB, BG_SCR, FG_SCRTXT, FG_SCRHI, _palette_idx
    _palette_idx = idx % len(PALETTES)
    name, bg, txt, hi = PALETTES[_palette_idx]
    SCR_RGB = bg
    BG_SCR = f"\033[48;2;{bg[0]};{bg[1]};{bg[2]}m"
    FG_SCRTXT = f"\033[38;2;{txt[0]};{txt[1]};{txt[2]}m"
    FG_SCRHI = f"\033[38;2;{hi[0]};{hi[1]};{hi[2]}m"
    return name


TTS_EN = "Daniel"
TTS_ES = "Jorge"
ANSI_RE = re.compile(r"\033\[[^m]*m")

# Official Gen 1 type colors (approx), drawn as badges.
# fmt: off
TYPE_COLORS = {
    "normal":   (168, 168, 120),
    "fire":     (240, 128,  48),
    "water":    (104, 144, 240),
    "electric": (248, 208,  48),
    "grass":    (120, 200,  80),
    "ice":      (152, 216, 216),
    "fighting": (192,  48,  40),
    "poison":   (160,  64, 160),
    "ground":   (224, 192, 104),
    "flying":   (168, 144, 240),
    "psychic":  (248,  88, 136),
    "bug":      (168, 184,  32),
    "rock":     (184, 160,  56),
    "ghost":    (112,  88, 152),
    "dragon":   (112,  56, 248),
    "dark":     (112,  88,  72),
    "steel":    (184, 184, 208),
    "fairy":    (238, 153, 172),
}
TYPE_ES = {
    "normal": "NORMAL", "fire": "FUEGO", "water": "AGUA",
    "electric": "ELEC", "grass": "PLANTA", "ice": "HIELO",
    "fighting": "LUCHA", "poison": "VENENO", "ground": "TIERRA",
    "flying": "VOLADOR", "psychic": "PSIQUI", "bug": "BICHO",
    "rock": "ROCA", "ghost": "FANTAS", "dragon": "DRAGON",
    "dark": "SINIES", "steel": "ACERO", "fairy": "HADA",
}
# fmt: on


def export_trainer_card(out_path=None):
    """Generate a Trainer Card PNG summarising the saved stats.

    Returns the path written (or None if Pillow failed).
    """
    from PIL import Image, ImageDraw, ImageFont

    _load_stats()
    seen = sorted(STATS.get("seen", []))
    caught = sorted(STATS.get("caught_safari", []))
    shiny = sorted(STATS.get("shiny_seen", []))
    badges = _gym_badges()
    best = STATS.get("best_quiz", {}) or {}

    W, H = 720, 880
    bg = Image.new("RGB", (W, H), (245, 245, 235))
    d = ImageDraw.Draw(bg)

    # Fonts: use default if Pillow truetype unavailable
    try:
        font_title = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Futura.ttc", 36)
        font_h = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Futura.ttc", 22)
        font_n = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Futura.ttc", 16)
    except (OSError, IOError):
        font_title = ImageFont.load_default()
        font_h = ImageFont.load_default()
        font_n = ImageFont.load_default()

    # Header bar
    d.rectangle([0, 0, W, 96], fill=(200, 38, 32))
    d.rectangle([0, 96, W, 104], fill=(155, 25, 20))
    d.text((24, 28), "POKEDEX TRAINER CARD", font=font_title, fill=(255, 255, 255))

    # Stats block
    y = 128
    today = datetime.date.today().isoformat()
    d.text((24, y), f"Fecha: {today}", font=font_h, fill=(40, 40, 40)); y += 34
    d.text((24, y), f"Vistos:    {len(seen):3d} / {REAL_POKE_COUNT}",
           font=font_h, fill=(40, 40, 40)); y += 28
    d.text((24, y), f"Atrapados: {len(caught):3d} / {REAL_POKE_COUNT}",
           font=font_h, fill=(40, 40, 40)); y += 28
    d.text((24, y), f"Shiny:     {len(shiny):3d}",
           font=font_h, fill=(40, 40, 40)); y += 28
    d.text((24, y), f"Medallas:  {len(badges):3d} / {len(GYM_LEADERS)}",
           font=font_h, fill=(40, 40, 40)); y += 40

    # Best quiz scores
    d.text((24, y), "Mejores puntajes Quiz:",
           font=font_h, fill=(40, 40, 40)); y += 30
    if best:
        for k in sorted(best):
            d.text((48, y), f"• {k:<18s} {best[k]}",
                   font=font_n, fill=(60, 60, 60))
            y += 22
    else:
        d.text((48, y), "(aun sin registros)",
               font=font_n, fill=(140, 140, 140)); y += 22
    y += 16

    # Caught pokemon grid with sprites
    d.text((24, y), f"Atrapados en Safari ({len(caught)}):",
           font=font_h, fill=(40, 40, 40)); y += 30
    cell_w = 68
    x = 24
    for num in caught[:30]:
        # Number below sprite
        name = None
        for n, nm in POKEMON[:REAL_POKE_COUNT]:
            if n == num:
                name = nm; break
        if name:
            img = dl_sprite(name)
            if img:
                s = img.copy()
                s.thumbnail((56, 56))
                bg.paste(s, (x + 6, y + 4), s)
            d.text((x + 6, y + 56),
                   f"#{num:03d}", font=font_n, fill=(60, 60, 60))
        x += cell_w
        if x + cell_w > W - 24:
            x = 24
            y += 82
    if len(caught) > 30:
        y += 82
        d.text((24, y), f"... y {len(caught) - 30} más",
               font=font_n, fill=(140, 140, 140))

    # Footer
    d.rectangle([0, H - 40, W, H], fill=(200, 38, 32))
    d.text((24, H - 32), "POKEDEX — Gen I-V CLI",
           font=font_n, fill=(255, 255, 255))

    # Resolve output path
    if out_path is None:
        downloads = os.path.expanduser("~/Downloads")
        if not os.path.isdir(downloads):
            downloads = os.path.expanduser("~")
        out_path = os.path.join(
            downloads, f"pokedex_trainer_card_{today}.png")
    try:
        bg.save(out_path)
        return out_path
    except OSError:
        return None


def _daily_pokemon_idx():
    """Deterministic 'pokemon of the day' index, seeded from today's date."""
    seed = datetime.date.today().toordinal()
    rng = random.Random(seed)
    return rng.randint(0, REAL_POKE_COUNT - 1)


_glitch_rng = random.Random()


def _missingno_glitch(my, mx, count=10):
    """Randomly stamp glitchy characters across the screen for MissingNo."""
    _, inn, _mrg, _sw, dx = _geom(mx)
    chars = "▓▒░█▀▄▌▐"
    parts = []
    for _ in range(count):
        col = dx + _glitch_rng.randint(0, inn)
        row = _glitch_rng.randint(1, my)
        ch = _glitch_rng.choice(chars)
        col_fg = _glitch_rng.choice([FG_WHITE, FG_GRAY, FG_DKGRAY])
        parts.append(f"\033[{row};{col + 1}H{BG_RED}{col_fg}{ch}")
    sys.stdout.write("".join(parts) + RST)
    sys.stdout.flush()


def _stat_bar(val, cap, width):
    """Horizontal bar for a stat (0..cap) in `width` cells, tiered color."""
    val = max(0, min(val, cap))
    filled = int(round(val / cap * width))
    empty = width - filled
    if val >= 120:
        c = FG_GLED
    elif val >= 80:
        c = FG_YLED
    elif val >= 50:
        c = FG_GRAY
    else:
        c = FG_RLED
    return f"{c}{'█' * filled}{FG_DKGRAY}{'░' * empty}{FG_WHITE}"


# Terminal capability detection: warn once if not truecolor.
_TRUECOLOR = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
if not _TRUECOLOR and os.environ.get("TERM", "") not in ("xterm-kitty",):
    sys.stderr.write(
        "[pokedex] aviso: COLORTERM=truecolor no detectado; "
        "los colores pueden verse aproximados.\n")

# ── Persistence (stats.json) ────────────────────────────────────────────────

STATS_FILE = os.path.join(_cache_root(), "stats.json")
STATS_DEFAULT = {
    "seen": [],              # list[int] pokemon numbers (1..649) viewed in detail
    "caught_safari": [],     # list[int] pokemon numbers caught in safari
    "best_quiz": {},         # {"silueta-10": 10, "cry-25": 23, ...}
    "best_memory": {},       # {"Normal": {"tries": 14, "seconds": 42}}
    "gym_badges": [],        # list[int] Kanto gym leader indices beaten
    "mute": False,
    "palette_idx": 0,
    "sprite_style": 0,       # index into SPRITE_STYLES
    "shiny_seen": [],        # pokemon numbers viewed in shiny mode
    "last_open_date": "",    # ISO date of last open (for daily)
}


def _fresh_stats():
    """Return an independent default stats tree."""
    return pokedex_stats.fresh_stats(STATS_DEFAULT)


def _coerce_int(value, default=0):
    return pokedex_stats.coerce_int(value, default)


def _clamp_int(value, default, lo, hi):
    return pokedex_stats.clamp_int(value, default, lo, hi)


def _normalise_int_list(value):
    return pokedex_stats.normalise_int_list(value)


def _normalise_stats(loaded=None):
    return pokedex_stats.normalise_stats(
        loaded, STATS_DEFAULT, len(PALETTES), len(SPRITE_STYLES))


STATS = _fresh_stats()


def _load_stats():
    """Load stats from disk, falling back to defaults."""
    global STATS
    STATS = pokedex_stats.load_stats(
        STATS_FILE, STATS_DEFAULT, len(PALETTES), len(SPRITE_STYLES))


def _save_stats():
    """Persist stats, creating directory if needed. Silent on errors."""
    pokedex_stats.save_stats(STATS_FILE, STATS)


def _mark_seen(num):
    if num and num not in STATS["seen"]:
        STATS["seen"].append(num)
        _save_stats()


def _mark_caught(num):
    if num and num not in STATS["caught_safari"]:
        STATS["caught_safari"].append(num)
        _save_stats()


def _mark_shiny(num):
    if num and num not in STATS["shiny_seen"]:
        STATS["shiny_seen"].append(num)
        _save_stats()


def _best_quiz_key(mode, count):
    return f"{mode}-{count}"


def _get_best_quiz(mode, count):
    return STATS["best_quiz"].get(_best_quiz_key(mode, count), 0)


def _set_best_quiz(mode, count, score):
    k = _best_quiz_key(mode, count)
    if score > STATS["best_quiz"].get(k, 0):
        STATS["best_quiz"][k] = score
        _save_stats()
        return True
    return False


def _gym_badges():
    return [i for i in STATS.get("gym_badges", [])
            if isinstance(i, int) and 0 <= i < len(GYM_LEADERS)]


def _mark_gym_badge(leader_idx):
    badges = STATS.setdefault("gym_badges", [])
    if leader_idx not in badges:
        badges.append(leader_idx)
        _save_stats()
        return True
    return False


# ── Audio mute (global) ──────────────────────────────────────────────────────
AUDIO_MUTED = False


def _audio_off():
    return AUDIO_MUTED


def _toggle_mute():
    global AUDIO_MUTED
    AUDIO_MUTED = not AUDIO_MUTED
    STATS["mute"] = AUDIO_MUTED
    _save_stats()
    return AUDIO_MUTED


# ── Resize handling (SIGWINCH) ───────────────────────────────────────────────
_resize_pending = False


def _on_resize(_signum, _frame):
    global _resize_pending
    _resize_pending = True


try:
    signal.signal(signal.SIGWINCH, _on_resize)
    # Do NOT auto-restart syscalls on SIGWINCH: _readkey can immediately
    # surface "RESIZE" instead of waiting for the next byte.
    signal.siginterrupt(signal.SIGWINCH, True)
except (AttributeError, ValueError):
    pass  # Windows / non-main-thread


# ── Terminal cleanup (atexit) ───────────────────────────────────────────────
_orig_termios = None


def _capture_termios():
    global _orig_termios
    try:
        _orig_termios = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        _orig_termios = None


def _restore_termios():
    if _orig_termios is not None:
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _orig_termios)
        except (termios.error, OSError):
            pass


_entered_alt_screen = False


def _cleanup_on_exit():
    """Final cleanup: kill any background audio, restore the terminal."""
    # _killp / _kill_tts are module-level and always defined by the time
    # atexit fires, and both already swallow OSError internally.
    _stop_cry()
    _kill_tts()
    _restore_termios()
    if _entered_alt_screen:
        try:
            sys.stdout.write(f"{RST}\033[?25h\033[?1049l")
            sys.stdout.flush()
        except OSError:
            pass


atexit.register(_cleanup_on_exit)

# ── Pokemon data ─────────────────────────────────────────────────────────────
# fmt: off
POKEMON = [
    (1,"bulbasaur"),(2,"ivysaur"),(3,"venusaur"),(4,"charmander"),(5,"charmeleon"),
    (6,"charizard"),(7,"squirtle"),(8,"wartortle"),(9,"blastoise"),(10,"caterpie"),
    (11,"metapod"),(12,"butterfree"),(13,"weedle"),(14,"kakuna"),(15,"beedrill"),(16,"pidgey"),
    (17,"pidgeotto"),(18,"pidgeot"),(19,"rattata"),(20,"raticate"),(21,"spearow"),
    (22,"fearow"),(23,"ekans"),(24,"arbok"),(25,"pikachu"),(26,"raichu"),(27,"sandshrew"),
    (28,"sandslash"),(29,"nidoran-f"),(30,"nidorina"),(31,"nidoqueen"),(32,"nidoran-m"),
    (33,"nidorino"),(34,"nidoking"),(35,"clefairy"),(36,"clefable"),(37,"vulpix"),
    (38,"ninetales"),(39,"jigglypuff"),(40,"wigglytuff"),(41,"zubat"),(42,"golbat"),
    (43,"oddish"),(44,"gloom"),(45,"vileplume"),(46,"paras"),(47,"parasect"),(48,"venonat"),
    (49,"venomoth"),(50,"diglett"),(51,"dugtrio"),(52,"meowth"),(53,"persian"),(54,"psyduck"),
    (55,"golduck"),(56,"mankey"),(57,"primeape"),(58,"growlithe"),(59,"arcanine"),
    (60,"poliwag"),(61,"poliwhirl"),(62,"poliwrath"),(63,"abra"),(64,"kadabra"),
    (65,"alakazam"),(66,"machop"),(67,"machoke"),(68,"machamp"),(69,"bellsprout"),
    (70,"weepinbell"),(71,"victreebel"),(72,"tentacool"),(73,"tentacruel"),(74,"geodude"),
    (75,"graveler"),(76,"golem"),(77,"ponyta"),(78,"rapidash"),(79,"slowpoke"),(80,"slowbro"),
    (81,"magnemite"),(82,"magneton"),(83,"farfetchd"),(84,"doduo"),(85,"dodrio"),(86,"seel"),
    (87,"dewgong"),(88,"grimer"),(89,"muk"),(90,"shellder"),(91,"cloyster"),(92,"gastly"),
    (93,"haunter"),(94,"gengar"),(95,"onix"),(96,"drowzee"),(97,"hypno"),(98,"krabby"),
    (99,"kingler"),(100,"voltorb"),(101,"electrode"),(102,"exeggcute"),(103,"exeggutor"),
    (104,"cubone"),(105,"marowak"),(106,"hitmonlee"),(107,"hitmonchan"),(108,"lickitung"),
    (109,"koffing"),(110,"weezing"),(111,"rhyhorn"),(112,"rhydon"),(113,"chansey"),
    (114,"tangela"),(115,"kangaskhan"),(116,"horsea"),(117,"seadra"),(118,"goldeen"),
    (119,"seaking"),(120,"staryu"),(121,"starmie"),(122,"mr-mime"),(123,"scyther"),
    (124,"jynx"),(125,"electabuzz"),(126,"magmar"),(127,"pinsir"),(128,"tauros"),
    (129,"magikarp"),(130,"gyarados"),(131,"lapras"),(132,"ditto"),(133,"eevee"),
    (134,"vaporeon"),(135,"jolteon"),(136,"flareon"),(137,"porygon"),(138,"omanyte"),
    (139,"omastar"),(140,"kabuto"),(141,"kabutops"),(142,"aerodactyl"),(143,"snorlax"),
    (144,"articuno"),(145,"zapdos"),(146,"moltres"),(147,"dratini"),(148,"dragonair"),
    (149,"dragonite"),(150,"mewtwo"),(151,"mew"),(152,"chikorita"),(153,"bayleef"),
    (154,"meganium"),(155,"cyndaquil"),(156,"quilava"),(157,"typhlosion"),(158,"totodile"),
    (159,"croconaw"),(160,"feraligatr"),(161,"sentret"),(162,"furret"),(163,"hoothoot"),
    (164,"noctowl"),(165,"ledyba"),(166,"ledian"),(167,"spinarak"),(168,"ariados"),
    (169,"crobat"),(170,"chinchou"),(171,"lanturn"),(172,"pichu"),(173,"cleffa"),
    (174,"igglybuff"),(175,"togepi"),(176,"togetic"),(177,"natu"),(178,"xatu"),(179,"mareep"),
    (180,"flaaffy"),(181,"ampharos"),(182,"bellossom"),(183,"marill"),(184,"azumarill"),
    (185,"sudowoodo"),(186,"politoed"),(187,"hoppip"),(188,"skiploom"),(189,"jumpluff"),
    (190,"aipom"),(191,"sunkern"),(192,"sunflora"),(193,"yanma"),(194,"wooper"),
    (195,"quagsire"),(196,"espeon"),(197,"umbreon"),(198,"murkrow"),(199,"slowking"),
    (200,"misdreavus"),(201,"unown"),(202,"wobbuffet"),(203,"girafarig"),(204,"pineco"),
    (205,"forretress"),(206,"dunsparce"),(207,"gligar"),(208,"steelix"),(209,"snubbull"),
    (210,"granbull"),(211,"qwilfish"),(212,"scizor"),(213,"shuckle"),(214,"heracross"),
    (215,"sneasel"),(216,"teddiursa"),(217,"ursaring"),(218,"slugma"),(219,"magcargo"),
    (220,"swinub"),(221,"piloswine"),(222,"corsola"),(223,"remoraid"),(224,"octillery"),
    (225,"delibird"),(226,"mantine"),(227,"skarmory"),(228,"houndour"),(229,"houndoom"),
    (230,"kingdra"),(231,"phanpy"),(232,"donphan"),(233,"porygon2"),(234,"stantler"),
    (235,"smeargle"),(236,"tyrogue"),(237,"hitmontop"),(238,"smoochum"),(239,"elekid"),
    (240,"magby"),(241,"miltank"),(242,"blissey"),(243,"raikou"),(244,"entei"),(245,"suicune"),
    (246,"larvitar"),(247,"pupitar"),(248,"tyranitar"),(249,"lugia"),(250,"ho-oh"),
    (251,"celebi"),(252,"treecko"),(253,"grovyle"),(254,"sceptile"),(255,"torchic"),
    (256,"combusken"),(257,"blaziken"),(258,"mudkip"),(259,"marshtomp"),(260,"swampert"),
    (261,"poochyena"),(262,"mightyena"),(263,"zigzagoon"),(264,"linoone"),(265,"wurmple"),
    (266,"silcoon"),(267,"beautifly"),(268,"cascoon"),(269,"dustox"),(270,"lotad"),
    (271,"lombre"),(272,"ludicolo"),(273,"seedot"),(274,"nuzleaf"),(275,"shiftry"),
    (276,"taillow"),(277,"swellow"),(278,"wingull"),(279,"pelipper"),(280,"ralts"),
    (281,"kirlia"),(282,"gardevoir"),(283,"surskit"),(284,"masquerain"),(285,"shroomish"),
    (286,"breloom"),(287,"slakoth"),(288,"vigoroth"),(289,"slaking"),(290,"nincada"),
    (291,"ninjask"),(292,"shedinja"),(293,"whismur"),(294,"loudred"),(295,"exploud"),
    (296,"makuhita"),(297,"hariyama"),(298,"azurill"),(299,"nosepass"),(300,"skitty"),
    (301,"delcatty"),(302,"sableye"),(303,"mawile"),(304,"aron"),(305,"lairon"),(306,"aggron"),
    (307,"meditite"),(308,"medicham"),(309,"electrike"),(310,"manectric"),(311,"plusle"),
    (312,"minun"),(313,"volbeat"),(314,"illumise"),(315,"roselia"),(316,"gulpin"),
    (317,"swalot"),(318,"carvanha"),(319,"sharpedo"),(320,"wailmer"),(321,"wailord"),
    (322,"numel"),(323,"camerupt"),(324,"torkoal"),(325,"spoink"),(326,"grumpig"),
    (327,"spinda"),(328,"trapinch"),(329,"vibrava"),(330,"flygon"),(331,"cacnea"),
    (332,"cacturne"),(333,"swablu"),(334,"altaria"),(335,"zangoose"),(336,"seviper"),
    (337,"lunatone"),(338,"solrock"),(339,"barboach"),(340,"whiscash"),(341,"corphish"),
    (342,"crawdaunt"),(343,"baltoy"),(344,"claydol"),(345,"lileep"),(346,"cradily"),
    (347,"anorith"),(348,"armaldo"),(349,"feebas"),(350,"milotic"),(351,"castform"),
    (352,"kecleon"),(353,"shuppet"),(354,"banette"),(355,"duskull"),(356,"dusclops"),
    (357,"tropius"),(358,"chimecho"),(359,"absol"),(360,"wynaut"),(361,"snorunt"),
    (362,"glalie"),(363,"spheal"),(364,"sealeo"),(365,"walrein"),(366,"clamperl"),
    (367,"huntail"),(368,"gorebyss"),(369,"relicanth"),(370,"luvdisc"),(371,"bagon"),
    (372,"shelgon"),(373,"salamence"),(374,"beldum"),(375,"metang"),(376,"metagross"),
    (377,"regirock"),(378,"regice"),(379,"registeel"),(380,"latias"),(381,"latios"),
    (382,"kyogre"),(383,"groudon"),(384,"rayquaza"),(385,"jirachi"),(386,"deoxys"),
    (387,"turtwig"),(388,"grotle"),(389,"torterra"),(390,"chimchar"),(391,"monferno"),
    (392,"infernape"),(393,"piplup"),(394,"prinplup"),(395,"empoleon"),(396,"starly"),
    (397,"staravia"),(398,"staraptor"),(399,"bidoof"),(400,"bibarel"),(401,"kricketot"),
    (402,"kricketune"),(403,"shinx"),(404,"luxio"),(405,"luxray"),(406,"budew"),
    (407,"roserade"),(408,"cranidos"),(409,"rampardos"),(410,"shieldon"),(411,"bastiodon"),
    (412,"burmy"),(413,"wormadam"),(414,"mothim"),(415,"combee"),(416,"vespiquen"),
    (417,"pachirisu"),(418,"buizel"),(419,"floatzel"),(420,"cherubi"),(421,"cherrim"),
    (422,"shellos"),(423,"gastrodon"),(424,"ambipom"),(425,"drifloon"),(426,"drifblim"),
    (427,"buneary"),(428,"lopunny"),(429,"mismagius"),(430,"honchkrow"),(431,"glameow"),
    (432,"purugly"),(433,"chingling"),(434,"stunky"),(435,"skuntank"),(436,"bronzor"),
    (437,"bronzong"),(438,"bonsly"),(439,"mime-jr"),(440,"happiny"),(441,"chatot"),
    (442,"spiritomb"),(443,"gible"),(444,"gabite"),(445,"garchomp"),(446,"munchlax"),
    (447,"riolu"),(448,"lucario"),(449,"hippopotas"),(450,"hippowdon"),(451,"skorupi"),
    (452,"drapion"),(453,"croagunk"),(454,"toxicroak"),(455,"carnivine"),(456,"finneon"),
    (457,"lumineon"),(458,"mantyke"),(459,"snover"),(460,"abomasnow"),(461,"weavile"),
    (462,"magnezone"),(463,"lickilicky"),(464,"rhyperior"),(465,"tangrowth"),
    (466,"electivire"),(467,"magmortar"),(468,"togekiss"),(469,"yanmega"),(470,"leafeon"),
    (471,"glaceon"),(472,"gliscor"),(473,"mamoswine"),(474,"porygon-z"),(475,"gallade"),
    (476,"probopass"),(477,"dusknoir"),(478,"froslass"),(479,"rotom"),(480,"uxie"),
    (481,"mesprit"),(482,"azelf"),(483,"dialga"),(484,"palkia"),(485,"heatran"),
    (486,"regigigas"),(487,"giratina"),(488,"cresselia"),(489,"phione"),(490,"manaphy"),
    (491,"darkrai"),(492,"shaymin"),(493,"arceus"),(494,"victini"),(495,"snivy"),
    (496,"servine"),(497,"serperior"),(498,"tepig"),(499,"pignite"),(500,"emboar"),
    (501,"oshawott"),(502,"dewott"),(503,"samurott"),(504,"patrat"),(505,"watchog"),
    (506,"lillipup"),(507,"herdier"),(508,"stoutland"),(509,"purrloin"),(510,"liepard"),
    (511,"pansage"),(512,"simisage"),(513,"pansear"),(514,"simisear"),(515,"panpour"),
    (516,"simipour"),(517,"munna"),(518,"musharna"),(519,"pidove"),(520,"tranquill"),
    (521,"unfezant"),(522,"blitzle"),(523,"zebstrika"),(524,"roggenrola"),(525,"boldore"),
    (526,"gigalith"),(527,"woobat"),(528,"swoobat"),(529,"drilbur"),(530,"excadrill"),
    (531,"audino"),(532,"timburr"),(533,"gurdurr"),(534,"conkeldurr"),(535,"tympole"),
    (536,"palpitoad"),(537,"seismitoad"),(538,"throh"),(539,"sawk"),(540,"sewaddle"),
    (541,"swadloon"),(542,"leavanny"),(543,"venipede"),(544,"whirlipede"),(545,"scolipede"),
    (546,"cottonee"),(547,"whimsicott"),(548,"petilil"),(549,"lilligant"),(550,"basculin"),
    (551,"sandile"),(552,"krokorok"),(553,"krookodile"),(554,"darumaka"),(555,"darmanitan"),
    (556,"maractus"),(557,"dwebble"),(558,"crustle"),(559,"scraggy"),(560,"scrafty"),
    (561,"sigilyph"),(562,"yamask"),(563,"cofagrigus"),(564,"tirtouga"),(565,"carracosta"),
    (566,"archen"),(567,"archeops"),(568,"trubbish"),(569,"garbodor"),(570,"zorua"),
    (571,"zoroark"),(572,"minccino"),(573,"cinccino"),(574,"gothita"),(575,"gothorita"),
    (576,"gothitelle"),(577,"solosis"),(578,"duosion"),(579,"reuniclus"),(580,"ducklett"),
    (581,"swanna"),(582,"vanillite"),(583,"vanillish"),(584,"vanilluxe"),(585,"deerling"),
    (586,"sawsbuck"),(587,"emolga"),(588,"karrablast"),(589,"escavalier"),(590,"foongus"),
    (591,"amoonguss"),(592,"frillish"),(593,"jellicent"),(594,"alomomola"),(595,"joltik"),
    (596,"galvantula"),(597,"ferroseed"),(598,"ferrothorn"),(599,"klink"),(600,"klang"),
    (601,"klinklang"),(602,"tynamo"),(603,"eelektrik"),(604,"eelektross"),(605,"elgyem"),
    (606,"beheeyem"),(607,"litwick"),(608,"lampent"),(609,"chandelure"),(610,"axew"),
    (611,"fraxure"),(612,"haxorus"),(613,"cubchoo"),(614,"beartic"),(615,"cryogonal"),
    (616,"shelmet"),(617,"accelgor"),(618,"stunfisk"),(619,"mienfoo"),(620,"mienshao"),
    (621,"druddigon"),(622,"golett"),(623,"golurk"),(624,"pawniard"),(625,"bisharp"),
    (626,"bouffalant"),(627,"rufflet"),(628,"braviary"),(629,"vullaby"),(630,"mandibuzz"),
    (631,"heatmor"),(632,"durant"),(633,"deino"),(634,"zweilous"),(635,"hydreigon"),
    (636,"larvesta"),(637,"volcarona"),(638,"cobalion"),(639,"terrakion"),(640,"virizion"),
    (641,"tornadus"),(642,"thundurus"),(643,"reshiram"),(644,"zekrom"),(645,"landorus"),
    (646,"kyurem"),(647,"keldeo"),(648,"meloetta"),(649,"genesect"),(0,"missingno")
]
# fmt: on
POKE_COUNT = len(POKEMON)
REAL_POKE_COUNT = 649  # indices [0..648] are real Pokemon; MissingNo. is POKE_COUNT-1

DISPLAY_NAMES = {
    "nidoran-f": "Nidoran\u2640", "nidoran-m": "Nidoran\u2642",
    "nidoranf": "Nidoran\u2640", "nidoranm": "Nidoran\u2642",
    "mr-mime": "Mr. Mime", "mr. mime": "Mr. Mime",
    "farfetchd": "Farfetch'd",
    "ho-oh": "Ho-Oh", "mime-jr": "Mime Jr.", "porygon-z": "Porygon-Z",
    "missingno": "MissingNo.",
}

QUIZ_ALIASES = {
    "nidoran-f": ["nidoran", "nidoran f", "nidoranf", "nidoran hembra", "nidoran female"],
    "nidoran-m": ["nidoran", "nidoran m", "nidoranm", "nidoran macho", "nidoran male"],
    "nidoranf": ["nidoran", "nidoran f", "nidoran hembra", "nidoran female"],
    "nidoranm": ["nidoran", "nidoran m", "nidoran macho", "nidoran male"],
    "mr-mime": ["mrmime", "mr mime", "mr.mime"],
    "mr. mime": ["mrmime", "mr mime", "mr.mime"],
    "mime-jr": ["mimejr", "mime jr", "mime.jr", "mime junior"],
    "farfetchd": ["farfetchd", "farfetch d", "farfetch"],
    "ho-oh": ["hooh", "ho oh"],
    "porygon-z": ["porygonz", "porygon z"],
}


def _dn(n):
    if n in DISPLAY_NAMES:
        return DISPLAY_NAMES[n]
    return " ".join(part.capitalize() for part in n.replace("-", " ").split())


def _sn(n):
    return n.replace(" ", "").replace(".", "").replace("'", "").replace("-", "")


POKEMON_NUM_BY_SLUG = {_sn(name).lower(): num for num, name in POKEMON if num}


def _pokemon_num_for_name(name):
    return POKEMON_NUM_BY_SLUG.get(_sn(str(name).lower()))


def _vl(s):
    """Visible columns: ANSI codes don't count, wide/emoji chars count as 2."""
    plain = ANSI_RE.sub("", s)
    total = 0
    for ch in plain:
        cp = ord(ch)
        # Anything outside the BMP (supplementary plane) is almost certainly
        # rendered as a wide cell by modern terminals — emoji, etc.
        if cp >= 0x10000:
            total += 2
        # Common CJK / fullwidth ranges
        elif 0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0xA4CF \
                or 0xAC00 <= cp <= 0xD7A3 or 0xF900 <= cp <= 0xFAFF \
                or 0xFE30 <= cp <= 0xFE4F or 0xFF00 <= cp <= 0xFF60:
            total += 2
        else:
            total += 1
    return total


# ── Network ──────────────────────────────────────────────────────────────────

def _ssl():
    return pokedex_network.create_ssl_context()


_ctx = _ssl()


def _get(url, timeout=10):
    return pokedex_network.get_bytes(url, _ctx, timeout=timeout)


def _get_with_retries(url, timeout=10, attempts=2, pause=0.35):
    """Fetch bytes with a tiny retry budget for batch prefetch commands."""
    for attempt in range(max(1, attempts)):
        data = _get(url, timeout=timeout)
        if data:
            return data
        if attempt < attempts - 1:
            time.sleep(pause * (attempt + 1))
    return None


# ── Sprite ───────────────────────────────────────────────────────────────────

def _gen_missingno():
    """Generate the classic MissingNo. backwards-L glitch sprite."""
    rng = random.Random(0)  # local RNG, deterministic glitch without polluting global state
    sz = 56
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    px = img.load()
    # Backwards-L shape: right column block + bottom row block
    for y in range(sz):
        for x in range(sz):
            in_right = x >= sz // 2
            in_bottom = y >= sz // 2
            if in_right or in_bottom:
                # Glitchy pixel pattern from "corrupted VRAM"
                v = ((x * 7 + y * 13 + rng.randint(0, 30)) % 4)
                if v == 0:
                    px[x, y] = (0, 0, 0, 255)
                elif v == 1:
                    px[x, y] = (255, 255, 255, 255)
                elif v == 2:
                    px[x, y] = (90, 90, 90, 255)
                else:
                    px[x, y] = (180, 180, 180, 255)
    return img


def _gen_pokeball():
    """Generate a 16x16 Pokeball with proper band + highlight."""
    sz = 16
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    px = img.load()
    pattern = [
        "....RRRRRRRR....",
        "..RRRRrrRRRRRR..",
        ".RRRrrrrRRRRRRR.",
        "RRRrrrrRRRRRRRRR",
        "RRRRrrRRRRRRRRRR",
        "RRRRRRRRRRRRRRRR",
        "RRRRRRRRRRRRRRRR",
        "KKKKKKKKKKKKKKKK",
        "KKKKKKPPPPKKKKKK",
        "KKKKKKKKKKKKKKKK",
        "WWWWWWWWWWWWWWWW",
        "WWWLLLWWWWWWWWWW",
        "WWLLLLWWWWWWWWWW",
        ".WWLLWWWWWWWWWW.",
        "..WWWWWWWWWWWW..",
        "....WWWWWWWW....",
    ]
    colors = {
        "R": (220, 48, 42, 255),     # red body
        "r": (255, 140, 130, 255),   # red highlight
        "K": (20, 20, 20, 255),      # black band
        "P": (190, 190, 200, 255),   # button
        "W": (245, 245, 245, 255),   # white body
        "L": (210, 210, 215, 255),   # white highlight
    }
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch in colors:
                px[x, y] = colors[ch]
    return img


def _gen_rock():
    """Generate a 6x6 rock sprite."""
    sz = 6
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    px = img.load()
    pattern = [
        "..GG..",
        ".GLGG.",
        "GGLGGG",
        "GGDGGG",
        ".GDDG.",
        "..GG..",
    ]
    colors = {
        "G": (140, 140, 130, 255), "L": (180, 180, 170, 255),
        "D": (95, 95, 85, 255),
    }
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch in colors:
                px[x, y] = colors[ch]
    return img


def _gen_bait():
    """Generate a 6x6 bait sprite."""
    sz = 6
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    px = img.load()
    pattern = [
        "..BB..",
        ".BTBB.",
        ".BTTB.",
        ".BTBB.",
        "..BB..",
        "......",
    ]
    colors = {
        "T": (210, 170, 110, 255), "B": (160, 110, 60, 255),
    }
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch in colors:
                px[x, y] = colors[ch]
    return img


def _gen_stars():
    """Generate an 8x8 capture sparkle sprite."""
    sz = 8
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    px = img.load()
    pattern = [
        "W......W",
        ".Y....Y.",
        "..W..W..",
        "........",
        "........",
        "..W..W..",
        ".Y....Y.",
        "W......W",
    ]
    colors = {
        "Y": (255, 255, 100, 255), "W": (255, 255, 255, 255),
    }
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch in colors:
                px[x, y] = colors[ch]
    return img


def _safari_item_lines(gen_func, tw=10):
    """Render a small PIL sprite to half-block lines on green background."""
    img = gen_func()
    return render_sprite(img, tw, bg_rgb=SCR_RGB)


def _safari_ball_lines():
    """Pokeball rendered bigger than plain items so it reads on screen."""
    return render_sprite(_gen_pokeball(), 16, bg_rgb=SCR_RGB)


def _safari_star_lines():
    """Sparkle rendered a touch bigger than rocks/bait."""
    return render_sprite(_gen_stars(), 12, bg_rgb=SCR_RGB)


_sprite_path_locks_lock = threading.Lock()
_sprite_path_locks = {}  # path -> threading.Lock; one lock per file on disk


def _path_lock(path):
    """Return (creating if needed) a per-path Lock for atomic writes."""
    with _sprite_path_locks_lock:
        lock = _sprite_path_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _sprite_path_locks[path] = lock
        return lock


def _sprite_style_candidates(name):
    """Return sprite styles worth trying for this Pokemon.

    Older Showdown sprite folders stop at their own generation. Skipping those
    impossible URLs avoids a slow 404/timeout before falling back to Gen 5.
    """
    selected = _sprite_style_key()
    num = _pokemon_num_for_name(name)
    style_keys = []
    limit = SPRITE_STYLE_DEX_LIMITS.get(selected, REAL_POKE_COUNT)
    if num is None or num <= limit:
        style_keys.append(selected)
    if "gen5" not in style_keys:
        style_keys.append("gen5")
    return style_keys


def _sprite_disk_cached(name):
    if name == "missingno":
        return True
    slug = _sn(name)
    for style_key in _sprite_style_candidates(name):
        p = os.path.join(CACHE_DIR, style_key, f"{slug}.png")
        if os.path.exists(p):
            return True
    return False


def dl_sprite(name):
    """Load a sprite PNG; caches per style so switching generations is instant.

    Writes are atomic (`.tmp` + `os.replace`) and serialised per path so the
    background prefetch and a foreground load can race on the same name
    without truncating each other's file mid-write.
    """
    if name == "missingno":
        return _gen_missingno()
    style_keys = _sprite_style_candidates(name)

    for style_key in style_keys:
        style_dir = os.path.join(CACHE_DIR, style_key)
        os.makedirs(style_dir, exist_ok=True)
        p = os.path.join(style_dir, f"{_sn(name)}.png")
        # Fast path (no lock) — readers tolerate a stale "exists" answer
        if os.path.exists(p):
            try:
                return Image.open(p).convert("RGBA")
            except (OSError, Image.UnidentifiedImageError):
                pass  # corrupt; fall through and re-download
        with _path_lock(p):
            # Re-check inside the lock: another thread may have just finished it.
            if os.path.exists(p):
                try:
                    return Image.open(p).convert("RGBA")
                except (OSError, Image.UnidentifiedImageError):
                    pass
            d = _get(f"{_sprite_base_for_key(style_key)}/{_sn(name)}.png")
            if not d:
                continue
            pokedex_cache.atomic_write_bytes(p, d)
            return Image.open(BytesIO(d)).convert("RGBA")
    return None


def dl_memory_icon(num):
    """Load a compact PC/party icon for the memory game."""
    if not num:
        return None
    os.makedirs(MEMORY_ICON_DIR, exist_ok=True)
    p = os.path.join(MEMORY_ICON_DIR, f"{num}.png")
    if os.path.exists(p):
        try:
            return Image.open(p).convert("RGBA")
        except (OSError, Image.UnidentifiedImageError):
            pass
    with _path_lock(p):
        if os.path.exists(p):
            try:
                return Image.open(p).convert("RGBA")
            except (OSError, Image.UnidentifiedImageError):
                pass
        d = _get(f"{MEMORY_ICON_BASE}/{num}.png")
        if not d:
            return None
        pokedex_cache.atomic_write_bytes(p, d)
        return Image.open(BytesIO(d)).convert("RGBA")


def _trim(img):
    """Crop transparent borders. Uses PIL getbbox() on a thresholded alpha channel."""
    alpha = img.split()[-1]
    mask = alpha.point(lambda p: 255 if p > 50 else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return img
    l, t, r, b = bbox
    w, h = img.size
    return img.crop((max(0, l - 1), max(0, t - 1), min(w, r + 1), min(h, b + 1)))


def render_sprite(img, target_w, bg_rgb=None, max_rows=None):
    """Render sprite to half-block lines. Scales down if it would exceed max_rows.

    When the scale factor is close to an integer (or to a clean half-step),
    snap to it so that every source pixel maps to the same number of target
    pixels — keeps the Game Boy pixel-art look crisp instead of aliased.
    """
    img = _trim(img)
    w, h = img.size
    sc = target_w / w

    def _snap_scale(s):
        """Snap `s` to a clean ratio (integer, half, or simple fraction).

        For `s < 1` we allow common fractions (1/2, 1/3, 2/3, 3/4…) so sub-1x
        downscales still map cleanly. For `s >= 1` we prefer integers or
        half-steps. Only snap when we're already close — otherwise keep the
        raw scale to preserve fit.
        """
        candidates = [0.25, 0.333333, 0.4, 0.5, 0.6, 0.666667, 0.75, 0.8]
        for base in range(1, 9):
            for half in (0.0, 0.5):
                candidates.append(base + half)
        best = min(candidates, key=lambda c: abs(c - s))
        return best if abs(best - s) <= 0.1 else s

    raw_sc = sc
    sc = min(_snap_scale(sc), raw_sc)  # snap down only — never exceed target_w
    nw = max(4, min(target_w, int(round(w * sc))))
    nh = max(4, int(round(h * sc)))
    if nh % 2:
        nh += 1

    # If too tall, scale down further to fit max_rows. Snap can overshoot in
    # both directions, so we clamp both results.
    if max_rows and nh // 2 > max_rows:
        raw_sc2 = (max_rows * 2) / h
        sc2 = min(_snap_scale(raw_sc2), raw_sc2)
        nw = max(4, min(target_w, int(round(w * sc2))))
        nh = max(4, int(round(h * sc2)))
        if nh % 2:
            nh += 1

    img = img.resize((nw, nh), Image.NEAREST)
    px = img.load()

    bg = f"\033[48;2;{bg_rgb[0]};{bg_rgb[1]};{bg_rgb[2]}m" if bg_rgb else "\033[49m"
    trans = f"{bg} "

    lines = []
    for y in range(0, nh, 2):
        parts = []
        for x in range(nw):
            top = px[x, y]
            bot = px[x, y + 1] if y + 1 < nh else (0, 0, 0, 0)
            tv, bv = top[3] > 50, bot[3] > 50
            if tv and bv:
                parts.append(
                    f"\033[38;2;{top[0]};{top[1]};{top[2]}m"
                    f"\033[48;2;{bot[0]};{bot[1]};{bot[2]}m\u2580")
            elif tv:
                parts.append(f"\033[38;2;{top[0]};{top[1]};{top[2]}m{bg}\u2580")
            elif bv:
                parts.append(f"\033[38;2;{bot[0]};{bot[1]};{bot[2]}m{bg}\u2584")
            else:
                parts.append(trans)
        lines.append("".join(parts))

    while lines and ANSI_RE.sub("", lines[-1]).strip() == "":
        lines.pop()
    while lines and ANSI_RE.sub("", lines[0]).strip() == "":
        lines.pop(0)
    return lines


def _safari_draw_reflection(my, mx, spr_lines, refl_lines):
    """Paint the reflection below the grass line, bounded by the ball row."""
    if not refl_lines or not spr_lines:
        return
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    grass_row = _safari_grass_row(scr_y, scr_h, spr_lines)
    refl_w = max((_vl(rl) for rl in refl_lines), default=0)
    col = scr_x + max(0, (sw - refl_w) // 2)
    bottom_limit = scr_y + scr_h - 2  # stop before the ball-count row
    parts = []
    for i, line in enumerate(refl_lines):
        r = grass_row + 1 + i
        if r >= bottom_limit:
            break
        parts.append(f"\033[{r};{col}H{line}")
    if parts:
        sys.stdout.write("".join(parts) + RST)
        sys.stdout.flush()


def _make_reflection(img, target_w, bg_rgb, max_rows):
    """Return rendered half-block lines for a faded upside-down reflection.

    The bottom third of the sprite is flipped vertically, re-trimmed to kill
    transparent padding, then blended toward `bg_rgb` with an alpha ramp that
    fades out with distance from the grass.
    """
    from PIL import Image
    w, h = img.size
    if h < 6 or not max_rows:
        return []
    crop_h = max(6, (h * 2) // 5)
    bot = img.crop((0, h - crop_h, w, h))
    flipped = bot.transpose(Image.FLIP_TOP_BOTTOM)
    # Re-trim: after flipping, transparent padding may be at the top.
    flipped = _trim(flipped)
    if flipped.size[1] < 3:
        return []
    flipped = flipped.copy()  # ensure we can write pixels
    px = flipped.load()
    nw, nh = flipped.size
    mix = 0.55
    for y in range(nh):
        t = y / max(1, nh - 1)  # 0 at top (closest to grass), 1 at bottom
        # Keep the top ~40% reasonably opaque (~140) so the trim threshold of 50
        # still catches it after multiplication.
        alpha_scale = max(0.0, 0.55 * (1.0 - t) ** 1.2)
        for x in range(nw):
            r, g, b, a = px[x, y]
            if a < 50:
                continue
            nr = int(r * (1 - mix) + bg_rgb[0] * mix)
            ng = int(g * (1 - mix) + bg_rgb[1] * mix)
            nb = int(b * (1 - mix) + bg_rgb[2] * mix)
            px[x, y] = (nr, ng, nb, int(a * alpha_scale))
    max_refl_rows = min(max_rows, 4)
    return render_sprite(flipped, target_w, bg_rgb=bg_rgb,
                         max_rows=max_refl_rows)


def _shiny_tint(img):
    """Return a shiny variant: rotate hue ~120 degrees + bump saturation."""
    from colorsys import rgb_to_hsv, hsv_to_rgb
    out = img.copy()
    px = out.load()
    w, h = out.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 50:
                continue
            hr, s, v = rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            hr = (hr + 0.33) % 1.0
            s = min(1.0, s * 1.1)
            nr, ng, nb = hsv_to_rgb(hr, s, v)
            px[x, y] = (int(nr * 255), int(ng * 255), int(nb * 255), a)
    return out


def _silhouette(img, color=None):
    """Return a copy with all visible pixels flattened to a single RGB color.

    color=None uses the current palette's dark text color (quiz silhouette).
    Pass an explicit RGB tuple for other effects (e.g. white absorb beam).
    """
    if color is None:
        _, _, color, _ = PALETTES[_palette_idx]
    out = img.copy()
    px = out.load()
    w, h = out.size
    for y in range(h):
        for x in range(w):
            if px[x, y][3] > 50:
                px[x, y] = (color[0], color[1], color[2], 255)
    return out


def _strip_accents(s):
    """Remove accents/diacritics for flexible name matching."""
    return pokedex_text.strip_accents(s)


def _lookup_key(s):
    """Normalize free-text pokemon input for search and quiz answers."""
    return pokedex_text.lookup_key(s)


def _lookup_compact_key(s):
    """Normalize text and remove punctuation/spaces for forgiving matches."""
    return pokedex_text.lookup_compact_key(s, _sn)


def _answer_keys(name):
    """All accepted normalized answer keys for a pokemon name."""
    return pokedex_text.answer_keys(name, _dn, _sn, QUIZ_ALIASES)


# ── Safari Zone mechanics ────────────────────────────────────────────────────

def _sfx(name, rate=1.0, volume=1.0):
    """Play a short UI sound asynchronously.

    `rate` is the playback rate (>1 → higher pitch + faster, <1 → lower pitch
    + slower) — we abuse it for a poor man's pitch shift to give otherwise
    identical sounds some personality. `volume` is 0.0..1.0.
    Respects the global mute flag.
    """
    pokedex_audio.play_sfx(name, muted=AUDIO_MUTED, rate=rate, volume=volume)


_sfx_nav_last = 0.0


def _sfx_nav():
    global _sfx_nav_last
    now = time.monotonic()
    if now - _sfx_nav_last < 0.045:
        return
    _sfx_nav_last = now
    _sfx("ui_nav")


def _sfx_select():
    _sfx("ui_select")


def _sfx_back():
    _sfx("ui_back")


def _sfx_scan():
    _sfx("ui_scan")


def _safari_modifiers(anger, eating):
    """Return (catch_mod, flee_mod) based on anger/eating counters."""
    catch_mod = 1.0
    flee_mod = 1.0
    if anger > 0:
        catch_mod *= 2.0
        flee_mod *= 2.0
    if eating > 0:
        catch_mod *= 0.5
        flee_mod *= 0.5
    return catch_mod, flee_mod


def _safari_catch_check(name, catch_mod):
    """Return True if the Pokemon is caught (Safari Ball factor 1.5)."""
    base_rate = CATCH_RATES.get(name, 45)
    modified = base_rate * 1.5 * catch_mod
    roll = random.randint(0, 255)
    return roll < modified


def _safari_flee_check(name, flee_mod):
    """Return True if the Pokemon flees (~15% base chance)."""
    chance = 0.15 * flee_mod
    return random.random() < chance


# ── Loading spinner ──────────────────────────────────────────────────────────
_spinner_stop = threading.Event()
_spinner_thread = None


def _spinner_run(my, mx, label):
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not _spinner_stop.is_set():
        ch = frames[i % len(frames)]
        text = f"{FG_CYAN}{ch} {FG_WHITE}{label}..."
        col = max(1, (mx - len(label) - 6) // 2)
        try:
            sys.stdout.write(f"\033[{my // 2};{col}H{text}{RST}")
            sys.stdout.flush()
        except OSError:
            return
        i += 1
        _spinner_stop.wait(0.08)


def spinner_start(my, mx, label="Cargando"):
    global _spinner_thread
    _spinner_stop.clear()
    _spinner_thread = threading.Thread(
        target=_spinner_run, args=(my, mx, label), daemon=True)
    _spinner_thread.start()


def spinner_stop():
    _spinner_stop.set()
    global _spinner_thread
    if _spinner_thread is not None:
        _spinner_thread.join(timeout=0.2)
        _spinner_thread = None


# ── Cries ────────────────────────────────────────────────────────────────────

_cry_proc = None
_cry_lock = threading.Lock()
_cry_request_id = 0


def _killp(p):
    pokedex_audio.kill_process(p)


def _stop_cry():
    """Cancel any pending cry request and stop the currently playing cry."""
    global _cry_proc, _cry_request_id
    with _cry_lock:
        _cry_request_id += 1
        proc = _cry_proc
        _cry_proc = None
    _killp(proc)


def play_cry(name):
    """Start a Pokemon cry without blocking navigation on downloads."""
    global _cry_proc, _cry_request_id
    with _cry_lock:
        _cry_request_id += 1
        token = _cry_request_id
        old_proc = _cry_proc
        _cry_proc = None
    _killp(old_proc)
    if AUDIO_MUTED:
        return

    def run():
        global _cry_proc
        proc = pokedex_audio.play_cry(
            name, None, AUDIO_MUTED, CRIES_DIR, CRIES_BASE, _sn, _get)
        with _cry_lock:
            stale = token != _cry_request_id or AUDIO_MUTED
            if not stale:
                _cry_proc = proc
                return
        _killp(proc)

    threading.Thread(target=run, daemon=True).start()


# ── PokeAPI ──────────────────────────────────────────────────────────────────

POKEAPI_POKE = "https://pokeapi.co/api/v2/pokemon"


def _data_path(num):
    return os.path.join(DATA_DIR, f"{num}.json")


def _data_disk_cached(num):
    return num == 0 or os.path.exists(_data_path(num))


def _cry_path(name):
    return os.path.join(CRIES_DIR, f"{_sn(name)}.mp3")


def _cry_disk_cached(name):
    return os.path.exists(_cry_path(name))


def _cache_cry(name, force=False):
    """Ensure a Pokemon cry is cached. Returns True when available locally."""
    os.makedirs(CRIES_DIR, exist_ok=True)
    path = _cry_path(name)
    if force:
        try:
            os.remove(path)
        except OSError:
            pass
    if os.path.exists(path):
        return True
    data = _get_with_retries(f"{CRIES_BASE}/{_sn(name)}.mp3", timeout=10)
    if not data:
        return False
    return pokedex_cache.atomic_write_bytes(path, data)


def _walk_evolution_chain(chain_link, out):
    """Recursive walk of the PokeAPI evolution chain JSON."""
    if not chain_link:
        return
    species = chain_link.get("species") or {}
    name = species.get("name") or ""
    if name:
        # Extract the pokemon number from the URL: .../pokemon-species/N/
        url = species.get("url", "")
        try:
            num = int(url.rstrip("/").split("/")[-1])
        except ValueError:
            num = 0
        out.append((num, name))
    for nxt in chain_link.get("evolves_to", []) or []:
        _walk_evolution_chain(nxt, out)


def fetch_data(num):
    if num == 0:  # MissingNo.
        return {
            "genus": "Pokémon ???",
            "description": "Un extraño Pokémon que aparece cuando los datos "
            "se corrompen. Su verdadera naturaleza es un misterio. "
            "Se dice que encontrarlo puede alterar la realidad.",
            "types": [],
            "stats": {},
            "evolution": [],
            "moves": [],
        }
    os.makedirs(DATA_DIR, exist_ok=True)
    cp = _data_path(num)
    if os.path.exists(cp):
        try:
            with open(cp) as f:
                data = json.load(f)
            # Backfill missing keys from an older cache file. ONLY persist
            # the upgrade if it actually came back with the new fields —
            # otherwise we'd overwrite the cached genus/desc with a partial
            # blob and re-trigger the upgrade on every launch until the
            # network is back.
            if "types" not in data or "stats" not in data:
                upgraded = _fetch_data_full(num, data)
                if upgraded and "types" in upgraded and "stats" in upgraded:
                    data = upgraded
                    _write_data_atomic(cp, data)
            return data
        except (json.JSONDecodeError, OSError):
            pass
    data = _fetch_data_full(num, {})
    if data is None:
        return None
    _write_data_atomic(cp, data)
    return data


def _write_data_atomic(path, data):
    """Atomic JSON write so concurrent readers never see a half-written file."""
    pokedex_cache.atomic_write_json(path, data)


def _fetch_data_full(num, seed):
    """Fetch species + pokemon + evolution-chain from PokeAPI.

    `seed` may contain previously-cached genus/description we want to reuse.
    """
    out = dict(seed) if seed else {}

    # 1) Species (genus + description + evolution_chain URL)
    d = _get(f"{POKEAPI}/{num}/", timeout=15)
    if not d:
        # If we at least have a partial cache, keep it
        return out if out else None
    try:
        raw = json.loads(d)
    except json.JSONDecodeError:
        return out if out else None

    if "genus" not in out:
        genus = ""
        for g in raw.get("genera", []):
            if g["language"]["name"] == "es":
                genus = g["genus"]; break
        out["genus"] = genus
    if "description" not in out:
        desc = ""
        for e in raw.get("flavor_text_entries", []):
            if e["language"]["name"] == "es":
                desc = e["flavor_text"]; break
        desc = re.sub(r"\s+", " ",
                      desc.replace("\f", " ").replace("\n", " ")).strip()
        out["description"] = desc

    # 2) Pokemon (types + stats + level-up moves)
    d2 = _get(f"{POKEAPI_POKE}/{num}/", timeout=15)
    types = []
    stats = {}
    moves = []
    if d2:
        try:
            p = json.loads(d2)
        except json.JSONDecodeError:
            p = None
        if p:
            for t in p.get("types", []):
                nm = t.get("type", {}).get("name")
                if nm:
                    types.append(nm)
            for s in p.get("stats", []):
                nm = s.get("stat", {}).get("name")
                val = s.get("base_stat", 0)
                if nm:
                    stats[nm] = val
            preferred_groups = (
                "black-white", "heartgold-soulsilver", "platinum",
                "diamond-pearl", "emerald", "firered-leafgreen",
                "crystal", "gold-silver", "yellow", "red-blue",
            )
            for m in p.get("moves", []):
                mname = m.get("move", {}).get("name", "").replace("-", " ")
                details = [
                    vd for vd in m.get("version_group_details", [])
                    if vd.get("move_learn_method", {}).get("name") == "level-up"
                ]
                for group in preferred_groups:
                    match = next((
                        vd for vd in details
                        if vd.get("version_group", {}).get("name") == group
                    ), None)
                    if match:
                        moves.append((match.get("level_learned_at", 0), mname))
                        break
            moves.sort(key=lambda x: (x[0], x[1]))
    out["types"] = types
    out["stats"] = stats
    out["moves"] = moves

    # 3) Evolution chain
    evo = []
    evo_url = (raw.get("evolution_chain") or {}).get("url")
    if evo_url:
        d3 = _get(evo_url, timeout=15)
        if d3:
            try:
                ec = json.loads(d3)
                _walk_evolution_chain(ec.get("chain"), evo)
            except json.JSONDecodeError:
                pass
    out["evolution"] = evo
    return out


# ── TTS ──────────────────────────────────────────────────────────────────────

_tts_player = pokedex_audio.TTSPlayer(TTS_EN, TTS_ES)


def _kill_tts():
    _tts_player.stop()


def speak(dname, genus, desc):
    """Speak English name, then Spanish genus+description. No shell=True.

    Commands run sequentially in a background thread so nothing blocks the UI.
    """
    _tts_player.speak(dname, genus, desc, muted=AUDIO_MUTED)


def speak_text_es(text):
    """Speak Spanish text without saying the Pokemon name."""
    _tts_player.speak_text(text, voice=TTS_ES, muted=AUDIO_MUTED)


# ── Search ───────────────────────────────────────────────────────────────────

def search(q):
    return pokedex_text.search_pokemon(
        q, POKEMON, REAL_POKE_COUNT, _dn, _sn, QUIZ_ALIASES)


# ── Frame building helpers ───────────────────────────────────────────────────

def _geom(mx):
    # Frame width: cap at 140 so we can use the full real estate of wide
    # terminals. On narrow terminals the cap kicks in at mx-2.
    dw = min(140, mx - 2)
    if dw < 40: dw = mx
    inn = dw - 2
    mrg = 3
    sw = inn - mrg * 2 - 2
    dx = (mx - dw) // 2
    return dw, inn, mrg, sw, dx


def _heavy(inn, kind):
    l, r = ("\u250f", "\u2513") if kind == "top" else ("\u2517", "\u251b")
    return f"{BG_DKRED}{FG_WHITE}{l}{'━' * inn}{r}"


def _cas(inn, content=""):
    vw = _vl(content)
    pad = max(0, inn - vw)
    return f"{BG_RED}{FG_WHITE}\u2503{BG_RED}{content}{BG_RED}{' ' * pad}{FG_WHITE}\u2503"


def _scr_brd(inn, mrg, sw, kind):
    # Curved corners feel friendlier on the green screen.
    l, m, r = ("\u256d", "\u2500", "\u256e") if kind == "top" else ("\u2570", "\u2500", "\u256f")
    brd = f"{FG_SCRHI}{l}{m * sw}{r}"
    return _cas(inn, f"{' ' * mrg}{brd}{' ' * mrg}")


def _scr_row(inn, mrg, sw, content=""):
    vw = _vl(content)
    rp = max(0, sw - vw)
    inner = (f"{' ' * mrg}{FG_SCRHI}\u2502{BG_SCR}{content}"
             f"{BG_SCR}{' ' * rp}{BG_RED}{FG_SCRHI}\u2502{' ' * mrg}")
    return _cas(inn, inner)


def _lights_row(inn):
    lights = (f"  {FG_CYAN}\u25c9{FG_WHITE}   "
              f"{FG_RLED}\u25cf{FG_WHITE} {FG_YLED}\u25cf{FG_WHITE} {FG_GLED}\u25cf{FG_WHITE}")
    label = f"{BOLD}POK\u00c9DEX"
    lv, rv = _vl(lights), _vl(label) + 2
    return _cas(inn, f"{lights}{' ' * max(1, inn - lv - rv)}{label}  ")


# ── List mode renderer ──────────────────────────────────────────────────────

def draw_list(my, mx, cursor, s_mode, s_buf, msg):
    _, inn, mrg, sw, dx = _geom(mx)

    # Layout: top(1) + lights(1) + pad(1) + header(1) + scr_top(1) + [list]
    #         + scr_bot(1) + pad(1) + ctrl(1) + bot(1) = 9 fixed
    fixed = 9
    scr_h = max(4, my - fixed)
    total = fixed + scr_h
    yo = max(0, (my - total) // 2)

    # Scrolling: keep cursor visible, centered
    half = scr_h // 2
    offset = max(0, min(cursor - half, POKE_COUNT - scr_h))
    if offset < 0:
        offset = 0

    # Scrollbar geometry (position within the list area)
    # Position of the scrollbar thumb (0..scr_h-1)
    if POKE_COUNT > scr_h:
        # Fraction of list scrolled
        denom = max(1, POKE_COUNT - scr_h)
        thumb_pos = int((offset / denom) * (scr_h - 1))
    else:
        thumb_pos = 0

    # Item column width (inside the screen) accounting for scrollbar column
    bar_col_w = 1  # reserve 1 char at the end for the scrollbar
    item_w = sw - bar_col_w

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    # Status counters (computed once)
    seen_total = len(STATS.get("seen", []))
    caught_total = len(STATS.get("caught_safari", []))

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    # Header row (inside screen): #cursor/total   ○{seen}  ●{caught}
    header_left = f"  {FG_SCRTXT}{BOLD}#{cursor + 1:03d}/{POKE_COUNT:03d}"
    header_right = (f"{FG_SCRHI}○ {seen_total:3d}  ● {caught_total:3d}  "
                    f"{'♫' if AUDIO_MUTED else ' '}  ")
    hlv = _vl(header_left)
    hrv = _vl(header_right)
    header_pad = max(0, sw - hlv - hrv)
    header = f"{header_left}{' ' * header_pad}{header_right}"
    at(row, _scr_row(inn, mrg, sw, header)); row += 1
    # Note: we ate one screen row for the header; list gets scr_h-1 items
    list_h = scr_h - 1

    # Re-compute offset with reduced list_h
    half = list_h // 2
    offset = max(0, min(cursor - half, POKE_COUNT - list_h))
    if offset < 0:
        offset = 0
    if POKE_COUNT > list_h:
        denom = max(1, POKE_COUNT - list_h)
        thumb_pos = int((offset / denom) * (list_h - 1))
    else:
        thumb_pos = 0

    caught_set = set(STATS.get("caught_safari", []))
    seen_set = set(STATS.get("seen", []))
    daily_idx = _daily_pokemon_idx()

    for i in range(list_h):
        idx = offset + i
        is_thumb = (i == thumb_pos) if POKE_COUNT > list_h else False
        bar_char = "█" if is_thumb else "│"
        if idx < POKE_COUNT:
            num, name = POKEMON[idx]
            dn = _dn(name)
            # Bullet: ★ daily, ● caught, ○ seen, · unknown, ? glitched
            if idx == daily_idx:
                bullet = f"{FG_YLED}★{FG_SCRHI}"
            elif num == 0:
                bullet = "?"
            elif num in caught_set:
                bullet = "●"
            elif num in seen_set:
                bullet = "○"
            else:
                bullet = "·"
            label = f"{bullet} #{num:03d}  {dn}"
            label_vlen = _vl(f"{' '}▶  {label}")
            pad_right = max(0, item_w - label_vlen - 2)
            if idx == cursor:
                # Full-width highlight bar with inverted bg
                row_content = f" {FG_SCRHI}▎{FG_SCRTXT}{BOLD} {label}{' ' * pad_right} {FG_SCRHI}{bar_char}"
            else:
                row_content = f"  {FG_SCRHI}{label}{' ' * pad_right} {FG_SCRHI}{bar_char}"
            at(row, _scr_row(inn, mrg, sw, row_content))
        else:
            # Empty row still draws scrollbar
            empty_content = f"{' ' * (item_w + 1)}{FG_SCRHI}{bar_char}"
            at(row, _scr_row(inn, mrg, sw, empty_content))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    # Controls
    if s_mode:
        ctrl = f"   Buscar: {s_buf}█ {FG_GRAY}(Enter=ir, Esc=cancelar){FG_WHITE}"
    elif msg:
        ctrl = f"   {FG_YLED}{msg}{FG_WHITE}"
    else:
        # Compact bar (single-row) — `·` separator + 3-letter labels.
        sep = f" {FG_DKGRAY}·{FG_WHITE} "
        ctrl = (f" {FG_GRAY}▲▼{FG_WHITE} Nav"
                f"{sep}{FG_GRAY}↵{FG_WHITE} Ver"
                f"{sep}{FG_GRAY}/{FG_WHITE} Buscar"
                f"{sep}{FG_GRAY}g{FG_WHITE} Quiz"
                f"{sep}{FG_GRAY}h{FG_WHITE} Safari"
                f"{sep}{FG_GRAY}M{FG_WHITE} Memoria"
                f"{sep}{FG_GRAY}B{FG_WHITE} Gim"
                f"{sep}{FG_GRAY}p{FG_WHITE} Tema"
                f"{sep}{FG_GRAY}m{FG_WHITE} Mute"
                f"{sep}{FG_GRAY}?{FG_WHITE} Ayuda")
        if _vl(ctrl) > inn:
            ctrl = (f" {FG_GRAY}▲▼{FG_WHITE} Nav"
                    f"{sep}{FG_GRAY}↵{FG_WHITE} Ver"
                    f"{sep}{FG_GRAY}g{FG_WHITE} Quiz"
                    f"{sep}{FG_GRAY}h{FG_WHITE} Saf"
                    f"{sep}{FG_GRAY}B{FG_WHITE} Gim"
                    f"{sep}{FG_GRAY}M{FG_WHITE} Mem")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


# ── Detail mode renderer ────────────────────────────────────────────────────

def draw_detail(my, mx, num, dname, genus, desc, spr_lines, s_mode, s_buf, msg,
                extra=None, breath_offset=0, is_shiny=False, cry_playing=False):
    """Detail dispatcher. Wide terminals (mx >= 110) get a side-by-side layout
    with the sprite on the left and the info panel on the right; narrow
    terminals fall back to the stacked layout."""
    if mx >= DETAIL_SIDE_THRESHOLD:
        _draw_detail_side(my, mx, num, dname, genus, desc, spr_lines,
                          s_mode, s_buf, msg, extra=extra,
                          breath_offset=breath_offset, is_shiny=is_shiny,
                          cry_playing=cry_playing)
    else:
        _draw_detail_stacked(my, mx, num, dname, genus, desc, spr_lines,
                             s_mode, s_buf, msg, extra=extra,
                             breath_offset=breath_offset, is_shiny=is_shiny,
                             cry_playing=cry_playing)


DETAIL_SIDE_THRESHOLD = 110  # mx >= this → use side-by-side layout


def _detail_side_geom(my, mx):
    """Compute geometry for the side-by-side detail layout.

    Returns dict with: yo, dx, inn, body_h, green_w, gs_inner_w, gs_inner_h,
    sprite_area, right_w, gap, mrg.
    """
    _, inn, mrg, sw, dx = _geom(mx)
    # 3 chrome rows top + body_h + 2 ctrl rows + 1 heavy bot = 6 + body_h
    body_h = max(10, my - 6)
    total = 6 + body_h
    yo = max(0, (my - total) // 2)
    avail = inn - 8  # 3 LM + 2 gap + 3 RM
    green_w = (avail * 6) // 10
    right_w = avail - green_w - 2  # minus gap
    gs_inner_w = max(8, green_w - 2)
    gs_inner_h = max(4, body_h - 2)
    sprite_area = max(2, gs_inner_h - 1)  # 1 row reserved for shadow
    return {
        "yo": yo, "dx": dx, "inn": inn, "mrg": mrg,
        "body_h": body_h, "green_w": green_w, "right_w": right_w,
        "gs_inner_w": gs_inner_w, "gs_inner_h": gs_inner_h,
        "sprite_area": sprite_area, "gap": 2,
    }


def _build_side_panel_lines(num, dname, genus, types, desc, evo_chain, stats,
                            moves, panel, width, is_shiny, cry_playing,
                            s_mode, s_buf, msg):
    """Return a list of strings for the right info panel (one per row)."""
    lines = []
    # 1 row breathing
    lines.append("")

    # Header row: ◀ #003 VENUSAUR ▶ (arrows on the OUTER edges)
    has_prev = num != 1 and num != 0
    has_next = 0 < num < REAL_POKE_COUNT
    la = f"{FG_GRAY if has_prev else FG_DKGRAY}\u25c0"
    ra = f"{FG_GRAY if has_next else FG_DKGRAY}\u25b6"
    name_part = f"{BOLD}#{num:03d}  {dname.upper()}{RST}{BG_RED}{FG_WHITE}"
    if is_shiny:
        name_part += f" {FG_YLED}\u2605{FG_WHITE}"
    pad = max(1, width - _vl(la) - _vl(name_part) - _vl(ra) - 2)
    lines.append(f"{la}  {name_part}{' ' * pad}{ra}")

    # Genus
    if genus:
        lines.append(f"{FG_GRAY}{genus}{FG_WHITE}")
    else:
        lines.append("")

    lines.append("")  # breathing

    # Type badges + status icons
    if types:
        badges = []
        for t in types[:2]:
            col = TYPE_COLORS.get(t.lower(), (120, 120, 120))
            bg_t = f"\033[48;2;{col[0]};{col[1]};{col[2]}m"
            fg_t = "\033[38;2;255;255;255m"
            badges.append(
                f"{bg_t}{fg_t}{BOLD} {TYPE_ES.get(t.lower(), t.upper()):<7s}{RST}{BG_RED}{FG_WHITE}")
        types_str = "  ".join(badges)
    else:
        types_str = f"{FG_DKGRAY}(sin tipos){FG_WHITE}"

    icons = []
    if cry_playing:
        glyph = "\u266a\u266b\u266c"[int(time.time() * 4) % 3]
        bar_steps = "\u2581\u2583\u2585\u2587\u2588"
        eq = "".join(
            bar_steps[int(time.time() * (6 + i)) % len(bar_steps)]
            for i in range(3))
        icons.append(f"{FG_CYAN}{glyph}{FG_GRAY}{eq}")
    if is_shiny:
        icons.append(f"{FG_YLED}\u2605")
    if AUDIO_MUTED:
        icons.append(f"{FG_GRAY}\u00d7")
    if num in STATS.get("caught_safari", []):
        icons.append(f"{FG_GLED}\u25cf")
    icon_str = " ".join(icons) if icons else ""

    pad = max(1, width - _vl(types_str) - _vl(icon_str))
    lines.append(f"{types_str}{' ' * pad}{icon_str}")

    lines.append("")  # breathing

    # Separator
    lines.append(f"{FG_DKGRAY}{'─' * width}{FG_WHITE}")

    # Panel content
    if panel == "stats" and stats:
        order = [("HP", "hp"), ("ATK", "attack"), ("DEF", "defense"),
                 ("SPA", "special-attack"), ("SPD", "special-defense"),
                 ("SPE", "speed")]
        bar_w = max(8, width - 14)
        for label, key in order:
            val = stats.get(key, 0)
            bar = _stat_bar(val, 255, min(bar_w, 16))
            lines.append(f"{FG_GRAY}{label:<4s}{bar} {val:>3d}{FG_WHITE}")
        bst = sum(stats.get(k, 0) for _, k in order)
        if bst >= 600:
            bst_color = FG_GLED
        elif bst >= 480:
            bst_color = FG_YLED
        elif bst >= 350:
            bst_color = FG_GRAY
        else:
            bst_color = FG_RLED
        lines.append("")
        lines.append(f"{FG_GRAY}BST  {bst_color}{BOLD}{bst}{RST}{BG_RED}{FG_WHITE}")
    elif panel == "moves" and moves:
        for mv in moves[:10]:
            lvl, mv_name = mv
            lines.append(f"{FG_GRAY}Lv{lvl:>2d}  {mv_name}{FG_WHITE}")
    else:
        # Description: wrap to width
        wrapped = textwrap.wrap(desc, width) if desc else ["Sin datos."]
        for dl in wrapped[:10]:
            lines.append(f"{FG_GRAY}{dl}{FG_WHITE}")

    lines.append("")  # breathing

    # Evolution chain
    if evo_chain:
        parts = []
        for (enum, ename, is_current) in evo_chain:
            dn_e = _dn(ename)
            if is_current:
                parts.append(f"{FG_YLED}{BOLD}[{dn_e}]{RST}{BG_RED}{FG_WHITE}")
            else:
                parts.append(f"{FG_GRAY}{dn_e}")
        sep_arrow = f" {FG_DKGRAY}\u2192{FG_WHITE} "
        evo_str = sep_arrow.join(parts)
        lines.append(f"{FG_GRAY}Evo:{FG_WHITE} {evo_str}")

    return lines


def _draw_detail_side(my, mx, num, dname, genus, desc, spr_lines, s_mode, s_buf, msg,
                     extra=None, breath_offset=0, is_shiny=False, cry_playing=False):
    """Side-by-side detail view.

    Layout (top→bottom):
      heavy_top (1)
      lights    (1)
      pad       (1)
      body_h rows: each row composes
        [3 left margin] [green screen cell] [2 gap] [right panel cell] [3 right margin]
        The green screen has its own ╭─╮│╰─╯ borders and contains the sprite.
        The right panel has no border, just text on red bg.
      ctrl_a (1)
      ctrl_b (1)
      heavy_bot (1)
    """
    extra = extra or {}
    types = extra.get("types", []) or []
    stats = extra.get("stats", {}) or {}
    evo_chain = extra.get("evolution", []) or []
    moves = extra.get("moves", []) or []
    panel = extra.get("panel", "desc")

    g = _detail_side_geom(my, mx)
    yo, dx, inn = g["yo"], g["dx"], g["inn"]
    body_h = g["body_h"]
    right_w, gap = g["right_w"], g["gap"]
    gs_inner_w = g["gs_inner_w"]
    sprite_area = g["sprite_area"]

    # Sprite vertical positioning
    sh = len(spr_lines) if spr_lines else 0
    spt = max(0, (sprite_area - sh) // 2) + breath_offset
    if spt < 0:
        spt = 0
    spr_w = max((_vl(s) for s in spr_lines), default=0) if spr_lines else 0

    # Pre-build right panel content lines (already styled, fits in right_w)
    right_lines = _build_side_panel_lines(
        num, dname, genus, types, desc, evo_chain, stats, moves, panel,
        right_w, is_shiny, cry_playing, s_mode, s_buf, msg)
    if len(right_lines) > body_h:
        right_lines = right_lines[:body_h]
    while len(right_lines) < body_h:
        right_lines.append("")

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    # === Top chrome ===
    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1

    # === Body: left (green screen) + right (info panel) ===
    for i in range(body_h):
        # Build left half (green screen with curved borders + interior)
        if i == 0:
            gs_part = f"{FG_SCRHI}\u256d{'─' * gs_inner_w}\u256e"
        elif i == body_h - 1:
            gs_part = f"{FG_SCRHI}\u2570{'─' * gs_inner_w}\u256f"
        else:
            interior_idx = i - 1  # 0..gs_inner_h-1
            si = interior_idx - spt
            if spr_lines and 0 <= si < sh:
                sl = spr_lines[si]
                slw = _vl(sl)
                lp = max(0, (gs_inner_w - slw) // 2)
                rp = max(0, gs_inner_w - lp - slw)
                inside = f"{' ' * lp}{sl}{BG_SCR}{' ' * rp}"
            elif interior_idx == sprite_area and spr_lines:
                shadow_w = max(4, spr_w - 2)
                lp = max(0, (gs_inner_w - shadow_w) // 2)
                rp = max(0, gs_inner_w - lp - shadow_w)
                inside = f"{' ' * lp}{FG_SCRHI}{'▁' * shadow_w}{BG_SCR}{' ' * rp}"
            else:
                inside = f"{' ' * gs_inner_w}"
            gs_part = (f"{FG_SCRHI}\u2502{BG_SCR}{inside}"
                       f"{BG_RED}{FG_SCRHI}\u2502")

        # Right panel line (already styled, may have trailing slack)
        right_text = right_lines[i]
        right_pad = max(0, right_w - _vl(right_text))
        right_part = f"{right_text}{' ' * right_pad}"

        # Compose: left_margin (3) + green_screen + gap (2) + right_panel + right_margin (3)
        content = f"   {gs_part}{' ' * gap}{right_part}   "
        # _cas wraps content with red `║` borders, padding any slack to inn
        at(row, _cas(inn, content[3:-3] if False else content))
        # Note: passing the full content (with margins) — _cas does its own
        # padding so that's fine; the leading/trailing spaces become the
        # inn-internal margin.
        row += 1

    # === Bottom chrome: ctrl rows (one combined row + spacer) ===
    sep = f" {FG_DKGRAY}\u00b7{FG_WHITE} "
    if s_mode:
        ctrl_a = f"   Buscar: {s_buf}\u2588 {FG_GRAY}(Enter=ir, Esc=cancelar){FG_WHITE}"
        ctrl_b = ""
    elif msg:
        ctrl_a = f"   {FG_YLED}{msg}{FG_WHITE}"
        ctrl_b = ""
    else:
        panel_hint = {"desc": "Desc", "stats": "Stats", "moves": "Moves"}.get(panel, "Desc")
        # Wide layout: a single dense ctrl row + one spacer
        ctrl_a = (f"  {FG_GRAY}\u25c0\u25b6{FG_WHITE} Nav"
                  f"{sep}{FG_GRAY}/{FG_WHITE} Buscar"
                  f"{sep}{FG_GRAY}c{FG_WHITE} Cry"
                  f"{sep}{FG_GRAY}v{FG_WHITE} Voz"
                  f"{sep}{FG_GRAY}n{FG_WHITE} {panel_hint}"
                  f"{sep}{FG_GRAY}s{FG_WHITE} Shiny"
                  f"{sep}{FG_GRAY}G{FG_WHITE} Sprite"
                  f"{sep}{FG_GRAY}p{FG_WHITE} Tema"
                  f"{sep}{FG_GRAY}m{FG_WHITE} Mute"
                  f"{sep}{FG_GRAY}?{FG_WHITE} Ayuda"
                  f"{sep}{FG_GRAY}Esc{FG_WHITE} Lista")
        ctrl_b = ""
    at(row, _cas(inn, ctrl_a)); row += 1
    at(row, _cas(inn, ctrl_b)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def _draw_detail_stacked(my, mx, num, dname, genus, desc, spr_lines, s_mode, s_buf, msg,
                         extra=None, breath_offset=0, is_shiny=False, cry_playing=False):
    """Render the Pokedex detail view with a stable row layout.

    The green screen interior is now reserved entirely for the sprite (plus
    a single shadow row at the bottom). Everything else — name, genus, type
    badges, icons, description, evolution, controls — lives BELOW the screen,
    so the Pokemon can be displayed at its largest pixel-perfect size that
    fits the terminal.
    """
    _, inn, mrg, sw, dx = _geom(mx)

    extra = extra or {}
    types = extra.get("types", []) or []
    stats = extra.get("stats", {}) or {}
    evo_chain = extra.get("evolution", []) or []
    moves = extra.get("moves", []) or []
    panel = extra.get("panel", "desc")

    dw_text = inn - 8
    desc_wrapped = textwrap.wrap(desc, dw_text) if desc else []

    PANEL_ROWS = 3
    EVO_ROWS = 1
    CTRL_ROWS = 2

    # Below-screen stack: scr_bot(1) + pad(1) + name(1) + types(1) + sep(1)
    # + panel(3) + evo(1) + ctrl(2) + heavy_bot(1) = 12 rows.
    fixed = 4 + 1 + 1 + 1 + 1 + 1 + PANEL_ROWS + EVO_ROWS + CTRL_ROWS + 1
    scr_h = max(8, my - fixed)
    total = fixed + scr_h
    yo = max(0, (my - total) // 2)

    # Inside the screen: sprite area + 1 shadow row.
    sprite_area = scr_h - 1
    sh = len(spr_lines) if spr_lines else 0
    spt = max(0, (sprite_area - sh) // 2) + breath_offset
    if spt < 0:
        spt = 0

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    # ── Sprite area + shadow (the screen is JUST this) ────────────────
    for i in range(sprite_area):
        si = i - spt
        if spr_lines and 0 <= si < sh:
            sl = spr_lines[si]
            slw = _vl(sl)
            lp = max(0, (sw - slw) // 2)
            at(row, _scr_row(inn, mrg, sw, f"{' ' * lp}{sl}{BG_SCR}"))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    if spr_lines:
        spr_w = max((_vl(s) for s in spr_lines), default=0)
        shadow_w = max(4, spr_w - 2)
        lp = max(0, (sw - shadow_w) // 2)
        shadow = f"{FG_SCRHI}{'▁' * shadow_w}"
        at(row, _scr_row(inn, mrg, sw, f"{' ' * lp}{shadow}"))
    else:
        at(row, _scr_row(inn, mrg, sw))
    row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    # ── Name row (BELOW the screen) ────────────────────────────────────
    has_prev = num != 1 and num != 0
    has_next = 0 < num < REAL_POKE_COUNT
    la = f"{FG_GRAY if has_prev else FG_DKGRAY}\u25c0"
    ra = f"{FG_GRAY if has_next else FG_DKGRAY}\u25b6"
    name_label = f"{BOLD}#{num:03d}  {dname.upper()}{RST}{BG_RED}{FG_WHITE}"
    if is_shiny:
        name_label += f" {FG_YLED}\u2605{FG_WHITE}"
    genus_label = f"{FG_GRAY}{genus}{FG_WHITE}" if genus else ""
    left = f"  {la}  {name_label}"
    right = f"{genus_label}  {ra}  "
    pad = max(1, inn - _vl(left) - _vl(right))
    at(row, _cas(inn, f"{left}{' ' * pad}{right}")); row += 1

    # ── Types + status icons row (BELOW the screen) ───────────────────
    if types:
        badges = []
        for t in types[:2]:
            col = TYPE_COLORS.get(t.lower(), (120, 120, 120))
            bg_t = f"\033[48;2;{col[0]};{col[1]};{col[2]}m"
            fg = "\033[38;2;255;255;255m"
            badges.append(
                f"{bg_t}{fg}{BOLD} {TYPE_ES.get(t.lower(), t.upper()):<7s}{RST}{BG_RED}{FG_WHITE}")
        types_str = "  ".join(badges)
    else:
        types_str = f"{FG_DKGRAY}(sin tipos)"

    icons = []
    if cry_playing:
        glyph = "\u266a\u266b\u266c"[int(time.time() * 4) % 3]
        bar_steps = "\u2581\u2583\u2585\u2587\u2588"
        eq = "".join(
            bar_steps[int(time.time() * (6 + i)) % len(bar_steps)]
            for i in range(3))
        icons.append(f"{FG_CYAN}{glyph}{FG_GRAY}{eq}")
    if is_shiny:
        icons.append(f"{FG_YLED}\u2605")
    if AUDIO_MUTED:
        icons.append(f"{FG_GRAY}\u00d7")
    if num in STATS.get("caught_safari", []):
        icons.append(f"{FG_GLED}\u25cf")
    icon_str = " ".join(icons) if icons else " "
    types_left = f"   {types_str}"
    icons_right = f"{icon_str}   "
    pad = max(1, inn - _vl(types_left) - _vl(icons_right))
    at(row, _cas(inn, f"{types_left}{' ' * pad}{icons_right}")); row += 1

    # ── Separator ──────────────────────────────────────────────────────
    at(row, _cas(inn, f"    {FG_DKGRAY}{'─' * (inn - 8)}{FG_WHITE}    ")); row += 1

    # ── Panel rows (always exactly PANEL_ROWS) ─────────────────────────
    panel_lines = []
    if panel == "desc":
        lines = desc_wrapped[:PANEL_ROWS] if desc_wrapped else ["Sin datos."]
        for dl in lines:
            panel_lines.append(f"    {FG_GRAY}{dl}{FG_WHITE}")
    elif panel == "stats" and stats:
        order = [("HP", "hp"), ("ATK", "attack"), ("DEF", "defense"),
                 ("SPA", "special-attack"), ("SPD", "special-defense"),
                 ("SPE", "speed")]
        for row_start in (0, 3):
            cells = []
            for j in range(3):
                label, key = order[row_start + j]
                val = stats.get(key, 0)
                bar = _stat_bar(val, 255, 8)
                cells.append(f"{label:<4s}{bar} {val:>3d}")
            panel_lines.append("  " + "  ".join(cells))
        bst = sum(stats.get(k, 0) for _, k in order)
        if bst >= 600:
            bst_color = FG_GLED
        elif bst >= 480:
            bst_color = FG_YLED
        elif bst >= 350:
            bst_color = FG_GRAY
        else:
            bst_color = FG_RLED
        panel_lines.append(
            f"   {FG_GRAY}BST {bst_color}{BOLD}{bst}{RST}{BG_RED}{FG_WHITE}")
    elif panel == "moves" and moves:
        shown = moves[:6]
        for i in range(0, len(shown), 2):
            a = shown[i]
            b = shown[i + 1] if i + 1 < len(shown) else None
            line_a = f"Lv{a[0]:>2d}  {a[1]:<14s}"
            line_b = f"Lv{b[0]:>2d}  {b[1]:<14s}" if b else ""
            panel_lines.append(f"   {FG_GRAY}{line_a}   {line_b}{FG_WHITE}")
    else:
        panel_lines.append(f"   {FG_GRAY}(sin datos)")
    while len(panel_lines) < PANEL_ROWS:
        panel_lines.append("")
    for line in panel_lines[:PANEL_ROWS]:
        at(row, _cas(inn, line)); row += 1

    # ── Evolution chain ────────────────────────────────────────────────
    if evo_chain:
        parts = []
        for (enum, ename, is_current) in evo_chain:
            dn_e = _dn(ename)
            if is_current:
                parts.append(f"{FG_YLED}{BOLD}[{dn_e}]{RST}{BG_RED}{FG_WHITE}")
            else:
                parts.append(f"{FG_GRAY}{dn_e}")
        sep = f" {FG_DKGRAY}\u2192{FG_WHITE} "
        evo_str = sep.join(parts)
        at(row, _cas(inn, f"   {FG_GRAY}Evo: {evo_str}{FG_WHITE}")); row += 1
    else:
        at(row, _cas(inn)); row += 1

    # ── Controls (two rows, grouped) ───────────────────────────────────
    sep = f" {FG_DKGRAY}\u00b7{FG_WHITE} "
    if s_mode:
        ctrl_a = f"   Buscar: {s_buf}\u2588 {FG_GRAY}(Enter=ir, Esc=cancelar){FG_WHITE}"
        ctrl_b = ""
    elif msg:
        ctrl_a = f"   {FG_YLED}{msg}{FG_WHITE}"
        ctrl_b = ""
    else:
        panel_hint = {"desc": "Desc", "stats": "Stats", "moves": "Moves"}.get(panel, "Desc")
        ctrl_a = (f"  {FG_GRAY}\u25c0\u25b6{FG_WHITE} Nav"
                  f"{sep}{FG_GRAY}/{FG_WHITE} Buscar"
                  f"{sep}{FG_GRAY}c{FG_WHITE} Cry"
                  f"{sep}{FG_GRAY}v{FG_WHITE} Voz"
                  f"{sep}{FG_GRAY}Esc{FG_WHITE} Lista")
        ctrl_b = (f"  {FG_GRAY}n{FG_WHITE} {panel_hint}"
                  f"{sep}{FG_GRAY}s{FG_WHITE} Shiny"
                  f"{sep}{FG_GRAY}G{FG_WHITE} Sprite"
                  f"{sep}{FG_GRAY}p{FG_WHITE} Tema"
                  f"{sep}{FG_GRAY}m{FG_WHITE} Mute"
                  f"{sep}{FG_GRAY}?{FG_WHITE} Ayuda")
    at(row, _cas(inn, ctrl_a)); row += 1
    at(row, _cas(inn, ctrl_b)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


# ── Quiz mode renderer ─────────────────────────────────────────────────────

def draw_quiz_menu(my, mx, menu_cursor, game_mode):
    """Draw quiz mode selection screen with game type toggle."""
    _, inn, mrg, sw, dx = _geom(mx)

    n_opts = len(QUIZ_OPTIONS)
    scr_h = max(4, my - 8)
    total_h = 8 + scr_h
    yo = max(0, (my - total_h) // 2)

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    # Content: title + mode toggle + blank + count options
    content_h = 4 + n_opts  # title + mode + blank + options
    pad_top = max(0, (scr_h - content_h) // 2)

    gm_label = GAME_MODES[game_mode]
    mode_line = f"  {FG_SCRHI}\u25c0 {FG_SCRTXT}{BOLD}{gm_label}{RST}{BG_SCR}{FG_SCRHI} \u25b6"

    for i in range(scr_h):
        ci = i - pad_top
        if ci == 0:
            title = f"{FG_SCRTXT}{BOLD}  Who's that Pokemon?"
            at(row, _scr_row(inn, mrg, sw, title))
        elif ci == 1:
            at(row, _scr_row(inn, mrg, sw))
        elif ci == 2:
            at(row, _scr_row(inn, mrg, sw, mode_line))
        elif ci == 3:
            at(row, _scr_row(inn, mrg, sw))
        elif 4 <= ci < 4 + n_opts:
            oi = ci - 4
            n = QUIZ_OPTIONS[oi]
            label = (f"{n} Pokemon" if n < REAL_POKE_COUNT
                     else f"Toda la Pokedex ({REAL_POKE_COUNT})")
            if oi == menu_cursor:
                text = f"  {FG_SCRTXT}{BOLD} \u25b6 {label}{RST}{BG_SCR}"
            else:
                text = f"    {FG_SCRHI}{label}{RST}{BG_SCR}"
            at(row, _scr_row(inn, mrg, sw, text))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    ctrl = (f"   {FG_GRAY}\u25c0 \u25b6{FG_WHITE} Modo  "
            f"{FG_DKGRAY}\u2502{FG_WHITE} "
            f"{FG_GRAY}\u25b2\u25bc{FG_WHITE} Cantidad  "
            f"{FG_DKGRAY}\u2502{FG_WHITE} "
            f"Enter Comenzar  "
            f"{FG_DKGRAY}\u2502{FG_WHITE} "
            f"{FG_GRAY}Esc{FG_WHITE} Volver")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def draw_quiz(my, mx, spr_lines, phase, input_buf, answer, score, current, total_q,
              desc_text=None, types_list=None):
    """Quiz renderer.

    In the ask phase, by default shows the (silhouette) sprite. If `desc_text`
    is provided, renders the flavor text wrapped inside the screen. If
    `types_list` is provided, renders coloured type badges. Reveal phases
    always show the full sprite.
    """
    _, inn, mrg, sw, dx = _geom(mx)

    fixed = 11
    scr_h = max(3, my - fixed)
    total_h = fixed + scr_h
    yo = max(0, (my - total_h) // 2)

    sh = len(spr_lines) if spr_lines else 0
    spt = max(0, (scr_h - sh) // 2)

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    # Decide screen content
    use_desc = (phase == "ask" and desc_text)
    use_types = (phase == "ask" and types_list and not desc_text)

    if use_desc:
        # Render wrapped flavor text inside the screen
        inner_w = sw - 4
        wrapped = textwrap.wrap(desc_text, inner_w) if desc_text else []
        wrapped = wrapped[: max(1, scr_h - 2)]
        pad_top = max(0, (scr_h - len(wrapped)) // 2)
        for i in range(scr_h):
            ci = i - pad_top
            if 0 <= ci < len(wrapped):
                line = f"  {FG_SCRTXT}{wrapped[ci]}"
                at(row, _scr_row(inn, mrg, sw, line))
            else:
                at(row, _scr_row(inn, mrg, sw))
            row += 1
    elif use_types:
        # Coloured type badges centered
        badges = []
        for t in types_list[:2]:
            col = TYPE_COLORS.get(t.lower(), (120, 120, 120))
            bg = f"\033[48;2;{col[0]};{col[1]};{col[2]}m"
            fg = "\033[38;2;255;255;255m"
            badges.append(f"{bg}{fg}{BOLD}  {TYPE_ES.get(t.lower(), t.upper())}  {RST}{BG_SCR}")
        badge_line = "   ".join(badges)
        hint = f"{FG_SCRTXT}{BOLD}Adivina este Pokemon..."
        bv = _vl(badge_line)
        hv = _vl(hint)
        lb = max(0, (sw - bv) // 2)
        lh = max(0, (sw - hv) // 2)
        mid = scr_h // 2
        for i in range(scr_h):
            if i == mid - 1:
                at(row, _scr_row(inn, mrg, sw, f"{' ' * lh}{hint}"))
            elif i == mid + 1:
                at(row, _scr_row(inn, mrg, sw, f"{' ' * lb}{badge_line}"))
            else:
                at(row, _scr_row(inn, mrg, sw))
            row += 1
    else:
        for i in range(scr_h):
            si = i - spt
            if spr_lines and 0 <= si < sh:
                sl = spr_lines[si]
                slw = _vl(sl)
                lp = max(0, (sw - slw) // 2)
                at(row, _scr_row(inn, mrg, sw, f"{' ' * lp}{sl}{BG_SCR}"))
            else:
                at(row, _scr_row(inn, mrg, sw))
            row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    # Progress + score
    progress = f"{FG_GRAY}{current}/{total_q}  \u2714 {score}{FG_WHITE}"
    if phase == "ask":
        prompt = f"   {BOLD}Quien es este Pokemon?{RST}{BG_RED}{FG_WHITE}"
        pv = _vl(prompt)
        sv = _vl(progress)
        at(row, _cas(inn, f"{prompt}{' ' * max(1, inn - pv - sv - 3)}{progress}   "))
        row += 1
        inp = f"   > {input_buf}\u2588"
        at(row, _cas(inn, inp))
    elif phase == "correct":
        result = f"   \033[38;2;75;225;75m\u2714 Correcto! {answer}{RST}{BG_RED}{FG_WHITE}"
        rv = _vl(result)
        sv = _vl(progress)
        at(row, _cas(inn, f"{result}{' ' * max(1, inn - rv - sv - 3)}{progress}   "))
        row += 1
        at(row, _cas(inn))
    else:  # wrong
        result = f"   \033[38;2;255;65;65m\u2718 Era {answer}!{RST}{BG_RED}{FG_WHITE}"
        rv = _vl(result)
        sv = _vl(progress)
        at(row, _cas(inn, f"{result}{' ' * max(1, inn - rv - sv - 3)}{progress}   "))
        row += 1
        at(row, _cas(inn))
    row += 1

    at(row, _cas(inn)); row += 1

    # Controls
    if phase == "ask":
        if desc_text:
            ctrl = (f"   {FG_GRAY}Enter{FG_WHITE} OK  "
                    f"{FG_DKGRAY}\u2502{FG_WHITE} "
                    f"{FG_GRAY}Tab/v{FG_WHITE} Voz  "
                    f"{FG_DKGRAY}\u2502{FG_WHITE} "
                    f"{FG_GRAY}Esc{FG_WHITE} Salir")
            if _vl(ctrl) > inn:
                ctrl = (f" {FG_GRAY}Enter{FG_WHITE} OK  "
                        f"{FG_GRAY}Tab/v{FG_WHITE} Voz  "
                        f"{FG_GRAY}Esc{FG_WHITE}")
        else:
            ctrl = (f"   {FG_GRAY}Enter{FG_WHITE} Confirmar  "
                    f"{FG_DKGRAY}\u2502{FG_WHITE} "
                    f"{FG_GRAY}Esc{FG_WHITE} Abandonar")
    else:
        ctrl = (f"   {FG_GRAY}Enter{FG_WHITE} Siguiente  "
                f"{FG_DKGRAY}\u2502{FG_WHITE} "
                f"{FG_GRAY}Esc{FG_WHITE} Abandonar")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def draw_quiz_end(my, mx, score, total_q):
    """Draw the end-of-quiz results screen."""
    _, inn, mrg, sw, dx = _geom(mx)

    scr_h = max(4, my - 8)
    total_h = 8 + scr_h
    yo = max(0, (my - total_h) // 2)

    # Rating
    pct = (score / total_q * 100) if total_q else 0
    if pct == 100:
        rating = "Maestro Pokemon!"
        rat_col = "\033[38;2;255;215;45m"
    elif pct >= 80:
        rating = "Entrenador experto!"
        rat_col = "\033[38;2;75;225;75m"
    elif pct >= 50:
        rating = "Buen intento!"
        rat_col = "\033[38;2;55;195;255m"
    else:
        rating = "Sigue entrenando..."
        rat_col = "\033[38;2;255;65;65m"

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    # Content lines to center
    lines = [
        f"{FG_SCRTXT}{BOLD}  Quiz completado!",
        "",
        f"{FG_SCRTXT}  Resultado: {score}/{total_q}  ({pct:.0f}%)",
        "",
        f"{rat_col}{BOLD}  {rating}",
    ]
    pad_top = max(0, (scr_h - len(lines)) // 2)

    for i in range(scr_h):
        ci = i - pad_top
        if 0 <= ci < len(lines):
            at(row, _scr_row(inn, mrg, sw, lines[ci]))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    ctrl = (f"   {FG_GRAY}Enter{FG_WHITE} Jugar de nuevo  "
            f"{FG_DKGRAY}\u2502{FG_WHITE} "
            f"{FG_GRAY}Esc{FG_WHITE} Volver a lista")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def draw_cry_quiz(my, mx, spr_lines, phase, input_buf, answer, score, current,
                   total_q):
    """Draw the cry quiz screen. Shows audio icon during ask, sprite on reveal."""
    _, inn, mrg, sw, dx = _geom(mx)

    fixed = 11
    scr_h = max(3, my - fixed)
    total_h = fixed + scr_h
    yo = max(0, (my - total_h) // 2)

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    if phase == "ask":
        # Show audio icon centered on green screen
        audio_art = [
            f"{FG_SCRHI}    \u266b   \u266a",
            f"{FG_SCRTXT}      )))",
            f"{FG_SCRTXT} {BOLD}\u25c9{RST}{BG_SCR}{FG_SCRTXT}  ))))",
            f"{FG_SCRTXT}      )))",
            f"{FG_SCRHI}    \u266a   \u266b",
            "",
            f"{FG_SCRTXT}  Escucha el cry...",
            f"{FG_SCRHI}  (Tab) para repetir",
        ]
        pad_top = max(0, (scr_h - len(audio_art)) // 2)
        for i in range(scr_h):
            ci = i - pad_top
            if 0 <= ci < len(audio_art):
                line = audio_art[ci]
                lw = _vl(line)
                lp = max(0, (sw - lw) // 2)
                at(row, _scr_row(inn, mrg, sw, f"{' ' * lp}{line}"))
            else:
                at(row, _scr_row(inn, mrg, sw))
            row += 1
    else:
        # Reveal: show the actual sprite
        sh = len(spr_lines) if spr_lines else 0
        spt = max(0, (scr_h - sh) // 2)
        for i in range(scr_h):
            si = i - spt
            if spr_lines and 0 <= si < sh:
                sl = spr_lines[si]
                slw = _vl(sl)
                lp = max(0, (sw - slw) // 2)
                at(row, _scr_row(inn, mrg, sw, f"{' ' * lp}{sl}{BG_SCR}"))
            else:
                at(row, _scr_row(inn, mrg, sw))
            row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    # Progress + score
    progress = f"{FG_GRAY}{current}/{total_q}  \u2714 {score}{FG_WHITE}"
    if phase == "ask":
        prompt = f"   {BOLD}Quien es este Pokemon?{RST}{BG_RED}{FG_WHITE}"
        pv = _vl(prompt)
        sv = _vl(progress)
        at(row, _cas(inn, f"{prompt}{' ' * max(1, inn - pv - sv - 3)}{progress}   "))
        row += 1
        inp = f"   > {input_buf}\u2588"
        at(row, _cas(inn, inp))
    elif phase == "correct":
        result = f"   \033[38;2;75;225;75m\u2714 Correcto! {answer}{RST}{BG_RED}{FG_WHITE}"
        rv = _vl(result)
        sv = _vl(progress)
        at(row, _cas(inn, f"{result}{' ' * max(1, inn - rv - sv - 3)}{progress}   "))
        row += 1
        at(row, _cas(inn))
    else:  # wrong
        result = f"   \033[38;2;255;65;65m\u2718 Era {answer}!{RST}{BG_RED}{FG_WHITE}"
        rv = _vl(result)
        sv = _vl(progress)
        at(row, _cas(inn, f"{result}{' ' * max(1, inn - rv - sv - 3)}{progress}   "))
        row += 1
        at(row, _cas(inn))
    row += 1

    at(row, _cas(inn)); row += 1

    if phase == "ask":
        ctrl = (f"   {FG_GRAY}Enter{FG_WHITE} Confirmar  "
                f"{FG_DKGRAY}\u2502{FG_WHITE} "
                f"{FG_GRAY}Tab{FG_WHITE} Repetir cry  "
                f"{FG_DKGRAY}\u2502{FG_WHITE} "
                f"{FG_GRAY}Esc{FG_WHITE} Abandonar")
    else:
        ctrl = (f"   {FG_GRAY}Enter{FG_WHITE} Siguiente  "
                f"{FG_DKGRAY}\u2502{FG_WHITE} "
                f"{FG_GRAY}Esc{FG_WHITE} Abandonar")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


# ── Safari Zone renderers ──────────────────────────────────────────────────

def draw_safari_entrance(my, mx):
    """Draw the Safari Zone welcome screen."""
    _, inn, mrg, sw, dx = _geom(mx)

    scr_h = max(4, my - 8)
    total_h = 8 + scr_h
    yo = max(0, (my - total_h) // 2)

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    lines = [
        f"{FG_SCRTXT}{BOLD}  Bienvenido a la",
        f"{FG_SCRTXT}{BOLD}  Zona Safari!",
        "",
        f"{FG_SCRHI}  Recibiras 30 Safari Ball.",
        f"{FG_SCRHI}  Lanza bolas para atrapar",
        f"{FG_SCRHI}  Pokemon salvajes.",
        "",
        f"{FG_SCRTXT}  Buena suerte, entrenador!",
    ]
    pad_top = max(0, (scr_h - len(lines)) // 2)

    for i in range(scr_h):
        ci = i - pad_top
        if 0 <= ci < len(lines):
            at(row, _scr_row(inn, mrg, sw, lines[ci]))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    ctrl = (f"   {FG_GRAY}Enter{FG_WHITE} Comenzar  "
            f"{FG_DKGRAY}\u2502{FG_WHITE} "
            f"{FG_GRAY}Esc{FG_WHITE} Volver")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def draw_safari_encounter(my, mx, spr_lines, balls, action_cursor, dname,
                          anger, eating, num=0, types=None, already_caught=False):
    """Safari Zone encounter, redesigned to match the new detail view.

    The green screen interior is reserved entirely for the sprite (plus a
    grass line at the bottom). All other info — Salvaje #NNN NAME, type
    badges, ball count, status — lives BELOW the screen so the wild Pokemon
    fills the device frame just like in the dex.
    """
    _, inn, mrg, sw, dx = _geom(mx)

    # Below-screen stack: scr_bot(1) + pad(1) + name(1) + types_balls(1)
    # + sep(1) + status(1) + actions(1) + pad(1) + ctrl(1) + heavy_bot(1)
    # = 10 rows. Above: heavy(1) + lights(1) + pad(1) + scr_top(1) = 4.
    fixed = 4 + 10
    scr_h = max(8, my - fixed)
    total_h = fixed + scr_h
    yo = max(0, (my - total_h) // 2)

    # Sprite area = scr_h - 1 (last row is the grass line)
    sprite_area = scr_h - 1
    sh = len(spr_lines) if spr_lines else 0
    spt = max(0, (sprite_area - sh) // 2)
    spr_w = max((_vl(sl) for sl in spr_lines), default=0) if spr_lines else 0

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    # ── Sprite area (the screen is JUST this) ──────────────────────────
    for i in range(sprite_area):
        si = i - spt
        if spr_lines and 0 <= si < sh:
            sl = spr_lines[si]
            slw = _vl(sl)
            lp = max(0, (sw - slw) // 2)
            at(row, _scr_row(inn, mrg, sw, f"{' ' * lp}{sl}{BG_SCR}"))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    # ── Grass line at the bottom of the screen ────────────────────────
    gw = min(max(16, spr_w + 8), sw - 4)
    glp = max(0, (sw - gw) // 2)
    grass = f"{FG_SCRHI}{'▁' * gw}"
    at(row, _scr_row(inn, mrg, sw, f"{' ' * glp}{grass}")); row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    # ── Name row ───────────────────────────────────────────────────────
    num_str = f"#{num:03d}" if num else "#???"
    name_label = f"{BOLD}Salvaje {num_str}  {dname.upper()}{RST}{BG_RED}{FG_WHITE}"
    name_left = f"   {name_label}"
    name_right = "   "
    pad = max(1, inn - _vl(name_left) - _vl(name_right))
    at(row, _cas(inn, f"{name_left}{' ' * pad}{name_right}")); row += 1

    # ── Types badges + ball counter row ───────────────────────────────
    if types:
        badges = []
        for t in types[:2]:
            col = TYPE_COLORS.get(t.lower(), (120, 120, 120))
            bg_t = f"\033[48;2;{col[0]};{col[1]};{col[2]}m"
            fg = "\033[38;2;255;255;255m"
            badges.append(
                f"{bg_t}{fg}{BOLD} {TYPE_ES.get(t.lower(), t.upper()):<7s}{RST}{BG_RED}{FG_WHITE}")
        types_str = "  ".join(badges)
    else:
        types_str = f"{FG_DKGRAY}(sin tipos)"

    right_icons = []
    if already_caught:
        right_icons.append(f"{FG_GLED}\u25cf")
    if AUDIO_MUTED:
        right_icons.append(f"{FG_GRAY}\u00d7")
    ri_str = " ".join(right_icons) + "  " if right_icons else ""
    ball_txt = f"{FG_YLED}{BOLD}\u25cf x{balls}{RST}{BG_RED}{FG_WHITE}"
    types_left = f"   {types_str}"
    ball_right = f"{ri_str}{ball_txt}   "
    pad = max(1, inn - _vl(types_left) - _vl(ball_right))
    at(row, _cas(inn, f"{types_left}{' ' * pad}{ball_right}")); row += 1

    # ── Separator ─────────────────────────────────────────────────────
    at(row, _cas(inn, f"    {FG_DKGRAY}{'─' * (inn - 8)}{FG_WHITE}    ")); row += 1

    # ── Status row (Furioso / Comiendo / blank) ────────────────────────
    if anger > 0:
        status_txt = f"   {FG_RLED}{BOLD}\u25cf Furioso!{FG_WHITE}"
    elif eating > 0:
        status_txt = f"   {FG_GLED}\u25cf Comiendo...{FG_WHITE}"
    else:
        status_txt = ""
    at(row, _cas(inn, status_txt)); row += 1

    # ── Action selector ────────────────────────────────────────────────
    actions = ["Bola", "Roca", "Cebo", "Huir"]
    parts = []
    for i, a in enumerate(actions):
        if i == action_cursor:
            parts.append(f"{FG_YLED}{BOLD}\u25b6 {a}{RST}{BG_RED}{FG_WHITE}")
        else:
            parts.append(f"{FG_GRAY}  {a}{FG_WHITE}")
    sel_line = f"   {'    '.join(parts)}"
    at(row, _cas(inn, sel_line)); row += 1

    at(row, _cas(inn)); row += 1

    sep = f" {FG_DKGRAY}\u00b7{FG_WHITE} "
    ctrl = (f"  {FG_GRAY}\u25c0\u25b6{FG_WHITE} Elegir"
            f"{sep}{FG_GRAY}Enter{FG_WHITE} Accion"
            f"{sep}{FG_GRAY}Esc{FG_WHITE} Abandonar")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def draw_safari_result(my, mx, spr_lines, result_msg, result_type, balls):
    """Draw the Safari Zone result screen (caught/fled/broke_free/out_of_balls)."""
    _, inn, mrg, sw, dx = _geom(mx)

    fixed = 10
    scr_h = max(3, my - fixed)
    total_h = fixed + scr_h
    yo = max(0, (my - total_h) // 2)

    show_sprite = result_type in ("caught", "info", "broke_free")
    sh = len(spr_lines) if spr_lines and show_sprite else 0
    spt = max(0, (scr_h - sh) // 2) if sh else 0

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    if show_sprite:
        for i in range(scr_h):
            si = i - spt
            if spr_lines and 0 <= si < sh:
                sl = spr_lines[si]
                slw = _vl(sl)
                lp = max(0, (sw - slw) // 2)
                at(row, _scr_row(inn, mrg, sw, f"{' ' * lp}{sl}{BG_SCR}"))
            else:
                at(row, _scr_row(inn, mrg, sw))
            row += 1
    else:
        # Empty screen with message centered
        msg_line = f"{FG_SCRTXT}{BOLD}  {result_msg}"
        mid = scr_h // 2
        for i in range(scr_h):
            if i == mid:
                at(row, _scr_row(inn, mrg, sw, msg_line))
            else:
                at(row, _scr_row(inn, mrg, sw))
            row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    # Result message below screen
    if result_type == "caught":
        msg_col = "\033[38;2;75;225;75m"
    elif result_type == "fled":
        msg_col = "\033[38;2;255;65;65m"
    elif result_type == "out_of_balls":
        msg_col = "\033[38;2;255;215;45m"
    else:
        msg_col = f"{FG_GRAY}"
    msg_display = f"   {msg_col}{result_msg}{RST}{BG_RED}{FG_WHITE}"
    at(row, _cas(inn, msg_display)); row += 1

    ctrl = f"   {FG_GRAY}Enter{FG_WHITE} Continuar"
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def draw_safari_end(my, mx, captured):
    """Draw the Safari Zone end/summary screen."""
    _, inn, mrg, sw, dx = _geom(mx)

    scr_h = max(4, my - 8)
    total_h = 8 + scr_h
    yo = max(0, (my - total_h) // 2)

    count = len(captured)
    if count >= 10:
        rating = "Maestro del Safari!"
        rat_col = "\033[38;2;255;215;45m"
    elif count >= 5:
        rating = "Gran aventura!"
        rat_col = "\033[38;2;75;225;75m"
    elif count >= 1:
        rating = "Buen intento!"
        rat_col = "\033[38;2;55;195;255m"
    else:
        rating = "Sigue intentando..."
        rat_col = "\033[38;2;255;65;65m"

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    lines = [
        f"{FG_SCRTXT}{BOLD}  Fin del Safari!",
        "",
        f"{FG_SCRTXT}  Pokemon capturados: {count}",
        "",
    ]
    for num, name in captured:
        lines.append(f"{FG_SCRHI}    \u25cf #{num:03d} {_dn(name)}")
    if not captured:
        lines.append(f"{FG_SCRHI}    (ninguno)")
    lines.append("")
    lines.append(f"{rat_col}{BOLD}  {rating}")

    pad_top = max(0, (scr_h - len(lines)) // 2)

    for i in range(scr_h):
        ci = i - pad_top
        if 0 <= ci < len(lines):
            at(row, _scr_row(inn, mrg, sw, lines[ci]))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    ctrl = (f"   {FG_GRAY}Enter{FG_WHITE} Jugar de nuevo  "
            f"{FG_DKGRAY}\u2502{FG_WHITE} "
            f"{FG_GRAY}Esc{FG_WHITE} Volver a lista")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


# ── Safari Zone animations ────────────────────────────────────────────────────
# All animations render within the green screen area of the Pokedex frame,
# computing bounds from _geom() so the red shell is never overwritten.

def _safari_scr_geom(my, mx):
    """Return green screen interior geometry for safari animations.

    Must match draw_safari_encounter's `fixed = 14` exactly so animations
    paint inside the same green box that the static frame uses.
    """
    _, inn, mrg, sw, dx = _geom(mx)
    scr_h = max(8, my - 14)
    total_h = 14 + scr_h
    yo = max(0, (my - total_h) // 2)
    scr_y = yo + 5          # first green content row  (1-indexed)
    scr_x = dx + mrg + 3    # first green content col  (1-indexed)
    return scr_y, scr_x, sw, scr_h


def _safari_clear_green(scr_y, scr_x, sw, scr_h, my, keep_header=False):
    """Fill the green screen interior with BG_SCR spaces.

    `keep_header` is kept as a kwarg for back-compat but no longer matters:
    the in-screen header was moved out, so animations clear the full area.
    """
    start = 1 if keep_header else 0
    for i in range(start, scr_h):
        r = scr_y + i
        if 1 <= r <= my:
            sys.stdout.write(f"\033[{r};{scr_x}H{BG_SCR}{' ' * sw}")


def _clip_rendered_line(line, visible_col, x_left, x_right):
    """Return (clipped_line, first_visible_col) for a pre-rendered ANSI line.

    Drops any visible chars whose column falls outside [x_left, x_right).
    ANSI colour codes for clipped chars are discarded (render_sprite emits
    self-contained (fg + bg + glyph) triples per cell, so dropping one cell's
    codes doesn't leak style into the next).

    Returns (None, None) when the line is entirely outside the window.
    """
    if not line:
        return None, None
    out = []
    cur = visible_col
    first_visible = None
    i = 0
    n = len(line)
    while i < n:
        m = ANSI_RE.match(line, i)
        if m:
            # Buffer any ANSI codes; only flush when the next visible char
            # is actually kept.
            out.append(("ansi", m.group()))
            i = m.end()
            continue
        ch = line[i]
        i += 1
        cp = ord(ch)
        if cp >= 0x10000:
            w = 2
        elif 0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0xA4CF or \
             0xAC00 <= cp <= 0xD7A3 or 0xF900 <= cp <= 0xFAFF or \
             0xFE30 <= cp <= 0xFE4F or 0xFF00 <= cp <= 0xFF60:
            w = 2
        else:
            w = 1
        if cur >= x_right:
            break  # rest is off the right edge
        if cur + w > x_left:
            if first_visible is None:
                first_visible = cur
            out.append(("ch", ch))
        else:
            # Dropped char: the ANSI buffered before it is also discarded,
            # since it belonged to that cell's styling.
            out = [p for p in out if p[0] != "ansi"]
        cur += w
    if first_visible is None:
        return None, None
    # Strip trailing ANSI (unnecessary, no following glyph)
    while out and out[-1][0] == "ansi":
        out.pop()
    return "".join(v for _, v in out), first_visible


def _safari_blit(lines, row, col, my, clip_x_left=None, clip_x_right=None):
    """Draw pre-rendered half-block lines at absolute terminal position.

    If clip_x_left/clip_x_right are provided, each line is clipped so that
    no visible cell falls outside [clip_x_left, clip_x_right). This is how
    slide-in / flee animations avoid painting on top of the red pokédex
    shell when the sprite travels past the green screen edge.
    """
    if not lines:
        return
    for i, line in enumerate(lines):
        r = row + i
        if not (1 <= r <= my):
            continue
        if clip_x_left is None and clip_x_right is None:
            sys.stdout.write(f"\033[{r};{col}H{line}")
            continue
        xl = clip_x_left if clip_x_left is not None else 1
        xr = clip_x_right if clip_x_right is not None else 10_000
        clipped, start = _clip_rendered_line(line, col, xl, xr)
        if clipped is not None:
            sys.stdout.write(f"\033[{r};{start}H{clipped}")


SAFARI_HEADER_H = 1  # must match the header row count in draw_safari_encounter


def _safari_spr_pos(spr_lines, scr_y, scr_x, sw, scr_h):
    """Return (spr_row, spr_col) for the sprite inside the green screen.

    The new layout puts only the grass line in the last row, so the sprite
    has scr_h - 1 rows centered vertically.
    """
    sh = len(spr_lines) if spr_lines else 0
    spr_w = max((_vl(sl) for sl in spr_lines), default=0) if spr_lines else 0
    avail = scr_h - 1
    spt = max(0, (avail - sh) // 2)
    spr_row = scr_y + spt
    spr_col = scr_x + max(0, (sw - spr_w) // 2)
    return spr_row, spr_col


def _smoothstep(t):
    """Ease in/out curve for smoother motion (t in [0, 1])."""
    return t * t * (3.0 - 2.0 * t)


def _safari_grass_row(scr_y, scr_h, spr_lines):
    """Grass sits in the last row of the green screen interior."""
    return scr_y + scr_h - 1


def _safari_draw_grass(my, mx, spr_lines):
    """Draw the ground line, anchored under the sprite when present."""
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    spr_w = max((_vl(sl) for sl in spr_lines), default=0) if spr_lines else 0
    gw = min(spr_w + 8, sw - 4) if spr_w else min(sw - 4, 24)
    gw = max(gw, 6)
    glp = max(0, (sw - gw) // 2)
    r = _safari_grass_row(scr_y, scr_h, spr_lines)
    if 1 <= r <= my:
        sys.stdout.write(
            f"\033[{r};{scr_x + glp}H{BG_SCR}{FG_SCRHI}{'▁' * gw}")


def _safari_anim_appear(my, mx, spr_lines):
    """Wild Pokemon slides in from the right of the screen.

    The sprite starts past the right edge of the green area; every frame is
    blitted with a hard clip to [scr_x, scr_x + sw) so the parts that are
    still "off-stage" never bleed onto the red pokédex shell.
    """
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    if not spr_lines:
        return
    spr_row, final_col = _safari_spr_pos(spr_lines, *g)

    frames = 8
    start_col = scr_x + sw  # fully off-screen right
    x_right = scr_x + sw    # exclusive right clip bound
    for f in range(frames):
        t = _smoothstep(f / max(1, frames - 1))
        col = int(start_col + (final_col - start_col) * t)
        _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
        _safari_draw_grass(my, mx, spr_lines)
        _safari_blit(spr_lines, spr_row, col, my,
                     clip_x_left=scr_x, clip_x_right=x_right)
        sys.stdout.write(RST)
        sys.stdout.flush()
        time.sleep(0.045)


def _safari_anim_throw(my, mx, spr_lines, item_lines):
    """Arc an item from the bottom-left corner toward the Pokemon, eased."""
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    if not item_lines:
        return
    spr_row, spr_col = _safari_spr_pos(spr_lines, *g)
    sh = len(spr_lines) if spr_lines else 0
    spr_w = max((_vl(sl) for sl in spr_lines), default=6) if spr_lines else 6
    iw = max((_vl(bl) for bl in item_lines), default=0)
    ih = len(item_lines)

    _sfx("Pop")

    start_y = scr_y + scr_h - ih - 2
    start_x = scr_x + 1
    end_y = spr_row + sh // 2 - ih // 2
    end_x = spr_col + spr_w // 2 - iw // 2

    frames = 14
    for f in range(frames):
        lin = f / max(1, frames - 1)
        t = _smoothstep(lin)
        cur_x = int(start_x + (end_x - start_x) * t)
        arc = math.sin(math.pi * lin) * min(5, scr_h // 2 - 1)
        cur_y = int(start_y + (end_y - start_y) * t - arc)

        _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
        _safari_draw_grass(my, mx, spr_lines)
        _safari_blit(spr_lines, spr_row, spr_col, my)
        _safari_blit(item_lines, cur_y, cur_x, my)
        sys.stdout.write(RST)
        sys.stdout.flush()
        time.sleep(0.04)


def _safari_anim_absorb(my, mx, spr_lines, sil_lines, ball_lines):
    """Gen-1 style capture sequence.

    1. Bright red beam travels from the ball on the ground up to the Pokemon.
    2. Pokemon flashes into a white silhouette (it's been "scanned").
    3. Silhouette squishes vertically → shrink illusion.
    4. Reduces to a single glowing dot.
    5. Ball snaps shut over the dot.
    """
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    if not spr_lines:
        return
    spr_row, spr_col = _safari_spr_pos(spr_lines, *g)
    sh = len(spr_lines)
    spr_w = max((_vl(sl) for sl in spr_lines), default=6)
    bh = len(ball_lines) if ball_lines else 0
    bw = max((_vl(bl) for bl in ball_lines), default=0) if ball_lines else 0

    # Beam endpoints: ball mid (bottom-left of screen-ish) → pokemon center
    ball_from_y = scr_y + scr_h - 3  # where the thrown ball is roughly
    ball_from_x = spr_col + spr_w // 2
    target_y = spr_row + sh // 2
    target_x = spr_col + spr_w // 2

    # 1) Red beam animation (3 frames)
    _sfx("Funk", rate=1.4, volume=0.6)  # energy crackle
    for step in range(3):
        _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
        _safari_draw_grass(my, mx, spr_lines)
        _safari_blit(spr_lines, spr_row, spr_col, my)
        # Draw a vertical-ish beam from ball to pokemon, dotted for motion
        steps = 10
        for i in range(steps):
            t = (i + (step * 0.33)) / steps
            if t > 1:
                t -= 1
            by = int(ball_from_y + (target_y - ball_from_y) * t)
            bx = int(ball_from_x + (target_x - ball_from_x) * t)
            if scr_y <= by < scr_y + scr_h - 2 and scr_x <= bx < scr_x + sw:
                glyph = "●" if i % 2 == 0 else "◦"
                sys.stdout.write(
                    f"\033[{by};{bx}H{BG_SCR}\033[38;2;255;80;80m{BOLD}{glyph}")
        sys.stdout.write(RST); sys.stdout.flush()
        time.sleep(0.05)

    _sfx("Tink", rate=1.3)

    # 2) Pokemon becomes a white silhouette (beam hit)
    _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
    _safari_draw_grass(my, mx, spr_lines)
    _safari_blit(sil_lines or spr_lines, spr_row, spr_col, my)
    sys.stdout.write(RST); sys.stdout.flush()
    time.sleep(0.12)

    # 3) Silhouette squished (shrink illusion)
    if sil_lines and sh >= 4:
        crop_top = sh // 4
        crop_bot = (sh * 3) // 4
        cropped = sil_lines[crop_top:crop_bot]
        _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
        _safari_draw_grass(my, mx, spr_lines)
        _safari_blit(cropped, spr_row + crop_top, spr_col, my)
        sys.stdout.write(RST); sys.stdout.flush()
        time.sleep(0.1)

    # 4) Reduced to a glowing dot
    dot_row = spr_row + sh // 2
    dot_col = spr_col + spr_w // 2
    _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
    _safari_draw_grass(my, mx, spr_lines)
    if 1 <= dot_row <= my:
        sys.stdout.write(
            f"\033[{dot_row};{dot_col}H{BG_SCR}\033[38;2;255;255;255m{BOLD}●")
    sys.stdout.write(RST); sys.stdout.flush()
    time.sleep(0.08)

    # 5) Ball snaps shut — heavier "clunk" (lower pitch Pop)
    ball_y = dot_row - bh // 2
    ball_x = dot_col - bw // 2
    _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
    _safari_draw_grass(my, mx, spr_lines)
    _safari_blit(ball_lines, ball_y, ball_x, my)
    sys.stdout.write(RST); sys.stdout.flush()
    _sfx("Pop", rate=0.7)
    time.sleep(0.25)


def _safari_anim_shake(my, mx, spr_lines, ball_lines, n_shakes):
    """Ball drops to the grass line, then wobbles with tension dots between shakes."""
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    if not ball_lines:
        return

    bh = len(ball_lines)
    bw = max((_vl(bl) for bl in ball_lines), default=0)
    ball_cx = scr_x + (sw - bw) // 2
    grass_row = _safari_grass_row(scr_y, scr_h, spr_lines)
    ball_y_ground = grass_row - bh

    # Start position = roughly where absorb left the ball (center of sprite zone)
    spr_row = _safari_spr_pos(spr_lines, *g)[0] if spr_lines else scr_y + scr_h // 2
    sh = len(spr_lines) if spr_lines else 0
    start_y = spr_row + sh // 2 - bh // 2

    def draw(bx, by, dots=0):
        _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
        _safari_draw_grass(my, mx, spr_lines)
        _safari_blit(ball_lines, by, bx, my)
        if dots > 0:
            dot_str = " ".join(["•"] * dots)
            dot_row = by - 1
            dot_col = bx + bw // 2 - len(dot_str) // 2
            if 1 <= dot_row <= my:
                sys.stdout.write(
                    f"\033[{dot_row};{dot_col}H{BG_SCR}{FG_SCRTXT}{BOLD}{dot_str}")
        sys.stdout.write(RST)
        sys.stdout.flush()

    # Fall to grass
    fall_frames = 4
    for f in range(fall_frames):
        t = (f + 1) / fall_frames
        y = int(start_y + (ball_y_ground - start_y) * t)
        draw(ball_cx, y)
        time.sleep(0.05)

    # Settle beat before the first shake
    draw(ball_cx, ball_y_ground)
    time.sleep(0.28)

    for shake in range(n_shakes):
        # Pitch rises each shake for tension: 1.0, 1.15, 1.3
        pitch = 1.0 + shake * 0.15
        _sfx("Tink", rate=pitch)
        draw(ball_cx - 2, ball_y_ground, dots=shake + 1)
        time.sleep(0.13)
        draw(ball_cx + 2, ball_y_ground, dots=shake + 1)
        time.sleep(0.13)
        draw(ball_cx, ball_y_ground, dots=shake + 1)
        time.sleep(0.3)


def _safari_anim_capture(my, mx, spr_lines, ball_lines, star_lines):
    """Caught! Expanding rings of star-dust around the ball + victory chime."""
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    if not ball_lines:
        return

    bw = max((_vl(bl) for bl in ball_lines), default=0)
    bh = len(ball_lines)
    ball_x = scr_x + (sw - bw) // 2
    grass_row = _safari_grass_row(scr_y, scr_h, spr_lines)
    ball_y = grass_row - bh
    cy = ball_y + bh // 2
    cx = ball_x + bw // 2

    _sfx("Glass")

    # Expanding rings of sparkle. 8 points around a circle, radius grows each
    # frame. Each frame also has a soft secondary inner ring for density.
    import math as _math
    frames = 7
    for frame in range(frames):
        r_outer = 3 + frame * 2
        r_inner = max(1, r_outer - 3)
        _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
        _safari_draw_grass(my, mx, spr_lines)
        _safari_blit(ball_lines, ball_y, ball_x, my)
        for ring_r, glyph, color in (
            (r_outer, "★", FG_YLED),
            (r_inner, "✦", FG_WHITE),
        ):
            n_points = 8
            for i in range(n_points):
                angle = (i / n_points) * 2 * _math.pi + frame * 0.3
                # half-block cells are roughly 2:1 ratio, so scale x
                px = int(cx + _math.cos(angle) * ring_r * 2)
                py = int(cy + _math.sin(angle) * ring_r)
                if (scr_x <= px < scr_x + sw
                        and scr_y + 1 <= py < scr_y + scr_h - 2
                        and 1 <= py <= my):
                    sys.stdout.write(
                        f"\033[{py};{px}H{BG_SCR}{color}{BOLD}{glyph}")
        sys.stdout.write(RST); sys.stdout.flush()
        time.sleep(0.12)

    # Victory chime on top of everything
    _sfx("Hero", rate=0.95, volume=0.8)
    time.sleep(0.2)


def _safari_anim_burst(my, mx, spr_lines, ball_lines):
    """Ball bursts open (break-free): explosion + pokemon re-emerges."""
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    if not ball_lines:
        return

    bh = len(ball_lines)
    bw = max((_vl(bl) for bl in ball_lines), default=0)
    ball_x = scr_x + (sw - bw) // 2
    grass_row = _safari_grass_row(scr_y, scr_h, spr_lines)
    ball_y = grass_row - bh
    cx = ball_x + bw // 2
    cy = ball_y + bh // 2

    _sfx("Funk", rate=0.7, volume=0.9)  # heavier "crack" on low pitch

    # Explosion: expanding ring of '*' for two frames
    import math as _math
    for frame in range(2):
        _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
        _safari_draw_grass(my, mx, spr_lines)
        _safari_blit(ball_lines, ball_y, ball_x, my)
        r = 3 + frame * 3
        for i in range(12):
            angle = i / 12 * 2 * _math.pi
            px = int(cx + _math.cos(angle) * r * 2)
            py = int(cy + _math.sin(angle) * r)
            if (scr_x <= px < scr_x + sw
                    and scr_y + 1 <= py < scr_y + scr_h - 2
                    and 1 <= py <= my):
                glyph = "*" if frame == 0 else "·"
                color = FG_YLED if frame == 0 else FG_SCRHI
                sys.stdout.write(
                    f"\033[{py};{px}H{BG_SCR}{color}{BOLD}{glyph}")
        sys.stdout.write(RST); sys.stdout.flush()
        time.sleep(0.07)

    # Bright pale flash — preserve header row
    for i in range(1, scr_h):
        r = scr_y + i
        if 1 <= r <= my:
            sys.stdout.write(
                f"\033[{r};{scr_x}H\033[48;2;240;240;200m{' ' * sw}")
    sys.stdout.write(RST); sys.stdout.flush()
    time.sleep(0.06)

    # Pokemon reappears at its home position
    _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
    _safari_draw_grass(my, mx, spr_lines)
    if spr_lines:
        spr_row, spr_col = _safari_spr_pos(spr_lines, *g)
        _safari_blit(spr_lines, spr_row, spr_col, my)
    sys.stdout.write(RST); sys.stdout.flush()
    time.sleep(0.25)


def _safari_anim_flee(my, mx, spr_lines):
    """Pokemon slides off the green screen to the right + puff of smoke."""
    g = _safari_scr_geom(my, mx)
    scr_y, scr_x, sw, scr_h = g
    if not spr_lines:
        return
    spr_row, spr_col = _safari_spr_pos(spr_lines, *g)
    sh = len(spr_lines)
    spr_w = max((_vl(sl) for sl in spr_lines), default=0)
    x_right = scr_x + sw

    _sfx("Sosumi", rate=1.1, volume=0.8)
    _sfx("Blow", rate=1.3, volume=0.5)  # whoosh layered on top

    orig_col = spr_col
    orig_mid_y = spr_row + sh // 2
    orig_mid_x = orig_col + spr_w // 2
    puff_chars = "*º·"  # dots for the smoke trail
    for frame in range(7):
        offset = frame * 3
        _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
        _safari_draw_grass(my, mx, spr_lines)
        _safari_blit(spr_lines, spr_row, spr_col + offset, my,
                     clip_x_left=scr_x, clip_x_right=x_right)
        # Puff of smoke at the pokemon's original position, growing then fading
        if frame < 5:
            radius = 1 + frame
            density = max(1, 4 - frame)
            for dx in range(-radius, radius + 1):
                for dy in range(-radius // 2, radius // 2 + 1):
                    if abs(dx) + abs(dy) * 2 > radius:
                        continue
                    px = orig_mid_x + dx * 2
                    py = orig_mid_y + dy
                    if (scr_x <= px < scr_x + sw
                            and scr_y + 1 <= py < scr_y + scr_h - 2
                            and 1 <= py <= my
                            and ((dx + dy + frame) % density == 0)):
                        ch = puff_chars[(dx + dy) % len(puff_chars)]
                        sys.stdout.write(
                            f"\033[{py};{px}H{BG_SCR}{FG_SCRHI}{ch}")
        sys.stdout.write(RST)
        sys.stdout.flush()
        time.sleep(0.055)

    _safari_clear_green(scr_y, scr_x, sw, scr_h, my)
    _safari_draw_grass(my, mx, spr_lines)
    sys.stdout.write(RST)
    sys.stdout.flush()


# ── Gym Challenge mechanics / renderers ─────────────────────────────────────

def _gym_ace_idx(leader):
    return max(0, min(REAL_POKE_COUNT - 1, int(leader["ace_num"]) - 1))


def _gym_leader_team_defs(leader):
    team = leader.get("team") or [(leader["ace_num"], leader["level"])]
    return [
        {
            "idx": max(0, min(REAL_POKE_COUNT - 1, int(num) - 1)),
            "level": int(level),
            "moves": leader["moves"],
        }
        for num, level in team
    ]


def _gym_move_tuple(raw):
    name, typ, power = raw
    return {"name": name, "type": typ, "power": int(power)}


def _gym_moves_from_types(types):
    moves = []
    seen_types = set()
    for typ in (types or [])[:2]:
        typ = typ.lower()
        if typ in GYM_TYPE_MOVES and typ not in seen_types:
            moves.append(_gym_move_tuple(GYM_TYPE_MOVES[typ]))
            seen_types.add(typ)
    for raw in (
        ("Golpe Cuerpo", "normal", 65),
        ("Ataque Rapido", "normal", 40),
        ("Placaje", "normal", 40),
        ("Foco Energia", "fighting", 45),
    ):
        move = _gym_move_tuple(raw)
        key = (move["name"], move["type"])
        if key not in {(m["name"], m["type"]) for m in moves}:
            moves.append(move)
        if len(moves) >= 4:
            break
    return moves[:4]


def _gym_type_multiplier(move_type, defender_types):
    mult = 1.0
    chart = TYPE_EFFECTIVENESS.get((move_type or "normal").lower(), {})
    for typ in defender_types or ["normal"]:
        mult *= chart.get(typ.lower(), 1.0)
    return mult


def _gym_effect_text(mult):
    if mult == 0:
        return "No afecta..."
    if mult >= 2.0:
        return "Es muy eficaz!"
    if 0 < mult < 1.0:
        return "No es muy eficaz..."
    return ""


def _gym_level_stats(base_stats, level):
    def base(key, fallback):
        try:
            return int((base_stats or {}).get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    level = max(1, int(level))
    hp = int(((base("hp", 60) * 2) * level) / 100) + level + 10
    return {
        "max_hp": max(18, hp),
        "attack": max(8, int(((base("attack", 60) * 2) * level) / 100) + 5),
        "defense": max(8, int(((base("defense", 60) * 2) * level) / 100) + 5),
        "special_attack": max(8, int(((base("special-attack", 60) * 2) * level) / 100) + 5),
        "special_defense": max(8, int(((base("special-defense", 60) * 2) * level) / 100) + 5),
        "speed": max(8, int(((base("speed", 60) * 2) * level) / 100) + 5),
    }


def _gym_build_mon(idx, level, data=None, move_defs=None):
    num, name = POKEMON[idx]
    data = data or {}
    types = [t.lower() for t in (data.get("types") or ["normal"])]
    stats = _gym_level_stats(data.get("stats") or {}, level)
    moves = [_gym_move_tuple(m) for m in move_defs] if move_defs else _gym_moves_from_types(types)
    return {
        "idx": idx,
        "num": num,
        "name": name,
        "dname": _dn(name),
        "level": int(level),
        "types": types,
        "stats": stats,
        "hp": stats["max_hp"],
        "max_hp": stats["max_hp"],
        "moves": moves,
    }


def _gym_damage(attacker, defender, move, random_factor=1.0):
    move_type = move.get("type", "normal")
    special = move_type in GYM_SPECIAL_TYPES
    atk_key = "special_attack" if special else "attack"
    def_key = "special_defense" if special else "defense"
    atk = max(1, attacker["stats"].get(atk_key, 20))
    defense = max(1, defender["stats"].get(def_key, 20))
    level = max(1, attacker.get("level", 5))
    power = max(1, int(move.get("power", 40)))
    mult = _gym_type_multiplier(move_type, defender.get("types", []))
    if mult == 0:
        return 0, mult
    stab = 1.5 if move_type in attacker.get("types", []) else 1.0
    base = ((((2 * level / 5) + 2) * power * atk / defense) / 50) + 2
    damage = int(base * stab * mult * max(0.85, min(1.0, random_factor)))
    return max(1, damage), mult


def _gym_apply_move(attacker, defender, move, rng=random):
    factor = rng.uniform(0.90, 1.0) if hasattr(rng, "uniform") else 1.0
    damage, mult = _gym_damage(attacker, defender, move, factor)
    defender["hp"] = max(0, defender["hp"] - damage)
    lines = [f"{attacker['dname']} uso {move['name']}!"]
    effect = _gym_effect_text(mult)
    if effect:
        lines.append(effect)
    if damage:
        lines.append(f"{defender['dname']} perdio {damage} PS.")
    return lines


def _gym_enemy_move(enemy, player):
    def score(move):
        stab = 1.5 if move["type"] in enemy.get("types", []) else 1.0
        return move["power"] * stab * _gym_type_multiplier(
            move["type"], player.get("types", []))
    return max(enemy["moves"], key=score)


def _gym_take_turn(player, enemy, player_move_idx, rng=random):
    player_move = player["moves"][player_move_idx]
    enemy_move = _gym_enemy_move(enemy, player)
    player_fast = player["stats"]["speed"] >= enemy["stats"]["speed"]
    order = ((player, enemy, player_move), (enemy, player, enemy_move))
    if not player_fast:
        order = tuple(reversed(order))

    log = []
    for attacker, defender, move in order:
        if attacker["hp"] <= 0 or defender["hp"] <= 0:
            continue
        log.extend(_gym_apply_move(attacker, defender, move, rng))
        if defender["hp"] <= 0:
            log.append(f"{defender['dname']} no puede continuar!")
            break

    if enemy["hp"] <= 0:
        return "win", log[-4:]
    if player["hp"] <= 0:
        return "lose", log[-4:]
    return "choose", log[-4:]


def _gym_roster():
    nums = []
    for key in ("caught_safari", "seen"):
        for num in STATS.get(key, []):
            try:
                n = int(num)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= REAL_POKE_COUNT and n not in nums:
                nums.append(n)
    indices = [n - 1 for n in nums]
    for idx in GYM_FALLBACK_ROSTER:
        if idx not in indices:
            indices.append(idx)
    return [i for i in indices if 0 <= i < REAL_POKE_COUNT][:24]


def _gym_player_team_indices(roster, cursor, count=3):
    roster = list(roster or GYM_FALLBACK_ROSTER)
    if not roster:
        roster = list(GYM_FALLBACK_ROSTER)
    picked = []
    for step in range(len(roster)):
        idx = roster[(cursor + step) % len(roster)]
        if idx not in picked:
            picked.append(idx)
        if len(picked) >= count:
            break
    return picked


def _gym_next_alive(team, start_after=-1):
    for i in range(start_after + 1, len(team)):
        if team[i].get("hp", 0) > 0:
            return i
    return None


def _gym_team_pips(team, active_slot):
    parts = []
    for i, mon in enumerate(team or []):
        if mon.get("hp", 0) <= 0:
            parts.append(f"{FG_DKGRAY}×")
        elif i == active_slot:
            parts.append(f"{FG_YLED}●")
        else:
            parts.append(f"{FG_SCRHI}●")
    return "".join(parts) + FG_WHITE


def _gym_hp_bar(cur, max_hp, width):
    max_hp = max(1, int(max_hp))
    cur = max(0, min(int(cur), max_hp))
    ratio = cur / max_hp
    filled = int(round(ratio * width))
    if ratio > 0.5:
        col = FG_GLED
    elif ratio > 0.2:
        col = FG_YLED
    else:
        col = FG_RLED
    return f"{col}{'█' * filled}{FG_DKGRAY}{'░' * (width - filled)}{FG_WHITE}"


def _plain_clip(text, width):
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[:width - 1] + "…"


def _gym_lr(left, right, width):
    pad = max(1, width - _vl(left) - _vl(right))
    return f"{left}{' ' * pad}{right}"


def _gym_sprite_row(line, width, side):
    visible = _vl(line)
    if side == "right":
        left = max(0, width - visible - 4)
    else:
        left = 4 if width - visible > 6 else max(0, (width - visible) // 2)
    return f"{' ' * left}{line}{BG_SCR}"


def _gym_flash_hit(my, mx, target):
    """Briefly flash the enemy/player half of the green screen after damage."""
    _, _inn, mrg, sw, dx = _geom(mx)
    fixed = 14
    scr_h = max(8, my - fixed)
    total_h = fixed + scr_h
    yo = max(0, (my - total_h) // 2)
    scr_top = yo + 5
    scr_left = dx + mrg + 3
    half = max(2, scr_h // 2)
    if target == "enemy":
        start = scr_top + 1
        rows = max(2, half - 1)
    else:
        start = scr_top + half
        rows = max(2, scr_h - half - 1)
    _sfx("Pop", rate=1.2 if target == "enemy" else 0.9, volume=0.35)
    flash = "\033[48;2;225;245;175m"
    for r in range(start, min(start + rows, scr_top + scr_h)):
        if 1 <= r <= my:
            sys.stdout.write(f"\033[{r};{scr_left}H{flash}{' ' * sw}")
    sys.stdout.write(RST)
    sys.stdout.flush()
    time.sleep(0.045)


def draw_gym_menu(my, mx, leader_cursor, roster_cursor, roster):
    _, inn, mrg, sw, dx = _geom(mx)
    scr_h = max(9, my - 8)
    total_h = 8 + scr_h
    yo = max(0, (my - total_h) // 2)
    badges = set(_gym_badges())
    roster = roster or GYM_FALLBACK_ROSTER
    team_indices = _gym_player_team_indices(roster, roster_cursor, 3)

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    badge_row = "".join(f"{FG_YLED}●" if i in badges else f"{FG_SCRHI}○"
                        for i in range(len(GYM_LEADERS)))
    lines = [
        f"{FG_SCRTXT}{BOLD}  DESAFIO DE GIMNASIOS",
        f"{FG_SCRHI}  Medallas: {badge_row}",
        f"{FG_SCRHI}  Elige lider y primer Pokemon.",
        "",
    ]
    team_rows = 3 if scr_h >= 13 else (1 if scr_h >= 11 else 0)
    leader_rows = max(3, min(len(GYM_LEADERS), scr_h - 6 - team_rows))
    offset = max(0, min(leader_cursor - leader_rows // 2,
                        len(GYM_LEADERS) - leader_rows))
    for i in range(offset, offset + leader_rows):
        leader = GYM_LEADERS[i]
        ace_idx = _gym_ace_idx(leader)
        num, name = POKEMON[ace_idx]
        team_count = len(_gym_leader_team_defs(leader))
        mark = f"{FG_YLED}▶" if i == leader_cursor else " "
        won = f"{FG_YLED}★" if i in badges else f"{FG_SCRHI}·"
        scroll_mark = "↑" if i == offset and offset > 0 else (
            "↓" if i == offset + leader_rows - 1
            and offset + leader_rows < len(GYM_LEADERS) else " ")
        label = _plain_clip(
            f"{i + 1}. {leader['name']:<10} {team_count}pkmn  #{num:03d} {_dn(name)}",
            max(10, sw - 6),
        )
        lines.append(f"{scroll_mark}{mark} {won} {FG_SCRTXT}{label}")
    lines.append("")
    if team_rows == 1:
        names = []
        for idx in team_indices:
            cnum, cname = POKEMON[idx]
            names.append(f"#{cnum:03d} {_plain_clip(_dn(cname), 8)}")
        lines.append(f"{FG_SCRHI}  Equipo: {FG_SCRTXT}{' / '.join(names)}")
    else:
        for i, idx in enumerate(team_indices[:team_rows]):
            cnum, cname = POKEMON[idx]
            tag = "PRIMERO" if i == 0 else "RESERVA"
            lines.append(
                f"{FG_SCRHI}  {tag:<7} {FG_SCRTXT}{BOLD}#{cnum:03d} {_plain_clip(_dn(cname), 18).upper()}")
    lines.append(f"{FG_SCRHI}  Ciudad: {GYM_LEADERS[leader_cursor]['city']}")

    pad_top = max(0, (scr_h - len(lines)) // 2)
    for i in range(scr_h):
        ci = i - pad_top
        if 0 <= ci < len(lines):
            at(row, _scr_row(inn, mrg, sw, lines[ci]))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    ctrl = (f"  {FG_GRAY}▲▼{FG_WHITE} Lider"
            f" {FG_DKGRAY}·{FG_WHITE} {FG_GRAY}◀▶{FG_WHITE} Retador"
            f" {FG_DKGRAY}·{FG_WHITE} {FG_GRAY}Enter{FG_WHITE} Combatir"
            f" {FG_DKGRAY}·{FG_WHITE} {FG_GRAY}Esc{FG_WHITE} Volver")
    if _vl(ctrl) > inn:
        ctrl = (f" {FG_GRAY}▲▼{FG_WHITE} Lider"
                f" {FG_GRAY}◀▶{FG_WHITE} Pkmn"
                f" {FG_GRAY}Enter{FG_WHITE} Fight"
                f" {FG_GRAY}Esc{FG_WHITE}")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def draw_gym_battle(my, mx, player, enemy, leader, move_cursor,
                    phase, log_lines, player_spr, enemy_spr,
                    player_team=None, enemy_team=None,
                    player_slot=0, enemy_slot=0):
    _, inn, mrg, sw, dx = _geom(mx)
    fixed = 14
    scr_h = max(8, my - fixed)
    total_h = fixed + scr_h
    yo = max(0, (my - total_h) // 2)

    buf = []

    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    hp_w = max(8, min(16, sw // 4))
    screen = [""] * scr_h

    enemy_name = (
        f"  {FG_SCRTXT}{BOLD}{_plain_clip(enemy['dname'].upper(), 14)}"
        f"{FG_SCRHI} Nv{enemy['level']:02d}")
    enemy_pips = _gym_team_pips(enemy_team or [enemy], enemy_slot)
    screen[0] = _gym_lr(enemy_name, f"{enemy_pips}  ", sw)
    enemy_hp = (
        f"  {FG_SCRHI}HP {_gym_hp_bar(enemy['hp'], enemy['max_hp'], hp_w)} "
        f"{FG_SCRTXT}{enemy['hp']:>3}/{enemy['max_hp']:<3}")
    screen[1] = enemy_hp

    enemy_sh = len(enemy_spr or [])
    enemy_last = min(scr_h - 4, 2 + enemy_sh)
    for i in range(max(0, enemy_last - 2)):
        screen[2 + i] = _gym_sprite_row(enemy_spr[i], sw, "right")

    player_sh = len(player_spr or [])
    player_top = max(3, scr_h - player_sh - 3)
    if enemy_last >= player_top:
        player_top = min(scr_h - 3, enemy_last + 1)
    for i in range(player_sh):
        target = player_top + i
        if 3 <= target < scr_h - 2:
            screen[target] = _gym_sprite_row(player_spr[i], sw, "left")

    if scr_h >= 7:
        vs_row = max(2, min(scr_h - 4, scr_h // 2))
        if not ANSI_RE.sub("", screen[vs_row]).strip():
            vs = f"{FG_SCRHI}{leader['name'].upper()}  ·  MEDALLA {leader['badge'].upper()}"
            screen[vs_row] = _ansi_center(vs, sw)

    player_pips = _gym_team_pips(player_team or [player], player_slot)
    player_name = (
        f"{FG_SCRTXT}{BOLD}{_plain_clip(player['dname'].upper(), 14)}"
        f"{FG_SCRHI} Nv{player['level']:02d}  ")
    screen[-2] = _gym_lr(f"  {player_pips}", player_name, sw)
    player_hp = (
        f"{FG_SCRHI}HP {_gym_hp_bar(player['hp'], player['max_hp'], hp_w)} "
        f"{FG_SCRTXT}{player['hp']:>3}/{player['max_hp']:<3}  ")
    screen[-1] = _gym_lr("", player_hp, sw)

    for line in screen:
        at(row, _scr_row(inn, mrg, sw, line)); row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1

    title = f"   {BOLD}{leader['name']} - Medalla {leader['badge']}{RST}{BG_RED}{FG_WHITE}"
    status = "Victoria!" if phase == "win" else ("Derrota..." if phase == "lose" else "Elige movimiento")
    right = f"{FG_GRAY}{status}{FG_WHITE}   "
    if _vl(title) + _vl(right) + 1 > inn:
        title = f"   {BOLD}{leader['name']}{RST}{BG_RED}{FG_WHITE}"
        right = f"{FG_GRAY}{_plain_clip(status, max(6, inn - _vl(title) - 5))}{FG_WHITE}   "
    pad = max(1, inn - _vl(title) - _vl(right))
    at(row, _cas(inn, f"{title}{' ' * pad}{right}")); row += 1

    visible_log = (log_lines or [])[-3:]
    for i in range(3):
        msg = visible_log[i] if i < len(visible_log) else ""
        color = FG_YLED if i == len(visible_log) - 1 else FG_WHITE
        at(row, _cas(inn, f"   {color}{_plain_clip(msg, max(1, inn - 4))}")); row += 1

    move_parts = []
    move_name_w = max(8, min(17, (inn - 10) // 2))
    for i, move in enumerate(player["moves"][:4]):
        typ = TYPE_ES.get(move["type"], move["type"].upper())
        name = _plain_clip(f"{move['name']} {typ}", move_name_w)
        if phase == "choose" and i == move_cursor:
            move_parts.append(f"{FG_YLED}{BOLD}▶ {name:<{move_name_w}s}{RST}{BG_RED}{FG_WHITE}")
        else:
            move_parts.append(f"{FG_GRAY}  {name:<{move_name_w}s}{FG_WHITE}")
        if i in (1, 3):
            left = move_parts[i - 1]
            right_move = move_parts[i]
            at(row, _cas(inn, f"   {left}   {right_move}")); row += 1

    type_hint = player["moves"][move_cursor]["type"] if phase == "choose" else ""
    hint = f"Tipo: {TYPE_ES.get(type_hint, type_hint.upper())}" if type_hint else ""
    if phase == "choose":
        ctrl = (f"  {FG_GRAY}▲▼◀▶{FG_WHITE} Movimiento"
                f" {FG_DKGRAY}·{FG_WHITE} {FG_GRAY}Enter{FG_WHITE} Atacar"
                f" {FG_DKGRAY}·{FG_WHITE} {hint}"
                f" {FG_DKGRAY}·{FG_WHITE} {FG_GRAY}Esc{FG_WHITE} Salir")
        if _vl(ctrl) > inn:
            ctrl = (f" {FG_GRAY}▲▼◀▶{FG_WHITE} Mov"
                    f" {FG_GRAY}Enter{FG_WHITE} Atacar"
                    f" {FG_GRAY}Esc{FG_WHITE}")
    else:
        ctrl = (f"  {FG_GRAY}Enter{FG_WHITE} Volver al menu"
                f" {FG_DKGRAY}·{FG_WHITE} {FG_GRAY}Esc{FG_WHITE} Lista")
        if _vl(ctrl) > inn:
            ctrl = f" {FG_GRAY}Enter{FG_WHITE} Menu  {FG_GRAY}Esc{FG_WHITE} Lista"
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


# ── Help overlay ─────────────────────────────────────────────────────────────

HELP_LINES = [
    ("LISTA",   ""),
    ("",        "  ▲▼ Nav  ·  Enter ver detalle  ·  / buscar"),
    ("",        "  g Quiz  ·  h Safari  ·  B Gimnasios  ·  M Memoria"),
    ("",        "  T tarjeta  ·  p tema"),
    ("",        ""),
    ("DETALLE", ""),
    ("",        "  ◀▶ navegar  ·  c reproducir cry  ·  v leer voz"),
    ("",        "  n cycle panel (desc/stats/moves)  ·  s shiny"),
    ("",        "  G ciclar generación de sprite  ·  Espacio autoplay"),
    ("",        ""),
    ("GLOBAL",  ""),
    ("",        "  m mute  ·  p tema  ·  ? esta ayuda  ·  Esc volver"),
    ("",        "  q salir"),
]


def draw_help_overlay(my, mx):
    """Render a centered help modal on top of whatever is currently drawn."""
    body_lines = []
    for kind, txt in HELP_LINES:
        if kind:
            body_lines.append(f" {BOLD}{FG_YLED}{kind}{RST}{BG_DKRED}{FG_WHITE}")
        elif txt:
            body_lines.append(f"{FG_GRAY}{txt}{FG_WHITE}")
        else:
            body_lines.append("")
    title = f"{BOLD}AYUDA — Pokédex Gen I-V{RST}{BG_DKRED}{FG_WHITE}"
    footer = f"{FG_GRAY}(cualquier tecla para cerrar){FG_WHITE}"

    # Compute width: content max width + padding
    content_w = max(_vl(line) for line in body_lines + [title, footer])
    box_w = min(mx - 4, content_w + 6)
    box_h = len(body_lines) + 4  # title row + blank + body + blank + footer
    box_x = max(1, (mx - box_w) // 2)
    box_y = max(1, (my - box_h) // 2)

    parts = []
    horiz = "─" * (box_w - 2)
    # Top border
    parts.append(f"\033[{box_y};{box_x}H{BG_DKRED}{FG_WHITE}╭{horiz}╮")
    # Title row
    tv = _vl(title)
    pad = (box_w - 2 - tv) // 2
    parts.append(
        f"\033[{box_y + 1};{box_x}H{BG_DKRED}{FG_WHITE}│"
        f"{' ' * pad}{title}{' ' * (box_w - 2 - pad - tv)}│")
    # Body
    for i, line in enumerate(body_lines):
        lv = _vl(line)
        rp = max(0, box_w - 2 - lv - 1)
        parts.append(
            f"\033[{box_y + 2 + i};{box_x}H{BG_DKRED}{FG_WHITE}│ "
            f"{line}{' ' * rp}│")
    # Footer
    fv = _vl(footer)
    pad = (box_w - 2 - fv) // 2
    parts.append(
        f"\033[{box_y + 2 + len(body_lines)};{box_x}H{BG_DKRED}{FG_WHITE}│"
        f"{' ' * pad}{footer}{' ' * (box_w - 2 - pad - fv)}│")
    # Bottom
    parts.append(
        f"\033[{box_y + 3 + len(body_lines)};{box_x}H{BG_DKRED}{FG_WHITE}╰{horiz}╯")

    sys.stdout.write("".join(parts) + RST)
    sys.stdout.flush()


# ── Background prefetch ─────────────────────────────────────────────────────
# When the user lands on Pokemon N we kick off tiny daemon threads that
# download the sprite + species data for N+1 and N-1. Because navigation is
# usually sequential, this means "Cargando..." almost never appears: by the
# time the user presses → the next Pokemon is already in the on-disk cache.

_prefetch_lock = threading.Lock()
_prefetch_inflight = set()  # POKEMON indices currently being fetched
MAX_PREFETCH_INFLIGHT = 6


def _prefetch_one(idx):
    try:
        num, name = POKEMON[idx]
        if num == 0:
            return
        dl_sprite(name)
        fetch_data(num)
    except Exception:
        pass
    finally:
        with _prefetch_lock:
            _prefetch_inflight.discard(idx)


def _prefetch_indices(indices):
    """Spawn bounded background prefetch threads for concrete Pokedex indices."""
    for nbr in indices:
        if not (0 <= nbr < POKE_COUNT):
            continue
        with _prefetch_lock:
            if (nbr in _prefetch_inflight
                    or len(_prefetch_inflight) >= MAX_PREFETCH_INFLIGHT):
                continue
            _prefetch_inflight.add(nbr)
        threading.Thread(target=_prefetch_one, args=(nbr,), daemon=True).start()


def prefetch_neighbors(idx):
    """Spawn background prefetch threads for nearby Pokemon."""
    offsets = (1, 2, 3, -1, -2)
    _prefetch_indices((idx + offset) % POKE_COUNT for offset in offsets)


# ── Memory minigame ─────────────────────────────────────────────────────────


def _memory_best_key(diff_idx, pairs=None):
    name = MEMORY_DIFFICULTIES[diff_idx][0]
    return name if pairs is None else f"{name}-{pairs}"


def _get_memory_best(diff_idx, pairs=None):
    bests = STATS.get("best_memory", {})
    return bests.get(_memory_best_key(diff_idx, pairs)) \
        or bests.get(_memory_best_key(diff_idx))


def _set_memory_best(diff_idx, tries, seconds, pairs=None):
    """Update best memory record if this run beats the previous one.

    Better = fewer tries, ties broken by faster time. Returns True on update.
    """
    key = _memory_best_key(diff_idx, pairs)
    bests = STATS.setdefault("best_memory", {})
    prev = bests.get(key)
    new = {"tries": tries, "seconds": int(seconds)}
    if prev is None or (tries < prev.get("tries", 1_000_000)) or (
            tries == prev.get("tries", 1_000_000)
            and new["seconds"] < prev.get("seconds", 1_000_000)):
        bests[key] = new
        _save_stats()
        return True
    return False


def _memory_make_deck(n_pairs, rng=None):
    """Pick n_pairs unique POKEMON indices, duplicate, shuffle. Skips MissingNo."""
    rng = rng or random
    pool = list(range(REAL_POKE_COUNT))
    chosen = rng.sample(pool, n_pairs)
    deck = chosen + chosen[:]
    rng.shuffle(deck)
    return deck


def _memory_desired_pairs(diff_idx):
    return MEMORY_DIFFICULTIES[diff_idx][1]


def _memory_layout_for_screen(sw, scr_h, desired_pairs):
    """Fit all requested pairs using large icon-first cards and scrolling."""
    desired_cards = max(2, desired_pairs * 2)
    gap_h = 1
    candidates = [
        (24, 12),
        (22, 11),
        (20, 10),
        (18, 9),
    ]
    for card_w, card_h in candidates:
        cols = min(desired_cards, max(1, (sw + 1) // (card_w + 1)))
        grid_w = cols * card_w + (cols - 1)
        if grid_w > sw or card_h > scr_h - 1:
            continue
        rows = math.ceil(desired_cards / cols)
        visible_rows = _memory_visible_rows(scr_h, rows, card_h, gap_h)
        visible_grid_h = visible_rows * card_h + (visible_rows - 1) * gap_h
        full_grid_h = rows * card_h + (rows - 1) * gap_h
        return {
            "card_w": card_w,
            "card_h": card_h,
            "rows": rows,
            "cols": cols,
            "cards": desired_cards,
            "pairs": desired_pairs,
            "grid_w": grid_w,
            "grid_h": full_grid_h,
            "visible_rows": visible_rows,
            "visible_grid_h": visible_grid_h,
            "gap_h": gap_h,
        }
    card_w, card_h = 16, 8
    cols = min(desired_cards, max(1, (sw + 1) // (card_w + 1)))
    rows = math.ceil(desired_cards / cols)
    visible_rows = _memory_visible_rows(scr_h, rows, card_h, gap_h)
    return {
        "card_w": card_w, "card_h": card_h,
        "rows": rows, "cols": cols,
        "cards": desired_cards, "pairs": desired_pairs,
        "grid_w": cols * card_w + (cols - 1),
        "grid_h": rows * card_h + (rows - 1) * gap_h,
        "visible_rows": visible_rows,
        "visible_grid_h": visible_rows * card_h + (visible_rows - 1) * gap_h,
        "gap_h": gap_h,
    }


def _memory_visible_rows(scr_h, rows, card_h, gap_h):
    grid_area_h = max(1, scr_h - 1)
    visible = max(1, (grid_area_h + gap_h) // (card_h + gap_h))
    return max(1, min(rows, visible))


def _memory_scroll_for_cursor(scroll_row, cursor, rows, cols, visible_rows):
    cursor_row = cursor // cols
    if cursor_row < scroll_row:
        scroll_row = cursor_row
    elif cursor_row >= scroll_row + visible_rows:
        scroll_row = cursor_row - visible_rows + 1
    max_scroll = max(0, rows - visible_rows)
    return max(0, min(scroll_row, max_scroll))


def _memory_scroll_marker(screen_row, grid_top, grid_h, scroll_row, rows, visible_rows):
    """Small right-edge scrollbar/arrow for the visible memory window."""
    if visible_rows >= rows or screen_row < grid_top or screen_row >= grid_top + grid_h:
        return ""
    rel = screen_row - grid_top
    if rel == 0:
        return "↑" if scroll_row > 0 else "│"
    if rel == grid_h - 1:
        return "↓" if scroll_row + visible_rows < rows else "│"
    if grid_h <= 2:
        return "┃"
    thumb_span = max(1, int(round(grid_h * visible_rows / rows)))
    usable = max(1, grid_h - 2 - thumb_span)
    max_scroll = max(1, rows - visible_rows)
    thumb_start = 1 + int(round((scroll_row / max_scroll) * usable))
    if thumb_start <= rel < thumb_start + thumb_span:
        return "┃"
    return "│"


_memory_icon_render_cache = {}


def _ansi_center(line, width):
    visible = _vl(line)
    if visible >= width:
        return line
    pad_l = (width - visible) // 2
    pad_r = width - visible - pad_l
    return f"{' ' * pad_l}{line}{RST}{BG_SCR}{' ' * pad_r}"


def _memory_icon_lines(num, target_w, max_rows):
    """Return rendered PC icon rows for a memory card."""
    key = (num, target_w, max_rows, _palette_idx)
    if key in _memory_icon_render_cache:
        return _memory_icon_render_cache[key]
    img = dl_memory_icon(num)
    if not img:
        _memory_icon_render_cache[key] = []
        return []
    lines = render_sprite(img, target_w, bg_rgb=SCR_RGB, max_rows=max_rows)
    lines = lines[:max_rows]
    _memory_icon_render_cache[key] = lines
    return lines


def _format_secs(secs):
    secs = int(secs)
    return f"{secs // 60}:{secs % 60:02d}"


def draw_memory_menu(my, mx, cursor):
    """Difficulty picker for the memory game."""
    _, inn, mrg, sw, dx = _geom(mx)
    fixed = 8
    scr_h = max(8, my - fixed)
    total = fixed + scr_h
    yo = max(0, (my - total) // 2)

    buf = []
    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    title = f"{FG_SCRTXT}{BOLD}  Memoria Pokedex"
    pad_top = max(0, (scr_h - 4 - len(MEMORY_DIFFICULTIES) * 2) // 2)
    for i in range(scr_h):
        ci = i - pad_top
        if ci == 0:
            at(row, _scr_row(inn, mrg, sw, title))
        elif ci == 2:
            at(row, _scr_row(inn, mrg, sw,
                f"  {FG_SCRHI}Encuentra todas las parejas en pocos intentos."))
        elif 4 <= ci < 4 + len(MEMORY_DIFFICULTIES) * 2 and (ci - 4) % 2 == 0:
            opt_idx = (ci - 4) // 2
            name, desired_pairs = MEMORY_DIFFICULTIES[opt_idx]
            layout = _memory_layout_for_screen(sw, max(8, my - 9), desired_pairs)
            label = f"{name}  ({desired_pairs} parejas"
            if layout["visible_rows"] < layout["rows"]:
                label += " · scroll"
            label += ")"
            best = _get_memory_best(opt_idx, desired_pairs)
            best_str = ""
            if best:
                best_str = (f"   {FG_SCRHI}\u00b7 mejor: {best['tries']} intentos "
                            f"({_format_secs(best['seconds'])})")
            if opt_idx == cursor:
                text = f"  {FG_SCRTXT}{BOLD}\u25b6 {label}{best_str}"
            else:
                text = f"    {FG_SCRHI}{label}{best_str}"
            at(row, _scr_row(inn, mrg, sw, text))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1
    sep = f" {FG_DKGRAY}\u00b7{FG_WHITE} "
    ctrl = (f"  {FG_GRAY}\u25b2\u25bc{FG_WHITE} Dificultad"
            f"{sep}{FG_GRAY}Enter{FG_WHITE} Empezar"
            f"{sep}{FG_GRAY}Esc{FG_WHITE} Volver")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def draw_memory_game(my, mx, diff_idx, cards, flipped, matched, cursor,
                     tries, elapsed, rows, cols, card_w, card_h, gap_h=1,
                     scroll_row=0):
    """Render the memory grid + header + controls."""
    _, inn, mrg, sw, dx = _geom(mx)
    name = MEMORY_DIFFICULTIES[diff_idx][0]
    n_total = len(cards)
    grid_w = cols * card_w + (cols - 1)
    fixed = 9  # 4 chrome top + 2 below screen + ctrl + heavy bot + 1 pad
    scr_h = max(8, my - fixed)
    visible_rows = _memory_visible_rows(scr_h, rows, card_h, gap_h)
    scroll_row = _memory_scroll_for_cursor(
        scroll_row, cursor, rows, cols, visible_rows)
    grid_h = visible_rows * card_h + (visible_rows - 1) * gap_h
    total = fixed + scr_h
    yo = max(0, (my - total) // 2)

    buf = []
    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    # Header inside the screen: difficulty + tries + time + pairs
    pairs_done = len(matched) // 2
    pairs_total = n_total // 2
    header_left = (f"  {FG_SCRTXT}{BOLD}{name}"
                   f"{FG_SCRHI}  \u00b7 Parejas {FG_SCRTXT}{pairs_done}/{pairs_total}")
    if visible_rows < rows:
        shown = f"{scroll_row + 1}-{scroll_row + visible_rows}/{rows}"
        more_up = "↑" if scroll_row > 0 else " "
        more_down = "↓" if scroll_row + visible_rows < rows else " "
        scroll_text = (f"{FG_SCRHI}  \u00b7 {more_up}{more_down} Filas "
                       f"{FG_SCRTXT}{shown}")
    else:
        scroll_text = ""
    header_right = (f"{FG_SCRHI}Intentos {FG_SCRTXT}{tries:>2d}"
                    f"{FG_SCRHI}  \u00b7 Tiempo {FG_SCRTXT}{_format_secs(elapsed)}"
                    f"{scroll_text}  ")
    hlv, hrv = _vl(header_left), _vl(header_right)
    pad = max(1, sw - hlv - hrv)
    at(row, _scr_row(inn, mrg, sw, f"{header_left}{' ' * pad}{header_right}"))
    row += 1

    # Compute grid origin (centered inside the screen rows after header)
    grid_offset_x = max(0, (sw - grid_w) // 2)
    remaining_rows = scr_h - 1
    grid_offset_y = max(0, (remaining_rows - grid_h) // 2)

    # Pre-render every screen row by composing card slices per row.
    # For each screen row inside the grid area, we figure out which row of
    # which card we're on, and render that slice.
    grid_top_row_idx = 1 + grid_offset_y
    for i in range(scr_h):
        if i < 1:
            continue  # header already drawn
        local = i - grid_top_row_idx
        if local < 0 or local >= grid_h:
            at(row, _scr_row(inn, mrg, sw))
            row += 1
            continue
        # which card row?
        gap_unit = card_h + gap_h
        card_row = scroll_row + local // gap_unit
        sub = local % gap_unit
        if sub >= card_h or card_row >= rows:
            # Empty gap row
            at(row, _scr_row(inn, mrg, sw))
            row += 1
            continue
        # Build the line by walking each column
        parts = [" " * grid_offset_x]
        for c in range(cols):
            idx = card_row * cols + c
            if idx >= n_total:
                parts.append(" " * card_w)
            else:
                parts.append(_memory_card_slice(
                    sub, card_w, card_h, idx, cards, flipped, matched, cursor))
            if c < cols - 1:
                parts.append(" ")
        line = "".join(parts)
        marker = _memory_scroll_marker(
            i, grid_top_row_idx, grid_h, scroll_row, rows, visible_rows)
        if marker:
            used = _vl(line)
            if used < sw - 1:
                line = f"{line}{' ' * (sw - 1 - used)}{FG_YLED}{marker}{FG_SCRTXT}"
        at(row, _scr_row(inn, mrg, sw, line))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1
    sep = f" {FG_DKGRAY}\u00b7{FG_WHITE} "
    ctrl = (f"  {FG_GRAY}\u25c0\u25b6\u25b2\u25bc{FG_WHITE} Mover"
            f"{sep}{FG_GRAY}Enter{FG_WHITE} Voltear"
            f"{sep}{FG_GRAY}Esc{FG_WHITE} Salir")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def _memory_card_slice(sub_row, card_w, card_h, idx, cards, flipped, matched, cursor):
    """Return the rendered string for one row of one card."""
    is_cursor = (idx == cursor)
    is_matched = (idx in matched)
    is_flipped = (idx in flipped)
    show = is_flipped or is_matched

    poke_idx = cards[idx]
    num, name = POKEMON[poke_idx]
    dn = _dn(name)
    # Pick border chars + colour
    if is_cursor:
        tl, tr, bl, br, h, v = "\u250f", "\u2513", "\u2517", "\u251b", "\u2501", "\u2503"
        border_color = FG_YLED
    else:
        tl, tr, bl, br, h, v = "\u256d", "\u256e", "\u2570", "\u256f", "\u2500", "\u2502"
        border_color = FG_SCRTXT if (show and not is_matched) else FG_SCRHI

    inner_w = card_w - 2

    if sub_row == 0:
        return f"{border_color}{tl}{h * inner_w}{tr}"
    if sub_row == card_h - 1:
        return f"{border_color}{bl}{h * inner_w}{br}"

    # Middle row(s): render content
    if not show:
        # Face-down: centered '?'
        if sub_row == card_h // 2:
            content = f"{FG_SCRHI}{BOLD}?".center(inner_w + len(BOLD) + len(FG_SCRHI))
            # Manually pad since the previous formula is wrong with ANSI codes
            q = "?"
            pad_l = (inner_w - 1) // 2
            pad_r = inner_w - 1 - pad_l
            content = f"{' ' * pad_l}{FG_SCRHI}{BOLD}{q}{RST}{BG_SCR}{' ' * pad_r}"
        elif sub_row == 1:
            slot = f"{idx + 1:02d}"
            pad_l = max(0, inner_w - len(slot) - 1)
            content = f"{' ' * pad_l}{FG_SCRHI}{slot}{RST}{BG_SCR} "
        else:
            content = " " * inner_w
        return f"{border_color}{v}{content}{border_color}{v}"

    # Face-up: large PC/party icon inside the card.
    fg = FG_SCRHI if is_matched else FG_SCRTXT
    weight = "" if is_matched else BOLD
    icon_rows = max(1, card_h - 2)
    icon_line_idx = sub_row - 1
    icon_lines = _memory_icon_lines(num, inner_w, icon_rows)
    if icon_lines:
        if icon_line_idx < len(icon_lines):
            content = _ansi_center(icon_lines[icon_line_idx], inner_w)
        else:
            content = " " * inner_w
        return f"{border_color}{v}{content}{border_color}{v}"

    if card_h == 4 and sub_row == 1:
        text = f"#{num:03d}".center(inner_w)
        content = f"{fg}{weight}{text}{RST}{BG_SCR}"
    elif card_h == 4 and sub_row == 2:
        truncated = dn if len(dn) <= inner_w else dn[: inner_w - 1] + "\u2026"
        text = truncated.center(inner_w)
        content = f"{fg}{text}{RST}{BG_SCR}"
    elif card_h >= 5:
        if sub_row == 1:
            text = f"#{num:03d}".center(inner_w)
            content = f"{fg}{weight}{text}{RST}{BG_SCR}"
        elif sub_row == card_h // 2:
            truncated = dn if len(dn) <= inner_w else dn[: inner_w - 1] + "\u2026"
            text = truncated.center(inner_w)
            content = f"{fg}{text}{RST}{BG_SCR}"
        else:
            content = " " * inner_w
    else:
        # card_h == 3: middle row shows name only
        text = f"#{num:03d} {dn}"
        if len(text) > inner_w:
            text = f"#{num:03d} " + dn[:max(0, inner_w - 5)]
        text = text[:inner_w].center(inner_w)
        content = f"{fg}{weight}{text}{RST}{BG_SCR}"
    return f"{border_color}{v}{content}{border_color}{v}"


def draw_memory_end(my, mx, diff_idx, tries, elapsed, new_record, pairs=None):
    """End-of-game summary: result + record check + replay/exit."""
    _, inn, mrg, sw, dx = _geom(mx)
    fixed = 8
    scr_h = max(8, my - fixed)
    total = fixed + scr_h
    yo = max(0, (my - total) // 2)

    buf = []
    def at(r, c):
        buf.append(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    row = 0
    at(row, _heavy(inn, "top")); row += 1
    at(row, _lights_row(inn)); row += 1
    at(row, _cas(inn)); row += 1
    at(row, _scr_brd(inn, mrg, sw, "top")); row += 1

    name, desired_pairs = MEMORY_DIFFICULTIES[diff_idx]
    pairs = pairs or desired_pairs

    title = f"{FG_SCRTXT}{BOLD}  Has completado la memoria!"
    summary = (f"{FG_SCRTXT}  Dificultad: {BOLD}{name}{RST}{BG_SCR}{FG_SCRTXT}"
               f"  ({pairs} parejas)")
    line_tries = f"{FG_SCRTXT}  Intentos: {BOLD}{tries}"
    line_time = f"{FG_SCRTXT}  Tiempo:   {BOLD}{_format_secs(elapsed)}"
    if new_record:
        record = f"{FG_YLED}{BOLD}  \u2605 Nuevo recor!"
    else:
        prev = _get_memory_best(diff_idx, pairs) or {}
        if prev:
            record = (f"{FG_SCRHI}  Mejor: {prev.get('tries', '?')} intentos "
                      f"({_format_secs(prev.get('seconds', 0))})")
        else:
            record = ""

    lines = [title, "", summary, "", line_tries, line_time, "", record]
    pad_top = max(0, (scr_h - len(lines)) // 2)
    for i in range(scr_h):
        ci = i - pad_top
        if 0 <= ci < len(lines):
            at(row, _scr_row(inn, mrg, sw, lines[ci]))
        else:
            at(row, _scr_row(inn, mrg, sw))
        row += 1

    at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1
    at(row, _cas(inn)); row += 1
    sep = f" {FG_DKGRAY}\u00b7{FG_WHITE} "
    ctrl = (f"  {FG_GRAY}Enter{FG_WHITE} Otra partida"
            f"{sep}{FG_GRAY}Esc{FG_WHITE} Volver")
    at(row, _cas(inn, ctrl)); row += 1
    at(row, _heavy(inn, "bottom"))

    sys.stdout.write("".join(buf))
    sys.stdout.flush()


# ── Intro animation ────────────────────────────────────────────────────────

def play_intro(my, mx):
    """Boot animation sequence before showing the list."""
    _, inn, mrg, sw, dx = _geom(mx)
    fixed = 9
    scr_h = max(4, my - fixed)
    total_rows = fixed + scr_h
    yo = max(0, (my - total_rows) // 2)

    def at(r, c):
        sys.stdout.write(f"\033[{yo + r + 1};{dx + 1}H{c}{RST}")

    def flush():
        sys.stdout.flush()

    def light_row(stage, status):
        big = FG_CYAN if stage >= 1 else FG_DKGRAY
        red = FG_RLED if stage >= 2 else FG_DKGRAY
        yellow = FG_YLED if stage >= 3 else FG_DKGRAY
        green = FG_GLED if stage >= 4 else FG_DKGRAY
        lights = (f"  {big}\u25c9{FG_WHITE}   "
                  f"{red}\u25cf{FG_WHITE} {yellow}\u25cf{FG_WHITE} {green}\u25cf{FG_WHITE}")
        status = _plain_clip(status, max(0, inn - _vl(lights) - 2))
        pad = max(1, inn - _vl(lights) - len(status))
        return _cas(inn, f"{lights}{' ' * pad}{FG_GRAY}{status}")

    def center_raw(text, width):
        text = _plain_clip(text, width)
        return f"{' ' * max(0, (width - len(text)) // 2)}{text}"

    def screen_center(text, color=FG_SCRTXT, width=None):
        width = sw if width is None else width
        return f"{color}{_ansi_center(_plain_clip(text, width), width)}"

    def logo_wordmark_lines():
        logo_blue = "\033[38;2;31;86;170m"
        logo_yellow = "\033[38;2;255;224;45m"
        glyphs = {
            "P": [
                "#### ",
                "#   #",
                "#   #",
                "#### ",
                "#    ",
                "#    ",
                "#    ",
            ],
            "O": [
                " ### ",
                "#   #",
                "#   #",
                "#   #",
                "#   #",
                "#   #",
                " ### ",
            ],
            "K": [
                "#   #",
                "#  # ",
                "# #  ",
                "##   ",
                "# #  ",
                "#  # ",
                "#   #",
            ],
            "É": [
                "  ## ",
                "#####",
                "#    ",
                "#### ",
                "#    ",
                "#    ",
                "#####",
            ],
            "D": [
                "#### ",
                "#   #",
                "#   #",
                "#   #",
                "#   #",
                "#   #",
                "#### ",
            ],
            "E": [
                "#####",
                "#    ",
                "#    ",
                "#### ",
                "#    ",
                "#    ",
                "#####",
            ],
            "X": [
                "#   #",
                " # # ",
                "  #  ",
                "  #  ",
                "  #  ",
                " # # ",
                "#   #",
            ],
        }
        word = "POKÉDEX"
        mask = []
        for y in range(7):
            mask.append(" ".join(glyphs[ch][y] for ch in word))
        width = max(len(row) for row in mask)
        mask = [row.ljust(width) for row in mask]
        if sw < width + 4 or scr_h < 9:
            rows = [
                screen_center("POKÉDEX", f"{BOLD}{FG_YLED}"),
                screen_center("NACIONAL I-V", FG_SCRHI),
                screen_center(f"{REAL_POKE_COUNT} ESPECIES", FG_SCRTXT),
            ]
            return rows

        rendered = []
        height = len(mask)
        for y in range(-1, height + 1):
            parts = []
            for x in range(-1, width + 1):
                filled = 0 <= y < height and 0 <= x < width and mask[y][x] == "#"
                near = False
                if not filled:
                    for yy in range(y - 1, y + 2):
                        for xx in range(x - 1, x + 2):
                            if (0 <= yy < height and 0 <= xx < width
                                    and mask[yy][xx] == "#"):
                                near = True
                                break
                        if near:
                            break
                if filled:
                    parts.append(f"{BOLD}{logo_yellow}█")
                elif near:
                    parts.append(f"{BOLD}{logo_blue}█")
                else:
                    parts.append(" ")
            rendered.append(_ansi_center("".join(parts), sw))

        subtitle = screen_center(
            f"NACIONAL I-V  ·  {REAL_POKE_COUNT} ESPECIES", FG_SCRHI)
        rendered.append(" " * sw)
        rendered.append(subtitle)
        return rendered

    def lid_border(kind):
        l, r = ("\u256d", "\u256e") if kind == "top" else ("\u2570", "\u256f")
        brd = f"{FG_WHITE}{l}{'─' * sw}{r}"
        return _cas(inn, f"{' ' * mrg}{brd}{' ' * mrg}")

    def lid_content(row_idx, width):
        if width <= 0:
            return ""
        title_row = max(0, scr_h // 2 - 2)
        latch_row = min(scr_h - 1, title_row + 2)
        seam_row = min(scr_h - 1, latch_row + 2)
        if row_idx == title_row and width >= 7:
            return f"{BOLD}{FG_WHITE}{center_raw('POKÉDEX', width)}"
        if row_idx == latch_row:
            if width >= 5:
                return f"{FG_DKGRAY}{' ' * max(0, width - 3)}▣  "
            return f"{FG_DKGRAY}{'■' * width}"
        if row_idx == seam_row:
            return f"{FG_DKGRAY}{'━' * width}"
        return " " * width

    def lid_row(row_idx):
        content = lid_content(row_idx, sw)
        pad = max(0, sw - _vl(content))
        inner = (f"{' ' * mrg}{FG_WHITE}│{BG_RED}{content}"
                 f"{BG_RED}{' ' * pad}{FG_WHITE}│{' ' * mrg}")
        return _cas(inn, inner)

    def boot_lines():
        rows = [""] * scr_h
        content = logo_wordmark_lines()
        top = max(0, (scr_h - len(content)) // 2)
        for i, line in enumerate(content):
            if top + i < scr_h:
                rows[top + i] = line
        return rows

    ready_rows = boot_lines()

    def opening_row(row_idx, cover_cols, scan=None):
        cover_cols = max(0, min(sw, cover_cols))
        if cover_cols <= 0:
            content = ""
            if scan == row_idx:
                content = f"{FG_SCRHI}{'─' * sw}"
            return _scr_row(inn, mrg, sw, content)

        edge_w = 1 if cover_cols < sw else 0
        green_w = max(0, sw - cover_cols - edge_w)
        cover = lid_content(row_idx, cover_cols)
        cover += " " * max(0, cover_cols - _vl(cover))
        edge = f"{FG_WHITE}▐" if edge_w else ""
        if scan == row_idx:
            screen = f"{FG_SCRHI}{'─' * green_w}"
        else:
            screen = " " * green_w
        inner = (f"{' ' * mrg}{FG_SCRHI}│{BG_RED}{cover}{edge}"
                 f"{BG_SCR}{screen}{FG_SCRHI}│{' ' * mrg}")
        return _cas(inn, inner)

    def draw_frame(stage=0, cover_cols=sw, closed=False,
                   lines=None, scan=None, status=""):
        row = 0
        at(row, _heavy(inn, "top")); row += 1
        at(row, light_row(stage, status)); row += 1
        at(row, _cas(inn)); row += 1
        hinge = f"  {FG_DKGRAY}{'═' * max(4, inn - 4)}"
        at(row, _cas(inn, hinge)); row += 1

        if closed:
            at(row, lid_border("top")); row += 1
            for i in range(scr_h):
                at(row, lid_row(i)); row += 1
            at(row, lid_border("bottom")); row += 1
        else:
            at(row, _scr_brd(inn, mrg, sw, "top")); row += 1
            lines = lines or [""] * scr_h
            for i in range(scr_h):
                if cover_cols <= 0 and lines:
                    content = lines[i] if i < len(lines) else ""
                    if scan == i:
                        content = f"{FG_SCRHI}{'─' * sw}"
                    at(row, _scr_row(inn, mrg, sw, content))
                else:
                    at(row, opening_row(i, cover_cols, scan=scan))
                row += 1
            at(row, _scr_brd(inn, mrg, sw, "bottom")); row += 1

        at(row, _cas(inn)); row += 1
        at(row, _cas(inn, f"  {FG_GRAY}{_plain_clip(status, max(0, inn - 2))}")); row += 1
        at(row, _heavy(inn, "bottom"))
        flush()

    _clear()
    draw_frame(0, closed=True, status="CERRADA")
    time.sleep(0.16)
    _sfx("dex_latch", volume=0.42)
    draw_frame(1, closed=True, status="CLICK")
    time.sleep(0.12)

    _sfx("dex_hinge", volume=0.48)
    steps = 24
    for step in range(steps + 1):
        progress = step / steps
        eased = progress * progress * (3.0 - 2.0 * progress)
        cover_cols = int(round(sw * (1.0 - eased)))
        stage = min(4, 1 + int(progress * 3))
        if step in (4, 12, 21):
            _sfx("dex_open", volume=0.32)
        draw_frame(stage, cover_cols=cover_cols, status="ABRIENDO")
        time.sleep(0.025)

    _sfx("dex_power", volume=0.32)
    scan_steps = min(12, max(4, scr_h))
    scan_rows = sorted(set(
        int(i * (scr_h - 1) / max(1, scan_steps - 1))
        for i in range(scan_steps)
    ))
    for n, scan in enumerate(scan_rows):
        if n in (0, len(scan_rows) // 2):
            _sfx("dex_scan", volume=0.2)
        draw_frame(4, cover_cols=0, lines=[""] * scr_h,
                   scan=scan, status="INICIANDO")
        time.sleep(0.022)

    _sfx("dex_ready", volume=0.38)
    draw_frame(4, cover_cols=0, lines=ready_rows, status="POKÉDEX")
    time.sleep(2.2)


# ── Main ─────────────────────────────────────────────────────────────────────

MODE_LIST = 0
MODE_DETAIL = 1
MODE_QUIZ_MENU = 2
MODE_QUIZ = 3
MODE_QUIZ_END = 4
MODE_CRY_QUIZ = 5
MODE_SAFARI_ENTER = 6
MODE_SAFARI_ENCOUNTER = 7
MODE_SAFARI_RESULT = 8
MODE_SAFARI_END = 9
MODE_MEMORY_MENU = 10
MODE_MEMORY_GAME = 11
MODE_MEMORY_END = 12
MODE_GYM_MENU = 13
MODE_GYM_BATTLE = 14

QUIZ_OPTIONS = [10, 25, 50, 151, 649]
GAME_MODES = ["Silueta", "Cry", "Descripcion", "Tipo"]

# Memory minigame — difficulty = maximum requested pair count.
MEMORY_DIFFICULTIES = [
    ("Facil", 6),
    ("Normal", 8),
    ("Dificil", 10),
]

# Gym Challenge — compact team battles through the Kanto leaders.
GYM_LEADERS = [
    {
        "name": "Brock", "badge": "Roca", "city": "Ciudad Plateada",
        "ace_num": 95, "level": 14,
        "team": [(74, 12), (95, 14)],
        "moves": [
            ("Lanzarrocas", "rock", 55),
            ("Atadura", "normal", 35),
            ("Placaje", "normal", 40),
            ("Fortaleza", "rock", 30),
        ],
    },
    {
        "name": "Misty", "badge": "Cascada", "city": "Ciudad Celeste",
        "ace_num": 121, "level": 21,
        "team": [(120, 18), (121, 21)],
        "moves": [
            ("Pistola Agua", "water", 55),
            ("Burbuja", "water", 40),
            ("Placaje", "normal", 40),
            ("Rapidez", "normal", 60),
        ],
    },
    {
        "name": "Lt. Surge", "badge": "Trueno", "city": "Ciudad Carmín",
        "ace_num": 26, "level": 24,
        "team": [(100, 21), (25, 18), (26, 24)],
        "moves": [
            ("Impactrueno", "electric", 50),
            ("Rayo", "electric", 80),
            ("Ataque Rapido", "normal", 40),
            ("Placaje", "normal", 40),
        ],
    },
    {
        "name": "Erika", "badge": "Arcoiris", "city": "Ciudad Azulona",
        "ace_num": 45, "level": 29,
        "team": [(71, 29), (114, 24), (45, 29)],
        "moves": [
            ("Hoja Afilada", "grass", 70),
            ("Absorber", "grass", 35),
            ("Acido", "poison", 40),
            ("Placaje", "normal", 40),
        ],
    },
    {
        "name": "Koga", "badge": "Alma", "city": "Ciudad Fucsia",
        "ace_num": 110, "level": 43,
        "team": [(109, 37), (89, 39), (110, 43)],
        "moves": [
            ("Residuos", "poison", 65),
            ("Bomba Lodo", "poison", 80),
            ("Placaje", "normal", 40),
            ("Explosion", "normal", 90),
        ],
    },
    {
        "name": "Sabrina", "badge": "Pantano", "city": "Ciudad Azafrán",
        "ace_num": 65, "level": 43,
        "team": [(64, 38), (122, 37), (49, 38), (65, 43)],
        "moves": [
            ("Psicorrayo", "psychic", 65),
            ("Psíquico", "psychic", 90),
            ("Rapidez", "normal", 60),
            ("Puño", "fighting", 50),
        ],
    },
    {
        "name": "Blaine", "badge": "Volcán", "city": "Isla Canela",
        "ace_num": 59, "level": 47,
        "team": [(58, 42), (77, 40), (78, 42), (59, 47)],
        "moves": [
            ("Lanzallamas", "fire", 90),
            ("Ascuas", "fire", 40),
            ("Mordisco", "normal", 55),
            ("Derribo", "normal", 70),
        ],
    },
    {
        "name": "Giovanni", "badge": "Tierra", "city": "Ciudad Verde",
        "ace_num": 112, "level": 50,
        "team": [(111, 45), (51, 42), (31, 44), (34, 45), (112, 50)],
        "moves": [
            ("Terremoto", "ground", 100),
            ("Avalancha", "rock", 75),
            ("Cornada", "normal", 65),
            ("Placaje", "normal", 40),
        ],
    },
]

GYM_FALLBACK_ROSTER = [24, 5, 8, 2, 142, 130, 93, 148]
GYM_SPECIAL_TYPES = {"fire", "water", "electric", "grass",
                     "ice", "psychic", "dragon", "dark"}

# Mostly modern type logic with Gen-1-relevant types; extra types are present
# because the data source may return modern typings for Kanto species.
TYPE_EFFECTIVENESS = {
    "normal": {"rock": 0.5, "ghost": 0.0, "steel": 0.5},
    "fire": {"fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 2.0,
             "bug": 2.0, "rock": 0.5, "dragon": 0.5, "steel": 2.0},
    "water": {"fire": 2.0, "water": 0.5, "grass": 0.5, "ground": 2.0,
              "rock": 2.0, "dragon": 0.5},
    "electric": {"water": 2.0, "electric": 0.5, "grass": 0.5,
                 "ground": 0.0, "flying": 2.0, "dragon": 0.5},
    "grass": {"fire": 0.5, "water": 2.0, "grass": 0.5, "poison": 0.5,
              "ground": 2.0, "flying": 0.5, "bug": 0.5, "rock": 2.0,
              "dragon": 0.5, "steel": 0.5},
    "ice": {"fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 0.5,
            "ground": 2.0, "flying": 2.0, "dragon": 2.0, "steel": 0.5},
    "fighting": {"normal": 2.0, "ice": 2.0, "poison": 0.5,
                 "flying": 0.5, "psychic": 0.5, "bug": 0.5,
                 "rock": 2.0, "ghost": 0.0, "dark": 2.0, "steel": 2.0,
                 "fairy": 0.5},
    "poison": {"grass": 2.0, "poison": 0.5, "ground": 0.5,
               "rock": 0.5, "ghost": 0.5, "steel": 0.0, "fairy": 2.0},
    "ground": {"fire": 2.0, "electric": 2.0, "grass": 0.5,
               "poison": 2.0, "flying": 0.0, "bug": 0.5,
               "rock": 2.0, "steel": 2.0},
    "flying": {"electric": 0.5, "grass": 2.0, "fighting": 2.0,
               "bug": 2.0, "rock": 0.5, "steel": 0.5},
    "psychic": {"fighting": 2.0, "poison": 2.0, "psychic": 0.5,
                "dark": 0.0, "steel": 0.5},
    "bug": {"fire": 0.5, "grass": 2.0, "fighting": 0.5,
            "poison": 0.5, "flying": 0.5, "psychic": 2.0,
            "ghost": 0.5, "dark": 2.0, "steel": 0.5, "fairy": 0.5},
    "rock": {"fire": 2.0, "ice": 2.0, "fighting": 0.5,
             "ground": 0.5, "flying": 2.0, "bug": 2.0, "steel": 0.5},
    "ghost": {"normal": 0.0, "psychic": 2.0, "ghost": 2.0, "dark": 0.5},
    "dragon": {"dragon": 2.0, "steel": 0.5, "fairy": 0.0},
    "dark": {"fighting": 0.5, "psychic": 2.0, "ghost": 2.0,
             "dark": 0.5, "fairy": 0.5},
    "steel": {"fire": 0.5, "water": 0.5, "electric": 0.5,
              "ice": 2.0, "rock": 2.0, "steel": 0.5, "fairy": 2.0},
    "fairy": {"fire": 0.5, "fighting": 2.0, "poison": 0.5,
              "dragon": 2.0, "dark": 2.0, "steel": 0.5},
}

GYM_TYPE_MOVES = {
    "normal": ("Golpe Cuerpo", "normal", 65),
    "fire": ("Lanzallamas", "fire", 90),
    "water": ("Surf", "water", 90),
    "electric": ("Rayo", "electric", 80),
    "grass": ("Hoja Afilada", "grass", 70),
    "ice": ("Rayo Hielo", "ice", 90),
    "fighting": ("Karate Chop", "fighting", 50),
    "poison": ("Residuos", "poison", 65),
    "ground": ("Excavar", "ground", 80),
    "flying": ("Ataque Ala", "flying", 60),
    "psychic": ("Psíquico", "psychic", 90),
    "bug": ("Dobleataque", "bug", 50),
    "rock": ("Avalancha", "rock", 75),
    "ghost": ("Lengüetazo", "ghost", 40),
    "dragon": ("Furia Dragon", "dragon", 60),
    "dark": ("Mordisco", "dark", 60),
    "steel": ("Garra Metal", "steel", 50),
    "fairy": ("Brillo Mágico", "fairy", 75),
}

# fmt: off
CATCH_RATES = {
    "bulbasaur": 45, "ivysaur": 45, "venusaur": 45,
    "charmander": 45, "charmeleon": 45, "charizard": 45,
    "squirtle": 45, "wartortle": 45, "blastoise": 45,
    "caterpie": 255, "metapod": 120, "butterfree": 45,
    "weedle": 255, "kakuna": 120, "beedrill": 45,
    "pidgey": 255, "pidgeotto": 120, "pidgeot": 45,
    "rattata": 255, "raticate": 127,
    "spearow": 255, "fearow": 90,
    "ekans": 255, "arbok": 90,
    "pikachu": 190, "raichu": 75,
    "sandshrew": 255, "sandslash": 90,
    "nidoranf": 235, "nidorina": 120, "nidoqueen": 45,
    "nidoranm": 235, "nidorino": 120, "nidoking": 45,
    "clefairy": 150, "clefable": 25,
    "vulpix": 190, "ninetales": 75,
    "jigglypuff": 170, "wigglytuff": 50,
    "zubat": 255, "golbat": 90,
    "oddish": 255, "gloom": 120, "vileplume": 45,
    "paras": 190, "parasect": 75,
    "venonat": 190, "venomoth": 75,
    "diglett": 255, "dugtrio": 50,
    "meowth": 255, "persian": 90,
    "psyduck": 190, "golduck": 75,
    "mankey": 190, "primeape": 75,
    "growlithe": 190, "arcanine": 75,
    "poliwag": 255, "poliwhirl": 120, "poliwrath": 45,
    "abra": 200, "kadabra": 100, "alakazam": 50,
    "machop": 180, "machoke": 90, "machamp": 45,
    "bellsprout": 255, "weepinbell": 120, "victreebel": 45,
    "tentacool": 190, "tentacruel": 60,
    "geodude": 255, "graveler": 120, "golem": 45,
    "ponyta": 190, "rapidash": 60,
    "slowpoke": 190, "slowbro": 75,
    "magnemite": 190, "magneton": 60,
    "farfetchd": 45,
    "doduo": 190, "dodrio": 45,
    "seel": 190, "dewgong": 75,
    "grimer": 190, "muk": 75,
    "shellder": 190, "cloyster": 60,
    "gastly": 190, "haunter": 90, "gengar": 45,
    "onix": 45,
    "drowzee": 190, "hypno": 75,
    "krabby": 225, "kingler": 60,
    "voltorb": 190, "electrode": 60,
    "exeggcute": 90, "exeggutor": 45,
    "cubone": 190, "marowak": 75,
    "hitmonlee": 45, "hitmonchan": 45,
    "lickitung": 45,
    "koffing": 190, "weezing": 60,
    "rhyhorn": 120, "rhydon": 60,
    "chansey": 30, "tangela": 45, "kangaskhan": 45,
    "horsea": 225, "seadra": 75,
    "goldeen": 225, "seaking": 60,
    "staryu": 225, "starmie": 60,
    "mr. mime": 45, "scyther": 45, "jynx": 45,
    "electabuzz": 45, "magmar": 45, "pinsir": 45,
    "tauros": 45,
    "magikarp": 255, "gyarados": 45,
    "lapras": 45, "ditto": 35,
    "eevee": 45, "vaporeon": 45, "jolteon": 45, "flareon": 45,
    "porygon": 45,
    "omanyte": 45, "omastar": 45,
    "kabuto": 45, "kabutops": 45,
    "aerodactyl": 45, "snorlax": 25,
    "articuno": 3, "zapdos": 3, "moltres": 3,
    "dratini": 45, "dragonair": 45, "dragonite": 45,
    "mewtwo": 3, "mew": 45,
}
# fmt: on


def _clear():
    """Home cursor + clear screen + clear scrollback + hide cursor."""
    sys.stdout.write("\033[H\033[2J\033[3J\033[?25l")
    sys.stdout.flush()


def _readkey(timeout=None):
    """Read a single keypress; returns a string token.

    Returns "RESIZE" if SIGWINCH fired during the read, "TIMEOUT" if
    `timeout` (seconds) elapsed without input. Captures the original
    termios on the first call so atexit can restore it cleanly.
    """
    global _resize_pending
    fd = sys.stdin.fileno()
    if _orig_termios is None:
        _capture_termios()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        # Already-pending resize → return immediately
        if _resize_pending:
            _resize_pending = False
            return "RESIZE"
        # Optional timeout via select before blocking read. SIGWINCH may
        # interrupt either select() or os.read(); both raise InterruptedError
        # when siginterrupt(SIGWINCH, False) is in effect.
        if timeout is not None:
            try:
                r, _, _ = select.select([fd], [], [], timeout)
            except InterruptedError:
                if _resize_pending:
                    _resize_pending = False
                    return "RESIZE"
                return "TIMEOUT"
            if not r:
                if _resize_pending:
                    _resize_pending = False
                    return "RESIZE"
                return "TIMEOUT"
        try:
            ch = os.read(fd, 1)
        except InterruptedError:
            if _resize_pending:
                _resize_pending = False
                return "RESIZE"
            return "TIMEOUT"
        if not ch:
            return "TIMEOUT"
        if ch == b"\x1b":
            if select.select([fd], [], [], 0.1)[0]:
                ch2 = os.read(fd, 1)
                if ch2 == b"[":
                    ch3 = os.read(fd, 1)
                    if ch3 == b"A": return "UP"
                    if ch3 == b"B": return "DOWN"
                    if ch3 == b"C": return "RIGHT"
                    if ch3 == b"D": return "LEFT"
                    if ch3 == b"H": return "HOME"
                    if ch3 == b"F": return "END"
                    if ch3 in (b"5", b"6"):
                        os.read(fd, 1)  # consume ~
                        return "PGUP" if ch3 == b"5" else "PGDN"
            return "ESC"
        if ch in (b"\r", b"\n"): return "ENTER"
        if ch in (b"\x7f", b"\x08"): return "BS"
        if ch == b"\x03": raise KeyboardInterrupt
        # Collect UTF-8 continuation bytes so ñ/é/ü land as one character
        if ch[0] >= 0x80:
            if ch[0] >= 0xF0:
                n_more = 3
            elif ch[0] >= 0xE0:
                n_more = 2
            elif ch[0] >= 0xC0:
                n_more = 1
            else:
                n_more = 0
            for _ in range(n_more):
                if select.select([fd], [], [], 0.05)[0]:
                    ch += os.read(fd, 1)
                else:
                    break
        return ch.decode("utf-8", errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main(args=None):
    global AUDIO_MUTED, SPRITE_STYLE_IDX
    # Load persisted stats (mute, palette, seen, caught, etc.)
    _load_stats()
    AUDIO_MUTED = bool(STATS.get("mute"))
    _apply_palette(STATS.get("palette_idx", 0))
    SPRITE_STYLE_IDX = max(0, min(
        len(SPRITE_STYLES) - 1, int(STATS.get("sprite_style", 0))))

    # Daily Pokemon: surface a banner the first time we open today.
    daily_idx = _daily_pokemon_idx()
    daily_num, daily_name = POKEMON[daily_idx]
    today_iso = datetime.date.today().isoformat()
    daily_banner = ""
    if STATS.get("last_open_date") != today_iso:
        daily_banner = f"★ Pokemon del dia: #{daily_num:03d} {_dn(daily_name)}"
        STATS["last_open_date"] = today_iso
        _save_stats()

    # Apply CLI overrides if provided
    if args is not None:
        if getattr(args, "no_audio", False):
            AUDIO_MUTED = True
        if getattr(args, "palette", None):
            for i, p in enumerate(PALETTES):
                if p[0].lower().replace(" ", "") == args.palette.lower().replace(" ", ""):
                    _apply_palette(i)
                    break
        if getattr(args, "sprite_gen", None):
            for i, (key, _, _) in enumerate(SPRITE_STYLES):
                if key == args.sprite_gen:
                    SPRITE_STYLE_IDX = i
                    break

    # Enter alternate screen buffer (no scrollback!) + hide cursor
    global _entered_alt_screen
    _entered_alt_screen = True
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()

    mode = MODE_LIST
    cursor = 0       # list cursor
    detail_idx = 0   # detail view index
    prev_detail = -1 # for cry trigger
    s_mode = False
    s_buf = ""
    msg = daily_banner
    spr_cache = {}   # (idx, max_rows, tw) -> lines | None
    sil_cache = {}   # (idx, max_rows, tw) -> shiny lines | None
    shiny_cache = {} # (idx, max_rows, tw) -> shiny lines | None
    data_cache = {}  # idx -> dict | None
    show_shiny = False        # toggle in detail with 's'
    show_moves = False        # part of the panel cycle in detail
    show_stats_panel = False  # part of the panel cycle in detail
    show_help = False         # toggle help overlay with '?'
    autoplay = False          # screensaver mode
    autoplay_delay = 4.5      # seconds
    need_clear = True

    # Quiz state
    quiz_menu_cursor = 0
    quiz_game_mode = 0   # 0=Silueta, 1=Cry
    quiz_queue = []      # shuffled list of POKEMON indices
    quiz_pos = 0         # current position in queue
    quiz_total_q = 0     # total pokemon in this quiz round
    quiz_phase = "ask"
    quiz_buf = ""
    quiz_score = 0
    quiz_spr = None
    quiz_sil = None
    quiz_answer = ""
    cry_played = False   # track if cry was played for current cry quiz question
    desc_spoken = False  # track description TTS per description-quiz question

    # Safari state
    safari_balls = 30
    safari_captured = []          # [(num, name), ...]
    safari_cur_idx = -1           # index in POKEMON
    safari_cur_spr = None         # rendered sprite (normal)
    safari_cur_sil = None         # white silhouette for absorb animation
    safari_cur_refl = None        # faded reflection below the grass
    safari_need_appear = False    # trigger slide-in on next draw
    safari_action_cursor = 0      # 0=Bola, 1=Roca, 2=Cebo, 3=Huir
    safari_anger = 0              # remaining anger turns
    safari_eating = 0             # remaining eating turns
    safari_result_msg = ""
    safari_result_type = ""       # "caught"/"fled"/"broke_free"/"out_of_balls"
    # Memory minigame state
    mem_diff_idx = 1               # Normal by default
    mem_cards = []                 # list[int] of POKEMON indices, paired+shuffled
    mem_flipped = []               # currently face-up unmatched (max 2)
    mem_matched = set()            # indices already paired
    mem_cursor = 0                 # which card is highlighted
    mem_tries = 0                  # pair attempts so far
    mem_start = 0.0                # timer start
    mem_final_elapsed = 0.0        # frozen timer for the end screen
    mem_rows = 0
    mem_cols = 0
    mem_card_w = 0
    mem_card_h = 0
    mem_gap_h = 1
    mem_pairs = 0
    mem_scroll_row = 0
    mem_new_record = False

    # Gym Challenge state
    gym_leader_cursor = 0
    gym_roster_cursor = 0
    gym_roster = []
    gym_player = None
    gym_enemy = None
    gym_player_team = []
    gym_enemy_team = []
    gym_player_slot = 0
    gym_enemy_slot = 0
    gym_player_spr = None
    gym_enemy_spr = None
    gym_move_cursor = 0
    gym_phase = "choose"
    gym_log = []
    gym_badge_new = False

    safari_ball_lines = None      # cached sprites
    safari_rock_lines = None
    safari_bait_lines = None
    safari_star_lines = None

    def invalidate_render_caches(include_safari_items=False):
        nonlocal safari_cur_spr, safari_cur_sil, safari_cur_refl
        nonlocal quiz_spr, quiz_sil
        nonlocal gym_player_spr, gym_enemy_spr
        nonlocal safari_ball_lines, safari_rock_lines
        nonlocal safari_bait_lines, safari_star_lines
        # Render caches are keyed by terminal dimensions/palette. Drop them so
        # the next draw renders fresh instead of growing stale entries forever.
        spr_cache.clear()
        sil_cache.clear()
        shiny_cache.clear()
        _memory_icon_render_cache.clear()
        safari_cur_spr = None
        safari_cur_sil = None
        safari_cur_refl = None
        quiz_spr = None
        quiz_sil = None
        gym_player_spr = None
        gym_enemy_spr = None
        if include_safari_items:
            safari_ball_lines = None
            safari_rock_lines = None
            safari_bait_lines = None
            safari_star_lines = None

    try:
        # Play intro animation (skip if jumping somewhere)
        sz = os.get_terminal_size()
        jump_target = None
        if args is not None:
            if getattr(args, "safari", False):
                jump_target = "safari"
            elif getattr(args, "gym", False):
                jump_target = "gym"
            elif getattr(args, "quiz", False):
                jump_target = "quiz"
            elif getattr(args, "screensaver", False):
                autoplay = True
                jump_target = "detail"
            elif getattr(args, "pokemon", None):
                idx = search(args.pokemon)
                if idx is not None:
                    detail_idx = idx
                    jump_target = "detail"
        if jump_target is None:
            play_intro(sz.lines, sz.columns)
        if jump_target == "safari":
            mode = MODE_SAFARI_ENTER
        elif jump_target == "gym":
            mode = MODE_GYM_MENU
        elif jump_target == "quiz":
            mode = MODE_QUIZ_MENU
        elif jump_target == "detail":
            mode = MODE_DETAIL
        need_clear = True

        while True:
            sz = os.get_terminal_size()
            my, mx = sz.lines, sz.columns
            if my < 16 or mx < 42:
                _clear()
                sys.stdout.write(
                    f"\033[1;1HTerminal muy peque\u00f1a ({mx}x{my}). Min: 42x16")
                sys.stdout.flush()
                _readkey()
                need_clear = True
                continue

            # ── LIST MODE ──
            if mode == MODE_LIST:
                if need_clear:
                    _clear()
                    need_clear = False
                draw_list(my, mx, cursor, s_mode, s_buf, msg)
                if show_help:
                    draw_help_overlay(my, mx)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if show_help:
                    show_help = False
                    need_clear = True
                    continue
                if key == "?":
                    _sfx_select()
                    show_help = True
                    continue
                if s_mode:
                    if key == "ESC":
                        _sfx_back()
                        s_mode = False; s_buf = ""; msg = ""
                    elif key == "ENTER":
                        r = search(s_buf)
                        if r is not None:
                            _sfx_select()
                            cursor = r; msg = ""
                        else:
                            _sfx("Basso", rate=1.25, volume=0.22)
                            msg = f"No encontrado: {s_buf}"
                        s_mode = False; s_buf = ""
                    elif key == "BS":
                        s_buf = s_buf[:-1]
                    elif len(key) == 1 and key.isprintable():
                        s_buf += key
                else:
                    msg = ""
                    if key in ("q", "Q"):
                        break
                    elif key in ("DOWN", "j", "s"):
                        old_cursor = cursor
                        cursor = min(POKE_COUNT - 1, cursor + 1)
                        if cursor != old_cursor:
                            _sfx_nav()
                    elif key in ("UP", "k", "w"):
                        old_cursor = cursor
                        cursor = max(0, cursor - 1)
                        if cursor != old_cursor:
                            _sfx_nav()
                    elif key == "PGDN":
                        old_cursor = cursor
                        cursor = min(POKE_COUNT - 1, cursor + 20)
                        if cursor != old_cursor:
                            _sfx_nav()
                    elif key == "PGUP":
                        old_cursor = cursor
                        cursor = max(0, cursor - 20)
                        if cursor != old_cursor:
                            _sfx_nav()
                    elif key == "HOME":
                        old_cursor = cursor
                        cursor = 0
                        if cursor != old_cursor:
                            _sfx_nav()
                    elif key == "END":
                        old_cursor = cursor
                        cursor = POKE_COUNT - 1
                        if cursor != old_cursor:
                            _sfx_nav()
                    elif key == "ENTER":
                        _sfx_select()
                        detail_idx = cursor
                        prev_detail = -1
                        mode = MODE_DETAIL
                        need_clear = True
                    elif key == "/":
                        _sfx_scan()
                        s_mode = True; s_buf = ""
                    elif key == "g":
                        _sfx_select()
                        mode = MODE_QUIZ_MENU
                        quiz_menu_cursor = 0
                        need_clear = True
                    elif key == "h":
                        _sfx_select()
                        mode = MODE_SAFARI_ENTER
                        need_clear = True
                    elif key == "M":
                        _sfx_select()
                        mode = MODE_MEMORY_MENU
                        need_clear = True
                    elif key in ("B", "b"):
                        _sfx_select()
                        gym_roster = _gym_roster()
                        gym_roster_cursor = min(gym_roster_cursor, max(0, len(gym_roster) - 1))
                        mode = MODE_GYM_MENU
                        need_clear = True
                    elif key == "p":
                        _sfx("Glass", rate=1.25, volume=0.32)
                        pname = _apply_palette(_palette_idx + 1)
                        STATS["palette_idx"] = _palette_idx
                        _save_stats()
                        invalidate_render_caches(include_safari_items=True)
                        msg = f"Tema: {pname}"
                        need_clear = True
                    elif key == "m":
                        now_muted = _toggle_mute()
                        if now_muted:
                            _stop_cry()
                            _kill_tts()
                        else:
                            _sfx("Ping", rate=1.1, volume=0.35)
                        msg = "Audio silenciado" if now_muted else "Audio activado"
                    elif key == "T":
                        path = export_trainer_card()
                        if path:
                            _sfx("Glass", rate=1.1, volume=0.35)
                        else:
                            _sfx("Basso", rate=1.0, volume=0.25)
                        msg = f"Tarjeta exportada: {path}" if path else "No se pudo exportar"

            # ── DETAIL MODE ──
            elif mode == MODE_DETAIL:
                num, name = POKEMON[detail_idx]
                dname = _dn(name)

                _, inn, mrg, sw, _ = _geom(mx)
                # New layout puts ALL chrome (name, genus, types, icons) BELOW
                # Render sprite for whichever detail layout the dispatcher
                # will choose. Wide terminals get the side-by-side layout
                # whose green screen is much taller and wider, so we render
                # at a much larger size there.
                if mx >= DETAIL_SIDE_THRESHOLD:
                    sg = _detail_side_geom(my, mx)
                    spr_tw = max(10, min(sg["gs_inner_w"], 96))
                    max_spr_rows = max(4, sg["sprite_area"])
                else:
                    # Stacked: 16 fixed rows + scr_h interior; 1 shadow row.
                    scr_h_est = max(8, my - 16)
                    spr_tw = max(10, min(sw - 2, 60))
                    max_spr_rows = max(4, scr_h_est - 1)

                needs_load = detail_idx not in data_cache
                # Cache keyed also by sprite style so generation swaps are clean.
                style_key = _sprite_style_key()
                ck = (detail_idx, max_spr_rows, spr_tw, style_key)
                needs_sprite = ck not in spr_cache
                needs_shiny = show_shiny and ck not in shiny_cache

                # Only _clear() on real transitions: first entry, mode
                # change, resize, palette change. Within the detail mode, the
                # draw_detail call has a stable row count so it overwrites the
                # previous frame cleanly and the red shell stays fixed on
                # screen — the dex no longer "disappears" when you navigate.
                is_new_pokemon = detail_idx != prev_detail

                if need_clear:
                    _clear()
                    need_clear = False

                data_ready = _data_disk_cached(num)
                sprite_ready = _sprite_disk_cached(name)
                cold_asset = (
                    (needs_load and not data_ready)
                    or ((needs_sprite or needs_shiny) and not sprite_ready)
                )
                if cold_asset:
                    _prefetch_indices([detail_idx])

                # Hot loads come from disk/cache and stay synchronous because
                # they are sub-millisecond. Cold network work happens in the
                # prefetch thread so holding an arrow never waits on HTTP.
                if needs_load and data_ready:
                    data_cache[detail_idx] = fetch_data(num)
                if needs_sprite and sprite_ready:
                    img = dl_sprite(name)
                    if img:
                        spr_cache[ck] = render_sprite(
                            img, spr_tw, bg_rgb=SCR_RGB,
                            max_rows=max_spr_rows)
                    else:
                        spr_cache[ck] = None
                if needs_shiny and sprite_ready:
                    img = dl_sprite(name)
                    if img:
                        shiny_cache[ck] = render_sprite(
                            _shiny_tint(img), spr_tw, bg_rgb=SCR_RGB,
                            max_rows=max_spr_rows)
                    else:
                        shiny_cache[ck] = None

                spr_regular = spr_cache.get(ck)
                spr_shiny = shiny_cache.get(ck) if show_shiny else None
                spr = spr_shiny if (show_shiny and spr_shiny) else spr_regular
                pd = data_cache.get(detail_idx)
                pending_data = needs_load and detail_idx not in data_cache
                pending_sprite = (
                    needs_sprite and ck not in spr_cache
                    or needs_shiny and ck not in shiny_cache
                )
                loading_assets = pending_data or pending_sprite
                genus = pd["genus"] if pd else (
                    "Cargando datos..." if pending_data else "")
                desc = pd["description"] if pd else (
                    "Descargando la ficha en segundo plano. Puedes seguir navegando."
                    if pending_data else "")
                types = pd.get("types", []) if pd else []
                stats = pd.get("stats", {}) if pd else {}
                evo_raw = pd.get("evolution", []) if pd else []
                moves_raw = pd.get("moves", []) if pd else []
                evolution = [(n, nm, n == num) for (n, nm) in evo_raw]
                draw_msg = msg
                if loading_assets and not msg and not s_mode:
                    draw_msg = "Cargando en segundo plano..."

                panel = "moves" if show_moves else ("stats" if show_stats_panel else "desc")

                # Reset per-pokemon UI toggles when we land on a fresh one.
                if is_new_pokemon:
                    _kill_tts()
                    show_moves = False
                    show_stats_panel = False

                cry_playing = (_cry_proc is not None
                               and _cry_proc.poll() is None)

                extra = {
                    "types": types, "stats": stats,
                    "evolution": evolution, "moves": moves_raw,
                    "panel": panel,
                }
                draw_detail(my, mx, num, dname, genus, desc,
                            spr, s_mode, s_buf, draw_msg,
                            extra=extra, breath_offset=0,
                            is_shiny=show_shiny, cry_playing=cry_playing)
                if show_help:
                    draw_help_overlay(my, mx)

                # Play the cry AFTER the sprite is on-screen so audio and
                # visual appear together — not cry first and then sprite.
                if is_new_pokemon:
                    play_cry(name)
                    if num > 0:
                        _mark_seen(num)
                    prev_detail = detail_idx
                    # Background-fetch the neighbors so the *next* nav has
                    # nothing to load — feels instant.
                    prefetch_neighbors(detail_idx)

                # Stamp glitches on top of the frame for MissingNo
                if num == 0:
                    _missingno_glitch(my, mx, count=12)

                # Only use a timeout when something on-screen genuinely needs
                # a periodic redraw. Otherwise block until the user presses a
                # key — avoids flicker from useless redraws every second.
                if autoplay:
                    read_timeout = autoplay_delay
                elif loading_assets:
                    read_timeout = 0.18  # poll for background cache completion
                elif cry_playing:
                    read_timeout = 0.5  # to erase the ♪ icon when the cry ends
                elif num == 0:
                    read_timeout = 0.35  # MissingNo glitch animation
                else:
                    read_timeout = None  # block until input

                key = _readkey(timeout=read_timeout)
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if key == "TIMEOUT":
                    if autoplay:
                        detail_idx = (detail_idx + 1) % REAL_POKE_COUNT
                        need_clear = True
                    # Else: just continue — draw_detail overwrites in place.
                    continue
                if show_help:
                    show_help = False
                    need_clear = True
                    continue
                if key == "?":
                    _sfx_select()
                    show_help = True
                    continue
                if s_mode:
                    if key == "ESC":
                        _sfx_back()
                        s_mode = False; s_buf = ""; msg = ""
                    elif key == "ENTER":
                        r = search(s_buf)
                        if r is not None:
                            _sfx_select()
                            detail_idx = r; msg = ""
                        else:
                            _sfx("Basso", rate=1.25, volume=0.22)
                            msg = f"No encontrado: {s_buf}"
                        s_mode = False; s_buf = ""
                    elif key == "BS":
                        s_buf = s_buf[:-1]
                    elif len(key) == 1 and key.isprintable():
                        s_buf += key
                else:
                    msg = ""
                    if key in ("q", "Q"):
                        break
                    elif key == "ESC":
                        _sfx_back()
                        _kill_tts()
                        cursor = detail_idx
                        autoplay = False
                        mode = MODE_LIST
                        need_clear = True
                    elif key in ("RIGHT", "d"):
                        # No fade: draw_detail overwrites the interior cleanly
                        # and the dex shell stays fixed on screen.
                        _sfx_nav()
                        detail_idx = (detail_idx + 1) % POKE_COUNT
                    elif key in ("LEFT", "a"):
                        _sfx_nav()
                        detail_idx = (detail_idx - 1) % POKE_COUNT
                    elif key == "c":
                        play_cry(name)
                    elif key == "v":
                        speak(dname, genus, desc)
                    elif key == "/":
                        _sfx_scan()
                        s_mode = True; s_buf = ""
                    elif key == "p":
                        _sfx("Glass", rate=1.25, volume=0.32)
                        pname = _apply_palette(_palette_idx + 1)
                        STATS["palette_idx"] = _palette_idx
                        _save_stats()
                        invalidate_render_caches(include_safari_items=True)
                        prev_detail = -1  # force sprite reload
                        msg = f"Tema: {pname}"
                        need_clear = True
                    elif key == "m":
                        now_muted = _toggle_mute()
                        if now_muted:
                            _stop_cry()
                            _kill_tts()
                        else:
                            _sfx("Ping", rate=1.1, volume=0.35)
                        msg = "Audio silenciado" if now_muted else "Audio activado"
                    elif key == "s":
                        _sfx("Glass", rate=1.45 if not show_shiny else 0.9, volume=0.28)
                        show_shiny = not show_shiny
                        if show_shiny and num > 0:
                            _mark_shiny(num)
                        msg = "Modo shiny ON" if show_shiny else "Modo shiny OFF"
                        need_clear = True
                    elif key == "n":
                        _sfx_nav()
                        # Cycle panel: desc → stats → moves → desc
                        if not show_stats_panel and not show_moves:
                            show_stats_panel = True
                        elif show_stats_panel and not show_moves:
                            show_stats_panel = False
                            show_moves = True
                        else:
                            show_moves = False
                            show_stats_panel = False
                        need_clear = True
                    elif key == "G":
                        _sfx("Glass", rate=1.1, volume=0.32)
                        # Cycle sprite generation (Gen 1 → Gen 2 → … → Gen 1).
                        # Cache keys include the style so the next render
                        # naturally pulls / draws the new generation.
                        SPRITE_STYLE_IDX = (SPRITE_STYLE_IDX + 1) % len(SPRITE_STYLES)
                        STATS["sprite_style"] = SPRITE_STYLE_IDX
                        _save_stats()
                        msg = f"Sprite: {SPRITE_STYLES[SPRITE_STYLE_IDX][2]}"
                    elif key == " ":
                        _sfx("Submarine", rate=1.3 if not autoplay else 0.9, volume=0.28)
                        autoplay = not autoplay
                        msg = "Autoplay ON (espacio para parar)" if autoplay else "Autoplay OFF"
                    elif key == "HOME":
                        if detail_idx != 0:
                            _sfx_nav()
                        detail_idx = 0
                    elif key == "END":
                        if detail_idx != POKE_COUNT - 1:
                            _sfx_nav()
                        detail_idx = POKE_COUNT - 1

            # ── QUIZ MENU ──
            elif mode == MODE_QUIZ_MENU:
                if need_clear:
                    _clear()
                    need_clear = False
                draw_quiz_menu(my, mx, quiz_menu_cursor, quiz_game_mode)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if key == "ESC":
                    _sfx_back()
                    mode = MODE_LIST
                    need_clear = True
                elif key in ("DOWN", "j", "s"):
                    old_cursor = quiz_menu_cursor
                    quiz_menu_cursor = min(len(QUIZ_OPTIONS) - 1, quiz_menu_cursor + 1)
                    if quiz_menu_cursor != old_cursor:
                        _sfx_nav()
                elif key in ("UP", "k", "w"):
                    old_cursor = quiz_menu_cursor
                    quiz_menu_cursor = max(0, quiz_menu_cursor - 1)
                    if quiz_menu_cursor != old_cursor:
                        _sfx_nav()
                elif key in ("LEFT", "a"):
                    quiz_game_mode = (quiz_game_mode - 1) % len(GAME_MODES)
                    _sfx_nav()
                elif key in ("RIGHT", "d"):
                    quiz_game_mode = (quiz_game_mode + 1) % len(GAME_MODES)
                    _sfx_nav()
                elif key == "ENTER":
                    _sfx_select()
                    # Build shuffled queue (indices 0-150, no MissingNo.)
                    quiz_total_q = QUIZ_OPTIONS[quiz_menu_cursor]
                    quiz_queue = random.sample(range(REAL_POKE_COUNT), quiz_total_q)
                    quiz_pos = 0
                    quiz_score = 0
                    quiz_phase = "ask"
                    quiz_buf = ""
                    quiz_spr = None
                    quiz_sil = None
                    cry_played = False
                    desc_spoken = False
                    if quiz_game_mode == 1:
                        mode = MODE_CRY_QUIZ
                    else:
                        mode = MODE_QUIZ
                    need_clear = True
                elif key in ("q", "Q"):
                    break

            # ── QUIZ MODE ──
            elif mode == MODE_QUIZ:
                qi = quiz_queue[quiz_pos]
                num, name = POKEMON[qi]
                quiz_answer = _dn(name)

                _, inn, mrg, sw, _ = _geom(mx)
                fixed_q = 11
                scr_h_q = max(3, my - fixed_q)
                spr_tw_q = max(10, sw)

                # Data needed for Descripcion (2) or Tipo (3) modes
                needs_data = quiz_game_mode in (2, 3) and qi not in data_cache

                if quiz_spr is None or needs_data:
                    if need_clear:
                        _clear()
                        spinner_start(my, mx)
                    if needs_data:
                        data_cache[qi] = fetch_data(num)
                    if quiz_spr is None:
                        img = dl_sprite(name)
                        if img:
                            quiz_spr = render_sprite(
                                img, spr_tw_q, bg_rgb=SCR_RGB,
                                max_rows=scr_h_q)
                            sil_img = _silhouette(img)
                            quiz_sil = render_sprite(
                                sil_img, spr_tw_q, bg_rgb=SCR_RGB,
                                max_rows=scr_h_q)
                        else:
                            quiz_spr = []
                            quiz_sil = []
                    spinner_stop()
                    need_clear = True

                if need_clear:
                    _clear()
                    need_clear = False

                current_num = quiz_pos + 1
                pd_q = data_cache.get(qi, {}) or {}
                desc_q = pd_q.get("description", "") if quiz_game_mode == 2 else None
                types_q = pd_q.get("types", []) if quiz_game_mode == 3 else None

                if quiz_phase == "ask":
                    # Silueta: silhouette sprite. Descripcion/Tipo: no sprite.
                    shown = quiz_sil if quiz_game_mode == 0 else None
                    draw_quiz(my, mx, shown, quiz_phase,
                              quiz_buf, quiz_answer, quiz_score,
                              current_num, quiz_total_q,
                              desc_text=desc_q, types_list=types_q)
                    if quiz_game_mode == 2 and desc_q and not desc_spoken:
                        speak_text_es(desc_q)
                        desc_spoken = True
                else:
                    draw_quiz(my, mx, quiz_spr, quiz_phase,
                              quiz_buf, quiz_answer, quiz_score,
                              current_num, quiz_total_q)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue

                if quiz_phase == "ask":
                    if key == "ESC":
                        _sfx_back()
                        if quiz_game_mode == 2:
                            _kill_tts()
                        mode = MODE_LIST
                        need_clear = True
                    elif quiz_game_mode == 2 and key in ("\t", "v", "V") and desc_q:
                        speak_text_es(desc_q)
                    elif key == "ENTER" and quiz_buf.strip():
                        if quiz_game_mode == 2:
                            _kill_tts()
                        guesses = {
                            _lookup_key(quiz_buf),
                            _lookup_compact_key(quiz_buf),
                        }
                        if guesses & _answer_keys(name):
                            quiz_phase = "correct"
                            quiz_score += 1
                            play_cry(name)
                        else:
                            _sfx("Basso", rate=1.05, volume=0.3)
                            quiz_phase = "wrong"
                        need_clear = True
                    elif key == "BS":
                        quiz_buf = quiz_buf[:-1]
                    elif len(key) == 1 and key.isprintable():
                        quiz_buf += key
                else:
                    # Result phase → next or end
                    if key == "ESC":
                        _sfx_back()
                        if quiz_game_mode == 2:
                            _kill_tts()
                        mode = MODE_LIST
                        need_clear = True
                    elif key == "ENTER" or (len(key) == 1 and key not in ("q", "Q")):
                        _sfx_select()
                        quiz_pos += 1
                        if quiz_pos >= quiz_total_q:
                            _set_best_quiz(
                                GAME_MODES[quiz_game_mode].lower(),
                                quiz_total_q, quiz_score)
                            mode = MODE_QUIZ_END
                        else:
                            quiz_phase = "ask"
                            quiz_buf = ""
                            quiz_spr = None
                            quiz_sil = None
                            desc_spoken = False
                        need_clear = True
                    elif key in ("q", "Q"):
                        if quiz_game_mode == 2:
                            _kill_tts()
                        break

            # ── CRY QUIZ MODE ──
            elif mode == MODE_CRY_QUIZ:
                qi = quiz_queue[quiz_pos]
                num, name = POKEMON[qi]
                quiz_answer = _dn(name)

                # Pre-download cry file if needed
                if not _cry_disk_cached(name):
                    if need_clear:
                        _clear()
                        sys.stdout.write(
                            f"\033[{my//2};{max(1,(mx-18)//2)}HCargando...")
                        sys.stdout.flush()
                    _cache_cry(name)
                    need_clear = True

                # Load sprite for reveal phase
                if quiz_phase != "ask" and quiz_spr is None:
                    _, inn, mrg, sw, _ = _geom(mx)
                    fixed_q = 11
                    scr_h_q = max(3, my - fixed_q)
                    spr_tw_q = max(10, sw)
                    img = dl_sprite(name)
                    if img:
                        quiz_spr = render_sprite(
                            img, spr_tw_q, bg_rgb=SCR_RGB,
                            max_rows=scr_h_q)
                    else:
                        quiz_spr = []
                    need_clear = True

                # Play cry on first show of each question
                if not cry_played:
                    play_cry(name)
                    cry_played = True

                if need_clear:
                    _clear()
                    need_clear = False

                current_num = quiz_pos + 1
                draw_cry_quiz(my, mx, quiz_spr, quiz_phase,
                              quiz_buf, quiz_answer, quiz_score,
                              current_num, quiz_total_q)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue

                if quiz_phase == "ask":
                    if key == "ESC":
                        _stop_cry()
                        mode = MODE_LIST
                        need_clear = True
                    elif key == "\t":
                        play_cry(name)
                    elif key == "ENTER" and quiz_buf.strip():
                        guesses = {
                            _lookup_key(quiz_buf),
                            _lookup_compact_key(quiz_buf),
                        }
                        if guesses & _answer_keys(name):
                            quiz_phase = "correct"
                            quiz_score += 1
                        else:
                            quiz_phase = "wrong"
                        play_cry(name)
                        need_clear = True
                    elif key == "BS":
                        quiz_buf = quiz_buf[:-1]
                    elif len(key) == 1 and key.isprintable():
                        quiz_buf += key
                else:
                    # Result phase → next or end
                    if key == "ESC":
                        mode = MODE_LIST
                        need_clear = True
                    elif key == "ENTER" or (len(key) == 1 and key not in ("q", "Q")):
                        quiz_pos += 1
                        if quiz_pos >= quiz_total_q:
                            _set_best_quiz(
                                GAME_MODES[quiz_game_mode].lower(),
                                quiz_total_q, quiz_score)
                            mode = MODE_QUIZ_END
                        else:
                            quiz_phase = "ask"
                            quiz_buf = ""
                            quiz_spr = None
                            cry_played = False
                        need_clear = True
                    elif key in ("q", "Q"):
                        break

            # ── QUIZ END ──
            elif mode == MODE_QUIZ_END:
                if need_clear:
                    _clear()
                    need_clear = False
                draw_quiz_end(my, mx, quiz_score, quiz_total_q)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if key == "ESC" or key in ("q", "Q"):
                    mode = MODE_LIST
                    need_clear = True
                elif key == "ENTER":
                    # Replay with same settings and same game mode
                    quiz_queue = random.sample(range(REAL_POKE_COUNT), quiz_total_q)
                    quiz_pos = 0
                    quiz_score = 0
                    quiz_phase = "ask"
                    quiz_buf = ""
                    quiz_spr = None
                    quiz_sil = None
                    cry_played = False
                    desc_spoken = False
                    if quiz_game_mode == 1:
                        mode = MODE_CRY_QUIZ
                    else:
                        mode = MODE_QUIZ
                    need_clear = True

            # ── GYM CHALLENGE MENU ──
            elif mode == MODE_GYM_MENU:
                if not gym_roster:
                    gym_roster = _gym_roster()
                    gym_roster_cursor = 0
                if need_clear:
                    _clear()
                    need_clear = False
                draw_gym_menu(my, mx, gym_leader_cursor,
                              gym_roster_cursor, gym_roster)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if key == "ESC":
                    mode = MODE_LIST
                    need_clear = True
                elif key in ("q", "Q"):
                    break
                elif key in ("DOWN", "j", "s"):
                    gym_leader_cursor = min(len(GYM_LEADERS) - 1, gym_leader_cursor + 1)
                elif key in ("UP", "k", "w"):
                    gym_leader_cursor = max(0, gym_leader_cursor - 1)
                elif key in ("RIGHT", "d"):
                    gym_roster_cursor = (gym_roster_cursor + 1) % len(gym_roster)
                elif key in ("LEFT", "a"):
                    gym_roster_cursor = (gym_roster_cursor - 1) % len(gym_roster)
                elif key == "ENTER":
                    leader = GYM_LEADERS[gym_leader_cursor]
                    player_indices = _gym_player_team_indices(
                        gym_roster, gym_roster_cursor, 3)
                    enemy_defs = _gym_leader_team_defs(leader)
                    fetch_indices = player_indices + [e["idx"] for e in enemy_defs]
                    if need_clear:
                        _clear()
                    spinner_start(my, mx)
                    for idx in fetch_indices:
                        if idx not in data_cache:
                            num, _name = POKEMON[idx]
                            data_cache[idx] = fetch_data(num)
                    spinner_stop()
                    player_level = max(10, int(leader["level"]) + 1)
                    gym_player_team = [
                        _gym_build_mon(idx, player_level, data_cache.get(idx))
                        for idx in player_indices
                    ]
                    gym_enemy_team = [
                        _gym_build_mon(e["idx"], e["level"],
                                       data_cache.get(e["idx"]),
                                       move_defs=e["moves"])
                        for e in enemy_defs
                    ]
                    gym_player_slot = 0
                    gym_enemy_slot = 0
                    gym_player = gym_player_team[gym_player_slot]
                    gym_enemy = gym_enemy_team[gym_enemy_slot]
                    gym_player_spr = None
                    gym_enemy_spr = None
                    gym_move_cursor = 0
                    gym_phase = "choose"
                    gym_badge_new = False
                    gym_log = [
                        f"{leader['name']} quiere combatir!",
                        f"{leader['name']} envia a {gym_enemy['dname']}!",
                        f"Adelante, {gym_player['dname']}!",
                    ]
                    play_cry(gym_enemy["name"])
                    mode = MODE_GYM_BATTLE
                    need_clear = True

            # ── GYM CHALLENGE BATTLE ──
            elif mode == MODE_GYM_BATTLE:
                if (gym_player is None or gym_enemy is None
                        or not gym_player_team or not gym_enemy_team):
                    mode = MODE_GYM_MENU
                    need_clear = True
                    continue

                if gym_player_spr is None or gym_enemy_spr is None:
                    _, _inn, _mrg, sw, _dx = _geom(mx)
                    scr_h_g = max(8, my - 14)
                    spr_tw = max(10, min(34, sw // 2))
                    max_rows = max(2, (scr_h_g - 4) // 2)
                    if need_clear:
                        _clear()
                        spinner_start(my, mx)
                    img = dl_sprite(gym_player["name"])
                    gym_player_spr = render_sprite(
                        img, spr_tw, bg_rgb=SCR_RGB,
                        max_rows=max_rows) if img else []
                    img = dl_sprite(gym_enemy["name"])
                    gym_enemy_spr = render_sprite(
                        img, spr_tw, bg_rgb=SCR_RGB,
                        max_rows=max_rows) if img else []
                    spinner_stop()
                    need_clear = True

                if need_clear:
                    _clear()
                    need_clear = False
                leader = GYM_LEADERS[gym_leader_cursor]
                draw_gym_battle(my, mx, gym_player, gym_enemy, leader,
                                gym_move_cursor, gym_phase, gym_log,
                                gym_player_spr, gym_enemy_spr,
                                gym_player_team, gym_enemy_team,
                                gym_player_slot, gym_enemy_slot)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if gym_phase == "choose":
                    if key == "ESC":
                        _stop_cry()
                        mode = MODE_LIST
                        need_clear = True
                    elif key in ("q", "Q"):
                        break
                    elif key in ("RIGHT", "d"):
                        gym_move_cursor = (gym_move_cursor + 1) % len(gym_player["moves"])
                    elif key in ("LEFT", "a"):
                        gym_move_cursor = (gym_move_cursor - 1) % len(gym_player["moves"])
                    elif key in ("DOWN", "j", "s"):
                        gym_move_cursor = (gym_move_cursor + 2) % len(gym_player["moves"])
                    elif key in ("UP", "k", "w"):
                        gym_move_cursor = (gym_move_cursor - 2) % len(gym_player["moves"])
                    elif key in ("1", "2", "3", "4"):
                        gym_move_cursor = min(int(key) - 1, len(gym_player["moves"]) - 1)
                    elif key == "ENTER":
                        old_player_hp = gym_player["hp"]
                        old_enemy_hp = gym_enemy["hp"]
                        _phase_hint, gym_log = _gym_take_turn(
                            gym_player, gym_enemy, gym_move_cursor)
                        if gym_enemy["hp"] < old_enemy_hp:
                            _gym_flash_hit(my, mx, "enemy")
                        if gym_player["hp"] < old_player_hp:
                            _gym_flash_hit(my, mx, "player")
                        if gym_enemy["hp"] <= 0:
                            next_enemy = _gym_next_alive(
                                gym_enemy_team, gym_enemy_slot)
                            if next_enemy is None:
                                gym_phase = "win"
                                gym_badge_new = _mark_gym_badge(gym_leader_cursor)
                                badge = leader["badge"]
                                earned = "Ganaste" if gym_badge_new else "Ya tenias"
                                gym_log = (gym_log + [f"{earned} la Medalla {badge}!"])[-4:]
                                play_cry(gym_player["name"])
                            else:
                                gym_enemy_slot = next_enemy
                                gym_enemy = gym_enemy_team[gym_enemy_slot]
                                gym_enemy_spr = None
                                gym_phase = "choose"
                                gym_log = (
                                    gym_log + [f"{leader['name']} envia a {gym_enemy['dname']}!"]
                                )[-4:]
                                play_cry(gym_enemy["name"])
                        elif gym_player["hp"] <= 0:
                            next_player = _gym_next_alive(
                                gym_player_team, gym_player_slot)
                            if next_player is None:
                                gym_phase = "lose"
                                gym_log = (gym_log + ["Has perdido el combate."])[-4:]
                                play_cry(gym_enemy["name"])
                            else:
                                gym_player_slot = next_player
                                gym_player = gym_player_team[gym_player_slot]
                                gym_player_spr = None
                                gym_move_cursor = 0
                                gym_phase = "choose"
                                gym_log = (
                                    gym_log + [f"Adelante, {gym_player['dname']}!"]
                                )[-4:]
                                play_cry(gym_player["name"])
                        else:
                            gym_phase = "choose"
                        need_clear = True
                else:
                    if key == "ESC":
                        mode = MODE_LIST
                        need_clear = True
                    elif key in ("q", "Q"):
                        break
                    elif key == "ENTER":
                        if gym_phase == "win":
                            gym_leader_cursor = min(
                                len(GYM_LEADERS) - 1, gym_leader_cursor + 1)
                        mode = MODE_GYM_MENU
                        need_clear = True

            # ── SAFARI ENTER ──
            elif mode == MODE_SAFARI_ENTER:
                if need_clear:
                    _clear()
                    need_clear = False
                draw_safari_entrance(my, mx)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if key == "ESC":
                    mode = MODE_LIST
                    need_clear = True
                elif key == "ENTER":
                    # Init safari session
                    safari_balls = 30
                    safari_captured = []
                    safari_action_cursor = 0
                    safari_anger = 0
                    safari_eating = 0
                    # Cache item sprites once (ball/stars bigger than rock/bait)
                    if safari_ball_lines is None:
                        safari_ball_lines = _safari_ball_lines()
                        safari_rock_lines = _safari_item_lines(_gen_rock)
                        safari_bait_lines = _safari_item_lines(_gen_bait)
                        safari_star_lines = _safari_star_lines()
                    # Pick first wild encounter (no MissingNo.)
                    safari_cur_idx = random.randint(0, REAL_POKE_COUNT - 1)
                    safari_cur_spr = None
                    safari_cur_sil = None
                    safari_cur_refl = None
                    safari_anger = 0
                    safari_eating = 0
                    mode = MODE_SAFARI_ENCOUNTER
                    need_clear = True
                elif key in ("q", "Q"):
                    break

            # ── SAFARI ENCOUNTER ──
            elif mode == MODE_SAFARI_ENCOUNTER:
                num, name = POKEMON[safari_cur_idx]
                dname = _dn(name)

                # Load sprite if needed
                if safari_cur_spr is None:
                    _, inn, mrg, sw, _ = _geom(mx)
                    # New layout: 14 fixed rows + scr_h interior. All chrome
                    # (name, types, balls, status, actions, ctrl) lives below
                    # the screen, so the sprite gets the whole green area
                    # minus the grass row.
                    fixed_s = 14
                    scr_h_s = max(8, my - fixed_s)
                    # Match detail's cap so the wild Pokemon is the same size
                    # as in the dex.
                    spr_tw_s = max(10, min(sw - 2, 60))
                    # Only the grass row is reserved inside the screen.
                    max_rows_s = max(4, scr_h_s - 1)
                    if need_clear:
                        _clear()
                        spinner_start(my, mx)
                    img = dl_sprite(name)
                    if img:
                        safari_cur_spr = render_sprite(
                            img, spr_tw_s, bg_rgb=SCR_RGB,
                            max_rows=max_rows_s)
                        sil_img = _silhouette(img, color=(240, 240, 240))
                        safari_cur_sil = render_sprite(
                            sil_img, spr_tw_s, bg_rgb=SCR_RGB,
                            max_rows=max_rows_s)
                        safari_cur_refl = _make_reflection(
                            img, spr_tw_s, SCR_RGB,
                            max_rows=max_rows_s)
                    else:
                        safari_cur_spr = []
                        safari_cur_sil = []
                        safari_cur_refl = []
                    # Also fetch species data for type badges on the header
                    if safari_cur_idx not in data_cache:
                        data_cache[safari_cur_idx] = fetch_data(num)
                    spinner_stop()
                    play_cry(name)
                    safari_need_appear = True
                    need_clear = True

                if need_clear:
                    _clear()
                    need_clear = False

                safari_types = (data_cache.get(safari_cur_idx) or {}).get("types", [])
                already_caught = num in STATS.get("caught_safari", [])
                draw_safari_encounter(my, mx, safari_cur_spr, safari_balls,
                                      safari_action_cursor, dname,
                                      safari_anger, safari_eating,
                                      num=num, types=safari_types,
                                      already_caught=already_caught)

                # Reflection is rendered AFTER the main drawer so it sits below
                # the grass line without being clobbered.
                _safari_draw_reflection(my, mx, safari_cur_spr, safari_cur_refl)

                if safari_need_appear:
                    _safari_anim_appear(my, mx, safari_cur_spr)
                    safari_need_appear = False

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if key == "ESC":
                    mode = MODE_SAFARI_END
                    need_clear = True
                elif key in ("LEFT", "a"):
                    safari_action_cursor = (safari_action_cursor - 1) % 4
                elif key in ("RIGHT", "d"):
                    safari_action_cursor = (safari_action_cursor + 1) % 4
                elif key == "ENTER":
                    catch_mod, flee_mod = _safari_modifiers(
                        safari_anger, safari_eating)
                    action = safari_action_cursor

                    if action == 3:  # Huir
                        mode = MODE_SAFARI_END
                        need_clear = True

                    elif action == 0:  # Bola
                        safari_balls -= 1

                        # 1) Throw ball → eased arc toward the Pokemon
                        _safari_anim_throw(my, mx, safari_cur_spr,
                                           safari_ball_lines)
                        # 2) Absorb: silhouette → dot → ball snaps shut
                        _safari_anim_absorb(my, mx, safari_cur_spr,
                                            safari_cur_sil,
                                            safari_ball_lines)

                        # 3) Determine catch result
                        caught = _safari_catch_check(name, catch_mod)
                        n_shakes = 3 if caught else random.randint(0, 2)

                        # 4) Ball falls to grass, wobbles with tension dots
                        _safari_anim_shake(my, mx, safari_cur_spr,
                                           safari_ball_lines, n_shakes)

                        if caught:
                            # 5a) Rotating sparkle around the ball
                            _safari_anim_capture(my, mx, safari_cur_spr,
                                                 safari_ball_lines,
                                                 safari_star_lines)
                            play_cry(name)
                            safari_captured.append((num, name))
                            _mark_caught(num)
                            safari_result_msg = (
                                f"Gotcha! {dname} fue capturado!")
                            safari_result_type = "caught"
                        else:
                            # 5b) Ball bursts and Pokemon pops back out
                            _safari_anim_burst(my, mx, safari_cur_spr,
                                               safari_ball_lines)
                            # Pokemon cry when it breaks free — gives the
                            # "it got away" moment an audio punctuation.
                            play_cry(name)
                            if _safari_flee_check(name, flee_mod):
                                _safari_anim_flee(my, mx, safari_cur_spr)
                                safari_result_msg = f"{dname} huyo!"
                                safari_result_type = "fled"
                            elif safari_balls <= 0:
                                _sfx("Submarine", rate=0.8, volume=0.9)
                                safari_result_msg = (
                                    "Se acabaron las Safari Ball!")
                                safari_result_type = "out_of_balls"
                            else:
                                safari_result_msg = f"{dname} se libero!"
                                safari_result_type = "broke_free"

                        # Decay anger/eating counters
                        if safari_anger > 0:
                            safari_anger -= 1
                        if safari_eating > 0:
                            safari_eating -= 1

                        mode = MODE_SAFARI_RESULT
                        need_clear = True

                    elif action == 1:  # Roca
                        _safari_anim_throw(my, mx, safari_cur_spr,
                                           safari_rock_lines)
                        safari_anger = random.randint(1, 5)
                        safari_eating = 0
                        _, flee_mod_now = _safari_modifiers(
                            safari_anger, safari_eating)
                        if _safari_flee_check(name, flee_mod_now):
                            _safari_anim_flee(my, mx, safari_cur_spr)
                            safari_result_msg = f"{dname} huyo!"
                            safari_result_type = "fled"
                            mode = MODE_SAFARI_RESULT
                        else:
                            # Toast that we made it angry but it stayed
                            safari_result_msg = f"{dname} esta furioso!"
                            safari_result_type = "info"
                            mode = MODE_SAFARI_RESULT
                        need_clear = True

                    elif action == 2:  # Cebo
                        _safari_anim_throw(my, mx, safari_cur_spr,
                                           safari_bait_lines)
                        safari_eating = random.randint(1, 5)
                        safari_anger = 0
                        _, flee_mod_now = _safari_modifiers(
                            safari_anger, safari_eating)
                        if _safari_flee_check(name, flee_mod_now):
                            _safari_anim_flee(my, mx, safari_cur_spr)
                            safari_result_msg = f"{dname} huyo!"
                            safari_result_type = "fled"
                            mode = MODE_SAFARI_RESULT
                        else:
                            safari_result_msg = f"{dname} esta comiendo..."
                            safari_result_type = "info"
                            mode = MODE_SAFARI_RESULT
                        need_clear = True

                elif key in ("q", "Q"):
                    break

            # ── SAFARI RESULT ──
            elif mode == MODE_SAFARI_RESULT:
                if need_clear:
                    _clear()
                    need_clear = False
                draw_safari_result(my, mx, safari_cur_spr,
                                   safari_result_msg, safari_result_type,
                                   safari_balls)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if key == "ENTER":
                    if (safari_result_type in ("fled", "caught")
                            or safari_balls <= 0):
                        if safari_balls <= 0:
                            mode = MODE_SAFARI_END
                        else:
                            # New encounter
                            safari_cur_idx = random.randint(0, REAL_POKE_COUNT - 1)
                            safari_cur_spr = None
                            safari_cur_sil = None
                            safari_cur_refl = None
                            safari_action_cursor = 0
                            safari_anger = 0
                            safari_eating = 0
                            mode = MODE_SAFARI_ENCOUNTER
                    elif safari_result_type in ("broke_free", "info"):
                        # Pokemon is still there, continue encounter
                        mode = MODE_SAFARI_ENCOUNTER
                    elif safari_result_type == "out_of_balls":
                        mode = MODE_SAFARI_END
                    need_clear = True
                elif key == "ESC":
                    mode = MODE_SAFARI_END
                    need_clear = True
                elif key in ("q", "Q"):
                    break

            # ── SAFARI END ──
            elif mode == MODE_SAFARI_END:
                if need_clear:
                    _clear()
                    need_clear = False
                draw_safari_end(my, mx, safari_captured)

                key = _readkey()
                if key == "RESIZE":
                    invalidate_render_caches()
                    need_clear = True
                    continue
                if key == "ESC" or key in ("q", "Q"):
                    mode = MODE_LIST
                    need_clear = True
                elif key == "ENTER":
                    # Play again
                    mode = MODE_SAFARI_ENTER
                    need_clear = True

            # ── MEMORY MENU ──
            elif mode == MODE_MEMORY_MENU:
                if need_clear:
                    _clear()
                    need_clear = False
                draw_memory_menu(my, mx, mem_diff_idx)

                key = _readkey()
                if key == "RESIZE":
                    need_clear = True
                    continue
                if key == "ESC" or key in ("q", "Q"):
                    mode = MODE_LIST
                    need_clear = True
                elif key in ("UP", "k", "w"):
                    mem_diff_idx = max(0, mem_diff_idx - 1)
                elif key in ("DOWN", "j", "s"):
                    mem_diff_idx = min(len(MEMORY_DIFFICULTIES) - 1,
                                       mem_diff_idx + 1)
                elif key == "ENTER":
                    # Initialise the run
                    _, _, _, sw_cur, _ = _geom(mx)
                    desired_pairs = _memory_desired_pairs(mem_diff_idx)
                    layout = _memory_layout_for_screen(
                        sw_cur, max(8, my - 9), desired_pairs)
                    mem_rows = layout["rows"]
                    mem_cols = layout["cols"]
                    mem_card_w = layout["card_w"]
                    mem_card_h = layout["card_h"]
                    mem_gap_h = layout["gap_h"]
                    mem_pairs = layout["pairs"]
                    mem_scroll_row = 0
                    mem_cards = _memory_make_deck(mem_pairs)
                    mem_flipped = []
                    mem_matched = set()
                    mem_cursor = 0
                    mem_tries = 0
                    mem_start = time.time()
                    mode = MODE_MEMORY_GAME
                    need_clear = True

            # ── MEMORY GAME ──
            elif mode == MODE_MEMORY_GAME:
                n_total = len(mem_cards)
                elapsed = time.time() - mem_start
                _, _, _, sw_cur, _ = _geom(mx)
                layout = _memory_layout_for_screen(sw_cur, max(8, my - 9), mem_pairs)
                mem_rows = layout["rows"]
                mem_cols = layout["cols"]
                mem_card_w = layout["card_w"]
                mem_card_h = layout["card_h"]
                mem_gap_h = layout["gap_h"]
                visible_rows = _memory_visible_rows(
                    max(8, my - 9), mem_rows, mem_card_h, mem_gap_h)
                mem_scroll_row = _memory_scroll_for_cursor(
                    mem_scroll_row, mem_cursor, mem_rows, mem_cols, visible_rows)

                if need_clear:
                    _clear()
                    need_clear = False
                draw_memory_game(my, mx, mem_diff_idx, mem_cards,
                                 mem_flipped, mem_matched, mem_cursor,
                                 mem_tries, elapsed, mem_rows, mem_cols,
                                 mem_card_w, mem_card_h, mem_gap_h,
                                 mem_scroll_row)

                # If we just placed the second flipped card, hold the frame
                # briefly so the player sees both before resolving the pair.
                if len(mem_flipped) == 2:
                    time.sleep(0.85)
                    a, b = mem_flipped
                    mem_tries += 1
                    if mem_cards[a] == mem_cards[b]:
                        _sfx("Glass", rate=1.2, volume=0.7)
                        mem_matched.add(a)
                        mem_matched.add(b)
                    else:
                        _sfx("Funk", rate=1.2, volume=0.5)
                    mem_flipped = []
                    if len(mem_matched) >= n_total:
                        mem_new_record = _set_memory_best(
                            mem_diff_idx, mem_tries, elapsed, mem_pairs)
                        _sfx("Hero", volume=0.9)
                        mem_final_elapsed = elapsed
                        mode = MODE_MEMORY_END
                    need_clear = True
                    continue

                # 1.0s timeout so the timer in the header keeps ticking
                key = _readkey(timeout=1.0)
                if key == "RESIZE":
                    mem_scroll_row = 0
                    need_clear = True
                    continue
                if key == "TIMEOUT":
                    # Just refresh the timer — no clear needed.
                    continue
                if key == "ESC" or key in ("q", "Q"):
                    mode = MODE_LIST
                    need_clear = True
                elif key in ("LEFT", "a"):
                    if mem_cursor % mem_cols > 0:
                        mem_cursor -= 1
                    mem_scroll_row = _memory_scroll_for_cursor(
                        mem_scroll_row, mem_cursor, mem_rows, mem_cols, visible_rows)
                elif key in ("RIGHT", "d"):
                    if (mem_cursor % mem_cols < mem_cols - 1
                            and mem_cursor + 1 < n_total):
                        mem_cursor += 1
                    mem_scroll_row = _memory_scroll_for_cursor(
                        mem_scroll_row, mem_cursor, mem_rows, mem_cols, visible_rows)
                elif key in ("UP", "k", "w"):
                    if mem_cursor // mem_cols > 0:
                        mem_cursor -= mem_cols
                    mem_scroll_row = _memory_scroll_for_cursor(
                        mem_scroll_row, mem_cursor, mem_rows, mem_cols, visible_rows)
                elif key in ("DOWN", "j", "s"):
                    if mem_cursor // mem_cols < mem_rows - 1 \
                            and mem_cursor + mem_cols < n_total:
                        mem_cursor += mem_cols
                    mem_scroll_row = _memory_scroll_for_cursor(
                        mem_scroll_row, mem_cursor, mem_rows, mem_cols, visible_rows)
                elif key == "ENTER":
                    if (mem_cursor not in mem_matched
                            and mem_cursor not in mem_flipped
                            and len(mem_flipped) < 2):
                        mem_flipped.append(mem_cursor)
                        _sfx("Tink", rate=1.4, volume=0.5)

            # ── MEMORY END ──
            elif mode == MODE_MEMORY_END:
                if need_clear:
                    _clear()
                    need_clear = False
                draw_memory_end(my, mx, mem_diff_idx, mem_tries,
                                mem_final_elapsed, mem_new_record, mem_pairs)

                key = _readkey()
                if key == "RESIZE":
                    need_clear = True
                    continue
                if key == "ESC" or key in ("q", "Q"):
                    mode = MODE_LIST
                    need_clear = True
                elif key == "ENTER":
                    mode = MODE_MEMORY_MENU
                    need_clear = True

    finally:
        # Leave alternate screen + show cursor
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def _build_arg_parser():
    p = argparse.ArgumentParser(
        prog="pokedex",
        description="National Pokedex I-V - Interactive CLI Pokedex with sprites, "
                    "cries, quizzes, Safari Zone, and Gym Challenge.",
    )
    p.add_argument("--pokemon", "-p", type=str, default=None,
                   help="Open the detail view of a Pokemon (number 1-649 or name)")
    p.add_argument("--no-audio", action="store_true",
                   help="Disable cries, SFX and TTS for this session")
    p.add_argument("--palette", type=str, default=None,
                   help="Pick a screen palette by name (e.g. 'DMG Green', 'GBC Red')")
    p.add_argument("--prefetch", action="store_true",
                   help="Download all sprites, cries and species data, then exit")
    p.add_argument("--prefetch-force", action="store_true",
                   help="Re-download cached assets during --prefetch")
    p.add_argument("--cache-status", action="store_true",
                   help="Print offline cache coverage and prefetch failures, then exit")
    p.add_argument("--safari", action="store_true",
                   help="Jump straight into the Safari Zone")
    p.add_argument("--gym", action="store_true",
                   help="Jump straight into the Gym Challenge")
    p.add_argument("--quiz", action="store_true",
                   help="Jump straight into the Quiz menu")
    p.add_argument("--screensaver", action="store_true",
                   help="Start in autoplay/screensaver mode")
    p.add_argument("--list-palettes", action="store_true",
                   help="List the available palettes and exit")
    p.add_argument("--stats", action="store_true",
                   help="Print persisted stats (seen, caught, badges, best quizzes) and exit")
    p.add_argument("--trainer-card", action="store_true",
                   help="Export a Trainer Card PNG summary to ~/Downloads and exit")
    p.add_argument("--sprite-gen", type=str, default=None,
                   choices=[k for k, _, _ in SPRITE_STYLES],
                   help="Which Showdown sprite generation to use (default: gen1)")
    return p


def _print_stats():
    _load_stats()
    seen = sorted(STATS.get("seen", []))
    caught = sorted(STATS.get("caught_safari", []))
    shiny = sorted(STATS.get("shiny_seen", []))
    badges = _gym_badges()
    best = STATS.get("best_quiz", {})
    print(f"Pokedex stats ({STATS_FILE})")
    print(f"  Vistos:    {len(seen)} / {REAL_POKE_COUNT}")
    print(f"  Atrapados: {len(caught)} / {REAL_POKE_COUNT}")
    print(f"  Shiny:     {len(shiny)}")
    print(f"  Medallas:  {len(badges)} / {len(GYM_LEADERS)}")
    print(f"  Mute:      {STATS.get('mute', False)}")
    print(f"  Paleta:    {PALETTES[STATS.get('palette_idx', 0)][0]}")
    if best:
        print("  Mejores quiz:")
        for k, v in sorted(best.items()):
            print(f"    {k:18s} {v}")


def _prefetch_progress_path():
    return os.path.join(_cache_root(), "prefetch-progress.json")


def _cache_status():
    return pokedex_cache.cache_status(
        POKEMON[:REAL_POKE_COUNT],
        _sprite_disk_cached,
        _cry_disk_cached,
        _data_disk_cached,
    )


def _print_cache_status():
    status = _cache_status()
    print(f"Cache ({_cache_root()})")
    print(f"  Sprites:   {status.sprites:3d} / {status.pokemon_count}")
    print(f"  Cries:     {status.cries:3d} / {status.pokemon_count}")
    print(f"  Data:      {status.data:3d} / {status.pokemon_count}")
    print(f"  Total:     {status.cached_assets:3d} / {status.total_assets}")
    if status.complete:
        print("  Offline:   listo")
    else:
        print(f"  Offline:   parcial ({status.missing_assets} assets pendientes)")

    progress = pokedex_cache.load_prefetch_progress(_prefetch_progress_path())
    failed = progress.get("failed", {})
    if failed:
        print(f"  Fallos:    {len(failed)} registrados")
        for key in sorted(failed)[:5]:
            print(f"    {key}: {failed[key]}")
        if len(failed) > 5:
            print(f"    ... y {len(failed) - 5} mas")
    if progress.get("updated_at"):
        print(f"  Progreso:  {progress['updated_at']}")


def _remove_cached_sprite(name):
    slug = _sn(name)
    for style_key in _sprite_style_candidates(name):
        try:
            os.remove(os.path.join(CACHE_DIR, style_key, f"{slug}.png"))
        except OSError:
            pass


def _remove_cached_data(num):
    try:
        os.remove(_data_path(num))
    except OSError:
        pass


def _prefetch_sprite(name, force=False):
    if force:
        _remove_cached_sprite(name)
    if _sprite_disk_cached(name):
        return True
    return dl_sprite(name) is not None or _sprite_disk_cached(name)


def _prefetch_cry(name, force=False):
    return _cache_cry(name, force=force)


def _prefetch_data(num, force=False):
    if force:
        _remove_cached_data(num)
    if _data_disk_cached(num):
        return True
    return fetch_data(num) is not None and _data_disk_cached(num)


def _prefetch_all(force=False):
    """Download every sprite, cry and species blob into the cache.

    The operation is resumable because cached assets are skipped on later runs,
    and failures are written to a small progress file under the cache root.
    """
    _load_stats()
    status = _cache_status()
    print(f"Pre-cargando {REAL_POKE_COUNT} Pokemon...")
    print(f"Cache inicial: {status.cached_assets}/{status.total_assets} assets")
    if force:
        print("Modo force: se re-descargaran los assets cacheados.")

    progress_path = _prefetch_progress_path()
    progress = pokedex_cache.load_prefetch_progress(progress_path)
    failures = []
    assets = (
        ("sprite", _prefetch_sprite),
        ("cry", _prefetch_cry),
        ("data", _prefetch_data),
    )

    for i, (num, name) in enumerate(POKEMON[:REAL_POKE_COUNT]):
        sys.stdout.write(
            f"\r  [{i+1:3d}/{REAL_POKE_COUNT}] {_dn(name):<20s}")
        sys.stdout.flush()
        for asset_name, prefetcher in assets:
            key = f"{asset_name}:{num:03d}"
            ok = prefetcher(name if asset_name != "data" else num, force=force)
            if ok:
                pokedex_cache.mark_done(progress, key)
            else:
                failures.append(key)
                pokedex_cache.mark_failed(progress, key, "download failed")
        if i % 10 == 0:
            pokedex_cache.save_prefetch_progress(progress_path, progress)

    pokedex_cache.save_prefetch_progress(progress_path, progress)
    status = _cache_status()
    print(f"\nCache final: {status.cached_assets}/{status.total_assets} assets")
    if failures:
        print(
            f"Aviso: {len(failures)} assets no se pudieron descargar. "
            "Vuelve a ejecutar --prefetch para reintentar.")
        return False
    print("Listo. Cache offline completo.")
    return True


def cli(argv=None):
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_palettes:
        for i, (pname, _, _, _) in enumerate(PALETTES):
            print(f"  {i}: {pname}")
        return 0
    if args.stats:
        _print_stats()
        return 0
    if args.cache_status:
        _print_cache_status()
        return 0
    if args.prefetch:
        return 0 if _prefetch_all(force=args.prefetch_force) else 1
    if args.trainer_card:
        path = export_trainer_card()
        if path:
            print(f"Tarjeta exportada a: {path}")
            return 0
        print("No se pudo exportar la tarjeta (Pillow o permisos de escritura).")
        return 1

    try:
        main(args)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        _stop_cry()
        _kill_tts()
        sys.stdout.write(f"{RST}\033[?25h\033[?1049l")
        sys.stdout.flush()
        print("Gracias por usar la Pok\u00e9dex Gen I-V!")


if __name__ == "__main__":
    sys.exit(cli())
