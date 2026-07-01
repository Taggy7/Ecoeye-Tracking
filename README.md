# 탄소배출권 가격 추적 대시보드 — 설치 가이드

매일 자동으로 KAU·KCU·KOC 가격을 수집해 웹에서 보여주는 대시보드입니다. 한 번 설치하면 **사람이 아무것도 안 해도 매일 자동 갱신**되고, 인터넷 주소(URL)로 어디서든 접속할 수 있습니다.

---

## 전체 그림

```
매일 오전 9시
   ↓
GitHub Actions (무료 자동 실행)
   ↓
공공데이터포털 배출권 API 호출 → KAU/KCU/KOC 수집
   ↓
data/prices.json 자동 갱신·커밋
   ↓
GitHub Pages 대시보드가 자동 반영 → URL로 접속
```

자동 갱신: **KAU·KCU·KOC** (국내 배출권)
수동 보완: EU·영국·중국·캘리포니아·RGGI·VCM·CORSIA (무료 API 없음)

---

## STEP 1. 공공데이터포털 API 키 발급 (5분, 무료)

1. https://www.data.go.kr 회원가입·로그인
2. https://www.data.go.kr/data/15094805/openapi.do 접속
3. 우측 **[활용신청]** 버튼 클릭
4. 활용목적 간단히 입력 (예: "개인 투자 분석용 배출권 시세 조회") → 신청
5. **자동승인** (즉시). 마이페이지 → 오픈API → 인증키에서 **일반 인증키(Encoding)** 복사
   - 이 키가 `SERVICE_KEY`입니다. 일 10,000회까지 무료

> ⚠️ 신청 직후 키가 활성화되기까지 최대 1시간 걸릴 수 있습니다. 401 오류가 나면 잠시 후 재시도

---

## STEP 2. GitHub 저장소 만들기 (5분)

1. https://github.com 회원가입·로그인
2. 우측 상단 **+** → **New repository**
3. 저장소 이름: `carbon-tracker` (원하는 이름)
4. **Public** 선택 (GitHub Pages 무료는 Public만)
5. **Create repository**
6. 생성된 저장소에서 **Add file → Upload files**
7. 이 폴더의 파일을 **구조 그대로** 업로드:
   ```
   carbon-tracker/
   ├── index.html
   ├── fetch_prices.py
   ├── README.md
   ├── .github/
   │   └── workflows/
   │       └── fetch.yml
   └── data/
       └── prices.json
   ```
   > `.github/workflows/` 폴더 구조가 중요합니다. 드래그앤드롭으로 폴더째 올리거나, 파일 경로에 `/`를 넣어 업로드하면 폴더가 생성됩니다
8. **Commit changes**

---

## STEP 3. API 키를 GitHub에 안전하게 등록 (2분)

키를 코드에 직접 넣으면 노출되므로, GitHub의 비밀 저장소(Secrets)에 넣습니다.

1. 저장소 → **Settings** → 좌측 **Secrets and variables** → **Actions**
2. **New repository secret**
3. Name: `SERVICE_KEY`
4. Secret: STEP 1에서 복사한 인증키 붙여넣기
5. **Add secret**

---

## STEP 4. GitHub Pages 켜기 (2분)

1. 저장소 → **Settings** → 좌측 **Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** / 폴더: **/ (root)** → **Save**
4. 1~2분 후 상단에 접속 주소가 표시됩니다:
   ```
   https://(내아이디).github.io/carbon-tracker/
   ```
   이 주소가 대시보드 URL입니다. 북마크하세요

---

## STEP 5. 자동 수집 작동 확인 (3분)

1. 저장소 → **Actions** 탭
2. 좌측 **탄소배출권 가격 자동 수집** 클릭
3. 우측 **Run workflow** → **Run workflow** (첫 실행은 수동으로)
4. 초록 체크 ✅ 가 뜨면 성공. `data/prices.json`이 갱신됩니다
5. 이후 **매일 오전 9시 자동 실행**됩니다 (별도 조작 불필요)

> 휴장일(주말·공휴일)엔 새 데이터가 없어 "변경사항 없음"으로 표시됩니다. 정상입니다

---

## 글로벌 지표 수동 갱신 방법

EU·VCM·CORSIA 등은 무료 자동 소스가 없어 가끔 직접 갱신합니다.

1. 저장소에서 `data/prices.json` 파일 클릭 → 연필(Edit) 아이콘
2. 해당 지표의 `series`에 새 날짜·가격 추가. 예:
   ```json
   "EU_ETS": { ..., "26.06": 81.3, "26.09": 83.5 }
   ```
3. **Commit changes** → 대시보드 자동 반영

가격 확인처:
- EU ETS: https://tradingeconomics.com/commodity/carbon
- RGGI: https://www.rggi.org/auctions/auction-results/prices-volumes
- 영국 UK ETS: GOV.UK (월 1회 평균 공개)
- VCM·CORSIA: Carbon Pulse (유료) 또는 뉴스 검색

> 또는 저(클로드)에게 "탄소배출권 최신 가격 prices.json 형식으로 정리해줘"라고 하면 붙여넣을 데이터를 만들어 드립니다

---

## 매년 1월 할 일 (종목 연도 갱신)

배출권 종목명은 연도가 붙습니다(KAU25 → KAU26). 매년 초 `fetch_prices.py`의 `TARGET_ITEMS`에서 연도를 업데이트하세요:
```python
TARGET_ITEMS = {
    "KAU25": ["KAU26", "KAU27"],   # 연도 +1
    ...
}
```

---

## 문제 해결

| 증상 | 원인·해결 |
|------|----------|
| Actions에서 빨간 X | Secrets의 `SERVICE_KEY` 오타 확인. data.go.kr 키 활성화 대기(최대 1시간) |
| "데이터 없음"만 표시 | 휴장일이거나 키 미활성. 평일에 재시도 |
| 대시보드가 안 열림 | Pages 설정의 Branch=main, 폴더=root 확인. 배포 1~2분 대기 |
| 401 오류 | data.go.kr 마이페이지에서 활용신청 "승인" 상태 확인 |
| 가격이 안 바뀜 | 공공데이터는 전일 종가를 익일 오후 제공. 하루 시차 정상 |

---

## 참고

- API: 금융위원회 일반상품시세정보 (무료, 일 10,000회)
- 자동 실행: GitHub Actions (Public 저장소 무료)
- 호스팅: GitHub Pages (무료)
- 데이터 갱신: 평일 1회 (전일 종가, 익일 제공)
