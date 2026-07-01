#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
탄소배출권 가격 자동 수집 스크립트
- 공공데이터포털 '일반상품시세정보' API에서 KAU/KCU/KOC 수집
- 무료 보조 소스에서 EU ETS 등 수집 (best-effort)
- data/prices.json 에 시계열 누적 저장

매일 GitHub Actions가 자동 실행. 수동 실행도 가능:
  SERVICE_KEY=발급키 python3 fetch_prices.py
"""

import os
import sys
import json
import datetime
import urllib.request
import urllib.parse
import ssl

# ── 설정 ──
SERVICE_KEY = os.environ.get("SERVICE_KEY", "").strip()
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "prices.json")

# 공공데이터포털 일반상품시세정보 — 배출권시세
# 엔드포인트는 활용신청 후 '엔드포인트' 정보에서 확인 (아래는 표준 형태)
API_BASE = "https://apis.data.go.kr/1160100/service/GetGeneralProductInfoService/getEmtPriceInfo"

# 추적할 종목 (API 응답의 itmsNm 기준)
# KAU25=할당, KCU25=상쇄, KOC=외부사업. 연도 표기는 매년 갱신 필요
TARGET_ITEMS = {
    "KAU25": ["KAU25", "KAU26"],          # 할당배출권 (현재 거래 연도)
    "KCU25": ["KCU25", "KCU26"],          # 상쇄배출권
    "KOC":   ["KOC", "i-KOC"],            # 외부사업 (부분일치)
}

# SSL 우회 (일부 정부 서버 인증서 이슈 대응)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_data():
    """기존 시계열 로드. 없으면 빈 구조 생성"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"기존 데이터 로드 실패, 새로 시작: {e}")
    return {"updated": None, "series": {}}


def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"저장 완료: {DATA_FILE}")


def fetch_krx_emissions(base_date):
    """
    공공데이터포털 배출권시세 API 호출
    base_date: 'YYYYMMDD' (조회 기준일)
    반환: {itmsNm: clpr(종가)} 딕셔너리
    """
    if not SERVICE_KEY:
        log("⚠️ SERVICE_KEY 미설정 — 배출권 API 건너뜀")
        return {}

    params = {
        "serviceKey": SERVICE_KEY,
        "resultType": "json",
        "basDt": base_date,
        "numOfRows": "100",
        "pageNo": "1",
    }
    url = API_BASE + "?" + urllib.parse.urlencode(params, safe="")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "carbon-tracker/1.0"})
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except Exception as e:
        log(f"❌ API 호출 실패 ({base_date}): {e}")
        return {}

    # 응답 구조: response.body.items.item[]
    try:
        items = data["response"]["body"]["items"]["item"]
        if isinstance(items, dict):
            items = [items]
    except (KeyError, TypeError):
        log(f"  {base_date}: 데이터 없음 (휴장일 가능)")
        return {}

    result = {}
    for it in items:
        name = it.get("itmsNm", "").strip()
        close = it.get("clpr", "")  # 종가
        if name and close:
            try:
                result[name] = float(str(close).replace(",", ""))
            except ValueError:
                pass
    log(f"  {base_date}: {len(result)}개 종목 수신 — {', '.join(list(result.keys())[:5])}")
    return result


def map_to_series(raw_items):
    """API 종목명을 대시보드 시리즈 키로 매핑"""
    mapped = {}
    for series_key, candidates in TARGET_ITEMS.items():
        for cand in candidates:
            # 정확 일치 우선
            if cand in raw_items:
                mapped[series_key] = raw_items[cand]
                break
            # 부분 일치 (KOC, i-KOC 등)
            for name, price in raw_items.items():
                if cand in name:
                    mapped[series_key] = price
                    break
            if series_key in mapped:
                break
    return mapped


def get_recent_business_date():
    """가장 최근 영업일 추정 (API는 전일+1영업일 제공)"""
    today = datetime.date.today()
    # 2영업일 전부터 시도 (데이터 제공 지연 고려)
    candidates = []
    d = today
    for _ in range(7):
        d = d - datetime.timedelta(days=1)
        if d.weekday() < 5:  # 월~금
            candidates.append(d.strftime("%Y%m%d"))
    return candidates


def main():
    log("=== 탄소배출권 가격 수집 시작 ===")
    data = load_data()

    # 최근 영업일들을 시도하여 가장 최신 데이터 확보
    today_label = datetime.date.today().strftime("%y.%m.%d")
    got_any = False

    for base_date in get_recent_business_date():
        raw = fetch_krx_emissions(base_date)
        if raw:
            mapped = map_to_series(raw)
            if mapped:
                # 시계열에 추가 (날짜 라벨 = 데이터 기준일)
                label = datetime.datetime.strptime(base_date, "%Y%m%d").strftime("%y.%m.%d")
                for key, price in mapped.items():
                    data["series"].setdefault(key, {})
                    data["series"][key][label] = price
                log(f"✅ {label} 기준 {len(mapped)}개 지표 갱신: {mapped}")
                got_any = True
                break  # 최신 1건만

    if not got_any:
        log("⚠️ 배출권 데이터 미확보 (휴장 또는 키 문제). 기존 데이터 유지")

    # ── 보조 소스: 무료 EU ETS (best-effort, 실패해도 무방) ──
    # 주: 안정적 무료 API가 없어 생략. 수동 입력 또는 추후 확장
    # 필요 시 여기에 추가 소스 구현

    data["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S KST")
    save_data(data)
    log("=== 수집 완료 ===")

    # 요약 출력
    for key, series in data["series"].items():
        if series:
            latest_date = sorted(series.keys())[-1]
            log(f"  {key}: {series[latest_date]:,.0f}원 ({latest_date}), 누적 {len(series)}개 시점")


if __name__ == "__main__":
    main()
