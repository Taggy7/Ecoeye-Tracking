# 탄소배출권 가격 추적 대시보드 (Ecoeye-Tracking)

사이트: https://taggy7.github.io/Ecoeye-Tracking/

## 무엇을 어떻게 모으나

| 지표 | 방식 | 원천 |
|---|---|---|
| KAU·KCU·KOC (새 연물·i-KOC·i-KCU 자동 추가) | **평일 하루 두 번 자동** (18:15 · 다음날 09:05 KST), 최근 15영업일 빈 날 자동 보충 | 공공데이터포털 일반상품시세정보 (KRX 배출권시장) |
| 거래량 | 자동 — 0 이면 「거래 없음」(기준가가 이어진 값) | 같은 API |
| 환율 USD·EUR·GBP·CNY | 자동 | ECB 기준환율 (Frankfurter, 키 불필요) |
| EU ETS | 수동 (무료 키 `OILPRICE_KEY` 를 Secrets 에 넣으면 자동) | oilpriceapi.com |
| CORSIA | **매일 자동** — ICE CORSIA 적격단위(2024-26) 선물 근월물 지연시세, 키 불필요 | 실패하면 기존 값 유지 · Actions 로그 `CORSIA 자동 실패` 줄에 원인. 수동 입력도 계속 가능 |
| VCM · 영국 · 중국 · 캘리포니아 · RGGI | **수동 입력** — 무료 공개 원천이 없음 | 입력할 때 출처를 함께 적음 |

## 수동 지표 넣는 법 (휴대폰 가능)

1. 저장소 **Actions** 탭 → **탄소배출권 가격 자동 수집** → **Run workflow**
2. 지표 선택, 값(원통화 숫자), 기준일(`2026-10-10` 또는 월 대표값 `2026-10`), 출처 입력 → **Run workflow**
3. 1~2분 뒤 사이트 새로고침. 출처는 표의 「출처」 칸에 그대로 보임

지표를 비워 두고 Run workflow 를 누르면 자동 수집만 즉시 1회 실행

## 파일

| 파일 | 누가 고치나 |
|---|---|
| `data/prices.json` | **봇 전용** — 직접 올리거나 고치지 말 것 (덮어쓰면 수집 기록이 사라짐) |
| `data/ecoeye.json` | 사람 — 에코아이 분기 공시 수치 (재고·수주잔고·CORSIA 실현가 등) |
| `fetch_prices.py` | 수집 코드 (v3) |
| `.github/workflows/fetch.yml` | 실행 시각·수동 입력칸 |
| `index.html` | 화면 |

## v3 (2026-10-11) 에서 바뀐 것

- 실행 1회에 하루치만 받던 것을 **빈 날 모두 보충**으로 변경 (7~10월 평일 10일 누락 해소, 첫 실행 때 6/30 이후 전체 보충)
- 값이 안 바뀌면 커밋하지 않음 — 주말·휴장일 빈 커밋·재배포 제거
- 종목명 규칙 구분 — KOC 와 i-KOC 를 섞지 않음, KCU26 화면 누락 해결, 응답에서 사라진 종목은 「수집 중단」 표시
- 거래량 저장, 환율 자동, 한국시간 기록
- 차트는 한 축만 사용 — 통화가 다른 지표는 「지수(기간 첫 값=100)」 보기로 비교
- 에코아이 연동 지표: 활성 KAU 자동 선택(KAU25 종료 대응), CORSIA 시장가 vs 에코아이 실현가, 잔여 CORSIA 가치·국내 재고 평가를 시세로 매일 재계산

투자판단 참고용, 매매 추천 아님


## 투자분석 요약 자동 생성 (v3)

수집·수동 입력 직후 회귀 테스트와 `build_report.py`를 실행하여
`data/report_digest.md` 및 `data/report_digest.json`을 가격 데이터와 함께 커밋한다.
보고서 생성기는 네트워크를 호출하거나 `data/prices.json`을 수정하지 않는다.

```bash
python3 -m unittest discover -s tests -v
python3 build_report.py --today 2026-10-13
```

- KOC는 KOC21-26 연물이며 i-KOC·전체 KOC 대표가격이 아니다.
- `KOC_TRD`·`IKOC_TRD`는 각 계열 연물 종가×양의 거래량 가중평균을 독립 검산한다.
  체결별 VWAP가 아니며 거래 연물 구성이 변하면 평균도 달라진다. 저장값과 0.5원 초과 차이는 경고한다.
- 거래량 0은 `reference_no_trade`, 누락은 `volume_unknown`이다.
  `published_price_changes`는 기준가격 변동, `changes`는 국내 지표의 거래 확인 관측 변화다.
  `last_trade` 및 `change_basis`의 양 끝 날짜·가격과 함께 읽는다. 장기간 무거래 시 현재 가격으로 해석하지 않는다.
- 월값은 관측기간을 보존하고 정확한 날짜(`last_date`)는 null로 둔다.
  수동 가격은 관측별 상품·출처 연속성을 알 수 없어 기간 수익률 계산에서 제외한다.
  `status.source`는 최신 출처일 뿐 과거 모든 관측의 출처가 아니다.
- 기간 기준점은 목표일 이전 7일 이내 일값만 허용한다. 기준점이 없으면 null이다.
- `derived.value`는 동일 관측일·동일 통화·거래 확인 조건을 만족할 때만 제공한다.
  비동기·무거래 쌍은 `latest_quote_ratio`에 참고값을, `last_common_trade`에 마지막 동일 거래일 비율을 별도 저장한다.
- 외화의 원화환산은 `fx_asof`의 최신 환율 스냅샷이며 역사적 관측일 환산이 아니다.
- JSON `schema_version: 3`은 기존 PR 스키마에서 변경됐다. 기존 소비자는 위 nullable 필드를 처리해야 한다.
- 검증 기록과 기존 보고서 대비 차이: [docs/report-v3-validation.md](docs/report-v3-validation.md).
- 회귀 테스트는 2026-10-13 원본 사본과 합성 사례를 사용하며 외부 API 키가 필요 없다.

보고서 생성일이 바뀌면 요약의 일자도 갱신된다. 가격 원본은 수집기가 실제 변경한 경우에만 바뀐다.
