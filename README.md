# 🎬 0원 쇼츠 스튜디오

**주제 한 줄 → AI 대본 → 1080×1920 세로 영상(MP4)** 까지, 서버비 0원 · 크레딧 0원으로 만드는 개인용 숏폼 자동 제작기입니다.

- 화면(스튜디오)은 **GitHub Pages**에서 무료로 뜹니다.
- 영상 렌더링은 **GitHub Actions 무료 러너**에서 `ffmpeg`로 돌아갑니다.
- 목소리는 **edge-tts**(마이크로소프트 신경망 음성), 무료입니다.
- 배경은 **Pexels**(무료 키, 선택) → **Pollinations.ai**(무료 AI 이미지) → **그라데이션 카드** 순서로 자동 대체되어 **절대 실패하지 않습니다**.

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

1. **주제 한 줄** 입력 → 톤(유머 썰 / 정보 꿀팁 / 제품 리뷰) · 목소리(남/여) 선택 → **AI 대본 생성**
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
| 길이 | 컷 5~6개, 약 30~45초 |
| 자막 | 나눔고딕 Bold, 검정 외곽선 + 그림자 |
| 훅 제목 | 상단 노란 형광펜 배너 |
| 모션 | 컷마다 Ken Burns 줌/팬 (정지 화면 없음) |
| 전환 | 컷 경계 페이드 인/아웃 + 오디오 페이드 |
| 안전 영역 | 릴스·쇼츠 UI를 피한 상·하단 마진 |

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
  "title": "월급 300만원 부업 썰",
  "voice": "ko-KR-InJoonNeural",
  "rate": "+12%",
  "cuts": [
    {"narration": "말할 문장", "subtitle": "화면 자막", "visual_query": "english visual prompt"}
  ]
}
```

`output/`에 `shorts_final.mp4`, `thumbnail.jpg`, `manifest.json`이 생성됩니다.

---

## 5. 폴더 구조

```
docs/index.html                     스튜디오 웹앱 (GitHub Pages)
scripts/render_shorts.py            렌더러 (edge-tts + ffmpeg + Pillow)
workflow_templates/render-shorts.yml GitHub Actions 워크플로 (웹에서 복사)
assets/fonts/NanumGothic-*.ttf      한국어 폰트 (OFL)
samples/payload_sample.json         로컬 테스트용 예시
```

---

## 6. 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| `렌더 워크플로 파일이 없습니다 (404)` | 위 **②** 단계를 하지 않은 상태입니다. 파일을 만들고 다시 시도하세요. |
| `토큰 권한 부족 (403)` | 토큰에 **Actions: Read and write** 권한이 없습니다. 토큰을 다시 만드세요. |
| 렌더가 실패로 끝남 | Actions 로그 확인. 대부분 네트워크 일시 오류 → 다시 렌더. |
| AI 대본 생성 실패(503) | Gemini 무료 한도/혼잡. 자동으로 `2.5 → 2.0 → 1.5` 모델로 재시도하고, 그래도 안 되면 직접 쓰기. |
| 배경이 어둡기만 함 | Pexels 키가 없고 Pollinations가 혼잡한 경우입니다. Pexels 무료 키를 넣으면 해결됩니다. |
| 자막이 □로 보임 | `assets/fonts/NanumGothic-Bold.ttf`가 있는지 확인. 없으면 워크플로가 `fonts-noto-cjk`를 설치합니다. |

---

## 라이선스

- 나눔고딕: SIL Open Font License 1.1 (`assets/fonts/OFL.txt`)
- Pollinations.ai · Pexels 이미지: 각 서비스 약관을 따릅니다.
