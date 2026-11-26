# 🎬 0원 쇼츠 스튜디오

**주제 한 줄 → AI 대본 → 1080×1920 세로 영상(MP4)** 까지, 서버비 0원 · 크레딧 0원으로 만드는 개인용 숏폼 자동 제작기입니다.

- 화면(스튜디오)은 **GitHub Pages**에서 무료로 뜹니다.
- 영상 렌더링은 **GitHub Actions 무료 러너**에서 `ffmpeg`로 돌아갑니다.
- 목소리는 **edge-tts**(마이크로소프트 신경망 음성), 무료입니다.
- 배경은 **Pexels**(무료 키, 선택) → **Pollinations.ai**(무료 AI 이미지 flux→turbo) → **Openverse 스톡 사진** → **Picsum** → **Wikimedia Commons** → **그라데이션 카드** 순서로 자동 대체되어 **절대 실패하지 않습니다**.
- 배경음악과 컷 전환 효과음이 기본으로 깔립니다(직접 만든 로열티 프리 소리, `assets/audio/`).

---

## 1. 처음 한 번만: 3분 설정

> 아래 3개만 하면 끝입니다. 스튜디오의 **설정 가이드** 버튼에서 상태 확인도 가능합니다.

### ① GitHub Pages 켜기

저장소 `Settings → Pages` → **Source: Deploy from a branch** → Branch **`main`** / 폴더 **`/docs`** → **Save**

1~2분 뒤 아래 주소가 열립니다:

```
https://twinspapa84.github.io/shorts-maker/
```

### ② 렌더 워크플로 파일 만들기 (1분, 복사·붙여넣기)

GitHub 앱 권한으로는 `.github/workflows/`에 파일을 올릴 수 없어서, 이 파일만 웹에서 한 번 만들어 줍니다.

1. 아래 링크를 엽니다 (새 파일 화면이 바로 열립니다)
   `https://github.com/twinspapa84/shorts-maker/new/main?filename=.github/workflows/render-shorts.yml`
2. 이 저장소의 **`workflow_templates/render-shorts.yml`** 내용을 그대로 복사해 붙여넣습니다.
   (스튜디오 화면에서 **YAML 복사** 버튼을 누르면 클립보드에 들어갑니다)
3. **Commit changes** 클릭.

> Actions 탭에 `render-shorts` 워크플로가 보이면 성공입니다.

### ③ GitHub 토큰 만들기 (Fine-grained)

`https://github.com/settings/personal-access-tokens/new`

| 항목 | 값 |
|---|---|
| Token name | `shorts-maker` |
| Expiration | 90 days |
| Repository access | **Only select repositories** → `shorts-maker` |
| Permissions → Repository → **Actions** | **Read and write** |
| Permissions → Repository → **Contents** | **Read** |
| Permissions → Repository → **Metadata** | Read (자동 포함) |

만든 토큰(`github_pat_...`)을 스튜디오 **⚙ 설정 → GitHub 토큰**에 넣고 저장하면 끝입니다.

---

## 2. 사용법 (3단계)

> 스튜디오 상단 **▶ 예시 영상** 버튼을 누르면 이 도구로 만든 24초 결과물을 바로 볼 수 있습니다.

1. **주제 한 줄** 입력 → 톤(유머 썰 / 정보 꿀팁 / 제품 리뷰 / **🛒 쇼핑·리뷰 후킹**) · 목소리(남/여) 선택 → **AI 대본 생성**
   - Gemini 키가 없으면 **직접 쓰기**로 진행할 수 있습니다.
2. **대본 다듬기** — 컷마다 나레이션 / 화면 자막 / 비주얼 프롬프트를 수정합니다. 오른쪽 9:16 프리뷰로 바로 확인.
3. **렌더링 시작** — GitHub Actions가 영상을 만들고, 끝나면 **Release에 MP4**가 올라옵니다.
   - 스튜디오에 영상 플레이어 · 다운로드 버튼 · **인스타/유튜브/틱톡 캡션**(1클릭 복사)이 자동으로 나옵니다.

### 선택: Pexels 무료 키 (화질 업)

[pexels.com/api](https://www.pexels.com/api/)에서 무료 키를 받아
저장소 `Settings → Secrets and variables → Actions → New repository secret` 에
이름 **`PEXELS_API_KEY`**, 값에 키를 넣으면 실제 영상/사진이 배경으로 쓰입니다.

---

## 3. 영상 사양

| 항목 | 값 |
|---|---|
| 해상도 | 1080×1920 (9:16) |
| 프레임 | 30fps, H.264 + AAC |
| 길이 | 컷 5~6개, 약 25~32초 (한 컷 3~5초) |
| 대본 | 컷당 나레이션 15~32자 짧은 입말 (첫 컷 후킹 · 마지막 컷 여운/댓글 유도) |
| 쇼핑 모드 | `🛒 쇼핑·리뷰 후킹` 톤이면 5컷 20~25초: 불편 공감 → 장점 → 실제 변화 → 단점 → 고정댓글 유도 |
| 상품 미디어 | 컷마다 `image_url` 에 사진(.jpg/.png/.webp)·**GIF**·**영상(.mp4/.mov/.webm)** 을 넣으면 그 미디어로 렌더. GIF·영상은 컷 길이에 맞춰 무음 반복 재생 (블러 배경 + 가운데 카드 / 화면 꽉 채움) |
| 자막 | 나레이션을 10~15자 호흡 단위로 쪼개 말 속도에 맞춰 순차 표시. **굵은 흰 글씨 + 노란 키워드 강조**(`**단어**`, 숫자는 자동) + 두꺼운 검정 외곽선 + 반투명 다크 필. 글씨 크기는 이전보다 약 15~20% 큼 |
| 훅 제목 | 상단 노란 형광펜 배너 (컷 내내 고정) |
| 모션 | 컷마다 다른 Ken Burns 줌/팬 (정지 화면 없음) |
| 전환 | 암전 없는 하드컷 (첫 컷 0.15초 인트로 ·마지막 컷 0.2초 아웃트로만) |
| 오디오 | 나레이션 0dB + BGM -20dB 루프 + 컷 전환 "스윽" (전체 -14 LUFS) |
| 안전 영역 | 릴스·쇼츠 UI를 피한 상·하단 마진 (자막 하단 1450px) |

---

## 4. 로컬에서 돌려보기

```bash
pip install -r requirements.txt
# 시스템에 ffmpeg 가 없다면: pip install imageio-ffmpeg (자동 사용)
python scripts/render_shorts.py --input samples/payload_sample.json --output output/shorts_final.mp4
```

payload 형식:

```json
{
  "title": "월 30만원 부업, 3주 만에 바뀐 것",
  "voice": "ko-KR-InJoonNeural",
  "rate": "+12%",
  "bgm": true,
  "cuts": [
    {"narration": "15~32자 입말 한 문장", "subtitle": "10~18자 입말", "visual_query": "english visual prompt 8~14 words"}
  ]
}
```

- `"bgm": false` 를 넣으면 배경음악·효과음 없이 나레이션만 렌더합니다(`--no-bgm` 옵션과 같음).
- 컷에 `"image_url": "https://…"` 을 넣으면 AI 이미지 대신 **그 상품 미디어**가 배경이 됩니다.
  사진(.jpg/.png/.webp) 은 물론 **움직이는 GIF · 짧은 영상(.mp4/.mov/.webm)** 도 됩니다(원본 소리는 지우고 컷 길이만큼 반복).
  `"image_fit": "blur"`(기본 — 어두운 블러 배경 위 가운데 둥근 카드에서 재생) 또는 `"fill"`(9:16 화면 꽉 채움).
- 나레이션에 `**단어**` 처럼 별표 두 개를 쓰면 그 단어만 **노란색(#FFD400)** 으로 강조되고, 목소리(TTS)는 별표를 뺀 문장으로 읽습니다. 마크업이 없어도 `18,700원`·`45종` 같은 숫자+단위는 자동으로 노란색이 됩니다.
  쇼핑 예시는 `samples/payload_shop.json` 에 있습니다.

`output/`에 `shorts_final.mp4`, `thumbnail.jpg`, `manifest.json`이 생성됩니다.

---

## 5. 폴더 구조

```
docs/index.html                     스튜디오 웹앱 (GitHub Pages)
docs/demo/sample.mp4                예시 결과 영상 (스튜디오 “예시 영상” 버튼)
scripts/render_shorts.py            렌더러 (edge-tts + ffmpeg + Pillow)
scripts/make_audio_assets.py        BGM·효과음 생성기 (assets/audio 를 다시 만들 때)
assets/audio/bgm_lofi.wav           30초 로파이 루프 BGM (직접 생성)
assets/audio/sfx_whoosh.wav         컷 전환 효과음
workflow_templates/render-shorts.yml GitHub Actions 워크플로 (웹에서 복사)
assets/fonts/NanumGothic-*.ttf      한국어 폰트 (OFL)
samples/payload_sample.json         로컬 테스트용 예시(6컷 썰)
samples/payload_shop.json           쇼핑/리뷰 예시(5컷 · 상품 사진 URL 포함)
```

---

## 6. 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| `렌더 워크플로 파일이 없습니다 (404)` | 위 **②** 단계를 하지 않은 상태입니다. 파일을 만들고 다시 시도하세요. |
| `토큰 권한 부족 (403)` | 토큰에 **Actions: Read and write** 권한이 없습니다. 토큰을 다시 만드세요. |
| 렌더가 실패로 끝남 | Actions 로그 확인. 대부분 네트워크 일시 오류 → 다시 렌더. |
| AI 대본 생성 실패(503) | Gemini 무료 한도/혼잡. 자동으로 `2.5 → 2.0 → 1.5` 모델로 재시도하고, 그래도 안 되면 직접 쓰기. |
| 배경이 어둡기만 함 | Pexels 키가 없고 이미지 서비스가 모두 혼잡한 경우입니다. Pexels 무료 키를 넣으면 해결됩니다. |
| 자막이 □로 보임 | `assets/fonts/NanumGothic-Bold.ttf`가 있는지 확인. 없으면 워크플로가 `fonts-noto-cjk`를 설치합니다. |

---

## 라이선스

- 나눔고딕: SIL Open Font License 1.1 (`assets/fonts/OFL.txt`)
- Pollinations.ai · Pexels 이미지: 각 서비스 약관을 따릅니다.
