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


def rows(html, must=r"\d"):
    out = []
    for tr in re.findall(r"<tr.*?</tr>", html, flags=re.S | re.I):
        cells = [snip(re.sub(r"<[^>]+>", " ", td), 40) for td in re.findall(r"<t[dh].*?</t[dh]>", tr, flags=re.S | re.I)]
        line = " | ".join(c.strip() for c in cells if c.strip())
        if line and re.search(must, line):
            out.append(line)
    return out


def raw_ctx(body, kw, n=4, w=160):
    for m in list(re.finditer(kw, body))[:n]:
        print(f"  <{kw}> …{snip(body[max(0, m.start() - w):m.end() + w], 2 * w + 40)}")


# 1) Abaxx 시세 페이지
st, ct, body = get("https://abaxx.exchange/market-data")
print(f"\n### Abaxx /market-data  status={st} len={len(body)}")
for kw in ("CP1", "CORSIA", "Settle", "settle"):
    raw_ctx(body, kw, 3)
print("  table rows:", rows(body)[:15])
urls = set(re.findall(r'https?://[^"\'\s<>\\]+', body))
print("  external urls:", sorted(u for u in urls if "abaxx" in u or "api" in u)[:30])
chunks = sorted(set(re.findall(r'/_next/static/chunks/[^"\'\s<>\\]+\.js', body)))[:40]
found = set()
for c in chunks:
    _, _, js = get("https://abaxx.exchange" + c)
    for u in re.findall(r'["\'`](https?://[^"\'`\s]+|/api/[^"\'`\s]+)["\'`]', js):
        if re.search(r"api|settle|market|price|quote|graphql|ws", u, re.I):
            found.add(u)
print(f"  js chunks scanned={len(chunks)} api-like:", sorted(found)[:40])

# 2) eSignal ICE CP1 표
st, ct, body = get("https://quotes.esignal.com/esignalprod/quote.action?symbol=CP1-ICE")
print(f"\n### eSignal CP1-ICE  status={st} len={len(body)}")
for r in rows(body)[:20]:
    print("  row:", r)
raw_ctx(body, "CP1", 4, 120)

# 3) TradingView
H = {"Content-Type": "application/json", "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}
q = json.dumps({"symbols": {"tickers": ["ABAXX:CP11!", "ABAXX:CP12!"], "query": {"types": []}},
                "columns": ["name", "description", "close", "change", "volume", "update_mode", "currency"]}).encode()
report("TradingView scanner ABAXX:CP11!", "https://scanner.tradingview.com/futures/scan", q, H)
report("TradingView search CORSIA (all exchanges)",
       "https://symbol-search.tradingview.com/symbol_search/v3/?text=CORSIA&lang=en&search_type=futures&domain=production", headers=H)
