#!/usr/bin/env python3
"""CORSIA 가격 원천 후보 접속 시험 (진단용 · 작업 브랜치에서만 사용)"""
import json
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}


def get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, "", f"{type(e).__name__}: {e}"


def snip(t, n=220):
    return re.sub(r"\s+", " ", t)[:n]


def report(name, url, data=None, headers=None):
    st, ct, body = get(url, data, headers)
    print(f"\n### {name}\nURL {url}\nstatus={st} type={ct} len={len(body)}")
    if st != 200:
        print("body:", snip(body, 300))
        return body
    if "json" in ct:
        print("json:", snip(body, 600))
        return body
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    for kw in ("Settle", "settle", "Last", "Price", "price", "USD"):
        for m in list(re.finditer(kw, text))[:3]:
            print(f"  [{kw}] …{snip(text[max(0, m.start() - 80):m.end() + 120])}")
    links = sorted(set(re.findall(r'(?:href|src)=["\']([^"\']+)["\']', body)))
    keep = [l for l in links if re.search(r"api|json|csv|xls|settle|market-?data|report|price|graphql", l, re.I)]
    print("  links:", keep[:25])
    apis = sorted(set(re.findall(r'https?://[^"\'\s<>]*(?:api|graphql|json)[^"\'\s<>]*', body)))
    print("  api-like urls:", apis[:15])
    return body


report("Abaxx CP1 page", "https://abaxx.exchange/markets/environmental/corsia")
report("Abaxx CPO page", "https://abaxx.exchange/markets-environmental/cpo")
report("eSignal CP1-ICE", "https://quotes.esignal.com/esignalprod/quote.action?symbol=CP1-ICE")
tv = json.dumps({"symbols": {"tickers": ["ABAXX:CP1!", "ABAXX:CP1Z2026", "ABAXX:CP1H2027"], "query": {"types": []}},
                 "columns": ["name", "description", "close", "change", "volume", "update_mode", "currency"]}).encode()
report("TradingView scanner (ABAXX CP1)", "https://scanner.tradingview.com/futures/scan", tv,
       {"Content-Type": "application/json", "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"})
tvs = "https://symbol-search.tradingview.com/symbol_search/v3/?text=CP1&exchange=ABAXX&lang=en&search_type=undefined&domain=production"
report("TradingView symbol search (ABAXX CP1)", tvs, headers={"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"})
