#!/usr/bin/env python3
"""抓取 Yahoo Finance 日 K 與新聞,計算策略訊號,輸出 data/data.json。
用法:python scripts/fetch_data.py [--sample] [--if-stale]
"""
import argparse, datetime as dt, email.utils, json, math, os, pathlib, random, sys, time, urllib.parse
import xml.etree.ElementTree as ET

import pandas as pd

from strategy import exit_check, regime, regime_info, resample, scan

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "data.json"
TPE = dt.timezone(dt.timedelta(hours=8))
KEEP = 260  # 網頁只保留近 260 根日 K;策略用 5 年資料計算
INDICES = [("^TWII", "加權指數", "TW", 23400), ("^TWOII", "櫃買指數", "TW", 268),
           ("^GSPC", "S&P 500", "US", 5900), ("^IXIC", "那斯達克", "US", 19300),
           ("^SOX", "費城半導體", "US", 5200)]


def load(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def to_bars(df, keep=KEEP):
    return [[int(pd.Timestamp(i.date()).timestamp()), round(float(r.open), 2), round(float(r.high), 2),
             round(float(r.low), 2), round(float(r.close), 2), int(r.volume)] for i, r in df.tail(keep).iterrows()]


def sample_df(sym, base, n=1260):
    r = random.Random(sym)
    idx = pd.bdate_range(end=dt.datetime.now(TPE).date(), periods=n)
    p, rows = 100.0, []
    for _ in range(n):
        o = p
        c = o * (1 + (r.random() - .48) * .02)
        h = max(o, c) * (1 + r.random() * .01)
        l = min(o, c) * (1 - r.random() * .01)
        rows.append([o, h, l, c, int((.5 + r.random()) * 1e6)])
        p = c
    f = base / rows[-1][3]
    return pd.DataFrame([[a * f, b * f, c_ * f, d * f, v] for a, b, c_, d, v in rows],
                        index=idx, columns=["open", "high", "low", "close", "volume"])


def parse_news(t):
    out = []
    try:
        for n in (t.news or [])[:6]:
            c = n.get("content", n)
            title = c.get("title")
            link = (c.get("canonicalUrl") or {}).get("url") or c.get("link")
            pub = (c.get("provider") or {}).get("displayName") or c.get("publisher") or ""
            if title and link:
                out.append({"title": title, "link": link, "pub": pub})
    except Exception as e:
        print("news error:", e, file=sys.stderr)
    return out


def fetch_real(sym):
    import yfinance as yf
    for k in range(3):
        try:
            t = yf.Ticker(sym)
            h = t.history(period="5y", interval="1d", auto_adjust=False)
            df = h[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]
            df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
            df = df.dropna(subset=["open", "high", "low", "close"])
            df["volume"] = df["volume"].fillna(0)
            if len(df) < 30:
                raise ValueError("no data")
            return df, parse_news(t)
        except Exception as e:
            print(f"{sym} 第 {k + 1} 次失敗:{e}", file=sys.stderr)
            time.sleep(3 * (k + 1))
    return None, []


def fetch_revenue():
    """證交所/櫃買公開資料:每月營收年增率(%)。盡力而為,失敗就回空。"""
    import requests
    out = {}
    for url in ("https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            for row in r.json():
                code = str(row.get("公司代號", "")).strip()
                k = next((k for k in row if "去年同月增減" in k), None)
                if code and k:
                    out[code] = float(str(row[k]).replace(",", ""))
        except Exception as e:
            print("營收資料失敗:", url, e, file=sys.stderr)
    return out


def mi(ym):
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def ymstr(i):
    return f"{i // 12}-{i % 12 + 1:02d}"


def pct(a, b):
    return None if a is None or not b or b <= 0 else round((a / b - 1) * 100, 1)


def yi(v):
    return round(v / 1e8, 2)


def tables_from_months(months):
    """台股:由月營收序列 [(YYYY-MM, 元)] 算出月/季/年表(YoY、MoM、QoQ),單位換成億。"""
    d = {mi(k): float(v) for k, v in months}
    if len(d) < 13:
        return None
    ks = sorted(d)
    m = [{"p": ymstr(i), "rev": yi(d[i]), "yoy": pct(d[i], d.get(i - 12)), "mom": pct(d[i], d.get(i - 1))} for i in ks[-24:]]
    qsum = {}
    for st in sorted({i - (i % 12) % 3 for i in ks}):
        if all(st + k in d for k in range(3)):
            qsum[st] = sum(d[st + k] for k in range(3))
    q = [{"p": f"{st // 12}Q{(st % 12) // 3 + 1}", "rev": yi(v), "yoy": pct(v, qsum.get(st - 12)), "qoq": pct(v, qsum.get(st - 3))}
         for st, v in sorted(qsum.items())[-8:]]
    ysum = {y: sum(d[y * 12 + k] for k in range(12)) for y in sorted({i // 12 for i in ks})
            if all(y * 12 + k in d for k in range(12))}
    y_t = [{"p": str(y), "rev": yi(v), "yoy": pct(v, ysum.get(y - 1))} for y, v in sorted(ysum.items())[-6:]]
    last = ks[-1]
    yy, n = last // 12, last % 12 + 1
    if yy not in ysum and all(yy * 12 + k in d for k in range(n)):
        cur = sum(d[yy * 12 + k] for k in range(n))
        prev = sum(d.get((yy - 1) * 12 + k, 0) for k in range(n)) if all((yy - 1) * 12 + k in d for k in range(n)) else None
        y_t.append({"p": f"{yy} 累計(1-{n}月)", "rev": yi(cur), "yoy": pct(cur, prev)})
    return {"m": m, "q": q, "y": y_t, "high": bool(len(d) >= 24 and d[last] >= max(d.values()))}


def us_tables(q, a):
    """美股:季/年財報 [(Timestamp, 美元)],沒有月營收。"""
    qt = [{"p": d.strftime("%Y-%m"), "rev": yi(v), "yoy": pct(v, q[i - 4][1]) if i >= 4 else None,
           "qoq": pct(v, q[i - 1][1]) if i >= 1 else None} for i, (d, v) in enumerate(q)]
    yt = [{"p": d.strftime("%Y"), "rev": yi(v), "yoy": pct(v, a[i - 1][1]) if i >= 1 else None} for i, (d, v) in enumerate(a)]
    return {"m": [], "q": qt[-8:], "y": yt[-6:], "high": False} if qt or yt else None


def fetch_rev_tw(code):
    """FinMind 公開資料(免費,未登入有每小時次數限制):台股月營收歷史。"""
    import requests
    p = {"dataset": "TaiwanStockMonthRevenue", "data_id": code, "start_date": "2014-01-01"}
    if os.environ.get("FINMIND_TOKEN"):
        p["token"] = os.environ["FINMIND_TOKEN"]
    for k in range(2):
        try:
            r = requests.get("https://api.finmindtrade.com/api/v4/data", params=p, timeout=30)
            r.raise_for_status()
            months = [(f'{x["revenue_year"]}-{int(x["revenue_month"]):02d}', float(x["revenue"]))
                      for x in (r.json().get("data") or []) if x.get("revenue") is not None]
            return tables_from_months(months)
        except Exception as e:
            print(f"{code} 月營收第 {k + 1} 次失敗:{e}", file=sys.stderr)
            time.sleep(2)
    return None


def fetch_rev_us(sym):
    """Yahoo 財報:季、年營收(通常只有近 4–5 季、4 年)。"""
    import yfinance as yf
    try:
        t = yf.Ticker(sym)

        def series(df):
            if df is None or df.empty or "Total Revenue" not in df.index:
                return []
            return sorted((pd.Timestamp(i), float(v)) for i, v in df.loc["Total Revenue"].dropna().items())

        return us_tables(series(t.quarterly_income_stmt), series(t.income_stmt))
    except Exception as e:
        print(f"{sym} 財報失敗:{e}", file=sys.stderr)
        return None


def sample_rev(sym, tw):
    r = random.Random("rev" + sym)
    if tw:
        last = mi(dt.datetime.now(TPE).strftime("%Y-%m")) - 1
        base = r.uniform(5e9, 3e11)
        return tables_from_months([(ymstr(last - 119 + k), base * (1 + .012 * k) * (1 + (r.random() - .5) * .25)) for k in range(120)])
    qd = [pd.Timestamp("2026-09-30") - pd.DateOffset(months=3 * k) for k in range(7, -1, -1)]
    b = r.uniform(5e9, 3e10)
    return us_tables([(d, b * (1 + .03 * k) * (1 + (r.random() - .5) * .1)) for k, d in enumerate(qd)],
                     [(pd.Timestamp(f"{y}-12-31"), b * 4 * (1 + .12 * (y - 2022))) for y in range(2022, 2026)])


def fetch_news_tw(code, name):
    """Google 新聞(台灣版)RSS:台股相關中文新聞。"""
    import requests
    q = urllib.parse.quote_plus(f"{code} {name}".strip())
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        out = []
        for it in ET.fromstring(r.content).iter("item"):
            title, link = it.findtext("title"), it.findtext("link")
            if not (title and link):
                continue
            src = it.find("source")
            try:
                d = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(TPE).strftime("%m/%d")
            except Exception:
                d = ""
            out.append({"title": title, "link": link, "pub": src.text if src is not None and src.text else "", "d": d})
            if len(out) >= 8:
                break
        return out
    except Exception as e:
        print(f"{code} 台灣新聞失敗:{e}", file=sys.stderr)
        return []


def rev_info(item, hist, fb_yoy):
    """營收盾牌等級:優先用月營收歷史(含創歷史新高),其次證交所公開資料,手動欄位 rev_yoy / rev_high 可覆蓋。"""
    yoy = item.get("rev_yoy")
    if yoy is None and hist and hist["m"]:
        yoy = hist["m"][-1]["yoy"]
    if yoy is None:
        yoy = fb_yoy
    high = bool(item.get("rev_high")) or bool(hist and hist.get("high"))
    if yoy is None and not high:
        return None
    lv = "LEVEL_1" if high else "LEVEL_2" if yoy >= 100 else "LEVEL_3" if yoy >= 30 else "FILTER_OUT"
    return {"yoy": None if yoy is None else round(yoy, 1), "level": lv}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="產生範例資料(不連網)")
    ap.add_argument("--if-stale", action="store_true", help="今天已更新就跳過(備援排程用)")
    a = ap.parse_args()
    now = dt.datetime.now(TPE)
    old = load(OUT, {})
    if a.if_stale and old.get("updated_date") == now.strftime("%Y-%m-%d") and not old.get("sample"):
        print("今天已更新,跳過")
        return
    cfg = load(ROOT / "holdings.json", {})
    H, W = cfg.get("holdings", []), cfg.get("watchlist", [])
    hmap = {h["sym"]: h for h in H}
    items = {**{w["sym"]: w for w in W}, **hmap}
    syms = [(h["sym"], h.get("cost", 100)) for h in H] + [(w["sym"], 100) for w in W if w["sym"] not in hmap]
    rev = {} if a.sample or not any(s.endswith((".TW", ".TWO")) for s, _ in syms) else fetch_revenue()

    def get(sym, base):
        return (sample_df(sym, base), []) if a.sample else fetch_real(sym)

    ok, indices, stocks = 0, {}, {}
    for sym, name, m, base in INDICES:
        df, _ = get(sym, base)
        if df is not None:
            ok += 1
            indices[sym] = {"name": name, "m": m, "bars": to_bars(df), "wk": to_bars(resample(df, "W-FRI"), 130),
                            "mo": to_bars(resample(df, "ME"), 60), "regime": regime(df), "rg": regime_info(df),
                            "asof": df.index[-1].strftime("%Y-%m-%d")}
        elif sym in old.get("indices", {}):
            indices[sym] = old["indices"][sym]
    for sym, base in syms:
        df, nw = get(sym, base)
        if df is not None:
            ok += 1
            tw = sym.endswith((".TW", ".TWO"))
            e = {"bars": to_bars(df), "wk": to_bars(resample(df, "W-FRI"), 130), "mo": to_bars(resample(df, "ME"), 60), "news": nw, "asof": df.index[-1].strftime("%Y-%m-%d"), "sig": None}
            code, item, prev_e = sym.split(".")[0], items.get(sym, {}), old.get("stocks", {}).get(sym, {})
            rv = sample_rev(sym, tw) if a.sample else (fetch_rev_tw(code) if tw else fetch_rev_us(sym))
            e["rev"] = rv or prev_e.get("rev")
            nm = item.get("name", "")
            if tw:
                nt = [{"title": "(範例)新聞標題", "link": "#", "pub": "範例", "d": "11/11"}] if a.sample else fetch_news_tw(code, "" if nm == sym else nm)
                e["news_tw"] = nt or prev_e.get("news_tw", [])
            try:
                e["sig"] = scan(df, tw)
                ri = rev_info(item, e["rev"], rev.get(code)) if tw else None
                if ri:
                    e["sig"]["rev"] = ri
                if sym in hmap:
                    e["exit"] = exit_check(df, hmap[sym])
            except Exception as ex:
                print(f"{sym} 策略計算失敗:{ex}", file=sys.stderr)
            stocks[sym] = e
        elif sym in old.get("stocks", {}):
            stocks[sym] = old["stocks"][sym]
    if ok == 0:
        sys.exit("全部抓取失敗,保留舊資料")
    data = {"updated": now.strftime("%Y-%m-%d %H:%M"), "updated_date": now.strftime("%Y-%m-%d"),
            "sample": a.sample, "holdings": H, "watchlist": W, "indices": indices, "stocks": stocks}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"完成:{ok} 檔成功,更新時間 {data['updated']}")


if __name__ == "__main__":
    main()
