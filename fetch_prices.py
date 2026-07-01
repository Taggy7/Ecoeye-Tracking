#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
탄소배출권 가격 자동 수집 스크립트 (v2 - 엔드포인트 자동탐색)
- 공공데이터포털 '일반상품시세정보' API의 배출권시세에서 KAU/KCU/KOC 수집
- 배출권 오퍼레이션명이 문서에 없어, 여러 후보를 자동 시도하여 맞는 것을 찾음
- data/prices.json 에 시계열 누적 저장

수동 실행: SERVICE_KEY=발급키 python3 fetch_prices.py
"""

import os
import sys
import json
import datetime
import urllib.request
import urllib.parse
import ssl

SERVICE_KEY = os.environ.get("SERVICE_KEY", "").strip()
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "prices.json")

# 서비스 URL (확인됨). 배출권 오퍼레이션명은 후보를 자동 시도
SERVICE_URL = "https://apis.data.go.kr/1160100/service/GetGeneralProductInfoService"

# 배출권 오퍼레이션명 후보 (명명규칙: getOilPriceInfo/getGoldPriceInfo 형태)
# 첫 성공 시 그 이름을 사용. 확정되면 이 리스트 맨 앞에 고정하면 됨
OP_CANDIDATES = [
    "getCertifiedEmissionReductionPriceInfo",  # ✅ 확정 (공공데이터포털 상세기능에서 확인)
]

# 추적 종목 매핑 (API itmsNm → 대시보드 시리즈 키)
# 종목 매핑: API가 주는 itmsNm에서 이 키워드를 부분일치로 찾음
# 실제 종목명이 "KAU25", "배출권-KAU25" 등 어떤 형태든 부분일치로 잡음
TARGET_ITEMS = {
    "KAU25": ["KAU25", "KAU 25", "KAU2 5"],   # 할당배출권 (현재 연도물 우선)
    "KAU26": ["KAU26", "KAU 26"],
    "KCU25": ["KCU25", "KCU 25"],              # 상쇄배출권
    "KCU26": ["KCU26"],
    "KOC":   ["KOC", "i-KOC", "iKOC"],         # 외부사업
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_data():
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


def try_call(op_name, base_date):
    """특정 오퍼레이션명으로 호출 시도. 성공하면 items 리스트, 실패하면 None"""
    params = {
        "serviceKey": SERVICE_KEY,
        "resultType": "json",
        "basDt": base_date,
        "numOfRows": "100",
        "pageNo": "1",
    }
    url = f"{SERVICE_URL}/{op_name}?" + urllib.parse.urlencode(params, safe="")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "carbon-tracker/2.0"})
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # 오퍼레이션명 틀림
        log(f"    HTTP {e.code} ({op_name})")
        return None
    except Exception as e:
        log(f"    호출 오류 ({op_name}): {e}")
        return None

    # JSON 파싱
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # XML 에러 응답일 수 있음 (키 문제 등)
        if "SERVICE_KEY" in raw or "SERVICE ERROR" in raw or "인증" in raw:
            log(f"    ⚠️ 인증 오류 응답 ({op_name}) — SERVICE_KEY 확인 필요")
        return None

    # 정상 구조 확인
    try:
        items = data["response"]["body"]["items"]["item"]
        if isinstance(items, dict):
            items = [items]
        return items
    except (KeyError, TypeError):
        # 응답은 왔으나 데이터 없음 (휴장일이거나 구조 다름)
        header = data.get("response", {}).get("header", {})
        code = header.get("resultCode", "")
        if code and code != "00":
            log(f"    응답코드 {code} ({op_name})")
        return None


def find_working_op(base_date):
    """맞는 오퍼레이션명을 자동 탐색. 저장된 것 우선 시도"""
    # 이전에 찾은 이름이 있으면 우선
    data = load_data()
    saved_op = data.get("_op_name")
    order = OP_CANDIDATES[:]
    if saved_op and saved_op in order:
        order.remove(saved_op)
        order.insert(0, saved_op)
    elif saved_op:
        order.insert(0, saved_op)

    for op in order:
        items = try_call(op, base_date)
        if items:
            log(f"  ✅ 작동하는 오퍼레이션 확인: {op}")
            return op, items
    return None, None


def parse_items(items):
    """API 응답에서 종목명:종가 추출"""
    result = {}
    for it in items:
        name = str(it.get("itmsNm", "")).strip()
        # 종가 필드는 clpr(종가) 우선, 없으면 다른 필드
        close = it.get("clpr") or it.get("basPrc") or it.get("trdPrc") or ""
        if name and close:
            try:
                result[name] = float(str(close).replace(",", ""))
            except ValueError:
                pass
    return result


def map_to_series(raw_items):
    mapped = {}
    for series_key, candidates in TARGET_ITEMS.items():
        for cand in candidates:
            if cand in raw_items:
                mapped[series_key] = raw_items[cand]
                break
            for name, price in raw_items.items():
                if cand in name:
                    mapped[series_key] = price
                    break
            if series_key in mapped:
                break
    return mapped


def recent_business_dates():
    today = datetime.date.today()
    out = []
    d = today
    for _ in range(10):
        d = d - datetime.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return out


def main():
    log("=== 탄소배출권 가격 수집 시작 (v2) ===")

    if not SERVICE_KEY:
        log("⚠️ SERVICE_KEY 미설정 — GitHub Secrets에 SERVICE_KEY를 등록하세요")
        log("   기존 데이터를 유지하고 종료합니다")
        data = load_data()
        data["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S KST") + " (키없음)"
        save_data(data)
        return

    data = load_data()
    got_any = False

    for base_date in recent_business_dates():
        op, items = find_working_op(base_date)
        if not op:
            continue  # 이 날짜는 데이터 없음, 다음 날짜 시도
        raw = parse_items(items)
        if not raw:
            log(f"  {base_date}: 응답은 왔으나 종목 없음")
            continue
        log(f"  {base_date}: {len(raw)}개 종목 — {', '.join(list(raw.keys())[:8])}")
        mapped = map_to_series(raw)
        if mapped:
            label = datetime.datetime.strptime(base_date, "%Y%m%d").strftime("%y.%m.%d")
            for key, price in mapped.items():
                data["series"].setdefault(key, {})
                data["series"][key][label] = price
            data["_op_name"] = op  # 다음 실행 위해 저장
            log(f"✅ {label} 기준 갱신: {mapped}")
            got_any = True
            break
        else:
            log(f"  {base_date}: KAU/KCU/KOC 종목 매칭 실패. 원시 종목명 확인: {list(raw.keys())}")

    if not got_any:
        log("⚠️ 배출권 데이터 미확보")
        log("   가능한 원인: (1) 모든 오퍼레이션 후보가 404 → 이름 재확인 필요")
        log("              (2) SERVICE_KEY 미승인 (data.go.kr 마이페이지에서 확인)")
        log("              (3) 연휴/휴장 구간")

    data["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S KST")
    save_data(data)
    log("=== 수집 완료 ===")

    for key, series in data.get("series", {}).items():
        if series and isinstance(series, dict):
            latest = sorted(series.keys())[-1]
            log(f"  {key}: {series[latest]:,.0f} ({latest}), 누적 {len(series)}개")


if __name__ == "__main__":
    main()
