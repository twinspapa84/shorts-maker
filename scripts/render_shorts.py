#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
0원 쇼츠 자동 제작기 — 렌더러
================================

- 한국어 TTS(edge-tts)로 컷별 나레이션 생성
- 무료 이미지(Pexels / Pollinations) 또는 그라데이션 배경
- 1080x1920 (9:16) 30fps, Ken Burns 무빙, 형광펜 훅 타이틀 + 외곽선 자막
- 컷별 페이드 인/아웃 후 이어붙여 output/shorts_final.mp4 생성

GitHub Actions(ubuntu-latest)와 로컬 모두에서 동작합니다.

사용 예:
    python scripts/render_shorts.py --input payload.json --output output/shorts_final.mp4

payload.json 예:
{
  "title": "월급 300으로 시작한 부업 썰",
  "voice": "ko-KR-InJoonNeural",
  "rate": "+12%",
  "tone": "유머 썰",
  "cuts": [
    {"narration": "나레이션 문장", "subtitle": "화면 자막", "visual_query": "english visual prompt"}
  ]
}
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

# ----------------------------------------------------------------------------
# 기본 상수
# ----------------------------------------------------------------------------
W, H = 1080, 1920
FPS = 30
SAFE_TOP = 150          # 상단 안전 여백
SUB_BOTTOM = 1512       # 자막 박스 하단(릴스/쇼츠 UI 침범 방지)
HOOK_Y = 150            # 훅 타이틀 시작 y
AMBER = (255, 205, 0)
AMBER_SOFT = (255, 214, 60)

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"

FONT_CANDIDATES = [
    FONT_DIR / "NanumGothic-Bold.ttf",
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/opentype/pickle-hwp/NotoSansCJKkr-Regular.otf"),
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
]


def log(msg: str) -> None:
    print(f"[render] {msg}", flush=True)


# ----------------------------------------------------------------------------
# 외부 도구
# ----------------------------------------------------------------------------
def find_ffmpeg() -> str:
    env = os.environ.get("FFMPEG_PATH")
    if env and Path(env).exists():
        return env
    for name in ("ffmpeg",):
        p = shutil.which(name)
        if p:
            return p
    try:  # pip install imageio-ffmpeg 로 들어오는 정적 바이너리
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise RuntimeError(
        "ffmpeg를 찾을 수 없습니다. `apt-get install -y ffmpeg` 또는 `pip install imageio-ffmpeg` 로 설치해 주세요."
    )


FFMPEG = find_ffmpeg()


def run(cmd: list[str], *, quiet: bool = True, timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = proc.stdout.decode("utf-8", "replace")[-1800:]
        raise RuntimeError(f"명령 실패({proc.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    if not quiet:
        print(proc.stdout.decode("utf-8", "replace")[-1500:])
    return proc


def find_font_path() -> Path:
    env = os.environ.get("FONT_PATH")
    if env and Path(env).exists():
        return Path(env)
    for p in FONT_CANDIDATES:
        if p.exists():
            return p
    raise RuntimeError(
        "한국어 폰트를 찾을 수 없습니다. assets/fonts/NanumGothic-Bold.ttf 를 두거나 FONT_PATH 를 지정해 주세요."
    )


FONT_PATH = find_font_path()


def load_font(size: int):
    from PIL import ImageFont

    if FONT_PATH.suffix.lower() == ".ttc":
        for idx in range(4):
            try:
                f = ImageFont.truetype(str(FONT_PATH), size, index=idx)
                if f.getbbox("가")[2] > 0:
                    return f
            except Exception:
                continue
        return ImageFont.truetype(str(FONT_PATH), size, index=0)
    return ImageFont.truetype(str(FONT_PATH), size)


# ----------------------------------------------------------------------------
# 오디오 길이 / TTS
# ----------------------------------------------------------------------------
def audio_duration(path: Path) -> float:
    """ffprobe가 있으면 ffprobe, 없으면 ffmpeg로 디코딩해 정확히 잽니다."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
            )
            val = out.stdout.decode().strip()
            if val:
                return float(val)
        except Exception:
            pass
    wav = path.with_suffix(".probe.wav")
    run([FFMPEG, "-y", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "wav", str(wav)])
    with wave.open(str(wav), "rb") as w:
        dur = w.getnframes() / float(w.getframerate())
    wav.unlink(missing_ok=True)
    return dur


FALLBACK_VOICES = {
    "ko-KR-InJoonNeural": ["ko-KR-InJoonNeural", "ko-KR-HyunsuMultilingualNeural"],
    "ko-KR-SunHiNeural": ["ko-KR-SunHiNeural", "ko-KR-JiMinNeural"],
}


async def _tts_async(text: str, voice: str, rate: str, out_path: Path) -> None:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(str(out_path))


def synthesize(text: str, voice: str, rate: str, out_path: Path) -> None:
    voices = FALLBACK_VOICES.get(voice, [voice, "ko-KR-SunHiNeural"])
    last_err: Exception | None = None
    for v in voices:
        for attempt in range(3):
            try:
                asyncio.run(_tts_async(text, v, rate, out_path))
                if out_path.exists() and out_path.stat().st_size > 1200:
                    return
                raise RuntimeError("빈 오디오 파일")
            except Exception as e:  # noqa: BLE001
                last_err = e
                log(f"  TTS 재시도 {attempt + 1}/3 ({v}): {e}")
                time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"TTS 생성 실패: {last_err}")


# ----------------------------------------------------------------------------
# 비주얼 소스
# ----------------------------------------------------------------------------
def _http_get(url: str, *, timeout: int = 60, headers: dict | None = None) -> bytes:
    import requests

    r = requests.get(url, timeout=timeout, headers=headers or {}, allow_redirects=True)
    r.raise_for_status()
    return r.content


def pexels_visual(query: str, api_key: str, dest: Path, seed: int) -> str | None:
    """Pexels에서 세로 영상/사진을 받아옵니다. 실패하면 None."""
    if not api_key:
        return None
    headers = {"Authorization": api_key}
    try:
        import requests

        r = requests.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "orientation": "portrait", "per_page": 8, "size": "medium"},
            headers=headers, timeout=30,
        )
        if r.ok:
            videos = r.json().get("videos", [])
            random.Random(seed).shuffle(videos)
            for v in videos[:4]:
                files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"]
                files = [f for f in files if (f.get("height") or 0) >= 720 and (f.get("width") or 0) < (f.get("height") or 0)]
                files.sort(key=lambda f: abs((f.get("height") or 1080) - 1920))
                for f in files[:1]:
                    try:
                        data = _http_get(f["link"], timeout=90)
                        if len(data) > 200_000:
                            dest = dest.with_suffix(".mp4")
                            dest.write_bytes(data)
                            return str(dest)
                    except Exception:
                        continue

        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "orientation": "portrait", "per_page": 8},
            headers=headers, timeout=30,
        )
        if r.ok:
            photos = r.json().get("photos", [])
            random.Random(seed).shuffle(photos)
            for p in photos[:3]:
                try:
                    data = _http_get(p["src"]["large2x"], timeout=60)
                    if len(data) > 30_000:
                        dest = dest.with_suffix(".jpg")
                        dest.write_bytes(data)
                        return str(dest)
                except Exception:
                    continue
    except Exception as e:  # noqa: BLE001
        log(f"  Pexels 실패: {e}")
    return None


POLLINATIONS_STYLE = (
    "vertical 9:16 composition, cinematic lighting, shallow depth of field, "
    "photorealistic, high detail, no text, no watermark, korean everyday life"
)


def pollinations_visual(query: str, dest: Path, seed: int) -> str | None:
    """Pollinations.ai 무료 이미지. IP당 45초 1장 제한이 있어 대기 후 재시도합니다."""
    import requests

    if os.environ.get("NO_POLLINATIONS"):
        return None
    prompt = f"{query}, {POLLINATIONS_STYLE}"
    url = (
        "https://image.pollinations.ai/prompt/"
        + _urlquote(prompt)
        + f"?width=1080&height=1920&nologo=true&seed={seed}"
    )
    waited = 0.0
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=120, allow_redirects=True)
            if r.status_code == 402:
                wait = 20.0
                try:
                    info = r.json().get("error", {})
                    m = re.search(r"Retry after (\d+)", info.get("message", ""))
                    if m:
                        wait = float(m.group(1)) + 3.0
                except Exception:  # noqa: BLE001
                    pass
                if waited + wait > 190:
                    log("  Pollinations 무료 한도 소진 — 다음 소스로 넘어갑니다.")
                    return None
                log(f"  Pollinations 무료 한도: {wait:.0f}초 대기 후 재시도…")
                time.sleep(wait)
                waited += wait
                continue
            r.raise_for_status()
            data = r.content
            if len(data) < 20_000:
                raise RuntimeError(f"이미지가 너무 작음({len(data)}B)")
            p = dest.with_suffix(".jpg")
            p.write_bytes(data)
            return str(p)
        except Exception as e:  # noqa: BLE001
            log(f"  Pollinations 재시도 {attempt + 1}/3: {e}")
            time.sleep(2 + attempt * 2)
    return None


def _urlquote(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def gradient_visual(query: str, dest: Path, seed: int, word: str = "") -> str:
    """마지막 안전장치: 고급스러운 다크 그라데이션 카드 (자막형 쇼츠 스타일)."""
    from PIL import Image, ImageDraw, ImageFilter

    rnd = random.Random(seed)
    palettes = [
        ((10, 14, 26), (28, 38, 72), (86, 120, 255)),
        ((16, 12, 24), (56, 24, 64), (255, 96, 148)),
        ((8, 18, 22), (18, 54, 58), (0, 210, 170)),
        ((22, 14, 10), (62, 36, 18), (255, 160, 64)),
    ]
    top, bottom, accent = palettes[seed % len(palettes)]
    img = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)

    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = rnd.randint(160, W - 160), rnd.randint(400, H - 700)
    rad = rnd.randint(320, 520)
    for i in range(16, 0, -1):
        rr = int(rad * i / 16)
        a = int(255 * (1 - i / 16) ** 1.6)
        gd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=tuple(int(accent[j] * a / 255) for j in range(3)))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img = Image.blend(img, Image.composite(glow, img, glow.convert("L")), 0.85)

    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(3):
        x = rnd.randint(-200, W)
        y = rnd.randint(-100, H)
        s = rnd.randint(260, 620)
        d.ellipse([x, y, x + s, y + int(s * 0.9)], outline=(255, 255, 255, 26), width=2)

    # 대각선 빛줄기
    streak = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(streak)
    sd.polygon([(-200, H * 0.75), (W * 0.55, -100), (W * 0.78, -100), (-200, H * 0.95)],
               fill=(255, 255, 255, 12))
    img = Image.alpha_composite(img.convert("RGBA"), streak.filter(ImageFilter.GaussianBlur(40)))

    # 배경 워터마크 텍스처 (자막 첫 낱말을 크게, 아주 옅게 · 화면 안에 맞춰 중앙 배치)
    if word:
        wm_size = 300
        while wm_size > 90 and text_width(ImageDraw.Draw(img), word, load_font(wm_size)) > 940:
            wm_size -= 10
        wf = load_font(wm_size)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (W / 2, 780), word, font=wf, fill=(255, 255, 255, 20), anchor="mm"
        )
        img = Image.alpha_composite(img, layer)

    # 필름 그레인 (고급스러운 질감)
    try:
        import numpy as np

        arr = np.asarray(img.convert("RGB")).astype(np.int16)
        noise = np.random.default_rng(seed).normal(0, 5.0, (H, W, 1))
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr, "RGB").convert("RGBA")
    except Exception:  # noqa: BLE001
        pass

    p = dest.with_suffix(".png")
    img.convert("RGB").save(p)
    return str(p)


def fetch_visual(
    query: str, index: int, work: Path, pexels_key: str = "", word: str = ""
) -> tuple[str, str]:
    """컷별 배경. (경로, 소스이름) — 절대 실패하지 않습니다."""
    query = (query or "").strip() or "calm cinematic korean city night"
    seed = int(hashlib.sha256(f"{query}-{index}".encode()).hexdigest()[:8], 16) % 2147483647
    dest = work / f"visual_{index:02d}"

    # 같은 workdir로 다시 돌릴 때 재사용 (재시도·디버깅 빠르게)
    for ext, kind in ((".mp4", "pexels"), (".jpg", "pollinations"), (".png", "gradient")):
        cached = dest.with_suffix(ext)
        if cached.exists() and cached.stat().st_size > 20_000:
            return str(cached), kind + " (cache)"

    if pexels_key:
        got = pexels_visual(query, pexels_key, dest, seed)
        if got:
            return got, "pexels"
    got = pollinations_visual(query, dest, seed)
    if got:
        return got, "pollinations"
    return gradient_visual(query, dest, seed, word), "gradient"


# ----------------------------------------------------------------------------
# 타이포그래피
# ----------------------------------------------------------------------------
def text_width(draw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def wrap_text(draw, text: str, font, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in str(text).split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = f"{cur} {w}".strip()
            if not cur or text_width(draw, cand, font) <= max_w:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    out: list[str] = []
    for ln in lines:
        while text_width(draw, ln, font) > max_w and len(ln) > 1:
            k = len(ln)
            while k > 1 and text_width(draw, ln[:k], font) > max_w:
                k -= 1
            out.append(ln[:k])
            ln = ln[k:]
        if ln:
            out.append(ln)
    return out


def balance_lines(draw, lines: list[str], font) -> list[str]:
    """마지막 줄에 단어 하나만 남는 어색한 줄바꿈을 고릅니다."""
    lines = list(lines)
    for _ in range(6):
        if len(lines) < 2:
            break
        a, b = lines[-2], lines[-1]
        wa, wb = text_width(draw, a, font), text_width(draw, b, font)
        if wb >= wa * 0.72 or " " not in a:
            break
        head, tail = a.rsplit(" ", 1)
        if not head:
            break
        new_b = f"{tail} {b}"
        if text_width(draw, new_b, font) > text_width(draw, a, font) * 1.05:
            break
        lines[-2], lines[-1] = head, new_b
    return lines


def fit_text(draw, text: str, max_w: float, max_lines: int, sizes: list[int]):
    for size in sizes:
        font = load_font(size)
        lines = balance_lines(draw, wrap_text(draw, text, font, max_w), font)
        if len(lines) <= max_lines:
            return font, lines
    font = load_font(sizes[-1])
    lines = balance_lines(draw, wrap_text(draw, text, font, max_w), font)[:max_lines]
    return font, lines


def rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_hook(img, title: str) -> None:
    """상단 훅 타이틀 — 노란 형광펜 마커 + 검정 굵은 글씨."""
    from PIL import ImageDraw

    if not title:
        return
    d = ImageDraw.Draw(img, "RGBA")
    font, lines = fit_text(d, title, 840, 2, [66, 60, 54, 48, 42])
    line_h = int(font.size * 1.42)
    y = HOOK_Y
    for line in lines:
        tw = text_width(d, line, font)
        x0 = (W - tw) / 2
        box = [x0 - 26, y, x0 + tw + 26, y + line_h - 6]
        rounded(d, box, radius=16, fill=(*AMBER, 246))
        # 살짝 위에 밝은 라인을 얹어 형광펜 질감
        rounded(d, [box[0], box[1], box[2], box[1] + 6], radius=3, fill=(255, 236, 130, 200))
        d.text(((W / 2), y + (line_h - 6) / 2 + 2), line, font=font, fill=(14, 14, 16, 255), anchor="mm")
        y += line_h


def draw_subtitle(img, text: str) -> None:
    """하단 자막 — 두꺼운 외곽선 + 그림자 (박스 없이 스크림 위에)."""
    from PIL import ImageDraw

    text = (text or "").strip()
    if not text:
        return
    d = ImageDraw.Draw(img, "RGBA")
    font, lines = fit_text(d, text, 900, 3, [82, 76, 70, 64, 58, 52])
    line_h = int(font.size * 1.3)
    block_h = line_h * len(lines)
    y_top = SUB_BOTTOM - block_h
    stroke = max(4, font.size // 9)

    # 그림자
    for i, line in enumerate(lines):
        cy = y_top + line_h * i + line_h / 2
        d.text((W / 2 + 2, cy + 7), line, font=font, fill=(0, 0, 0, 150), anchor="mm", align="center")
    # 본문
    for i, line in enumerate(lines):
        cy = y_top + line_h * i + line_h / 2
        d.text(
            (W / 2, cy), line, font=font, fill=(255, 255, 255, 255), anchor="mm",
            align="center", stroke_width=stroke, stroke_fill=(0, 0, 0, 255),
        )


def draw_progress(img, index: int, total: int) -> None:
    from PIL import ImageDraw

    d = ImageDraw.Draw(img, "RGBA")
    margin = 34
    gap = 8
    usable = W - margin * 2 - gap * (total - 1)
    seg_w = usable / max(total, 1)
    y = 44
    h = 7
    for i in range(total):
        x0 = margin + i * (seg_w + gap)
        if i < index:
            rounded(d, [x0, y, x0 + seg_w, y + h], radius=4, fill=(*AMBER, 235))
        elif i == index:
            rounded(d, [x0, y, x0 + seg_w, y + h], radius=4, fill=(255, 255, 255, 70))
            rounded(d, [x0, y, x0 + seg_w * 0.45, y + h], radius=4, fill=(*AMBER, 235))
        else:
            rounded(d, [x0, y, x0 + seg_w, y + h], radius=4, fill=(255, 255, 255, 70))


def make_overlay(title: str, subtitle: str, index: int, total: int, dest: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")

    # 상단 스크림
    top_h = 620
    for y in range(0, top_h, 2):
        t = y / top_h
        a = int(205 * (1 - t) ** 1.5)
        d.rectangle([0, y, W, y + 2], fill=(4, 6, 12, a))
    # 하단 스크림 (맨 아래는 완전히 덮어 이미지 워터마크까지 가림)
    bot_start = 1040
    for y in range(bot_start, H, 2):
        t = (y - bot_start) / (H - bot_start)
        a = int(45 + 210 * (t ** 1.2))
        if y > H - 110:
            a = min(255, a + int((255 - a) * ((y - (H - 110)) / 110)))
        d.rectangle([0, y, W, y + 2], fill=(3, 4, 9, a))

    # 비네트 (가장자리 살짝 어둡게)
    vign = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vign)
    vd.ellipse([-W * 0.35, -H * 0.22, W * 1.35, H * 1.22], fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(160))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 90))
    img = Image.composite(img, Image.alpha_composite(img, dark), vign)
    d = ImageDraw.Draw(img, "RGBA")

    draw_progress(img, index, total)
    draw_hook(img, title)
    draw_subtitle(img, subtitle)

    img.save(dest)
    return dest


# ----------------------------------------------------------------------------
# 영상 합성
# ----------------------------------------------------------------------------
def ken_burns_filter(mode: str, frames: int) -> str:
    """정지 이미지에 부드러운 줌/팬 모션."""
    frames = max(frames, 2)
    zoom_in = "min(1.0+0.00110*on,1.13)"
    zoom_out = "max(1.13-0.00110*on,1.0)"
    if mode == "in":
        z, x, y = zoom_in, "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mode == "out":
        z, x, y = zoom_out, "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mode == "pan_r":
        z = "1.10"
        x = f"(iw-iw/zoom)*(on/{frames - 1})"
        y = "ih/2-(ih/zoom/2)"
    elif mode == "pan_l":
        z = "1.10"
        x = f"(iw-iw/zoom)*(1-on/{frames - 1})"
        y = "ih/2-(ih/zoom/2)"
    else:  # pan_up
        z = "1.10"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*(1-on/{frames - 1})"
    return (
        "scale=1350:2400:force_original_aspect_ratio=increase,crop=1350:2400,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},"
        "eq=saturation=1.07:contrast=1.03:brightness=0.01,setsar=1"
    )


MOTION_MODES = ["in", "pan_r", "out", "pan_l", "in", "pan_up"]


def render_cut(
    *,
    index: int,
    total: int,
    visual: str,
    visual_kind: str,
    narration: str,
    subtitle: str,
    title: str,
    voice: str,
    rate: str,
    work: Path,
) -> dict:
    idx = index
    audio_path = work / f"voice_{idx:02d}.mp3"
    log(f"[{idx + 1}/{total}] 음성 생성 중…")
    synthesize(narration, voice, rate, audio_path)
    voice_dur = audio_duration(audio_path)
    dur = round(voice_dur + 0.45, 3)

    overlay = make_overlay(title, subtitle, idx, total, work / f"overlay_{idx:02d}.png")

    out = work / f"cut_{idx:02d}.mp4"
    fade_in = 0.32 if idx == 0 else 0.14
    fade_out = 0.42 if idx == total - 1 else 0.16
    fade_out_start = max(dur - fade_out - 0.02, 0.1)
    post = (
        f"[v0]fade=t=in:st=0:d={fade_in},fade=t=out:st={fade_out_start:.3f}:d={fade_out},"
        f"format=yuv420p[v]"
    )
    afilter = (
        f"afade=t=in:st=0:d=0.05,afade=t=out:st={max(dur - 0.3, 0.1):.3f}:d=0.3,"
        f"apad=pad_dur=0.45,loudnorm=I=-14:TP=-1.5:LRA=11"
    )

    log(f"[{idx + 1}/{total}] 컷 렌더링 ({dur:.1f}s, {visual_kind})…")
    if visual.endswith(".mp4"):
        cmd = [
            FFMPEG, "-y", "-stream_loop", "-1", "-i", visual,
            "-loop", "1", "-framerate", str(FPS), "-i", str(overlay),
            "-i", str(audio_path),
            "-filter_complex",
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"fps={FPS},eq=saturation=1.05:contrast=1.03,setsar=1[bg];"
            f"[bg][1:v]overlay=0:0:format=auto[v0];{post}",
            "-map", "[v]", "-map", "2:a",
            "-af", afilter,
            "-t", f"{dur:.3f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(out),
        ]
    else:
        frames = int(math.ceil(dur * FPS)) + 2
        kb = ken_burns_filter(MOTION_MODES[idx % len(MOTION_MODES)], frames)
        cmd = [
            FFMPEG, "-y", "-loop", "1", "-framerate", str(FPS), "-i", visual,
            "-loop", "1", "-framerate", str(FPS), "-i", str(overlay),
            "-i", str(audio_path),
            "-filter_complex",
            f"[0:v]{kb}[bg];"
            f"[bg][1:v]overlay=0:0:format=auto[v0];{post}",
            "-map", "[v]", "-map", "2:a",
            "-af", afilter,
            "-t", f"{dur:.3f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(out),
        ]
    run(cmd, timeout=1200)
    return {
        "index": idx + 1,
        "narration": narration,
        "subtitle": subtitle,
        "duration": dur,
        "visual_source": visual_kind,
        "file": str(out),
    }


def concat_cuts(cuts: list[dict], out_path: Path, work: Path) -> None:
    list_file = work / "concat.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for c in cuts:
            f.write(f"file '{Path(c['file']).resolve().as_posix()}'\n")
    log("컷 이어붙이는 중…")
    run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(out_path),
    ])


def make_thumbnail(video: Path, dest: Path) -> None:
    run([FFMPEG, "-y", "-ss", "0.8", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(dest)])


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="0원 쇼츠 렌더러")
    ap.add_argument("--input", required=True, help="payload.json 경로")
    ap.add_argument("--output", default="output/shorts_final.mp4")
    ap.add_argument("--workdir", default="", help="중간 산출물 폴더(기본: output/_build)")
    ap.add_argument("--keep", action="store_true", help="중간 산출물 보관")
    args = ap.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    cuts_in = payload.get("cuts") or []
    if not cuts_in:
        raise SystemExit("payload에 cuts가 비어 있습니다.")

    title = (payload.get("title") or "").strip()
    voice = payload.get("voice") or "ko-KR-InJoonNeural"
    rate = payload.get("rate") or "+12%"
    pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(args.workdir) if args.workdir else out_path.parent / "_build"
    work.mkdir(parents=True, exist_ok=True)

    log(f"폰트: {FONT_PATH}")
    log(f"컷 {len(cuts_in)}개 · 보이스 {voice} · 속도 {rate} · Pexels {'사용' if pexels_key else '없음'}")

    started = time.time()
    results: list[dict] = []
    for i, cut in enumerate(cuts_in):
        narration = (cut.get("narration") or cut.get("subtitle") or "").strip()
        subtitle = (cut.get("subtitle") or cut.get("narration") or "").strip()
        if not narration:
            continue
        visual, kind = fetch_visual(
            cut.get("visual_query", ""), i, work, pexels_key,
            word=re.sub(r"[^가-힣A-Za-z0-9 ]", " ", subtitle).split(" ")[0] if subtitle else "",
        )
        results.append(render_cut(
            index=i, total=len(cuts_in), visual=visual, visual_kind=kind,
            narration=narration, subtitle=subtitle, title=title,
            voice=voice, rate=rate, work=work,
        ))

    if not results:
        raise SystemExit("유효한 컷이 없습니다.")

    concat_cuts(results, out_path, work)
    thumb = out_path.with_name("thumbnail.jpg")
    try:
        make_thumbnail(out_path, thumb)
    except Exception as e:  # noqa: BLE001
        log(f"썸네일 생성 실패(무시): {e}")

    total_dur = audio_duration(out_path)
    manifest = {
        "title": title,
        "voice": voice,
        "rate": rate,
        "cut_count": len(results),
        "duration": round(total_dur, 2),
        "output": str(out_path),
        "thumbnail": str(thumb) if thumb.exists() else None,
        "cuts": [{k: v for k, v in c.items() if k != "file"} for c in results],
        "render_seconds": round(time.time() - started, 1),
    }
    (out_path.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"완료: {out_path} ({total_dur:.1f}s, {out_path.stat().st_size / 1e6:.1f}MB)")
    print(json.dumps({"ok": True, "output": str(out_path), "duration": round(total_dur, 2)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
