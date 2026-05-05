"""Audio and TTS helpers for the Pokedex CLI."""

import math
import os
import random
import subprocess
import struct
import sys
import threading
import wave


SFX_RATE = 22050


def _cache_root():
    base = os.environ.get("XDG_CACHE_HOME")
    if not base:
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Caches")
        else:
            base = os.path.expanduser("~/.cache")
    return os.path.join(base, "pokedex", "sfx")


LEGACY_SFX_ALIASES = {
    "Basso": "ui_error",
    "Blow": "ui_back",
    "Funk": "dex_burst",
    "Glass": "ui_palette",
    "Hero": "dex_win",
    "Ping": "ui_ready",
    "Pop": "ui_select",
    "Purr": "dex_power",
    "Sosumi": "dex_alert",
    "Submarine": "dex_low",
    "Tink": "ui_nav",
}


CUSTOM_SFX = {
    "dex_alert",
    "dex_burst",
    "dex_hinge",
    "dex_latch",
    "dex_low",
    "dex_open",
    "dex_power",
    "dex_ready",
    "dex_scan",
    "dex_win",
    "ui_back",
    "ui_error",
    "ui_nav",
    "ui_palette",
    "ui_ready",
    "ui_scan",
    "ui_select",
}


def play_sfx(name, muted=False, rate=1.0, volume=1.0):
    """Play a short Pokedex SFX asynchronously.

    Built-in effect names are generated as small WAV files and cached. Unknown
    names fall back to macOS system sounds to preserve compatibility.
    """
    if muted:
        return None
    wav = _sfx_path(name)
    if wav:
        return _play_file(wav, rate=rate, volume=volume)
    return _play_file(f"/System/Library/Sounds/{name}.aiff",
                      rate=rate, volume=volume)


def _play_file(path, rate=1.0, volume=1.0):
    args = ["afplay"]
    if rate != 1.0:
        args += ["-r", f"{rate:.2f}"]
    if volume != 1.0:
        args += ["-v", f"{max(0.0, min(1.0, volume)):.2f}"]
    args.append(path)
    try:
        return subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, OSError):
        return None


def _sfx_path(name):
    effect = LEGACY_SFX_ALIASES.get(name, name)
    if effect not in CUSTOM_SFX:
        return None
    root = _cache_root()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"{effect}.wav")
    if not os.path.exists(path):
        _write_wav(path, _build_sfx(effect))
    return path


def _write_wav(path, samples):
    peak = max(1.0, max(abs(s) for s in samples))
    scale = 0.95 / peak
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SFX_RATE)
        frames = bytearray()
        for s in samples:
            amp = int(max(-1.0, min(1.0, s * scale)) * 32767)
            frames += struct.pack("<h", amp)
        f.writeframes(bytes(frames))


def _env(i, n, attack=0.01, release=0.05):
    if n <= 1:
        return 0.0
    t = i / SFX_RATE
    dur = n / SFX_RATE
    if attack and t < attack:
        return t / attack
    tail = max(0.001, dur - t)
    if release and tail < release:
        return tail / release
    return 1.0


def _wave_value(kind, phase):
    x = phase % 1.0
    if kind == "square":
        return 1.0 if x < 0.5 else -1.0
    if kind == "tri":
        return 4.0 * abs(x - 0.5) - 1.0
    if kind == "noise":
        return random.uniform(-1.0, 1.0)
    return math.sin(2.0 * math.pi * x)


def _tone(freq, duration, volume=0.4, wave_kind="square",
          attack=0.004, release=0.04, slide=0.0):
    n = max(1, int(duration * SFX_RATE))
    out = []
    phase = 0.0
    for i in range(n):
        t = i / max(1, n - 1)
        f = max(20.0, freq + slide * t)
        phase += f / SFX_RATE
        out.append(_wave_value(wave_kind, phase) * volume *
                   _env(i, n, attack=attack, release=release))
    return out


def _noise(duration, volume=0.25, attack=0.001, release=0.05):
    n = max(1, int(duration * SFX_RATE))
    return [
        random.uniform(-1.0, 1.0) * volume *
        _env(i, n, attack=attack, release=release)
        for i in range(n)
    ]


def _silence(duration):
    return [0.0] * max(1, int(duration * SFX_RATE))


def _mix(*tracks):
    length = max((offset + len(samples)) for offset, samples in tracks)
    out = [0.0] * length
    for offset, samples in tracks:
        for i, sample in enumerate(samples):
            out[offset + i] += sample
    return out


def _join(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def _build_sfx(effect):
    random.seed(effect)
    if effect == "ui_nav":
        return _mix(
            (0, _tone(940, 0.045, 0.35, "square", release=0.025)),
            (int(0.012 * SFX_RATE), _tone(1380, 0.038, 0.22, "tri", release=0.02)),
        )
    if effect == "ui_select":
        return _mix(
            (0, _tone(520, 0.055, 0.28, "square", slide=170, release=0.035)),
            (int(0.025 * SFX_RATE), _tone(1040, 0.07, 0.34, "tri", release=0.04)),
        )
    if effect == "ui_back":
        return _mix(
            (0, _noise(0.08, 0.2, release=0.07)),
            (0, _tone(360, 0.11, 0.28, "tri", slide=-140, release=0.08)),
        )
    if effect == "ui_scan":
        return _tone(1550, 0.035, 0.22, "square", slide=520, release=0.012)
    if effect == "ui_palette":
        return _join(
            _tone(650, 0.05, 0.24, "tri"),
            _tone(980, 0.05, 0.24, "tri"),
            _tone(1320, 0.08, 0.24, "tri"),
        )
    if effect == "ui_ready":
        return _join(
            _tone(880, 0.055, 0.26, "square"),
            _tone(1320, 0.08, 0.26, "square"),
        )
    if effect == "ui_error":
        return _mix(
            (0, _tone(180, 0.12, 0.32, "square", slide=-35, release=0.08)),
            (0, _noise(0.08, 0.09, release=0.07)),
        )
    if effect == "dex_latch":
        return _mix(
            (0, _noise(0.035, 0.46, release=0.025)),
            (int(0.012 * SFX_RATE), _tone(260, 0.06, 0.42, "square", release=0.035)),
        )
    if effect == "dex_hinge":
        return _mix(
            (0, _tone(150, 0.18, 0.24, "tri", slide=75, release=0.08)),
            (0, _noise(0.18, 0.11, attack=0.02, release=0.09)),
        )
    if effect == "dex_open":
        return _mix(
            (0, _tone(210, 0.2, 0.28, "tri", slide=280, release=0.09)),
            (int(0.06 * SFX_RATE), _noise(0.13, 0.12, release=0.09)),
        )
    if effect == "dex_power":
        return _mix(
            (0, _tone(95, 0.28, 0.22, "tri", slide=120, attack=0.04, release=0.08)),
            (int(0.09 * SFX_RATE), _tone(390, 0.18, 0.14, "square", release=0.08)),
        )
    if effect == "dex_scan":
        return _mix(
            (0, _tone(760, 0.18, 0.18, "square", slide=780, release=0.05)),
            (int(0.02 * SFX_RATE), _noise(0.16, 0.06, release=0.06)),
        )
    if effect == "dex_burst":
        return _join(
            _tone(330, 0.045, 0.28, "square"),
            _tone(660, 0.045, 0.25, "square"),
            _tone(990, 0.08, 0.24, "tri"),
        )
    if effect == "dex_ready":
        return _join(
            _tone(523, 0.055, 0.22, "square"),
            _silence(0.015),
            _tone(784, 0.055, 0.22, "square"),
            _silence(0.015),
            _tone(1046, 0.11, 0.25, "tri"),
        )
    if effect == "dex_low":
        return _tone(120, 0.18, 0.36, "tri", slide=-40, release=0.12)
    if effect == "dex_alert":
        return _join(
            _tone(300, 0.065, 0.3, "square"),
            _silence(0.025),
            _tone(240, 0.09, 0.3, "square"),
        )
    if effect == "dex_win":
        return _join(
            _tone(523, 0.08, 0.24, "square"),
            _tone(659, 0.08, 0.24, "square"),
            _tone(784, 0.08, 0.24, "square"),
            _tone(1046, 0.16, 0.24, "tri"),
        )
    return _tone(440, 0.08, 0.2, "square")


def kill_process(proc):
    if proc:
        try:
            proc.kill()
            proc.wait()
        except OSError:
            pass


def play_cry(name, current_proc, muted, cries_dir, cries_base, slug_func, get_func):
    """Stop the current cry, cache the requested one, and play it via afplay."""
    kill_process(current_proc)
    if muted:
        return None
    os.makedirs(cries_dir, exist_ok=True)
    slug = slug_func(name)
    cache_path = os.path.join(cries_dir, f"{slug}.mp3")
    if not os.path.exists(cache_path):
        data = get_func(f"{cries_base}/{slug}.mp3")
        if data:
            with open(cache_path, "wb") as f:
                f.write(data)
    if os.path.exists(cache_path):
        try:
            return subprocess.Popen(
                ["afplay", cache_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (FileNotFoundError, OSError):
            return None
    return None


class TTSPlayer:
    def __init__(self, voice_en, voice_es):
        self.voice_en = voice_en
        self.voice_es = voice_es
        self._procs = []
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def stop(self):
        """Stop any currently-speaking TTS processes cleanly and reap them."""
        self._stop.set()
        with self._lock:
            snapshot = list(self._procs)
            self._procs.clear()
        for proc in snapshot:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except (OSError, ProcessLookupError):
                pass
        for proc in snapshot:
            try:
                proc.wait(timeout=0.1)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def speak(self, dname, genus, desc, muted=False):
        """Speak English name, then Spanish genus and description."""
        self.stop()
        if muted:
            return
        es = ". ".join(p for p in [genus, desc] if p)
        commands = [["say", "-v", self.voice_en, dname]]
        if es:
            commands.append(["say", "-v", self.voice_es, es])
        self._speak_commands(commands)

    def speak_text(self, text, voice=None, muted=False):
        """Speak one text fragment without revealing the Pokemon name."""
        self.stop()
        if muted or not text:
            return
        self._speak_commands([["say", "-v", voice or self.voice_es, text]])

    def _speak_commands(self, commands):
        self._stop.clear()
        def runner():
            for cmd in commands:
                if self._stop.is_set():
                    return
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except (FileNotFoundError, OSError):
                    return
                with self._lock:
                    if self._stop.is_set():
                        try:
                            os.killpg(os.getpgid(proc.pid), 9)
                        except (OSError, ProcessLookupError):
                            pass
                        try:
                            proc.wait(timeout=0.1)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                        return
                    self._procs.append(proc)
                try:
                    proc.wait()
                except Exception:
                    return

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()
