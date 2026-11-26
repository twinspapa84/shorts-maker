#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
0원 쇼츠 자동 제작기 — 렌더러 v2 (퀄리티 업그레이드)
=====================================================

- 한국어 TTS(edge-tts)로 컷별 나레이션 생성
- 무료 이미지(Pexels / Pollinations flux·turbo / Wikimedia / LoremFlickr / Picsum) → 그라데이션
- 1080x1920 (9:16) 30fps, 컷마다 다른 Ken Burns 무빙
- 하단 자막: 나레이션 문장을 10~14자 호흡 단위로 쪼개 말 속도에 맞춰 순차 표시
- 컷 사이 암전 없음 (첫 컷 인트로 페이드 인 + 마지막 컷 아웃트로만)
- BGM(-20dB 루프) + 컷 전환 SFX 자동 믹싱 (payload "bgm": false 로 끔)

GitHub Actions(ubuntu-latest)와 로컬 모두에서 동작합니다.

사용 예:
    python scripts/render_shorts.py --input payload.json --output output/shorts_final.mp4

payload.json 예:
{
  "title": "월급 300으로 시작한 부업 썰",
  "voice": "ko-KR-InJoonNeural",
  "rate": "+12%",
  "tone": "유머 썰",
  "bgm": true,
  "cuts": [
    {"narration": "말할 문장", "subtitle": "화면 자막", "visual_query": "english visual prompt"}
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
import time
import wave
from pathlib import Path

# ----------------------------------------------------------------------------
# 기본 상수
# ----------------------------------------------------------------------------
W, H = 1080, 1920
FPS = 30
SAFE_TOP = 150          # 상단 안전 여백
HOOK_Y = 150            # 훅 타이틀 시작 y
SUB_BOTTOM = 1450       # 자막 블록 하단(릴스·쇼츠 UI 침범 방지)
STRIP_H = 430           # 자막 스트립 높이
STRIP_Y = SUB_BOTTOM - STRIP_H
SUB_MAX_W = 900
AMBER = (255, 205, 0)
AMBER_SOFT = (255, 214, 60)

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
AUDIO_DIR = ROOT / "assets" / "audio"
BGM_PATH = AUDIO_DIR / "bgm_lofi.wav"
SFX_WHOOSH = AUDIO_DIR / "sfx_whoosh.wav"
SFX_POP = AUDIO_DIR / "sfx_pop.wav"

UA = "shorts-maker/2.0 (https://github.com/twinspapa84/shorts-maker)"

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
    p = shutil.which("ffmpeg")
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
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
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

    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    r = requests.get(url, timeout=timeout, headers=h, allow_redirects=True)
    r.raise_for_status()
    return r.content


def _is_image(data: bytes) -> bool:
    """HTML 오류 페이지를 이미지로 착각하지 않도록 PIL로 실제 디코딩해 봅니다."""
    if len(data) < 10_000:
        return False
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            return min(im.size) >= 400
    except Exception:
        return False


def pexels_visual(query: str, api_key: str, dest: Path, seed: int) -> tuple[str, str] | None:
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
                            p = dest.with_suffix(".pexels.mp4")
                            p.write_bytes(data)
                            return str(p), "pexels"
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
                    if _is_image(data):
                        d = dest.with_suffix(".pexels.jpg")
                        d.write_bytes(data)
                        return str(d), "pexels"
                except Exception:
                    continue
    except Exception as e:  # noqa: BLE001
        log(f"  Pexels 실패: {e}")
    return None


POLLINATIONS_STYLE = (
    "vertical 9:16 composition, cinematic lighting, shallow depth of field, "
    "photorealistic, high detail, no text, no watermark, korean everyday life"
)


def pollinations_visual(query: str, dest: Path, seed: int, model: str = "flux",
                        wait_budget: float = 18.0) -> tuple[str, str] | None:
    """Pollinations.ai 무료 이미지. 402(한도)는 짧게 기다렸다 한 번만 재시도합니다."""
    import requests
    from urllib.parse import quote

    if os.environ.get("NO_POLLINATIONS"):
        return None
    prompt = f"{query}, {POLLINATIONS_STYLE}"
    url = (
        "https://image.pollinations.ai/prompt/"
        + quote(prompt, safe="")
        + f"?width=1080&height=1920&nologo=true&seed={seed}&model={model}"
    )
    waited = 0.0
    for attempt in range(2):
        try:
            r = requests.get(url, timeout=100, headers={"User-Agent": UA}, allow_redirects=True)
            if r.status_code == 402:
                wait = 15.0
                try:
                    info = r.json().get("error", {})
                    m = re.search(r"Retry after (\d+)", str(info.get("message", "")))
                    if m:
                        wait = float(m.group(1)) + 2.0
                except Exception:  # noqa: BLE001
                    pass
                if waited + wait > wait_budget:
                    log(f"  Pollinations({model}) 무료 한도 — 다음 소스로 넘어갑니다.")
                    return None
                log(f"  Pollinations({model}) 한도: {wait:.0f}초 대기 후 재시도…")
                time.sleep(wait)
                waited += wait
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                log(f"  Pollinations({model}) {r.status_code} — 재시도 {attempt + 1}/2")
                time.sleep(1.5 + attempt * 2)
                continue
            r.raise_for_status()
            data = r.content
            if not _is_image(data):
                raise RuntimeError(f"이미지 아님({len(data)}B)")
            p = dest.with_suffix(f".{model}.jpg")
            p.write_bytes(data)
            return str(p), f"pollinations-{model}"
        except Exception as e:  # noqa: BLE001
            log(f"  Pollinations({model}) 재시도 {attempt + 1}/2: {e}")
            time.sleep(1.5 + attempt * 2)
    return None


COMMONS_STOP = {
    "cinematic", "lighting", "photo", "photograph", "photorealistic", "high", "detail",
    "moody", "shallow", "depth", "field", "vertical", "composition", "korean", "bokeh",
    "soft", "warm", "scene", "view", "close", "background", "blurred", "film", "style",
    "still", "shot", "lifestyle", "everyday", "life", "no", "text", "watermark", "9", "16",
    "and", "with", "the", "for", "from", "into", "very", "ultra", "realistic", "portrait",
}

# 사진이 아니라 자료·도해·로고인 제목은 배경으로 쓰지 않습니다(지도·도표·로고 등)
BAD_TITLE = re.compile(
    r"(\bmap\b|\bplan\b|diagram|graph\b|chart|logo|poster|book cover|document|screenshot|"
    r"\bstamp\b|banknote|\bcoin\b|drawing|painting|sketch|illustration|infographic|sheet music|"
    r"\bpage\b|manuscript|\bbank\b|seal\b|coat of arms|\bflag\b|workboard|interface|\bui\b|"
    r"\bapps?\b|billboard|whiteboard|signage|text|screens?hot)", re.I)


def _keywords(query: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z]+", (query or "").lower())
            if len(w) > 2 and w not in COMMONS_STOP]


def _relevance(title: str, words: list[str]) -> int:
    """제목에 검색어가 '단어 단위로' 들어 있는지 세어 봅니다(manager ≠ man)."""
    t = (title or "").lower()
    return sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", t))


def openverse_visual(query: str, dest: Path, seed: int, *, sources: str = "",
                    timeout: int = 20) -> tuple[str, str] | None:
    """Openverse(StockSnap·Flickr·위키미디어 통합)에서 키 없이 실제 사진을 찾습니다.
    sources 를 주면 그 제공처만 봅니다(사진 품질이 좋은 곳부터)."""
    import requests

    words = _keywords(query)
    if not words:
        return None
    need = 2 if len(words) > 2 else 1
    params = {"q": " ".join(words[:3]), "page_size": 10, "license_type": "all",
              "aspect_ratio": "tall", "size": "large"}
    if sources:
        params["source"] = sources
    try:
        r = requests.get("https://api.openverse.org/v1/images/", params=params,
                         headers={"User-Agent": UA}, timeout=timeout)
        if not r.ok:
            log(f"  Openverse {r.status_code}")
            return None
        results = r.json().get("results", [])
    except Exception as e:  # noqa: BLE001
        log(f"  Openverse 실패: {e}")
        return None

    rnd = random.Random(seed)
    scored = []
    for it in results:
        title = it.get("title") or ""
        if BAD_TITLE.search(title):
            continue
        score = _relevance(title, words)
        if score < need:
            continue
        portrait = 1 if (it.get("height") or 0) > (it.get("width") or 0) else 0
        scored.append((score, portrait, rnd.random(), it))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))

    for _s, _p, _r, it in scored[:3]:
        url = it.get("url")
        if not url:
            continue
        try:
            data = _http_get(url, timeout=40)
            if not _is_image(data):
                continue
            p = dest.with_suffix(".openverse.jpg")
            p.write_bytes(data)
            log(f"  Openverse: {(it.get('title') or '')[:44]}")
            return str(p), "openverse"
        except Exception:
            continue
    return None


def commons_visual(query: str, dest: Path, seed: int) -> tuple[str, str] | None:
    """Wikimedia Commons에서 키 없이 실제 사진을 찾습니다."""
    import requests

    words = _keywords(query)
    if not words:
        return None
    candidates = []
    for n in (4, 3, 2, 1):
        q = " ".join(words[:n])
        if q and q not in candidates:
            candidates.append(q)

    for q in candidates[:3]:
        try:
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query", "format": "json", "generator": "search",
                    "gsrnamespace": 6, "gsrsearch": f"{q} filetype:bitmap", "gsrlimit": 8,
                    "prop": "imageinfo", "iiprop": "url|mime|size", "iiurlwidth": 1200,
                },
                headers={"User-Agent": UA}, timeout=20,
            )
            if not r.ok:
                continue
            pages = r.json().get("query", {}).get("pages", {})
            ordered = [v for _, v in sorted(pages.items(), key=lambda kv: kv[1].get("index", 999))]
            for page in ordered:
                info = (page.get("imageinfo") or [{}])[0]
                if info.get("mime") not in ("image/jpeg", "image/png"):
                    continue
                if (info.get("width") or 0) < 700:
                    continue
                title = page.get("title") or ""
                # 검색어와 무관한 사진(지도·도해·로고 등)은 배경으로 쓰지 않습니다
                if BAD_TITLE.search(title) or _relevance(title, words) < min(2, len(words)):
                    continue
                url = info.get("thumburl") or info.get("url")
                if not url:
                    continue
                try:
                    data = _http_get(url, timeout=40)
                    if not _is_image(data):
                        continue
                    p = dest.with_suffix(".commons.jpg")
                    p.write_bytes(data)
                    log(f"  Wikimedia: {page.get('title', '')[:48]} ({q})")
                    return str(p), "wikimedia"
                except Exception:
                    continue
        except Exception as e:  # noqa: BLE001
            log(f"  Wikimedia 실패: {e}")
    return None


def loremflickr_visual(query: str, dest: Path, seed: int) -> tuple[str, str] | None:
    """LoremFlickr — 키 없이 태그 기반 사진. 서비스가 불안정하면 조용히 건너뜁니다."""
    import requests

    words = [w for w in re.findall(r"[a-zA-Z]+", (query or "").lower())
             if len(w) > 2 and w not in COMMONS_STOP]
    if not words:
        return None
    kw = ",".join(words[:2])
    try:
        r = requests.get(
            f"https://loremflickr.com/1080/1920/{kw}",
            params={"lock": seed % 997}, headers={"User-Agent": UA}, timeout=12,
        )
        if r.ok and _is_image(r.content):
            p = dest.with_suffix(".loremflickr.jpg")
            p.write_bytes(r.content)
            return str(p), "loremflickr"
    except Exception:
        pass
    return None


def picsum_visual(dest: Path, seed: int) -> tuple[str, str] | None:
    """Picsum — 검색과 무관하지만 '진짜 사진'은 보장되는 마지막 사진 폴백."""
    try:
        data = _http_get(f"https://picsum.photos/seed/{seed}/1080/1920", timeout=25)
        if _is_image(data):
            p = dest.with_suffix(".picsum.jpg")
            p.write_bytes(data)
            return str(p), "picsum"
    except Exception:
        pass
    return None


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

    # 배경 워터마크 텍스처 (자막 첫 낱말을 크게, 아주 옅게)
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


def fetch_visual(query: str, index: int, work: Path, pexels_key: str = "",
                 word: str = "") -> tuple[str, str]:
    """컷별 배경. (경로, 소스이름) — 503이 나도 사진이 들어가고, 절대 실패하지 않습니다."""
    query = (query or "").strip() or "calm cinematic korean city night"
    seed = int(hashlib.sha256(f"{query}-{index}".encode()).hexdigest()[:8], 16) % 2147483647
    dest = work / f"visual_{index:02d}"
    meta = work / f"visual_{index:02d}.src"

    # 같은 workdir로 다시 돌릴 때 재사용 (재시도·디버깅 빠르게)
    if meta.exists():
        try:
            line = meta.read_text(encoding="utf-8").strip().split("\t")
            p = Path(line[0])
            if p.exists() and p.stat().st_size > 20_000:
                return str(p), (line[1] if len(line) > 1 else "cache") + " (cache)"
        except Exception:
            pass

    def done(path: str, name: str) -> tuple[str, str]:
        log(f"  배경: {name}")
        try:
            meta.write_text(f"{path}\t{name}", encoding="utf-8")
        except Exception:
            pass
        return path, name

    if pexels_key:
        got = pexels_visual(query, pexels_key, dest, seed)
        if got:
            return done(*got)

    for model in ("flux", "turbo"):
        got = pollinations_visual(query, dest, seed, model=model)
        if got:
            return done(*got)

    # 사진 품질이 검증된 스톡 제공처(StockSnap 등)부터 — 제목이 짧아 엉뚱한 사진이 적습니다
    got = openverse_visual(query, dest, seed, sources="stocksnap,nappy,rawpixel")
    if got:
        return done(*got)

    # 주제와 무관해도 '진짜 사진'은 보장되는 안전망
    got = picsum_visual(dest, seed)
    if got:
        return done(*got)

    # 주제에 맞는 실제 사진 (검색 정확도가 낮아 뒤쪽에 둡니다)
    got = commons_visual(query, dest, seed)
    if got:
        return done(*got)

    got = loremflickr_visual(query, dest, seed)
    if got:
        return done(*got)

    return done(gradient_visual(query, dest, seed, word), "gradient")


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


def _grad_layer(fn, color=(4, 6, 12)) -> "Image.Image":
    """행마다 알파가 다른 그라데이션 레이어(이음매 없이 부드럽게)."""
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(H):
        a = int(fn(y))
        if a > 0:
            d.line([(0, y), (W, y)], fill=(*color, min(a, 255)))
    return layer


def make_ui_overlay(title: str, index: int, total: int, dest: Path) -> Path:
    """컷마다 고정으로 깔리는 UI(스크림·비네트·진행바·훅 타이틀)."""
    from PIL import Image

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # 전체를 아주 살짝 눌러 주는 베일 — 사진이 하얗게 날아와도 자막이 읽히게
    img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (6, 8, 14, 24)))

    # 상단 스크림 (타이틀 뒤)
    top_h = 660
    img = Image.alpha_composite(img, _grad_layer(
        lambda y: 210 * (1 - y / top_h) ** 1.5 if y < top_h else 0
    ))

    # 하단 스크림 (자막 뒤 · 맨 아래는 완전히 덮어 이미지 워터마크까지 가림)
    bot_start = 980

    def bot_alpha(y: int) -> float:
        if y < bot_start:
            return 0
        t = (y - bot_start) / (H - bot_start)
        a = (45 + 210 * (t ** 1.2)) * min(1.0, (y - bot_start) / 90)
        if y > H - 110:
            a += (255 - a) * ((y - (H - 110)) / 110)
        return min(a, 255)

    img = Image.alpha_composite(img, _grad_layer(bot_alpha, (3, 4, 9)))

    # 비네트 — 가운데는 살리고 가장자리만 부드럽게 어둡게
    from PIL import Image as _I

    vign = _I.radial_gradient("L").resize((W, H), _I.BICUBIC)
    alpha = vign.point(lambda v: int(118 * max(0.0, (v / 255 - 0.35) / 0.65) ** 1.4))
    layer = _I.new("RGBA", (W, H), (4, 6, 12, 0))
    layer.putalpha(alpha)
    img = _I.alpha_composite(img, layer)

    draw_progress(img, index, total)
    draw_hook(img, title)

    img.save(dest)
    return dest


def pick_sub_font(chunks: list[str]):
    """한 컷 안의 모든 자막 조각이 2줄 안에 들어가는 가장 큰 글씨."""
    from PIL import Image, ImageDraw

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for size in (88, 82, 76, 70, 64, 58, 52):
        font = load_font(size)
        if all(len(wrap_text(probe, c, font, SUB_MAX_W)) <= 2 for c in chunks):
            return font
    return load_font(52)


def make_subtitle_strip(text: str, font, dest: Path) -> Path:
    """하단 자막 한 조각 — 굵은 흰 글씨 + 검정 외곽선 + 반투명 다크 필 + 그림자."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("RGBA", (W, STRIP_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    lines = balance_lines(d, wrap_text(d, text, font, SUB_MAX_W), font)
    if not lines:
        return dest

    line_h = int(font.size * 1.24)
    block_h = line_h * len(lines)
    y_top = STRIP_H - 16 - block_h
    maxw = max(text_width(d, ln, font) for ln in lines)
    pad_x, pad_y = 44, 22
    box = [(W - maxw) / 2 - pad_x, y_top - pad_y, (W + maxw) / 2 + pad_x, y_top + block_h + pad_y]

    # 필 뒤 그림자
    shadow = Image.new("RGBA", (W, STRIP_H), (0, 0, 0, 0))
    rounded(ImageDraw.Draw(shadow), [box[0] + 2, box[1] + 10, box[2] + 2, box[3] + 10],
            radius=34, fill=(0, 0, 0, 160))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(16)))

    d = ImageDraw.Draw(img, "RGBA")
    rounded(d, box, radius=34, fill=(8, 9, 13, 168))

    stroke = max(4, font.size // 10)
    for i, line in enumerate(lines):
        cy = y_top + line_h * i + line_h / 2
        d.text((W / 2 + 2, cy + 6), line, font=font, fill=(0, 0, 0, 150), anchor="mm", align="center")
        d.text((W / 2, cy), line, font=font, fill=(255, 255, 255, 255), anchor="mm",
               align="center", stroke_width=stroke, stroke_fill=(0, 0, 0, 255))

    img.save(dest)
    return dest


# ----------------------------------------------------------------------------
# 자막 호흡 분할 · 타이밍
# ----------------------------------------------------------------------------
def split_chunks(text: str, max_len: int = 15) -> list[str]:
    """나레이션을 화면에 띄울 호흡 단위(10~15자)로 나눕니다.
    한국어는 조사가 단어에 붙으므로 '낱말 개수'를 기준으로 끊는 쪽이 말투가 자연스럽습니다."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    words = text.split(" ")
    n_chunks = 3 if len(text) > 30 else 2
    if len(words) < n_chunks:
        return [text]

    parts: list[str] = []
    prev = 0
    for k in range(1, n_chunks):
        # 낱말 개수로 균등하게 끊고, 조각이 너무 짧거나 길 때만 한 칸 보정
        best = round(len(words) * k / n_chunks)
        while best < len(words) - 1 and len(" ".join(words[prev:best])) < 4:
            best += 1
        while best > prev + 1 and len(" ".join(words[prev:best])) > 20:
            best -= 1
        parts.append(" ".join(words[prev:best]))
        prev = best
    parts.append(" ".join(words[prev:]))
    return [p for p in parts if p]


def chunk_timings(parts: list[str], voice_dur: float, dur: float) -> list[tuple[float, float]]:
    """말 속도에 맞춰 조각별 표시 구간을 배분합니다(글자 수 + 쉼표 가중)."""
    if not parts:
        return []
    weights = []
    for p in parts:
        w = max(len(re.sub(r"\s", "", p)), 1)
        if re.search(r"[,.?!~]", p):
            w += 1.5
        weights.append(w)
    total_w = sum(weights)
    out: list[tuple[float, float]] = []
    acc = 0.0
    for w in weights:
        start = voice_dur * acc / total_w
        acc += w
        end = voice_dur * acc / total_w
        out.append([start, end])  # type: ignore[arg-type]
    # 첫 조각은 즉시, 다음 조각은 살짝 먼저 떠서 말과 맞물리게
    out = [[max(0.0, s - 0.06), e] for s, e in out]
    s, e = out[-1]
    out[-1] = [s, min(e + 0.22, dur - 0.05)]
    for i in range(len(out) - 1):
        out[i][1] = min(out[i][1] + 0.02, out[i + 1][0])
    return [(round(a, 3), round(b, 3)) for a, b in out]


# ----------------------------------------------------------------------------
# 영상 합성
# ----------------------------------------------------------------------------
MOTION_MODES = [("in", 1.30), ("pan_r", 1.28), ("out", 1.34), ("pan_l", 1.26),
                ("in", 1.36), ("pan_up", 1.30)]


def ken_burns_filter(mode: str, frames: int, dur: float) -> str:
    """정지 이미지에 컷마다 다른 줌/팬 모션. 짧은 컷은 속도를 살짝 낮춥니다."""
    frames = max(frames, 2)
    d = frames - 1
    base = 1.24
    delta = 0.09 + 0.06 * min(dur, 4.0) / 4.0   # 2.5초 컷 ≈ 13%, 4초 컷 ≈ 15%
    z_in = f"1+{(base - 1 + delta):.4f}*on/{d}"
    z_out = f"{base + delta:.4f}-{delta:.4f}*on/{d}"
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"
    if mode == "in":
        z, x, y = z_in, cx, cy
    elif mode == "out":
        z, x, y = z_out, cx, cy
    elif mode == "pan_r":
        z, x, y = f"{base + 0.04:.3f}", f"(iw-iw/zoom)*on/{d}", cy
    elif mode == "pan_l":
        z, x, y = f"{base + 0.04:.3f}", f"(iw-iw/zoom)*(1-on/{d})", cy
    elif mode == "pan_up":
        z, x, y = f"{base + 0.04:.3f}", cx, f"(ih-ih/zoom)*(1-on/{d})"
    else:  # pan_down
        z, x, y = f"{base + 0.04:.3f}", cx, f"(ih-ih/zoom)*on/{d}"
    return (
        "scale=1350:2400:force_original_aspect_ratio=increase,crop=1350:2400,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},"
        "eq=saturation=1.08:contrast=1.04:brightness=0.012,setsar=1"
    )


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
    sub_font=None,
) -> dict:
    idx = index
    is_last = idx == total - 1
    audio_path = work / f"voice_{idx:02d}.mp3"
    log(f"[{idx + 1}/{total}] 음성 생성 중…")
    synthesize(narration, voice, rate, audio_path)
    voice_dur = audio_duration(audio_path)
    pad_after = 0.5 if is_last else (0.24 if idx == 0 else 0.3)
    dur = round(max(voice_dur + pad_after, 1.6), 3)

    # 화면 자막은 나레이션 문장을 호흡 단위로 쪼개 순차 표시
    spoken = narration if narration.strip() else subtitle
    chunks = split_chunks(spoken) or [spoken]
    times = chunk_timings(chunks, voice_dur, dur)
    font = sub_font or pick_sub_font(chunks)

    inputs = ["-loop", "1", "-framerate", str(FPS), "-i", visual] if not visual.endswith(".mp4") \
        else ["-stream_loop", "-1", "-i", visual]
    ui = make_ui_overlay(title, idx, total, work / f"ui_{idx:02d}.png")
    inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(ui)]
    strip_paths: list[Path] = []
    for k, chunk in enumerate(chunks):
        sp = make_subtitle_strip(chunk, font, work / f"sub_{idx:02d}_{k}.png")
        strip_paths.append(sp)
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(sp)]
    audio_idx = 2 + len(strip_paths)
    inputs += ["-i", str(audio_path)]

    if visual.endswith(".mp4"):
        bg = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},eq=saturation=1.05:contrast=1.03,setsar=1")
    else:
        frames = int(math.ceil(dur * FPS)) + 2
        mode, _z = MOTION_MODES[idx % len(MOTION_MODES)]
        bg = ken_burns_filter(mode, frames, dur)

    filt = [f"[0:v]{bg}[bg]"]
    prev, n = "bg", 1
    filt.append(f"[{prev}][1:v]overlay=0:0:format=auto[v{n}]")
    prev = f"v{n}"
    n += 1
    for k, (s, e) in enumerate(times):
        if e <= s + 0.05:
            continue
        filt.append(
            f"[{prev}][{2 + k}:v]overlay=0:{STRIP_Y}:"
            f"enable='between(t,{s:.3f},{e:.3f})':format=auto[v{n}]"
        )
        prev = f"v{n}"
        n += 1

    post = []
    if idx == 0:
        post.append("fade=t=in:st=0:d=0.15")
    if is_last:
        post.append(f"fade=t=out:st={max(dur - 0.22, 0.1):.3f}:d=0.22")
    post.append("format=yuv420p")
    filt.append(f"[{prev}]{','.join(post)}[v]")

    afilter = [f"apad=pad_dur={pad_after + 0.1:.2f}", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    if idx == 0:
        afilter.append("afade=t=in:st=0:d=0.05")
    if is_last:
        afilter.append(f"afade=t=out:st={max(dur - 0.3, 0.1):.3f}:d=0.3")

    out = work / f"cut_{idx:02d}.mp4"
    log(f"[{idx + 1}/{total}] 컷 렌더링 ({dur:.1f}s · {visual_kind} · 자막 {len(chunks)}조각)…")
    cmd = [
        FFMPEG, "-y", *inputs,
        "-filter_complex", ";".join(filt),
        "-map", "[v]", "-map", f"{audio_idx}:a",
        "-af", ",".join(afilter),
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
        "chunks": chunks,
        "duration": dur,
        "voice_seconds": round(voice_dur, 2),
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


def mix_audio(
    video: Path,
    out_path: Path,
    *,
    starts: list[float],
    total: float,
    use_bgm: bool = True,
    use_sfx: bool = True,
    bgm_gain: float = 0.10,
    work: Path,
) -> str:
    """나레이션(0dB) 아래로 BGM(-20dB)과 컷 전환 SFX를 깔아 최종 믹스를 만듭니다."""
    bgm_ok = use_bgm and BGM_PATH.exists()
    boundaries = [s for s in starts[1:] if 0.2 < s < total - 0.45]
    sfx_ok = use_sfx and SFX_WHOOSH.exists() and bool(boundaries)
    if not bgm_ok and not sfx_ok:
        return "none"

    inputs = ["-i", str(video)]
    filt: list[str] = []
    mix_labels = ["[0:a]"]
    idx = 1
    if bgm_ok:
        inputs += ["-stream_loop", "-1", "-i", str(BGM_PATH)]
        filt.append(
            f"[{idx}:a]atrim=0:{total:.3f},asetpts=N/SR/TB,"
            f"aformat=sample_rates=44100:channel_layouts=stereo,volume={bgm_gain},"
            f"afade=t=in:st=0:d=0.8,afade=t=out:st={max(total - 1.2, 0.5):.3f}:d=1.2[bgm]"
        )
        mix_labels.append("[bgm]")
        idx += 1
    if sfx_ok:
        inputs += ["-i", str(SFX_WHOOSH)]
        n = len(boundaries)
        filt.append(f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,asplit={n}"
                    + "".join(f"[s{k}]" for k in range(n)))
        for k, b in enumerate(boundaries):
            ms = int(max(b - 0.12, 0) * 1000)
            filt.append(f"[s{k}]adelay={ms}|{ms},volume=0.32[o{k}]")
            mix_labels.append(f"[o{k}]")
        idx += 1

    filt.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0,"
        "alimiter=limit=0.95:level=0[a]"
    )

    tmp = work / "mixed.mp4"
    log(f"오디오 믹싱 (BGM {'ON' if bgm_ok else 'OFF'} · SFX {len(boundaries) if sfx_ok else 0}개)…")
    try:
        run([
            FFMPEG, "-y", *inputs,
            "-filter_complex", ";".join(filt),
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(tmp),
        ], timeout=900)
    except Exception as e:  # noqa: BLE001
        log(f"믹싱 실패 — 나레이션만 유지합니다: {e}")
        return "failed"
    shutil.move(str(tmp), str(out_path))
    return "bgm+sfx" if (bgm_ok and sfx_ok) else ("bgm" if bgm_ok else "sfx")


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
    ap.add_argument("--no-bgm", action="store_true", help="BGM·SFX 없이 렌더")
    args = ap.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    cuts_in = payload.get("cuts") or []
    if not cuts_in:
        raise SystemExit("payload에 cuts가 비어 있습니다.")

    title = (payload.get("title") or "").strip()
    voice = payload.get("voice") or "ko-KR-InJoonNeural"
    rate = payload.get("rate") or "+12%"
    pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()
    use_bgm = (payload.get("bgm", True) is not False) and not args.no_bgm
    use_sfx = (payload.get("sfx", True) is not False) and not args.no_bgm

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(args.workdir) if args.workdir else out_path.parent / "_build"
    work.mkdir(parents=True, exist_ok=True)

    log(f"폰트: {FONT_PATH}")
    log(f"컷 {len(cuts_in)}개 · 보이스 {voice} · 속도 {rate} · "
        f"Pexels {'사용' if pexels_key else '없음'} · BGM {'ON' if use_bgm else 'OFF'}")

    # 자막 글씨 크기는 영상 전체에서 같아야 깔끔합니다
    all_chunks: list[str] = []
    for cut in cuts_in:
        spoken = (cut.get("narration") or cut.get("subtitle") or "").strip()
        all_chunks += split_chunks(spoken) or ([spoken] if spoken else [])
    sub_font = pick_sub_font(all_chunks) if all_chunks else None

    started = time.time()
    results: list[dict] = []
    for i, cut in enumerate(cuts_in):
        narration = (cut.get("narration") or cut.get("subtitle") or "").strip()
        subtitle = (cut.get("subtitle") or cut.get("narration") or "").strip()
        if not narration:
            continue
        word_src = re.sub(r"[^가-힣A-Za-z0-9 ]", " ", subtitle).split(" ")
        visual, kind = fetch_visual(
            cut.get("visual_query", ""), i, work, pexels_key,
            word=next((w for w in word_src if w), ""),
        )
        results.append(render_cut(
            index=i, total=len(cuts_in), visual=visual, visual_kind=kind,
            narration=narration, subtitle=subtitle, title=title,
            voice=voice, rate=rate, work=work, sub_font=sub_font,
        ))

    if not results:
        raise SystemExit("유효한 컷이 없습니다.")

    raw = work / "concat_raw.mp4"
    concat_cuts(results, raw, work)

    starts: list[float] = []
    acc = 0.0
    for c in results:
        starts.append(round(acc, 3))
        acc += c["duration"]

    audio_mode = mix_audio(
        raw, out_path, starts=starts, total=acc,
        use_bgm=use_bgm, use_sfx=use_sfx, work=work,
    )
    if audio_mode in ("none", "failed"):
        shutil.copyfile(raw, out_path)

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
        "audio": audio_mode,
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
