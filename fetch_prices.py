#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
탄소배출권 가격 수집 v3 (2026-10-11)

v2 → v3 에서 바뀐 것
 1. 빠진 날 자동 보충 — 최근 15영업일 중 비어 있는 날을 모두 조회한다
    (v2는 실행 1회에 하루치만 받아 7~10월 평일 10일이 비어 있었다)
 2. 바뀐 값이 없으면 파일을 쓰지 않는다 — 주말·휴장일의 빈 커밋과 사이트 재배포를 없앤다
 3. 종목 이름을 규칙으로 구분한다 — KOC 와 i-KOC 를 섞지 않고, KAU27 같은 새 연물은 자동 추가한다
 4. 거래량을 함께 저장한다 — 거래 없이 기준가만 이어진 날을 화면에서 구분한다
 5. 환율을 자동 갱신한다 — ECB 기준환율(Frankfurter, 키 불필요)
 6. 수동 지표 입력 — GitHub Actions 「Run workflow」 입력칸으로 CORSIA·VCM 등을 휴대폰에서도 갱신한다
 7. 시각을 한국시간으로 기록한다 (v2는 UTC 시각에 KST 를 붙였다)

실행
  자동 수집 : SERVICE_KEY=발급키 python3 fetch_prices.py
  수동 지표 : MANUAL_SERIES=CORSIA MANUAL_VALUE=12.4 MANUAL_DATE=2026-10-10 \
              MANUAL_SOURCE="출처" python3 fetch_prices.py --manual
"""

import os
import re
import sys
import json
import ssl
import datetime
import urllib.request
import urllib.parse
import urllib.error
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "data", "prices.json")

SERVICE_KEY = os.environ.get("SERVICE_KEY", "").strip()
SERVICE_URL = "https://apis.data.go.kr/1160100/service/GetGeneralProductInfoService"
OP_NAME = "getCertifiedEmissionReductionPriceInfo"   # 공공데이터포털 상세기능에서 확인된 이름
BACKFILL_WEEKDAYS = 15                               # 최근 15영업일(약 3주)을 매번 점검
STOP_AFTER_WEEKDAYS = 10                             # 이만큼 안 들어오면 「수집 중단」으로 표시
UA = {"User-Agent": "carbon-tracker/3.0"}

# 수동 입력을 받는 지표 (자동 수집 원천이 없는 것)
MANUAL_KEYS = ["CORSIA", "EU_ETS", "UK_ETS", "CN_CEA", "CA_ETS", "RGGI", "VCM"]

# 새로 생기는 국내 종목의 기본 색
NEW_COLORS = ["#3b82f6", "#22d3ee", "#a5b4fc", "#38bdf8", "#818cf8", "#06b6d4"]

_UNVERIFIED = ssl.create_default_context()
_UNVERIFIED.check_hostname = False
_UNVERIFIED.verify_mode = ssl.CERT_NONE
_ssl_warned = False


# ───────────────────────── 공통 ─────────────────────────
def now_kst():
    return datetime.datetime.now(KST)


def log(msg):
    print(f"[{now_kst().strftime('%H:%M:%S')}] {msg}", flush=True)


def http_get(url, headers=None, timeout=30):
    """인증서 검증을 먼저 시도하고, data.go.kr 처럼 인증서 체인이 불완전한 곳만 검증 없이 재시도"""
    global _ssl_warned
    req = urllib.request.Request(url, headers=headers or UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8")
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLError):
            if not _ssl_warned:
                log("  ℹ️ 인증서 검증 실패 → 검증 없이 재시도 (data.go.kr 인증서 체인 문제)")
                _ssl_warned = True
            with urllib.request.urlopen(req, timeout=timeout, context=_UNVERIFIED) as r:
                return r.read().decode("utf-8")
        raise


def label_of(d):
    """date → 'yy.mm.dd'"""
    return d.strftime("%y.%m.%d")


def parse_label(s):
    p = [int(x) for x in s.split(".")]
    y = p[0] + 2000 if p[0] < 100 else p[0]
    return datetime.date(y, p[1] if len(p) > 1 else 1, p[2] if len(p) > 2 else 1)


def recent_weekdays(n, today):
    out, d = [], today
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= datetime.timedelta(days=1)
    return sorted(out)


def weekdays_between(a, b):
    """a < b 일 때 (a, b] 사이 평일 수"""
    n, d = 0, a
    while d < b:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


# ───────────────────────── 파일 ─────────────────────────
def load_data():
    if not os.path.exists(DATA_FILE):
        log("⛔ data/prices.json 이 없습니다. 기존 완전판을 먼저 올려 주세요.")
        return None
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        log(f"⛔ data/prices.json 을 읽지 못했습니다({e}). 덮어쓰기를 막기 위해 중단합니다.")
        return None
    d.setdefault("series", {})
    d.setdefault("meta", {})
    return d


def snapshot(d):
    return json.dumps({k: v for k, v in d.items() if k != "updated"}, ensure_ascii=False, sort_keys=True)


def save_if_changed(data, before):
    if snapshot(data) == before:
        log("변경 없음 — 파일을 쓰지 않습니다 (빈 커밋 없음)")
        return False
    data["updated"] = now_kst().strftime("%Y-%m-%d %H:%M KST")
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)
    log(f"저장: series {len(data['series'])}종 · {data['updated']}")
    return True


# ───────────────────────── 구조 정리 (매 실행, 여러 번 돌려도 결과 동일) ─────────────────────────
def migrate(data):
    meta, series = data["meta"], data["series"]
    status = data.setdefault("status", {})
    data.setdefault("volume", {})

    # v2에서 수집은 되는데 화면에 안 보이던 KCU26
    if "KCU26" in series and "KCU26" not in meta:
        meta["KCU26"] = {"label": "KCU26 (26년물 상쇄)", "cat": "kr", "cur": "KRW",
                         "color": "#3b5bab", "eco": True, "auto": True}

    # 로컬 사본에만 있고 사이트에 올라가지 않았던 수동 입력 2건 (한 번만 반영)
    seeds = data.setdefault("_seeded", [])
    one_off = [
        ("EU_ETS", "26.08", 83.83, "2026-08-24 수동 입력(로컬 사본) — 출처 기록 없음"),
        ("CORSIA", "26.08", 12.4, "ICE Futures Europe CORSIA EEU(2024-2026) 선물 — 블로그(빤딱이 26.8.12) 인용, 원문 미확인"),
    ]
    for key, lab, val, src in one_off:
        tag = f"{key}@{lab}"
        if tag in seeds:
            continue
        series.setdefault(key, {})
        if lab not in series[key]:
            series[key][lab] = val
            status[key] = {"mode": "manual", "last": lab, "source": src}
        seeds.append(tag)

    # 상태표 초기화 — 수동 지표는 기존 meta.asof·note 를 출처로 옮긴다
    for key, m in meta.items():
        s = series.get(key) or {}
        last = max(s, key=parse_label) if s else None
        st = status.setdefault(key, {})
        if m.get("auto"):
            st.setdefault("mode", "auto")
        else:
            st["mode"] = "manual"
            st.setdefault("source", m.get("asof") or "기존 대표값(출처는 note 참조)")
        if last:
            st["last"] = max(last, st.get("last") or last, key=parse_label)

    data["note"] = ("KAU·KCU·KOC 는 공공데이터포털 일반상품시세(KRX 배출권시장)에서 평일 하루 두 번 자동 수집하고 빠진 날을 보충한다. "
                    "환율은 ECB 기준환율(Frankfurter) 자동. CORSIA·VCM·EU·영국·중국·캘리포니아·RGGI 는 무료 공개 원천이 없어 "
                    "GitHub Actions 「Run workflow」로 값·날짜·출처를 입력한다(status.<지표>.source 에 출처 보관). "
                    "거래량 0 인 날은 거래 없이 기준가가 이어진 값이다.")
    return data


# ───────────────────────── KRX 배출권 (공공데이터포털) ─────────────────────────
def krx_call(day):
    """그 날짜의 종목 목록. 데이터 없음 → [], 호출 실패 → None"""
    params = {"serviceKey": SERVICE_KEY, "resultType": "json", "basDt": day.strftime("%Y%m%d"),
              "numOfRows": "100", "pageNo": "1"}
    url = f"{SERVICE_URL}/{OP_NAME}?" + urllib.parse.urlencode(params, safe="")
    try:
        raw = http_get(url)
    except Exception as e:
        log(f"  {day} 호출 실패: {str(e)[:80]}")
        return None
    try:
        j = json.loads(raw)
    except json.JSONDecodeError:
        if "SERVICE_KEY" in raw or "SERVICE ERROR" in raw or "인증" in raw:
            log("  ⚠️ 인증 오류 — GitHub Secrets 의 SERVICE_KEY 확인 필요")
        else:
            log(f"  {day} 응답 형식 오류: {raw[:80]}")
        return None
    body = (j.get("response") or {}).get("body") or {}
    items = (body.get("items") or {}).get("item") if isinstance(body.get("items"), dict) else None
    if not items:
        code = ((j.get("response") or {}).get("header") or {}).get("resultCode", "")
        if code and code != "00":
            log(f"  {day} 응답코드 {code}")
        return []
    return [items] if isinstance(items, dict) else items


def classify(name):
    """KRX 종목명 → 시리즈 키. 국제(i-) 종목과 국내 종목을 섞지 않는다."""
    n = re.sub(r"\s+", "", str(name)).upper()
    intl = re.search(r"(^|[^A-Z])I-?(KCU|KOC)", n) is not None
    m = re.search(r"(KAU|KCU|KOC)[-_]?(\d{2})(?!\d)", n)
    if intl:
        kind = "IKCU" if "KCU" in n else "IKOC"
        return kind + (m.group(2) if m else "")
    if m:
        return m.group(1) + m.group(2)
    if "KOC" in n:
        return "KOC"
    if "KAU" in n or "KCU" in n:
        return None   # 연물 표시 없는 KAU/KCU 는 어느 해인지 몰라 받지 않는다
    return None


def to_num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def ensure_meta(data, key):
    meta = data["meta"]
    if key in meta:
        return
    m = re.fullmatch(r"(KAU|KCU|KOC|IKOC|IKCU)(\d{2})?", key)
    kind, yy = (m.group(1), m.group(2)) if m else (key, None)
    names = {"KAU": "할당", "KCU": "상쇄", "KOC": "외부사업", "IKOC": "국제 외부사업", "IKCU": "국제 상쇄"}
    shown = {"IKOC": "i-KOC", "IKCU": "i-KCU"}.get(kind, kind)
    label = f"{shown}{yy or ''} ({'20' + yy + '년물 ' if yy else ''}{names.get(kind, '')})"
    color = NEW_COLORS[len([k for k in meta if meta[k].get('cat') == 'kr']) % len(NEW_COLORS)]
    meta[key] = {"label": label, "cat": "kr", "cur": "KRW", "color": color,
                 "eco": kind in ("KAU", "KCU", "KOC", "IKOC"), "auto": True}
    log(f"  ➕ 새 종목 추가: {key} ({label})")


def apply_krx_day(data, day, items):
    """하루치 응답을 시리즈에 반영. 반영한 키 목록을 돌려준다."""
    lab = label_of(day)
    groups = {}
    for it in items:
        name = str(it.get("itmsNm", "")).strip()
        key = classify(name)
        close = to_num(it.get("clpr") or it.get("basPrc") or it.get("trdPrc"))
        if not key or close is None or close <= 0:
            continue
        groups.setdefault(key, []).append((name, close, to_num(it.get("trqu"))))
    done = []
    for key, rows in groups.items():
        note = ""
        if len({r[0] for r in rows}) > 1:
            exact = [r for r in rows if re.sub(r"\s+", "", r[0]).upper() == key]
            rows = exact or rows
            note = "같은 분류에 종목명이 여럿: " + ", ".join(sorted({r[0] for r in groups[key]})) + f" → '{rows[0][0]}' 사용"
            log(f"  ⚠️ {key}: {note}")
        name, close, vol = rows[0]
        ensure_meta(data, key)
        data["series"].setdefault(key, {})[lab] = close
        if vol is not None:
            data["volume"].setdefault(key, {})[lab] = int(vol)
        st = data["status"].setdefault(key, {})
        st.update({"mode": "auto", "name": name, "source": "공공데이터포털 일반상품시세정보(KRX 배출권시장)"})
        st["last"] = max(lab, st.get("last") or lab, key=parse_label)
        if note:
            st["note"] = note
        else:
            st.pop("note", None)
        done.append(key)
    if groups:
        data["krx_names"] = {"date": lab, "names": sorted({str(it.get('itmsNm', '')).strip() for it in items})}
    return done


def fetch_krx(data, today=None, caller=krx_call):
    if not SERVICE_KEY and caller is krx_call:
        log("⚠️ SERVICE_KEY 미설정 — GitHub Secrets 에 SERVICE_KEY 를 등록하세요. KRX 수집을 건너뜁니다.")
        return 0
    today = today or now_kst().date()
    auto_keys = [k for k, m in data["meta"].items() if m.get("auto") and m.get("cat") == "kr"]
    have = set()
    for k in auto_keys:
        have |= {lab for lab in (data["series"].get(k) or {}) if lab.count(".") == 2}
    window = recent_weekdays(BACKFILL_WEEKDAYS, today)
    seeds = data.setdefault("_seeded", [])
    deep = "deep_backfill_v3" not in seeds and have
    if deep:   # v3 첫 실행 1회: 일별 기록이 시작된 날부터 오늘까지 빈 평일을 모두 보충
        start = min(parse_label(l) for l in have)
        window = [start + datetime.timedelta(days=i) for i in range((today - start).days + 1)]
        window = [d for d in window if d.weekday() < 5]
    todo = [d for d in window if label_of(d) not in have]
    log(f"KRX: {'첫 실행 전체 보충 — ' if deep else ''}최근 {len(window)}영업일 중 빈 날 {len(todo)}일 조회")
    filled, answered = 0, 0
    for d in todo:
        items = caller(d)
        if items is not None:
            answered += 1
        if not items:
            continue
        keys = apply_krx_day(data, d, items)
        if keys:
            filled += 1
            log(f"  ✅ {label_of(d)}: " + ", ".join(f"{k} {data['series'][k][label_of(d)]:,.0f}" for k in sorted(keys)))
    if deep and answered:   # API가 한 번이라도 응답했을 때만 「전체 보충 완료」로 기록
        seeds.append("deep_backfill_v3")
    mark_stopped(data)
    log(f"KRX: {filled}일 새로 채움")
    return filled


def mark_stopped(data):
    """다른 종목은 들어오는데 이 종목만 오래 안 들어오면 「수집 중단」으로 표시"""
    kr = [k for k, m in data["meta"].items() if m.get("auto") and m.get("cat") == "kr"]
    lasts = {}
    for k in kr:
        daily = [l for l in (data["series"].get(k) or {}) if l.count(".") == 2]
        if daily:
            lasts[k] = max(daily, key=parse_label)
    if not lasts:
        return
    newest = max(lasts.values(), key=parse_label)
    for k, l in lasts.items():
        st = data["status"].setdefault(k, {})
        gap = weekdays_between(parse_label(l), parse_label(newest))
        if gap > STOP_AFTER_WEEKDAYS:
            st["mode"] = "stopped"
            st["note"] = f"{l} 이후 API 응답에 이 종목이 없음"
        elif st.get("mode") == "stopped":
            st["mode"] = "auto"
            st.pop("note", None)


# ───────────────────────── 환율 (ECB 기준, 키 불필요) ─────────────────────────
FX_URLS = [
    "https://api.frankfurter.dev/v1/latest?base=KRW&symbols=USD,EUR,GBP,CNY",
    "https://api.frankfurter.app/latest?from=KRW&to=USD,EUR,GBP,CNY",
]


def fetch_fx(data, getter=http_get):
    for url in FX_URLS:
        try:
            j = json.loads(getter(url, UA, 20))
            rates = j.get("rates") or {}
            fx = {c: round(1 / rates[c]) for c in ("USD", "EUR", "GBP", "CNY") if rates.get(c)}
            if not (900 <= fx.get("USD", 0) <= 2500):
                raise ValueError(f"USD/KRW 범위 이상: {fx.get('USD')}")
            data["fx"] = fx
            data["fx_asof"] = f"{j.get('date')} ECB 기준환율(Frankfurter)"
            log(f"✅ 환율 {j.get('date')}: " + ", ".join(f"{c} {v:,}" for c, v in fx.items()))
            return True
        except Exception as e:
            log(f"  환율 실패({url.split('/')[2]}): {str(e)[:60]}")
    log("  환율: 기존 값 유지")
    return False


# ───────────────────────── EU ETS (선택: 무료 키가 있으면 자동) ─────────────────────────
def fetch_eu(data, getter=http_get):
    key = os.environ.get("OILPRICE_KEY", "").strip()
    if not key:
        log("  ℹ️ OILPRICE_KEY 없음 — EU ETS 는 수동 입력")
        return False
    try:
        j = json.loads(getter("https://api.oilpriceapi.com/v1/prices/latest?by_code=EU_CARBON",
                              {"Authorization": f"Token {key}", "Content-Type": "application/json"}, 20))
        price = (j.get("data") or {}).get("price")
        created = (j.get("data") or {}).get("created_at", "")[:10]
        if not price:
            raise ValueError("가격 없음")
        d = datetime.date.fromisoformat(created) if created else now_kst().date()
        lab = label_of(d)
        data["series"].setdefault("EU_ETS", {})[lab] = round(float(price), 2)
        data["meta"]["EU_ETS"]["auto"] = True
        data["status"]["EU_ETS"] = {"mode": "auto", "last": lab, "source": "OilPriceAPI EU_CARBON (ICE EUA 선물)"}
        log(f"✅ EU ETS {lab}: €{price}")
        return True
    except Exception as e:
        log(f"  EU ETS 자동 실패(기존 값 유지): {str(e)[:60]}")
        return False


# ───────────────────────── 수동 입력 ─────────────────────────
def manual_update(data):
    key = os.environ.get("MANUAL_SERIES", "").strip()
    val = to_num(os.environ.get("MANUAL_VALUE", ""))
    date = os.environ.get("MANUAL_DATE", "").strip()
    src = os.environ.get("MANUAL_SOURCE", "").strip()
    if key not in MANUAL_KEYS:
        raise SystemExit(f"⛔ 지표 이름 오류: '{key}' (가능: {', '.join(MANUAL_KEYS)})")
    if val is None or val <= 0:
        raise SystemExit(f"⛔ 값 오류: '{os.environ.get('MANUAL_VALUE')}' — 숫자만 입력 (원통화 기준, 예: 12.4)")
    if not src:
        raise SystemExit("⛔ 출처를 비워 둘 수 없습니다")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        d = datetime.date.fromisoformat(date)
        lab = label_of(d)
    elif re.fullmatch(r"\d{4}-\d{2}", date):
        d = datetime.date.fromisoformat(date + "-01")
        lab = d.strftime("%y.%m")
    else:
        raise SystemExit(f"⛔ 날짜 오류: '{date}' — 2026-10-10 또는 2026-10 형식")
    if d > now_kst().date():
        raise SystemExit("⛔ 미래 날짜는 입력할 수 없습니다")
    old = (data["series"].get(key) or {}).get(lab)
    data["series"].setdefault(key, {})[lab] = val
    data["meta"].setdefault(key, {})["asof"] = date
    st = data["status"].setdefault(key, {})
    st.update({"mode": "manual", "source": src})
    st["last"] = max(lab, st.get("last") or lab, key=parse_label)
    cur = data["meta"][key].get("cur", "")
    log(f"✅ 수동 입력 {key} {lab} = {val} {cur}" + (f" (기존 {old} → 교체)" if old is not None else "") + f" · 출처: {src}")


# ───────────────────────── 실행 ─────────────────────────
def main(argv):
    log("=== 탄소배출권 가격 수집 v3 ===")
    data = load_data()
    if data is None:
        sys.exit(1)
    before = snapshot(data)
    migrate(data)
    if "--manual" in argv:
        manual_update(data)
    else:
        fetch_krx(data)
        fetch_fx(data)
        fetch_eu(data)
    changed = save_if_changed(data, before)
    for k in data["meta"]:
        s = data["series"].get(k) or {}
        if s:
            last = max(s, key=parse_label)
            mode = (data.get("status", {}).get(k) or {}).get("mode", "?")
            log(f"  {k:7s} {s[last]:>10,.2f}  ({last}, {mode})")
    log("=== 끝 ===" + (" (저장함)" if changed else " (변경 없음)"))


if __name__ == "__main__":
    main(sys.argv[1:])
