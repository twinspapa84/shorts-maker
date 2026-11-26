#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
렌더에 쓰는 오디오 소재를 직접 만들어 assets/audio/ 에 넣습니다.

- bgm_lofi.wav   : 30초 무한 루프용 로파이 앰비언트(코드 패드 + 소프트 비트 + 바이닐 노이즈)
- sfx_whoosh.wav : 컷 전환용 짧은 "스윽"
- sfx_pop.wav    : 컷 전환용 짧은 "톡"

모두 이 스크립트가 만든 자체 제작 소리라 라이선스 걱정이 없습니다.
    python scripts/make_audio_assets.py
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

SR = 32000
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "audio"

BPM = 64.0
BEAT = 60.0 / BPM          # 0.9375s
BAR = BEAT * 4             # 3.75s
BARS = 8
LOOP_SEC = BAR * BARS      # 30.0s


def midi(m: int) -> float:
    return 440.0 * (2.0 ** ((m - 69) / 12.0))


def env_adsr(n: int, attack: float, release: float, sr: int = SR) -> np.ndarray:
    a = max(1, int(attack * sr))
    r = max(1, int(release * sr))
    e = np.ones(n, dtype=np.float32)
    a = min(a, n)
    r = min(r, n)
    e[:a] = np.linspace(0.0, 1.0, a, dtype=np.float32) ** 1.5
    e[n - r:] *= (np.linspace(1.0, 0.0, r, dtype=np.float32) ** 1.2)
    return e


def lowpass(x: np.ndarray, fc: float, sr: int = SR) -> np.ndarray:
    a = 1.0 - math.exp(-2.0 * math.pi * fc / sr)
    y = np.empty_like(x)
    acc = 0.0
    # 파이썬 루프는 느리므로 1차 IIR을 스택으로 한 번에 근사
    for i in range(len(x)):
        acc += a * (float(x[i]) - acc)
        y[i] = acc
    return y


def lowpass_fast(x: np.ndarray, fc: float, sr: int = SR) -> np.ndarray:
    """scipy 없이 쓰는 1차 저역 필터(FFT 근사, 짧은 구간용)."""
    n = len(x)
    X = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    resp = 1.0 / np.sqrt(1.0 + (freqs / max(fc, 1.0)) ** 4)
    return np.fft.irfft(X * resp, n=n).astype(np.float32)


def pad_voice(f: float, n: int, detune: float) -> np.ndarray:
    """부드러운 패드 — 기음 + 살짝 얹은 2배음, 천천히 흔들리는 비브라토."""
    t = np.arange(n, dtype=np.float32) / SR
    vib = 0.006 * np.sin(2 * np.pi * 0.17 * t) + 0.003 * np.sin(2 * np.pi * 0.31 * t + 1.7)
    base = np.sin(2 * np.pi * f * (1.0 + detune) * t + vib)
    harm = 0.22 * np.sin(2 * np.pi * f * 2.0 * (1.0 + detune) * t)
    return (base + harm).astype(np.float32)


def pluck_voice(f: float, n: int) -> np.ndarray:
    """로파이 피아노 느낌의 짧은 톡 — 부드러운 어택, 긴 감쇠."""
    t = np.arange(n, dtype=np.float32) / SR
    tone = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * f * 2 * t)
            + 0.12 * np.sin(2 * np.pi * f * 3 * t))
    env = np.exp(-t * 3.6).astype(np.float32)
    a = max(1, int(0.012 * SR))
    env[:a] *= np.linspace(0.0, 1.0, a, dtype=np.float32)
    return (tone * env).astype(np.float32)


def build_bgm(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    total = int(LOOP_SEC * SR) + SR  # 꼬리 여유
    mix = np.zeros(total, dtype=np.float32)

    # ── 코드 진행 (Am7 – Fmaj7 – Cmaj7 – G6, 두 바퀴)
    chords = [
        [45, 52, 55, 60],   # Am7
        [41, 48, 52, 57],   # Fmaj7
        [48, 55, 59, 64],   # Cmaj7
        [43, 50, 59, 64],   # G6
    ]
    prog = [0, 1, 2, 3, 0, 1, 2, 3]

    for bar, ci in enumerate(prog):
        start = int(bar * BAR * SR)
        seg_len = int((BAR + 0.9) * SR)
        env = env_adsr(seg_len, attack=0.55, release=1.1)
        n = min(seg_len, total - start)
        if n <= 0:
            continue
        for j, m in enumerate(chords[ci]):
            f = midi(m + 12) if j == 0 else midi(m)   # 맨 아래 음은 한 옥타브 위로
            amp = 0.13 if j == 0 else 0.095
            v = pad_voice(f, n, detune=(0.0 if j % 2 == 0 else 0.0012))
            mix[start:start + n] += (v * env[:n] * amp)

    # ── 아르페지오 (8분음표, 코드 톤을 위로 훑는 로파이 패턴)
    arp = [0, 1, 2, 3, 2, 1, 2, 3]
    for bar, ci in enumerate(prog):
        notes = [midi(m + 12) for m in chords[ci]]
        for k, idx in enumerate(arp):
            start = int((bar * BAR + k * BEAT * 0.5) * SR)
            n = min(int(1.5 * SR), total - start)
            if n <= 0:
                continue
            gain = 0.055 * (1.0 if k % 4 == 0 else 0.72)
            mix[start:start + n] += pluck_voice(notes[idx] * 2.0, n) * gain

    # ── 베이스 (각 마디 1·3박)
    for bar in range(BARS):
        root = midi(chords[prog[bar]][0])
        for b in (0, 2):
            start = int((bar * BAR + b * BEAT) * SR)
            n = int(BEAT * 1.7 * SR)
            n = min(n, total - start)
            if n <= 0:
                continue
            t = np.arange(n, dtype=np.float32) / SR
            tone = np.sin(2 * np.pi * root * t) + 0.25 * np.sin(2 * np.pi * root * 2 * t)
            env = np.exp(-t * 2.6).astype(np.float32)
            env[: int(0.02 * SR)] *= np.linspace(0, 1, int(0.02 * SR), dtype=np.float32)
            mix[start:start + n] += tone * env * 0.17

    # ── 소프트 킥 (1·3박) + 짧은 햇 (엇박)
    for bar in range(BARS):
        for b in (0, 2):
            start = int((bar * BAR + b * BEAT) * SR)
            n = min(int(0.26 * SR), total - start)
            t = np.arange(n, dtype=np.float32) / SR
            f = 52 + 90 * np.exp(-t * 26)
            ph = 2 * np.pi * np.cumsum(f) / SR
            kick = np.sin(ph) * np.exp(-t * 13) * 0.24
            mix[start:start + n] += kick.astype(np.float32)
        for b in (0.5, 1.5, 2.5, 3.5):
            start = int((bar * BAR + b * BEAT) * SR)
            n = min(int(0.055 * SR), total - start)
            if n <= 0:
                continue
            hat = rng.normal(0, 1, n).astype(np.float32)
            hat = hat - np.concatenate(([0.0], hat[:-1]))  # 고역 강조
            hat *= np.exp(-np.arange(n) / (0.012 * SR)).astype(np.float32) * 0.030
            mix[start:start + n] += hat

    # ── 바이닐 노이즈 + 가끔 지직
    noise = rng.normal(0, 1, total).astype(np.float32)
    noise = lowpass_fast(noise, 4200)
    mix += noise * 0.005
    for _ in range(int(LOOP_SEC * 1.6)):
        pos = int(rng.uniform(0, total - 800))
        ln = int(rng.uniform(40, 260))
        mix[pos:pos + ln] += rng.normal(0, 1, ln).astype(np.float32) * rng.uniform(0.002, 0.010)

    # ── 마무리: 살짝 따뜻하게, 부드럽게 눌러주고 정규화
    mix = lowpass_fast(mix, 5200)
    mix = np.tanh(mix * 1.05) / 1.05
    peak = float(np.max(np.abs(mix))) or 1.0
    mix *= 0.80 / peak

    # ── 루프 이음매: 끝 0.45초를 앞부분과 크로스페이드
    loop = mix[: int(LOOP_SEC * SR)].copy()
    fade = int(0.45 * SR)
    head = loop[:fade].copy()
    tail = loop[-fade:]
    w = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    loop[-fade:] = tail * (1.0 - w) + head * w
    return loop


def build_whoosh(seed: int = 11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(0.34 * SR)
    src = rng.normal(0, 1, n).astype(np.float32)
    out = np.empty(n, dtype=np.float32)
    low = band = 0.0
    q = 0.55
    for i in range(n):
        prog = i / n
        fc = 320 + 2600 * math.sin(math.pi * prog) ** 2   # 400Hz → 3kHz → 400Hz
        f = 2.0 * math.sin(math.pi * min(fc, SR / 6) / SR)
        low += f * band
        high = src[i] - low - q * band
        band += f * high
        out[i] = band
    env = np.clip(np.sin(np.pi * np.linspace(0, 1, n, dtype=np.float32)), 0.0, None) ** 1.6
    out = out * env
    # 살짝 스테레오 느낌을 주려고 오른쪽 채널은 8ms 늦춤 → 여기서는 모노로 반환
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.55).astype(np.float32)


def build_pop(seed: int = 13) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(0.13 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    f = 320 + 900 * np.exp(-t * 24)
    ph = 2 * np.pi * np.cumsum(f) / SR
    tone = np.sin(ph) * np.exp(-t * 34)
    click = rng.normal(0, 1, n).astype(np.float32) * np.exp(-t * 340) * 0.5
    out = (tone + click).astype(np.float32)
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.6).astype(np.float32)


def write_wav(path: Path, data: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    print(f"  {path.name}  {len(pcm) / sr:.2f}s  {path.stat().st_size / 1024:.0f}KB")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("오디오 소재 생성:")
    write_wav(OUT / "bgm_lofi.wav", build_bgm())
    write_wav(OUT / "sfx_whoosh.wav", build_whoosh())
    write_wav(OUT / "sfx_pop.wav", build_pop())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
