#!/usr/bin/env python3
"""Gate for markets-feed.json (Natwill Markets & Macro board v5.2).

Usage: python3 validate.py NEW.json [PRIOR.json]
Exit 0 = safe to push. Exit 1 = do not push (board keeps last good payload).
Never edit this file from the refresh task. Only Josh changes the contract.
"""
import json, re, sys
from datetime import datetime, timedelta, timezone

KEYS = ["GENERATED_AT", "UNDERWRITING_BASE_10YR", "LOCK_TRIGGER_BP", "TAPE_TIMES",
        "TAPE_NOTES", "MARKS", "UST10", "SPX", "QUOTES", "CURVE", "CURVE_2S10S",
        "ODDS", "FOMC", "TRIPWIRES", "DESK", "EVENTS", "CALL_LINES", "FOOT_LINE"]
FORBIDDEN_KEYS = {"OPEN_QUOTES", "DEALS"}
# Anything that identifies a Natwill deal, counterparty, or property stays out of the public feed.
PRIVATE_TERMS = ["Lument", "Orchard", "4931", "Rivers Ave", "Rivers matures", "Glasshouse", "Dropbox",
                 "Savannah", "Clemson", "Charleston", "Hines", "Ares", "Morgan Stanley", "CBRE",
                 "Natwill", "Pearl", "Newton", "Doug", "Erin", "Chrystal", "Adguary", "Varsity"]
DESK_LOCAL_SYMS = {"Underwrite at", "Agency SBL spread"}
QUOTE_SYMS = ["S&P 500", "NASDAQ", "DOW", "WTI", "VIX", "10-YR", "2-YR", "30-YR"]

errs, warns = [], []
def err(m): errs.append(m)
def warn(m): warns.append(m)

def main():
    new_path = sys.argv[1]
    prior_path = sys.argv[2] if len(sys.argv) > 2 else None
    raw = open(new_path, encoding="utf-8").read()
    try:
        d = json.loads(raw)
    except Exception as e:
        print("FAIL: not valid JSON:", e); sys.exit(1)
    prior = None
    if prior_path:
        try: prior = json.load(open(prior_path, encoding="utf-8"))
        except Exception as e: warn("prior payload unreadable (%s); history checks skipped" % e)

    # keys
    if list(d.keys()) != KEYS:
        missing = [k for k in KEYS if k not in d]; extra = [k for k in d if k not in KEYS]
        if missing: err("missing keys: %s" % missing)
        if extra: err("unexpected keys: %s" % extra)
        if not missing and not extra: err("keys out of contract order")
    for k in FORBIDDEN_KEYS & set(d): err("forbidden key in public feed: %s" % k)

    # privacy + typography
    if "—" in raw: err("em-dash present (never allowed)")
    for t in PRIVATE_TERMS:
        if re.search(r"\b" + re.escape(t) + r"\b", raw, re.I): err("private term in public feed: %s" % t)
    for r in d.get("DESK", []):
        if r.get("sym") in DESK_LOCAL_SYMS or r.get("local"): err("local DESK row in feed: %s" % r.get("sym"))
    for e in d.get("EVENTS", []):
        if e.get("local"): err("local EVENT in feed: %s" % e.get("label"))

    # stable constants
    if d.get("UNDERWRITING_BASE_10YR") != 4.50: err("UNDERWRITING_BASE_10YR must be 4.50")
    if d.get("LOCK_TRIGGER_BP") != 20: err("LOCK_TRIGGER_BP must be 20")
    if prior and d.get("TRIPWIRES") != prior.get("TRIPWIRES"): err("TRIPWIRES changed (only Josh changes them)")

    # timestamp
    ga = d.get("GENERATED_AT", "")
    try:
        t = datetime.fromisoformat(ga)
        if t.tzinfo is None: err("GENERATED_AT has no UTC offset")
        else:
            now = datetime.now(timezone.utc)
            if t > now + timedelta(minutes=5): err("GENERATED_AT is in the future")
            if t < now - timedelta(hours=36): warn("GENERATED_AT older than 36h")
            if prior and "GENERATED_AT" in prior:
                try:
                    if t < datetime.fromisoformat(prior["GENERATED_AT"]): err("GENERATED_AT moved backwards")
                except Exception: pass
    except Exception:
        err("GENERATED_AT not ISO-8601: %r" % ga)

    # tape parity
    tt, tn = d.get("TAPE_TIMES", []), d.get("TAPE_NOTES", [])
    u, s = d.get("UST10", {}), d.get("SPX", {})
    ut, st = u.get("tape", []), s.get("tape", [])
    if not (len(tt) == len(tn) == len(ut) == len(st)):
        err("tape parity: TAPE_TIMES %d, TAPE_NOTES %d, UST10.tape %d, SPX.tape %d" % (len(tt), len(tn), len(ut), len(st)))
    if len(tt) == 0: err("empty tape")
    for i in range(1, len(ut)):
        if abs(ut[i] - ut[i-1]) > 0.15: err("UST10 tape jump over 15 bp at %s" % tt[i] if i < len(tt) else i)
    for x in ut:
        if not isinstance(x, (int, float)) or not (2.0 < x < 8.0): err("UST10 tape value out of range: %r" % x)

    # hero parity
    close = u.get("close")
    if not isinstance(close, (int, float)): err("UST10.close missing")
    else:
        if d.get("CURVE", {}).get("ten") != close: err("CURVE.ten != UST10.close")
        want = "%.2f%%" % (int(close * 100 + 0.5) / 100.0)  # matches the board's Math.round(close*100)/100
        if u.get("display") != want: err("UST10.display %r != %r" % (u.get("display"), want))
        if ut and abs(ut[-1] - round(close, 2)) > 0.011: err("last UST10 tape pulse differs from close")
    if not isinstance(s.get("close"), (int, float)): err("SPX.close missing")
    if st and isinstance(s.get("close"), (int, float)) and abs(st[-1] - s["close"]) > 0.6:
        err("last SPX tape pulse differs from close")

    # as_of everywhere
    def need_asof(obj, label):
        if not isinstance(obj, dict) or not obj.get("as_of"): err("%s missing as_of" % label)
    need_asof(u, "UST10"); need_asof(s, "SPX"); need_asof(d.get("CURVE"), "CURVE"); need_asof(d.get("CURVE_2S10S"), "CURVE_2S10S")
    for m in d.get("MARKS", []): need_asof(m, "MARKS[%s]" % m.get("label"))
    need_asof(d.get("ODDS", {}).get("fedwatch"), "ODDS.fedwatch")
    syms = [q.get("sym") for q in d.get("QUOTES", [])]
    for q in d.get("QUOTES", []): need_asof(q, "QUOTES[%s]" % q.get("sym"))
    for want in QUOTE_SYMS:
        if want not in syms: err("QUOTES missing %s" % want)

    # curve history: append-only, one per day
    hist = d.get("CURVE_2S10S", {}).get("history", [])
    if prior:
        ph = prior.get("CURVE_2S10S", {}).get("history", [])
        if hist[:len(ph)] != ph: err("CURVE_2S10S.history rewritten (must be append-only)")
        if len(hist) > len(ph) + 1: err("CURVE_2S10S.history grew by more than one point")
    c = d.get("CURVE", {})
    if isinstance(c.get("two"), (int, float)) and isinstance(c.get("ten"), (int, float)) and hist:
        spread_bp = round((c["ten"] - c["two"]) * 100)
        if abs(hist[-1] - spread_bp) > 1.5: warn("last 2s10s history point %s vs computed %s bp" % (hist[-1], spread_bp))

    # voice
    if len(d.get("CALL_LINES", [])) != 3: err("CALL_LINES must be exactly 3")
    for l in d.get("CALL_LINES", []):
        if not isinstance(l, str) or len(l) < 40: err("call line too short: %r" % l)
    if not isinstance(d.get("FOOT_LINE"), str) or not d["FOOT_LINE"]: err("FOOT_LINE missing")

    for w in warns: print("WARN:", w)
    if errs:
        for e in errs: print("FAIL:", e)
        sys.exit(1)
    print("PASS: %d keys, %d pulses, close %s, generated %s" % (len(d), len(tt), close, ga))

if __name__ == "__main__":
    main()
