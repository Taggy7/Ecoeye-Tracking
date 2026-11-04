#!/usr/bin/env python3
"""Abaxx 일일 정산 파일(EOD SPAN)에 CP1 정산가가 있는지 확인 (진단용 · 작업 브랜치에서만 사용)"""
import io
import json
import re
import urllib.request
import zipfile

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Referer": "https://abaxx.exchange/market-data"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers.get("Content-Type", ""), r.headers.get("Content-Disposition", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), "", e.read()[:400]
    except Exception as e:
        return None, "", "", f"{type(e).__name__}: {e}".encode()


def show_text(name, raw):
    t = raw.decode("utf-8", "replace")
    print(f"  [{name}] {len(t)}자, 앞부분: {t[:300]!r}")
    hits = [ln for ln in t.splitlines() if re.search(r"CP1|CORSIA", ln)]
    print(f"  [{name}] CP1/CORSIA 포함 줄 {len(hits)}개")
    for ln in hits[:12]:
        print("    ", ln[:240])
    for m in list(re.finditer(r"CP1", t))[:4]:          # XML 처럼 줄바꿈이 없는 경우
        print("    ctx:", re.sub(r"\s+", " ", t[max(0, m.start() - 200):m.end() + 400]))


st, ct, cd, body = get("https://abaxx.exchange/api/eod-span")
print(f"### /api/eod-span status={st} type={ct} len={len(body)}")
print("  ", body[:800].decode("utf-8", "replace"))
dates = []
try:
    j = json.loads(body)
    dates = re.findall(r"\d{4}-?\d{2}-?\d{2}", json.dumps(j))
except Exception:
    dates = re.findall(r"\d{4}-?\d{2}-?\d{2}", body.decode("utf-8", "replace"))
dates = sorted(set(dates))[-3:]
print("  최근 asof 후보:", dates)
for asof in reversed(dates[-2:]):
    st, ct, cd, raw = get(f"https://abaxx.exchange/api/eod-span/download?asof={asof}")
    print(f"\n### download asof={asof} status={st} type={ct} disp={cd} len={len(raw)} head={raw[:8]!r}")
    if st != 200:
        print("  ", raw[:300])
        continue
    if raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw))
        print("  zip 파일:", [(i.filename, i.file_size) for i in z.infolist()][:20])
        for i in z.infolist()[:6]:
            show_text(i.filename, z.read(i))
    else:
        show_text("body", raw)
    break
