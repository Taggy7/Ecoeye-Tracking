#!/usr/bin/env python3
"""Read-only v3 price digest. No network, no changes to prices.json.

Returns distinguish published/reference-price changes from confirmed-trade changes.
Weighted prices are vintage closing prices times volume, not transaction-level VWAP.
Monthly labels denote an interval; their exact observation day is unknown.
"""
import argparse
import calendar
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
WINDOWS = [("1주", 7), ("1개월", 30), ("3개월", 91), ("6개월", 182), ("1년", 365)]
WEIGHTED = {"KOC_TRD": "KOC", "IKOC_TRD": "IKOC"}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def period(label):
    parts = [int(p) for p in label.replace('-', '.').split('.')]
    if len(parts) not in (2, 3):
        raise ValueError(label)
    y, m = parts[:2]
    y = y + 2000 if y < 100 else y
    start = dt.date(y, m, parts[2] if len(parts) == 3 else 1)
    end = start if len(parts) == 3 else dt.date(y, m, calendar.monthrange(y, m)[1])
    return start, end, 'day' if len(parts) == 3 else 'month'


def observations(series, volumes, today):
    out, excluded = [], []
    for label, raw in series.items():
        try:
            start, end, precision = period(label)
        except (ValueError, TypeError):
            excluded.append({'label': label, 'reason': 'invalid_date'})
            continue
        price = number(raw)
        if price is None or price <= 0:
            excluded.append({'label': label, 'reason': 'invalid_price'})
            continue
        # A month is usable only when its whole interval has elapsed.
        if end > today:
            excluded.append({'label': label, 'reason': 'future_or_incomplete_period'})
            continue
        volume = number(volumes.get(label))
        if volume is not None and volume < 0:
            excluded.append({'label': label, 'reason': 'invalid_volume'})
            volume = None
        out.append({'label': label, 'period_start': start.isoformat(),
                    'period_end': end.isoformat(), 'precision': precision,
                    'price': price, 'volume': volume})
    return sorted(out, key=lambda p: (p['period_end'], p['precision'] == 'day', p['label'])), excluded


def change(new, old):
    return new / old - 1 if old else None


def returns(pts, manual=False):
    values = {name: None for name, _ in WINDOWS}
    values['직전관측'] = None
    bases = {}
    if not pts:
        return values, bases
    last = pts[-1]
    end = dt.date.fromisoformat(last['period_end'])
    if len(pts) > 1 and last['precision'] == pts[-2]['precision'] == 'day':
        values['직전관측'] = change(last['price'], pts[-2]['price'])
        bases['직전관측'] = {'from': pts[-2]['label'], 'to': last['label'], 'from_price': pts[-2]['price'], 'to_price': last['price']}
    for name, days in WINDOWS:
        target = end - dt.timedelta(days=days)
        candidates = [p for p in pts[:-1] if dt.date.fromisoformat(p['period_end']) <= target]
        if manual or last['precision'] != 'day' or not candidates:
            continue
        base = candidates[-1]
        lag = (target - dt.date.fromisoformat(base['period_end'])).days
        bases[name] = {'from': base['label'], 'to': last['label'],
                       'target': target.isoformat(), 'baseline_lag_days': lag, 'from_price': base['price'], 'to_price': last['price']}
        # Do not call a months-old quote a one-week or one-month comparison.
        if base['precision'] == 'day' and lag <= 7:
            values[name] = change(last['price'], base['price'])
    return values, bases


def weighted_observations(data, family, today):
    acc = {}
    for key, series in data.get('series', {}).items():
        if not ((family == 'KOC' and key == 'KOC') or re.fullmatch(family + r'\d{2}', key)):
            continue
        pts, _ = observations(series, data.get('volume', {}).get(key, {}), today)
        for p in pts:
            if p['precision'] != 'day' or p['volume'] is None or p['volume'] <= 0:
                continue
            a = acc.setdefault(p['label'], {'numerator': 0., 'volume': 0., 'members': []})
            a['numerator'] += p['price'] * p['volume']
            a['volume'] += p['volume']
            a['members'].append({'key': key, 'close': p['price'], 'volume': p['volume']})
    return {lab: dict(a, price=a['numerator'] / a['volume']) for lab, a in acc.items()}


def build(data, today):
    res, warnings = {}, []
    keys = set(data.get('series', {}))
    for key, family in WEIGHTED.items():
        if any(k == family or re.fullmatch(family + r'\d{2}', k) for k in keys):
            keys.add(key)
    for key in sorted(keys):
        meta = data.get('meta', {}).get(key, {})
        status = data.get('status', {}).get(key, {})
        series = data.get('series', {}).get(key, {})
        vols = data.get('volume', {}).get(key, {})
        pts, excluded = observations(series, vols, today)
        audit = None
        if key in WEIGHTED:
            calc = weighted_observations(data, WEIGHTED[key], today)
            original = {p['label']: p for p in pts}
            mismatches = []
            for lab in sorted(set(original) | set(calc)):
                p, c = original.get(lab), calc.get(lab)
                if c is None or p is None or abs(p['price'] - c['price']) > .500001 or p['volume'] != c['volume']:
                    mismatches.append(lab)
            audit = {'method': 'sum(vintage_close * positive_volume) / sum(positive_volume)',
                     'transaction_vwap': False, 'stored_mismatches': mismatches, 'components': calc}
            pts, _ = observations({lab: c['price'] for lab, c in calc.items()},
                                  {lab: c['volume'] for lab, c in calc.items()}, today)
            if mismatches:
                warnings.append(f'{key}: 저장된 가중평균과 검산 불일치 {len(mismatches)}건; 보고서는 검산값 사용')
        if not pts:
            warnings.append(f'{key}: 유효 관측 또는 확인된 실거래 없음')
            continue
        domestic = key in WEIGHTED or meta.get('cat') == 'kr' or bool(re.fullmatch(r'(KAU|KCU|KOC|IKOC|IKCU)\d*', key))
        manual = status.get('mode') == 'manual' or not meta.get('auto', domestic)
        source = status.get('source') or data.get('sources', {}).get(key, {}).get('src')
        source_since = data.get('sources', {}).get(key, {}).get('since')
        if source_since:
            _, cutoff, _ = period(source_since)
            pts = [p for p in pts if p['period_end'] >= cutoff.isoformat()]
            if not pts:
                continue
        last = pts[-1]
        traded = [p for p in pts if p['precision'] == 'day' and p['volume'] is not None and p['volume'] > 0] if domestic else []
        published_changes, published_bases = returns(pts, manual)
        trade_changes, trade_bases = returns(traded)
        usable = trade_changes if domestic else published_changes
        bases = trade_bases if domestic else published_bases
        kind = ('traded_close_weighted' if key in WEIGHTED else
                'trade_confirmed_close' if domestic and last['volume'] is not None and last['volume'] > 0 else
                'reference_no_trade' if domestic and last['volume'] == 0 else
                'volume_unknown' if domestic else 'manual_quote' if manual else 'published_quote')
        cur = meta.get('cur', 'KRW')
        rate = 1. if cur == 'KRW' else number(data.get('fx', {}).get(cur))
        alerts = []
        if kind == 'reference_no_trade':
            alerts.append('거래량 0: 거래 없는 기준가격; 가격 변경을 실거래 상승·하락으로 해석 금지')
        if kind == 'volume_unknown':
            alerts.append('거래량 미확인: 실거래 여부 판단 불가')
        if key == 'KOC':
            alerts.append("KOC는 KOC21-26 연물; i-KOC·전체 KOC 대표값 아님. 과거 월값은 연물 불명")
        if domestic and traded and (today - dt.date.fromisoformat(traded[-1]['period_end'])).days > 7:
            alerts.append(f"마지막 거래 확인일 {traded[-1]['label']}; 최근 시장가격으로 간주 금지")
        age = (today - dt.date.fromisoformat(last['period_end'])).days
        if age > (120 if manual else 7):
            alerts.append(f'관측 지연 {age}일 (월값은 월말 기준 최소 경과일)')
        if status.get('mode') == 'stopped':
            alerts.append('수집 중단: ' + status.get('note', '원천 확인 필요'))
        if manual:
            alerts.append('관측별 출처·상품 일치 미확인: 기간 수익률 계산 제외')
        if usable.get('1개월') is not None and abs(usable['1개월']) >= .1:
            alerts.append(f"거래 확인 기준 1개월 {usable['1개월']:+.1%}" if domestic else f"1개월 {usable['1개월']:+.1%}")
        analysis_pts = traded if domestic else pts
        yr = [p for p in analysis_pts if dt.date.fromisoformat(p['period_end']) >= dt.date.fromisoformat(analysis_pts[-1]['period_end']) - dt.timedelta(days=365)] if analysis_pts else []
        hi = max((p['price'] for p in yr), default=None)
        lo = min((p['price'] for p in yr), default=None)
        range_1y = {'high': hi, 'low': lo, 'pos': (analysis_pts[-1]['price'] - lo) / (hi - lo) if hi is not None and hi != lo else None,
                    'basis': 'confirmed_trades' if domestic else 'published_quotes',
                    'end_label': analysis_pts[-1]['label'] if analysis_pts else None,
                    'covers_full_year': bool(analysis_pts and dt.date.fromisoformat(analysis_pts[0]['period_end']) <= dt.date.fromisoformat(analysis_pts[-1]['period_end']) - dt.timedelta(days=365))}
        flat_run = 0
        for p in reversed(pts):
            if p['price'] != last['price']:
                break
            flat_run += 1
        res[key] = dict(key=key, label=meta.get('label', key), cur=cur, auto=not manual,
                        eco=meta.get('eco', key in WEIGHTED), vintage=meta.get('vintage', False),
                        instrument=status.get('name', key), mode=status.get('mode'),
                        last=last['price'], last_date=last['period_end'] if last['precision'] == 'day' else None,
                        last_label=last['label'], observation=last, volume=last['volume'], price_kind=kind,
                        last_krw=last['price'] * rate if rate and rate > 0 else None,
                        fx_asof=data.get('fx_asof'), fx_basis='latest_snapshot_not_historical' if cur != 'KRW' else None,
                        source=source, source_scope='latest_status_only_not_per_observation', source_since=source_since,
                        changes=usable, change_basis=bases, published_price_changes=published_changes,
                        published_change_basis=published_bases, last_trade=traded[-1] if traded else None,
                        n_points=len(pts), range_1y=range_1y, flat_run=flat_run, excluded=excluded, weighted_audit=audit, alerts=alerts,
                        history=pts)
    ratios = []
    pairs = [('KOC_TRD', 'KAU26'), ('IKOC_TRD', 'KAU26'), ('KOC', 'KAU26'),
             ('KCU26', 'KAU26'), ('KAU26', 'KAU25'), ('KAU26', 'EU_ETS'),
             ('CORSIA', 'KAU26'), ('VCM', 'KAU26')]
    for akey, bkey in pairs:
        a, b = res.get(akey), res.get(bkey)
        if not a or not b:
            continue
        ap, bp = a['observation'], b['observation']
        same = ap['precision'] == bp['precision'] == 'day' and ap['label'] == bp['label']
        active = all(i['price_kind'] in ('trade_confirmed_close', 'traded_close_weighted', 'published_quote') and i['mode'] != 'stopped' for i in (a, b))
        # Cross-currency ratios need dated historical FX; v3 has only one snapshot.
        comparable = same and active and a['cur'] == b['cur']
        val = a['last_krw'] / b['last_krw'] if a['last_krw'] is not None and b['last_krw'] else None
        common = []
        for p in a['history']:
            if p['precision'] != 'day' or (a['cur'] == 'KRW' and not (p['volume'] and p['volume'] > 0)):
                continue
            for q in b['history']:
                if q['precision'] == 'day' and p['label'] == q['label'] and a['cur'] == b['cur'] and (b['cur'] != 'KRW' or (q['volume'] and q['volume'] > 0)):
                    common.append({'label': p['label'], 'value': p['price'] / q['price']})
        ratios.append({'name': f'{akey} / {bkey}', 'value': val if comparable else None,
                       'latest_quote_ratio': val, 'comparable': comparable,
                       'basis': f"{akey} {ap['label']} / {bkey} {bp['label']}",
                       'last_common_trade': max(common, key=lambda p: period(p['label'])[1]) if common else None,
                       'note': '동일 관측일·거래 확인' if comparable else '참고값: 관측일·거래 여부·상품·환율 기준 차이; 현재 스프레드 해석 금지'})
    for item in res.values():
        item.pop('history')
    return {'schema_version': 3, 'as_of': today.isoformat(), 'source_updated': data.get('updated'),
            'fx': data.get('fx', {}), 'fx_asof': data.get('fx_asof'),
            'warnings': warnings, 'indicators': list(res.values()), 'derived': ratios}


def fmt(value, percent=False):
    return '–' if value is None else f'{value:+.1%}' if percent else f'{value:,.2f}'


def render_md(report):
    lines = [f"# 탄소배출권 트래킹 요약 — {report['as_of']}", '',
             f"- 원천 갱신: {report['source_updated']}; SHA256: `{report.get('source_sha256', '')}`",
             '- KOC=KOC21-26 연물. i-KOC와 분리. 연물별 기준가격과 거래 확인 가격을 구분한다.',
             '- 실거래 가중평균=연물별 종가×양의 거래량/합계 거래량. 체결별 VWAP가 아니며 연물 구성 변화 영향을 받는다.',
             '- 거래량 미확인·0은 실거래 수익률에서 제외. 기간 기준점은 목표일 이전 7일 이내 일값만 사용.',
             '- 월값의 정확한 관측일은 알 수 없다. 수동 지표의 출처·상품 연속성이 미확인되어 기간 수익률은 계산하지 않는다.',
             f"- 원화환산은 최신 환율 스냅샷({report.get('fx_asof')}); 관측 당시 환율 환산이 아니다.", '',
             '## 가격·연물·거래량', '',
             '| 지표 | 가격 | 관측일/월 | 거래량 | 가격 성격 | 마지막 거래일·가격 | 거래 확인 1개월 |',
             '|---|---:|---|---:|---|---|---:|']
    names = {'reference_no_trade': '기준가격(거래 없음)', 'volume_unknown': '거래량 미확인',
             'trade_confirmed_close': '거래 확인 종가', 'traded_close_weighted': '종가 거래량 가중평균',
             'manual_quote': '수동 대표값', 'published_quote': '공표 가격'}
    for i in report['indicators']:
        t = i['last_trade']
        lines.append(f"| {i['label']} | {fmt(i['last'])} {i['cur']} | {i['last_label']} | {fmt(i['volume'])} | {names[i['price_kind']]} | {t['label'] + ' · ' + fmt(t['price']) if t else '–'} | {fmt(i['changes'].get('1개월'), True)} |")
    lines += ['', '## 기간 변화율 (거래 확인일 기준; 수동 지표는 제외)', '',
              '| 지표 | 기준 관측 | 직전 | 1주 | 1개월 | 3개월 | 6개월 | 1년 |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    for i in report['indicators']:
        end = i['last_trade']['label'] if i['last_trade'] else i['last_label']
        values = ' | '.join(fmt(i['changes'].get(n), True) for n in ['직전관측'] + [n for n, _ in WINDOWS])
        lines.append(f"| {i['key']} | {end} | {values} |")
        if i['changes'].get('1개월') is not None:
            b = i['change_basis']['1개월']
            lines.append(f"<!-- {i['key']} 1개월: {b['from']} → {b['to']}; baseline lag {b['baseline_lag_days']} days -->")
    lines += ['', '## 출처·주의사항', '']
    for w in report['warnings']:
        lines.append('- ' + w)
    for i in report['indicators']:
        lines.append(f"- **{i['key']}**: {i['source'] or '출처 기록 없음'} (출처는 최신 status 기준; 과거 관측에 소급하지 않음)")
        for a in i['alerts']:
            lines.append('  - ' + a)
        if i['published_price_changes'].get('1개월') is not None and i['price_kind'] in ('reference_no_trade', 'volume_unknown'):
            lines.append(f"  - 공표 기준가격 1개월 {i['published_price_changes']['1개월']:+.1%}; 실거래 상승률 아님")
    lines += ['', '## 파생 비율', '', '| 비율 | 비교 가능한 최신값 | 서로 다른 기준의 참고값 | 기준 | 마지막 동일 거래일·비율 |', '|---|---:|---:|---|---|']
    for r in report['derived']:
        c = r['last_common_trade']
        lines.append(f"| {r['name']} | {fmt(r['value'])} | {fmt(r['latest_quote_ratio']) if not r['comparable'] else '–'} | {r['basis']} · {r['note']} | {c['label'] + ' · ' + fmt(c['value']) if c else '–'} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--today', type=dt.date.fromisoformat, default=dt.datetime.now(ZoneInfo('Asia/Seoul')).date())
    parser.add_argument('--input', type=Path, default=ROOT / 'data/prices.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    raw = args.input.read_bytes()
    report = build(json.loads(raw), args.today)
    report['source_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'report_digest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (args.output_dir / 'report_digest.md').write_text(render_md(report), encoding='utf-8')
    print(f"요약 생성: {len(report['indicators'])}개 지표; 가격 원본 변경 없음")


if __name__ == '__main__':
    main()
