"""Synthesized soundtrack for Studio videos (numpy only, no samples, no downloads).

music(total, starts, mood)  -> stereo float32 bed: pad chords, sub bass, soft kick/hats, pluck arp; a lift at each scene
sfx(events)                  -> whoosh / pop / tick / ding / boom / riser hits at given times
mix(voice, music, sfx)       -> music ducked under the voice, soft-clipped, written as 16-bit WAV"""
import math, wave

import numpy as np

SR = 44100
_rng = np.random.default_rng(11)

MOODS = {   # chord roots (MIDI) + quality, bpm
    "upbeat": ([57, 53, 48, 55], [[0, 3, 7], [0, 4, 7], [0, 4, 7], [0, 4, 7]], 104),     # Am F C G
    "dramatic": ([50, 46, 53, 48], [[0, 3, 7], [0, 4, 7], [0, 4, 7], [0, 4, 7]], 92),   # Dm Bb F C
    "chill": ([53, 57, 50, 55], [[0, 4, 7, 11], [0, 3, 7, 10], [0, 3, 7, 10], [0, 4, 7, 10]], 86),
}


def note(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _t(n):
    return np.arange(int(n * SR)) / SR


def lowpass(x, cut):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X *= 1 / (1 + (f / cut) ** 4)
    return np.fft.irfft(X, len(x))


def band(x, lo, hi):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X *= (1 / (1 + (lo / np.maximum(f, 1)) ** 4)) * (1 / (1 + (f / hi) ** 4))
    return np.fft.irfft(X, len(x))


def saw(freq, n, harm=10, det=0.0):
    t = _t(n)
    out = np.zeros_like(t)
    for d in ((-det, 0, det) if det else (0,)):
        f = freq * (1 + d)
        for k in range(1, harm + 1):
            if f * k > 12000:
                break
            out += np.sin(2 * np.pi * f * k * t + k) / k
    return out / (3 if det else 1)


def env(n, a=0.01, r=0.2):
    m = int(n * SR)
    e = np.ones(m)
    na = max(1, int(a * SR))
    e[:na] = np.linspace(0, 1, na)
    e *= np.exp(-np.arange(m) / (r * SR))
    return e


def kick(n=0.4):
    t = _t(n)
    f = 46 + 90 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 8)


def hat(n=0.05):
    x = np.diff(_rng.standard_normal(int(n * SR) + 1))
    return x * np.exp(-np.arange(len(x)) / (0.01 * SR)) * 0.2


def whoosh(n=0.5):
    x = band(_rng.standard_normal(int(n * SR)), 500, 5000)
    t = np.linspace(0, 1, len(x))
    e = np.sin(np.pi * t ** 0.7) ** 2
    sweep = np.sin(2 * np.pi * np.cumsum(300 + 1400 * t) / SR) * 0.06
    y = (x / (np.abs(x).max() + 1e-9)) * e * 0.8 + sweep * e
    return y


def pop(n=0.14):
    t = _t(n)
    f = 520 + 900 * np.exp(-t * 40)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 32)
    click = band(_rng.standard_normal(len(t)), 2000, 9000) * np.exp(-t * 300) * 0.4
    return (s + click) * 0.8


def tick(n=0.06):
    t = _t(n)
    return np.sin(2 * np.pi * 2600 * t) * np.exp(-t * 120) * 0.5


def ding(base=84, n=1.3):
    t = _t(n)
    out = np.zeros_like(t)
    for i, m in enumerate((base, base + 7, base + 12)):
        tt = np.clip(t - i * 0.04, 0, None)
        out += np.sin(2 * np.pi * note(m) * tt) * np.exp(-tt * 3.2) * (tt > 0)
    return out * 0.28


def boom(n=1.4):
    t = _t(n)
    f = 32 + 70 * np.exp(-t * 10)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.6) + lowpass(_rng.standard_normal(len(t)), 1800) * np.exp(-t * 10) * 0.25


def riser(n=1.6):
    t = _t(n)
    f = 220 * 2 ** (t / n * 2.5)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.2
    return (s + whoosh(n) * 0.5) * (t / n) ** 2


def _add(buf, sig, t, gain=1.0, pan=0.0):
    i = int(t * SR)
    if i >= len(buf) or i < 0:
        return
    j = min(len(buf), i + len(sig))
    s = sig[:j - i] * gain
    buf[i:j, 0] += s * (1 - pan) * 0.75
    buf[i:j, 1] += s * (1 + pan) * 0.75


def music(total, starts, mood="upbeat"):
    roots, quals, bpm = MOODS.get(mood, MOODS["upbeat"])
    B = 60 / bpm
    bar = 4 * B
    n = int((total + 1) * SR)
    buf = np.zeros((n, 2))
    nb = int(total / bar) + 1
    for b in range(nb):
        t0 = b * bar
        r = roots[b % 4]
        q = quals[b % 4]
        padsig = sum(saw(note(r + iv), bar + 0.3, 6, 0.004) for iv in q)
        padsig = padsig * np.minimum(1, np.arange(len(padsig)) / (0.25 * SR)) * np.minimum(1, (len(padsig) - np.arange(len(padsig))) / (0.3 * SR))
        _add(buf, lowpass(padsig, 1500), t0, 0.05)
        for k in range(8):                         # bass 8ths
            tt = t0 + k * B / 2
            s = saw(note(r - 12), B / 2 * 0.9, 4) * env(B / 2 * 0.9, 0.005, 0.12)
            _add(buf, s, tt, 0.16)
        for k in range(4):
            tt = t0 + k * B
            _add(buf, kick(), tt, 0.34)
            _add(buf, hat(), tt + B / 2, 0.5, 0.25)
            if k % 2:
                _add(buf, hat(0.03), tt + B / 4, 0.25, -0.25)
        if b >= 1:                                 # pluck arp after the intro bar
            arp = [r + 12 + q[0], r + 12 + q[1], r + 12 + q[2], r + 24 + q[0]]
            for k in range(8):
                tt = t0 + k * B / 2
                s = saw(note(arp[k % 4]), 0.25, 5) * env(0.25, 0.003, 0.08)
                _add(buf, s, tt, 0.045, 0.35 if k % 2 else -0.35)
    for s in starts[1:]:                           # lift into each new scene
        _add(buf, kick(0.5), s, 0.25)
    fade = np.ones(n)
    fn = int(1.5 * SR)
    end = int(total * SR)
    fade[max(0, end - fn):end] = np.linspace(1, 0, min(fn, end))
    fade[end:] = 0
    buf *= fade[:, None]
    return buf[:end]


SFX = {"whoosh": (whoosh, 0.5, -0.12), "pop": (pop, 0.45, 0.0), "tick": (tick, 0.25, 0.0), "ding": (ding, 0.55, 0.0),
       "ding_s": (lambda: ding(79), 0.4, 0.0), "boom": (boom, 0.6, 0.0), "boom_s": (lambda: boom(0.8), 0.35, 0.0),
       "riser": (riser, 0.35, -0.2)}


def sfx(events, total):
    buf = np.zeros((int(total * SR), 2))
    for t, kind in events:
        fn, g, off = SFX.get(kind, SFX["pop"])
        _add(buf, fn(), max(0, t + off), g, _rng.uniform(-0.2, 0.2))
    return buf


def read_wav(p):
    with wave.open(str(p)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        ch = w.getnchannels()
    a = a.reshape(-1, ch)
    return a if ch == 2 else np.repeat(a, 2, axis=1)


def mix(voice, mus, fx, out):
    n = max(len(voice), len(mus), len(fx))
    def pad(x):
        return np.pad(x, ((0, n - len(x)), (0, 0)))
    voice, mus, fx = pad(voice), pad(mus), pad(fx)
    # levels relative to the voice: music bed ~10 dB under speech, ducked a further ~7 dB while someone talks
    e = np.abs(voice).mean(1)
    k = int(0.12 * SR)
    e = np.convolve(e, np.ones(k) / k, mode="same")
    talk = e > 0.01
    vr = np.sqrt((voice[talk] ** 2).mean()) if talk.any() else 0.1
    mr = np.sqrt((mus ** 2).mean()) or 1e-6
    mus = mus * (vr * 10 ** (-10 / 20) / mr)
    fp = np.abs(fx).max() or 1e-6
    fx = fx * (np.abs(voice).max() * 0.55 / fp)
    duck = 1 - 0.55 * np.clip(e / 0.02, 0, 1)
    m = voice + mus * duck[:, None] + fx
    m = np.tanh(m * 1.1) / 1.1
    m /= max(np.abs(m).max(), 1e-6) * 1.05
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((m * 32767).astype(np.int16).tobytes())
    return out
