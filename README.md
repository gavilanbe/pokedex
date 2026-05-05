# National Pokedex I-V CLI

Interactive terminal Pokedex for Pokemon #001-#649 (Gen I-V), with sprites,
cries, quiz modes, Safari Zone, Gym Challenge battles, memory game with large
scrollable PC-style card icons, palettes, local cache, and a Trainer Card
export.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

If you only want runtime dependencies:

```bash
python3 -m pip install -r pokedex_requirements.txt
```

## Run

```bash
python3 pokedex_gen1.py
# or, after `python3 -m pip install -e ".[dev]"`
pokedex
```

Useful commands:

```bash
python3 pokedex_gen1.py --pokemon pikachu
python3 pokedex_gen1.py --quiz
python3 pokedex_gen1.py --safari
python3 pokedex_gen1.py --gym
python3 pokedex_gen1.py --stats
python3 pokedex_gen1.py --trainer-card
python3 pokedex_gen1.py --cache-status
python3 pokedex_gen1.py --prefetch
python3 pokedex_gen1.py --prefetch --prefetch-force
```

## Controls

- Arrow keys, `j`/`k`, or `w`/`s`: move through lists
- `ENTER`: open/select/continue
- `/`: search
- `g`: quiz menu
- `h`: Safari Zone
- `B`: Gym Challenge
- `M`: memory game
- `p`: cycle palette
- `m`: mute/unmute audio
- `T`: export Trainer Card
- `?`: help overlay
- `q` or `ESC`: back/quit depending on the screen

## Tests

```bash
python3 -m pytest
python3 -m ruff check .
```

The tests focus on pure helpers and persistence so they can run without terminal
I/O, subprocess audio, or network calls.

## Project Layout

- `pokedex_gen1.py`: interactive CLI, rendering, modes, and compatibility facade
- `pokedex_cache.py`: cache status, atomic writes, and resumable prefetch progress
- `pokedex_stats.py`: stats defaults, validation, load/save helpers
- `pokedex_text.py`: text normalization, aliases, and Pokemon search
- `pokedex_network.py`: SSL context and HTTP byte fetches
- `pokedex_audio.py`: sound effects, cries, and TTS process management

## Local Data

Sprites, cries, PokeAPI data, and stats are cached under the platform cache
directory. On macOS this is:

```text
~/Library/Caches/pokedex
```

Memory game card icons are cached under `memory-icons/bwicons` in that same
folder.

`--prefetch` skips already-cached files, records failed assets in
`prefetch-progress.json`, and can be rerun later to continue filling the cache.
Use `--cache-status` to see whether the app is ready for full offline use.
