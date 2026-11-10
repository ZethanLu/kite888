#!/usr/bin/env python3
"""抓取 Yahoo Finance 日 K 與新聞,計算策略訊號,輸出 data/data.json。
用法:python scripts/fetch_data.py [--sample] [--if-stale]
"""
import argparse, datetime as dt, json, math, pathlib, random, sys, time

import pandas as pd

from strategy import exit_check, regime, scan

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "data.json"
TPE = dt.timezone(dt.timedelta(hours=8))
KEEP = 260  # 網頁只保留近 260 根日 K;策略用 5 年資料計算
INDICES = [("^TWII", "加權指數", "TW", 23400), ("^TWOII", "櫃買指數", "TW", 268),
           ("^GSPC", "S&P 500", "US", 5900), ("^IXIC", "那斯達克", "US", 19300)]


def load(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def to_bars(df):
    return [[int(pd.Timestamp(i.date()).timestamp()), round(float(r.open), 2), round(float(r.high), 2),
             round(float(r.low), 2), round(float(r.close), 2), int(r.volume)] for i, r in df.tail(KEEP).iterrows()]


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


def rev_info(item, code, rev):
    yoy = item.get("rev_yoy", rev.get(code))
    high = bool(item.get("rev_high"))
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
            indices[sym] = {"name": name, "m": m, "bars": to_bars(df), "regime": regime(df),
                            "asof": df.index[-1].strftime("%Y-%m-%d")}
        elif sym in old.get("indices", {}):
            indices[sym] = old["indices"][sym]
    for sym, base in syms:
        df, nw = get(sym, base)
        if df is not None:
            ok += 1
            tw = sym.endswith((".TW", ".TWO"))
            e = {"bars": to_bars(df), "news": nw, "asof": df.index[-1].strftime("%Y-%m-%d"), "sig": None}
            try:
                e["sig"] = scan(df, tw)
                ri = rev_info(items.get(sym, {}), sym.split(".")[0], rev) if tw else None
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
