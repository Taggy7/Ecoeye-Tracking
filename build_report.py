#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
에코아이 투자분석용 탄소배출권 요약본 생성기
- data/prices.json (fetch_prices.py가 갱신)을 읽어 보고서에 바로 붙일 수 있는 요약을 만든다.
- 출력
    data/report_digest.md    사람이 읽는 요약 (보고서·LLM에 그대로 전달)
    data/report_digest.json  기계가 읽는 요약 (분석 파이프라인용)
- 새로 만들어내는 수치 없음: 전부 prices.json 값의 변화율·환산·비율 계산이다.
  수동 갱신 지표(auto=false)는 분기 대표값이라 일간 변동 해석에 쓰지 않도록 표시한다.

실행: python3 build_report.py [--today YYYY-MM-DD]
"""

import os
import sys
import json
import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "data", "prices.json")
OUT_MD = os.path.join(ROOT, "data", "report_digest.md")
OUT_JSON = os.path.join(ROOT, "data", "report_digest.json")

# 판정 기준 (보고서 검토용 알림 임계값)
BIG_MOVE_1M = 0.10        # 1개월 변동 ±10% 이상
BIG_MOVE_1D = 0.03        # 직전 관측 대비 ±3% 이상
STALE_AUTO_DAYS = 7       # 자동 수집 지표가 이보다 오래 갱신 안 되면 경고
STALE_MANUAL_DAYS = 120   # 수동 지표가 이보다 오래되면 경고
FLAT_RUN_WARN = 10        # 같은 값이 연속 N회 이상이면 '무거래/미갱신 의심'

WINDOWS = [("1주", 7), ("1개월", 30), ("3개월", 91), ("6개월", 182), ("1년", 365)]


def parse_label(label):
    """'26.10.07' → 2026-10-07 / '26.09' → 2026-09-30(월말, 분기·월 대표값)"""
    parts = label.split(".")
    yy, mm = int(parts[0]) + 2000, int(parts[1])
    if len(parts) >= 3:
        return datetime.date(yy, mm, int(parts[2]))
    nxt = datetime.date(yy + (mm == 12), mm % 12 + 1, 1)
    return nxt - datetime.timedelta(days=1)


def points(series):
    """[(date, value)] 날짜순. 같은 날짜가 겹치면 일간(yy.mm.dd) 값을 우선."""
    by_date = {}
    for label, v in series.items():
        try:
            d = parse_label(label)
        except (ValueError, IndexError):
            continue
        if v is None:
            continue
        daily = label.count(".") >= 2
        if d not in by_date or daily:
            by_date[d] = float(v)
    return sorted(by_date.items())


def value_at_or_before(pts, target):
    prev = None
    for d, v in pts:
        if d <= target:
            prev = (d, v)
        else:
            break
    return prev


def pct(new, old):
    return None if not old else new / old - 1


def meta_for(key, meta):
    """meta에 없는 연도물(KAU26·KCU26 등)은 같은 계열(KCU25)의 meta를 따른다."""
    if key in meta:
        return meta[key]
    base = key.rstrip("0123456789")
    for k, m in meta.items():
        if k.rstrip("0123456789") == base:
            return dict(m, label=key)
    return {}


def analyze(key, meta, series, fx, today, source=None):
    pts = points(series)
    if not pts:
        return None
    # 수집 원천이 바뀐 지표(예: CORSIA 수동 대표값 → ICE 선물)는 바뀐 날 이후만 비교한다
    since = (source or {}).get("since")
    if since:
        cut = parse_label(since)
        newer = [p for p in pts if p[0] >= cut]
        if newer:
            pts = newer
    last_d, last_v = pts[-1]
    cur = meta.get("cur", "KRW")
    rate = 1.0 if cur == "KRW" else fx.get(cur)
    out = {
        "key": key,
        "label": meta.get("label", key),
        "cur": cur,
        "auto": bool(meta.get("auto")),
        "eco": bool(meta.get("eco")),
        "emph": bool(meta.get("emph")),
        "last_date": last_d.isoformat(),
        "last": last_v,
        "last_krw": round(last_v * rate) if rate else None,
        "age_days": (today - last_d).days,
        "n_points": len(pts),
        "changes": {},
        "source": (source or {}).get("src"),
        "source_since": parse_label(since).isoformat() if since else None,
        "alerts": [],
    }

    if len(pts) >= 2:
        out["changes"]["직전관측"] = pct(last_v, pts[-2][1])
        out["prev_date"] = pts[-2][0].isoformat()
    for name, days in WINDOWS:
        ref = value_at_or_before(pts, last_d - datetime.timedelta(days=days))
        out["changes"][name] = pct(last_v, ref[1]) if ref else None
        # 수동(분기 대표값) 지표는 3개월 미만 창이 의미 없음
        if not out["auto"] and days < 90:
            out["changes"][name] = None
    if not out["auto"]:
        out["changes"]["직전관측"] = out["changes"].get("직전관측")  # 직전 분기 대비로 해석

    # 최근 1년 구간 고저 (데이터가 1년 미만이면 보유 전체 기간)
    yr = [v for d, v in pts if d >= last_d - datetime.timedelta(days=365)]
    hi, lo = max(yr), min(yr)
    out["range_1y"] = {"high": hi, "low": lo,
                       "pos": None if hi == lo else (last_v - lo) / (hi - lo),
                       "covers_full_year": pts[0][0] <= last_d - datetime.timedelta(days=365)}

    # 동일가 연속 횟수 (최신부터 거슬러)
    run = 1
    for i in range(len(pts) - 2, -1, -1):
        if pts[i][1] == last_v:
            run += 1
        else:
            break
    out["flat_run"] = run

    # 알림
    limit = STALE_AUTO_DAYS if out["auto"] else STALE_MANUAL_DAYS
    if out["age_days"] > limit:
        kind = "자동수집" if out["auto"] else "수동갱신"
        out["alerts"].append(f"최신값이 {out['age_days']}일 전({out['last_date']}) — {kind} 지표 갱신 지연")
    if run >= FLAT_RUN_WARN and out["auto"]:
        out["alerts"].append(f"같은 가격이 {run}회 연속 — 거래 부재 또는 API 미갱신 가능성(원천 확인 필요)")
    c1m = out["changes"].get("1개월")
    if out["auto"] and c1m is not None and abs(c1m) >= BIG_MOVE_1M:
        out["alerts"].append(f"1개월 {c1m:+.1%} 변동")
    c1 = out["changes"].get("직전관측")
    if out["auto"] and c1 is not None and abs(c1) >= BIG_MOVE_1D:
        out["alerts"].append(f"직전 관측 대비 {c1:+.1%}")
    if out["auto"] and len(yr) >= 20:
        if last_v >= hi and lo != hi:
            out["alerts"].append("보유기간 내 신고가")
        elif last_v <= lo and lo != hi:
            out["alerts"].append("보유기간 내 신저가")
    return out


def derived(res):
    """KRW 환산 기준 파생 지표 (둘 다 있을 때만)."""
    d = []

    def g(k):
        return res.get(k)

    kau26, kau25, koc, kcu = g("KAU26"), g("KAU25"), g("KOC"), g("KCU26") or g("KCU25")
    eua, corsia, vcm = g("EU_ETS"), g("CORSIA"), g("VCM")

    def add(name, a, b, note):
        if a and b and a["last_krw"] and b["last_krw"]:
            manual = [x["key"] for x in (a, b) if not x["auto"]]
            if manual:
                note += f" — {'·'.join(manual)} 수동 대표값"
            d.append({"name": name, "value": a["last_krw"] / b["last_krw"],
                      "basis": f"{a['key']} {a['last_date']} / {b['key']} {b['last_date']}", "note": note})

    add("KOC / KAU26", koc, kau26, "외부사업 상쇄배출권의 할당배출권 대비 가격 수준(1.0 초과 시 KOC가 더 비쌈)")
    add("KCU / KAU26", kcu, kau26, "상쇄배출권의 할당배출권 대비 할인율 판단용")
    add("KAU26 / KAU25", kau26, kau25, "연도물 간 가격차")
    add("KAU26 / EUA", kau26, eua, "국내 가격의 EU 대비 수준")
    add("CORSIA / KAU26", corsia, kau26, "국제 항공 상쇄(CORSIA) 가격의 국내 할당 대비 수준")
    add("VCM / KAU26", vcm, kau26, "자발적 시장 대비")
    return d


def fmt_p(v):
    return "–" if v is None else f"{v:+.1%}"


def fmt_n(v, cur):
    if cur == "KRW":
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def render_md(updated, today, items, drv, fx):
    L = []
    L.append(f"# 탄소배출권 트래킹 요약 (에코아이 분석용) — {today.isoformat()}")
    L.append("")
    L.append(f"- 원천: `data/prices.json` (수집 갱신 시각 {updated})")
    L.append("- 자동 수집(일간): " + "·".join(i["key"] for i in items if i["auto"])
             + " / 수동 갱신(분기 대표값, 일간 변동 해석 금지): " + "·".join(i["key"] for i in items if not i["auto"]))
    L.append(f"- 환율(원): " + ", ".join(f"{k} {v:,}" for k, v in fx.items()))
    for i in items:
        if i.get("source"):
            L.append(f"- {i['key']} 자동수집 원천: {i['source']} — {i['source_since']} 이후 값만 변동률·고저 계산에 사용"
                     "(이전 수동 대표값과 직접 비교 불가)")
    L.append("- 아래 수치는 모두 prices.json 값에서 계산한 변화율·환산이며 별도 추정치는 없음.")
    L.append("")

    alerts = [(i, a) for i in items for a in i["alerts"]]
    L.append("## 1. 검토 필요 알림")
    if alerts:
        for i, a in alerts:
            L.append(f"- **{i['key']}**: {a}")
    else:
        L.append("- 없음")
    L.append("")

    for title, flt in (("2. 에코아이 직접연관 지표 (★ 표시 지표)", lambda i: i["eco"]),
                       ("3. 비교 지표 (참고)", lambda i: not i["eco"])):
        L.append(f"## {title}")
        L.append("")
        L.append("| 지표 | 최신(기준일) | 원화환산 | 직전관측 | 1주 | 1개월 | 3개월 | 6개월 | 1년 | 1년내 위치* | 수집 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for i in filter(flt, items):
            c = i["changes"]
            pos = i["range_1y"]["pos"]
            posS = "–" if pos is None else f"{pos:.0%}"
            if not i["range_1y"]["covers_full_year"]:
                posS += "†"
            L.append("| {lab} | {v} {cur} ({d}) | {krw} | {c0} | {c1} | {c2} | {c3} | {c4} | {c5} | {pos} | {src} |".format(
                lab=("**" + i["label"] + "**") if i["emph"] else i["label"],
                v=fmt_n(i["last"], i["cur"]), cur=i["cur"], d=i["last_date"],
                krw=f"{i['last_krw']:,}원" if i["last_krw"] else "–",
                c0=fmt_p(c.get("직전관측")), c1=fmt_p(c.get("1주")), c2=fmt_p(c.get("1개월")),
                c3=fmt_p(c.get("3개월")), c4=fmt_p(c.get("6개월")), c5=fmt_p(c.get("1년")),
                pos=posS, src="자동" if i["auto"] else "수동"))
        L.append("")
    L.append("\\* 최근 1년 저점=0%, 고점=100%. † 보유 데이터가 1년 미만이라 보유기간 기준.")
    L.append("")

    L.append("## 4. 파생 비율 (원화 환산 기준)")
    if drv:
        L.append("")
        L.append("| 비율 | 값 | 기준 | 해석 메모 |")
        L.append("|---|---|---|---|")
        for x in drv:
            L.append(f"| {x['name']} | {x['value']:.2f} | {x['basis']} | {x['note']} |")
    else:
        L.append("- 계산 가능한 쌍 없음")
    L.append("")

    L.append("## 5. 보고서 검토 체크리스트")
    L.append("- 알림 항목은 원천(KRX ETS 등)에서 확인 전까지 사실로 단정하지 않는다.")
    L.append("- 에코아이 매출·이익 영향(KOC 판매단가 가정 등)은 이 요약에 포함되지 않음 — 보고서의 가정 단가와 위 KOC 최신가를 대조해 검토한다.")
    L.append("- 수동 지표는 갱신일을 확인하고, 오래된 값은 보고서에 '기준일'을 병기한다.")
    L.append("")
    return "\n".join(L)


def main():
    today = datetime.date.today()
    if "--today" in sys.argv:
        today = datetime.date.fromisoformat(sys.argv[sys.argv.index("--today") + 1])
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)
    fx = data.get("fx", {})
    meta = data.get("meta", {})

    res = {}
    for key, series in data.get("series", {}).items():
        r = analyze(key, meta_for(key, meta), series, fx, today,
                    data.get("sources", {}).get(key))
        if r:
            res[key] = r
    items = sorted(res.values(), key=lambda i: (not i["eco"], not i["emph"], i["key"]))
    drv = derived(res)

    updated = data.get("updated", "?")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render_md(updated, today, items, drv, fx))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"as_of": today.isoformat(), "source_updated": updated, "fx": fx,
                   "indicators": items, "derived": drv}, f, ensure_ascii=False, indent=2)
    print(f"요약 생성: {OUT_MD} ({len(items)}개 지표, 알림 {sum(len(i['alerts']) for i in items)}건)")


if __name__ == "__main__":
    main()
