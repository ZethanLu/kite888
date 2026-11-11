"""策略掃描與出場評估(依規格書:風度圖、四大策略 A–D、放風箏出場)。"""
import operator as op

import pandas as pd


def macd_osc(s, fast=12, slow=26, sig=9):
    dif = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    return dif - dif.ewm(span=sig, adjust=False).mean()


def resample(df, rule):
    try:
        g = df.resample(rule)
    except ValueError:  # 舊版 pandas 的月底代碼是 "M"
        g = df.resample({"ME": "M"}.get(rule, rule))
    return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                         "close": g["close"].last(), "volume": g["volume"].sum()}).dropna()


def at(s, i=-1):
    try:
        v = s.iloc[i]
        return None if pd.isna(v) else float(v)
    except IndexError:
        return None


def cmp(a, b, f):
    return False if a is None or b is None else bool(f(a, b))


def prep(df):
    w, m = resample(df, "W-FRI"), resample(df, "ME")
    c = df["close"]
    return {"w": w, "m": m, "sma": {n: c.rolling(n).mean() for n in (5, 20, 60, 120, 240)},
            "dosc": macd_osc(c), "wosc": macd_osc(w["close"]), "mosc": macd_osc(m["close"]),
            "wsma5": w["close"].rolling(5).mean(), "turn": c * df["volume"]}


def regime_info(df):
    """風度圖(依課程圖示):指數是否在 20MA 之上(>=),加上當日 MACD 柱是否大於前一交易日。
    >=20MA 且柱變大=強風;>=20MA 且柱變小=亂流;<20MA 且柱變大=陣風;<20MA 且柱變小=無風。"""
    c = df["close"]
    if len(c) < 30:
        return None
    osc, ma20 = macd_osc(c), c.rolling(20).mean()
    above, rising = bool(c.iloc[-1] >= ma20.iloc[-1]), bool(osc.iloc[-1] > osc.iloc[-2])
    state = {(True, True): "STRONG_WIND", (True, False): "CHOPPY", (False, True): "GUST", (False, False): "NO_WIND"}[(above, rising)]
    return {"state": state, "close": round(float(c.iloc[-1]), 2), "ma20": round(float(ma20.iloc[-1]), 2),
            "osc": round(float(osc.iloc[-1]), 3), "osc_prev": round(float(osc.iloc[-2]), 3)}


def regime(df):
    i = regime_info(df)
    return i["state"] if i else "CHOPPY"


def surge(m, n):
    mm = m.tail(n)
    return bool(((mm["close"] - mm["open"]) / mm["open"] > 0.3).any())


def scan(df, tw=True):
    """四大策略的進場條件掃描(盤後)。美股略過 1 億台幣成值門檻。"""
    P = prep(df)
    c, sma = at(df["close"]), P["sma"]
    up = lambda s: cmp(at(s), at(s, -2), op.gt)
    dn = lambda s: cmp(at(s), at(s, -2), op.lt)
    turn, avg15 = at(P["turn"]), float(P["turn"].tail(15).mean())
    liq = (turn is not None and turn >= 1e8) if tw else True
    liq15 = (avg15 >= 1e8) if tw else True
    note = "" if tw else "(美股略過)"
    wo, do = at(P["wosc"]), at(P["dosc"])
    S = {
        "A": [["4 個月內曾單月漲幅 > 30%", surge(P["m"], 4)], ["月 OSC 向上", up(P["mosc"])],
              ["收盤 > 年線(240MA)", cmp(c, at(sma[240]), op.gt)], ["單日成值 ≥ 1 億" + note, liq]],
        "B": [["6 個月內曾單月漲幅 > 30%", surge(P["m"], 6)], ["週 OSC 拉回(本週 < 上週)", dn(P["wosc"])],
              ["季線(60MA)上揚", up(sma[60])],
              ["收盤在半年線之上、月線之下", cmp(at(sma[120]), c, op.lt) and cmp(c, at(sma[20]), op.lt)],
              ["單日成值 ≥ 1 億" + note, liq]],
        "C": [["週 OSC 向上", up(P["wosc"])],
              ["週 OSC 由負翻正", cmp(wo, 0, op.gt) and cmp(at(P["wosc"], -2), 0, op.lt)],
              ["單日成值 ≥ 1 億" + note, liq]],
        "D": [["週 OSC > 0 且向上", cmp(wo, 0, op.gt) and up(P["wosc"])],
              ["日 OSC > 0 且向上", cmp(do, 0, op.gt) and up(P["dosc"])],
              ["均線多頭排列 5MA > 20MA > 60MA", cmp(at(sma[5]), at(sma[20]), op.gt) and cmp(at(sma[20]), at(sma[60]), op.gt)],
              ["近 15 日均成值 ≥ 1 億" + note, liq15]],
    }
    out = {k: {"ok": all(x[1] for x in v), "c": v} for k, v in S.items()}
    s5 = at(sma[5])
    out.update(near5=bool(s5 and c and abs(c / s5 - 1) <= 0.01),
               turn=round(turn / 1e8, 2) if turn else 0, avg15=round(avg15 / 1e8, 2))
    return out


STOP_RULES = [
    ("m_osc_down", "月 MACD 柱體比上月差"),
    ("w_osc_down", "週 MACD 柱體比上週差(週五判斷)"),
    ("w_close_down", "週收盤比上週差(週五判斷)"),
    ("below_w5", "收盤跌破 5 週均線"),
    ("below_ma20", "收盤跌破 20 日線"),
    ("below_ma60", "收盤跌破 60 日線(季線)"),
    ("below_ma120", "收盤跌破 120 日線(半年線)"),
    ("below_prev_wlow", "收盤跌破上週最低點"),
    ("below_entry_low", "收盤跌破進場日低點"),
    ("loss5", "虧損達 -5%"),
]
TP_RULES = [
    ("w_close_down", "週收盤比上週差(週五判斷)"),
    ("w_high_fail", "當週最高價未創前週新高(週五判斷)"),
    ("w_osc_down", "週 MACD 柱體比上週差(週五判斷)"),
    ("m_osc_down", "月 MACD 柱體比上月差"),
    ("below_ma5", "收盤跌破 5 日線"),
    ("below_w5", "收盤跌破 5 週均線"),
    ("fall_back", "獲利跌回門檻(保本)"),
]
LABEL = {"stop": dict(STOP_RULES), "tp": dict(TP_RULES)}


def exit_check(df, h):
    """持股出場評估。
    1) 策略 A–D 的內建規則(有指定策略時);
    2) 自訂規則 rules:{"stop":[...], "tp":[...]}。停損規則隨時有效,停利規則在最高損益達門檻(tp_after,預設 10%)後才啟用;
    3) 能換算成價位的規則會輸出動態停損價/動態停利價(跌破即出場的位置)。"""
    st = h.get("strategy") or None
    thr = float(h.get("tp_after") or 10) / 100
    P = prep(df)
    c, cost = at(df["close"]), float(h["cost"])
    pnl = c / cost - 1
    seg, ed = None, h.get("entry_date")
    if ed:
        try:
            seg = df.loc[ed:]
        except Exception:
            seg = None
        if seg is not None and len(seg) == 0:
            seg = None
    maxp = max(pnl, float(seg["close"].max() / cost - 1)) if seg is not None else pnl
    days = len(seg) - 1 if seg is not None else None
    elow = float(seg["low"].iloc[0]) if seg is not None else None
    fri = df.index[-1].weekday() == 4
    w = P["w"]
    prev_fri = at(w["close"], -2)
    wk_fail = len(w) >= 2 and float(w["high"].iloc[-1]) <= float(w["high"].iloc[-2])
    wdn = cmp(at(P["wosc"]), at(P["wosc"], -2), op.lt)
    mdn = cmp(at(P["mosc"]), at(P["mosc"], -2), op.lt)
    wk5, d5, s120 = at(P["wsma5"]), at(P["sma"][5]), at(P["sma"][120])
    kite = maxp >= thr
    tp_txt = f"{thr * 100:g}%"
    r = []
    if kite:
        if pnl <= thr:
            r.append(f"獲利跌回 +{tp_txt},保本出場")
        if fri and cmp(c, prev_fri, op.lt):
            r.append("週五收盤低於上週五")
        if st in ("A", "B") and ((fri and wdn) or mdn):
            r.append("週/月 OSC 柱體轉弱")
        if st in ("C", "D") and fri and wk_fail:
            r.append("當週最高價未再創前週新高")
        if st == "C" and cmp(c, wk5, op.lt):
            r.append("跌破 5 週均線")
        if st == "D" and cmp(c, d5, op.lt):
            r.append("跌破 5 日線")
    else:
        if st in ("A", "B") and mdn:
            r.append("月 OSC 柱體轉向下")
        if st == "B" and cmp(c, s120, op.lt):
            r.append("跌破半年線(120MA)")
        if st == "C":
            if fri and wdn:
                r.append("週五週 OSC 柱體縮短")
            if fri and cmp(c, prev_fri, op.lt):
                r.append("週五收盤低於上週五")
            if cmp(c, wk5, op.lt):
                r.append("收盤跌破 5 週均線")
        if st == "D":
            if pnl <= -0.05:
                r.append("固定停損 -5%")
            if days is not None and days >= 3 and pnl <= 0:
                r.append("持股滿 3 個交易日仍未上漲")
            if elow is not None and c < elow:
                r.append("跌破進場日低點")
    stop, tp = float(h.get("stop") or 0), float(h.get("tp") or 0)
    if stop and c <= stop:
        r.append("跌破預計停損價")
    if tp and c >= tp:
        r.append("已達預計停利價")

    # ---- 自訂規則與動態價位 ----
    level = {"below_w5": wk5, "below_ma5": d5, "below_ma20": at(P["sma"][20]), "below_ma60": at(P["sma"][60]),
             "below_ma120": s120, "below_prev_wlow": at(w["low"], -2), "below_entry_low": elow,
             "loss5": cost * 0.95, "w_close_down": prev_fri, "fall_back": cost * (1 + thr)}
    hit = {"m_osc_down": mdn, "w_osc_down": fri and wdn, "w_close_down": fri and cmp(c, prev_fri, op.lt),
           "w_high_fail": fri and wk_fail, "fall_back": pnl <= thr, "loss5": pnl <= -0.05}
    for k in ("below_w5", "below_ma5", "below_ma20", "below_ma60", "below_ma120", "below_prev_wlow", "below_entry_low"):
        hit[k] = cmp(c, level[k], op.lt)
    rules = h.get("rules") or {}
    custom, lv = {"stop": [], "tp": []}, {"stop": [], "tp": []}
    for kind in ("stop", "tp"):
        active = True if kind == "stop" else kite
        for rid in rules.get(kind) or []:
            if rid not in LABEL[kind]:
                continue
            lvl = level.get(rid)
            trig = bool(active and hit.get(rid))
            custom[kind].append({"id": rid, "label": LABEL[kind][rid], "hit": trig, "active": active,
                                 "level": None if lvl is None else round(lvl, 2)})
            if active and lvl is not None:
                lv[kind].append((lvl, LABEL[kind][rid]))
            if trig:
                r.append(("自訂停損:" if kind == "stop" else "自訂停利:") + LABEL[kind][rid])

    def pick(items):
        if not items:
            return None
        price, by = max(items, key=lambda x: x[0])
        return {"price": round(price, 2), "by": by}

    dyn = None
    if custom["stop"] or custom["tp"]:
        dyn = {"thr": round(thr * 100, 2), "kite": kite, "stop": pick(lv["stop"]), "tp": pick(lv["tp"]), "custom": custom}
    return {"strategy": st, "mode": "kite" if kite else "guard", "pnl": round(pnl * 100, 2),
            "maxpnl": round(maxp * 100, 2), "days": days, "reasons": r, "state": "EXIT" if r else "HOLD", "dyn": dyn}
