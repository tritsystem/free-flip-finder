"""Checks for the classifier/scorer.  python test_engines.py"""
import datetime as dt
import engines

NOW = dt.datetime.now(dt.timezone.utc).isoformat()


def item(title, body="", lat=38.73, lon=-120.80, posted=NOW):
    return {"title": title, "body": body, "attrs": {}, "lat": lat, "lon": lon, "posted": posted}


CASES = [
    # (title, body, expect_type or None, expect_verdict or None)
    ("Wood (Free)", "bringing a chain saw is a good idea", None, None),
    ("13 foot boat and trailer", "You could put an outboard on it", None, None),
    ("Jeep Cherokee 4.0L engine", "spare parts", None, None),
    ("Free Ryobi 40V cordless mower", "battery not included", "Electric / battery push / self-propelled mower", None),
    # appliances / AC / electronics / tools
    ("Free pressure washer", "runs", "Pressure washer", None),           # not "Washing machine"
    ("Whirlpool washer", "won't drain, otherwise works", "Washing machine", None),
    ("Washer and dryer", "free, you haul", "Washer + dryer set", None),
    ("Window AC unit 8000 BTU", "doesn't cool anymore", "Window AC", None),
    ("Portable air conditioner", "needs freon recharge", "Portable AC", "Parts / scrap"),
    ("Samsung 55\" TV", "screen is cracked", "Flat-screen TV", "Parts / scrap"),
    ("Vizio 50 inch TV", "sound but no picture", "Flat-screen TV", None),
    ("Marantz receiver", "works, one channel crackles", "Stereo / hi-fi audio", None),
    ("TV stand", "solid oak", None, None),                              # furniture, not a TV
    ("Xbox 360", "red ring", "Game console", None),
    ("Microwave", "works", "Microwave", None),
    ("Air compressor", "won't build pressure", "Air compressor", None),
    ("Mini fridge", "works great", "Mini fridge / beverage cooler", None),
    ("Range hood and fan - parts only", "", None, None),
    ("Free stuff - moving", "old microwave, couch, boxes", "Microwave", None),
    # real titles from the 2026-10-16 live run that were misclassified
    ("Nintendo Switch 2 Scammer. Do not send money!", "", None, None),
    ("Channel Master TV Antenna Rotator, Controller", "", None, None),
    ("Computer repair", "I fix computers", None, None),
    ("Coleman RV air conditioner covers", "", None, None),
    ("Free New Vacuum Filters for Shark Upright", "", None, None),
    ("Moving boxes 4 u free.", "boxes from our move, dryer box too", None, None),
    ("Over range microwave, doesn't work", "", "Microwave", None),
    ("Free 19 inch PC monitor", "", "Computer monitor", None),
    ("1977 Yamaha E70 Electone Synthesizer Organ - Free", "", "Electric organ", None),
    ("Honda GCV160 self propelled mower", "runs great, just needs blade sharpened", "Push / self-propelled mower", "Grab it"),
    ("Free mower", "engine is seized", "Push / self-propelled mower", "Parts / scrap"),
    ("Craftsman tiller", "sat for 2 years, needs carb cleaned", "Tiller / cultivator", None),
    ("Free stuff - garage cleanout", "old chainsaw, boxes, lamps", "Chainsaw", None),
    ("Generac 7500 watt generator", "won't start", "Generator", None),
    ("Weed eater", "doesn't run", "String trimmer / edger", None),
]


def main():
    bad = 0
    for title, body, etype, verdict in CASES:
        a = engines.analyze(item(title, body))
        got_t = a["type"] if a else None
        ok = got_t == etype and (verdict is None or (a and a["verdict"] == verdict))
        bad += not ok
        print("%s  %-40s type=%s verdict=%s" % ("PASS" if ok else "FAIL", title[:40], got_t, a and a["verdict"]))
    # runs must not fire on "doesn't run"
    a = engines.analyze(item("Weed eater", "doesn't run"))
    ok = a["condition"] != "Runs"
    bad += not ok
    print("%s  'doesn't run' is not 'Runs' (condition=%s)" % ("PASS" if ok else "FAIL", a["condition"]))
    # fault rules are scoped to their group: "won't drain" is an appliance fault
    a = engines.analyze(item("Whirlpool washer", "won't drain, otherwise works"))
    ok = "drain" in a["condition"].lower() and a["fix_high"] > 15
    bad += not ok
    print("%s  washer drain fault kept despite 'works' (condition=%s)" % ("PASS" if ok else "FAIL", a["condition"]))
    a = engines.analyze(item("Window AC unit 8000 BTU", "doesn't cool anymore"))
    ok = a["specs"].get("Cooling") == "8000 BTU" and a["verdict"] != "Parts / scrap"
    bad += not ok
    print("%s  AC BTU parsed, 'doesn't cool' still fixable (%s, %s)" % ("PASS" if ok else "FAIL", a["specs"].get("Cooling"), a["verdict"]))
    a = engines.analyze(item("Marantz receiver", "works"))
    b = engines.analyze(item("Onkyo receiver", "works"))
    ok = a["resale_high"] > b["resale_high"]
    bad += not ok
    print("%s  Marantz > Onkyo resale (%d > %d)" % ("PASS" if ok else "FAIL", a["resale_high"], b["resale_high"]))
    a = engines.analyze(item("Kenmore refrigerator", "works"))
    ok = a["heavy"] and any("truck" in w for w in a["why"])
    bad += not ok
    print("%s  fridge flagged heavy" % ("PASS" if ok else "FAIL"))
    a = engines.analyze(item("Whirlpool fridge (not getting cold )",
                             "just stopped getting cold. The air blows but compressor don�t seem to be working."))
    ok = "Compressor" in a["condition"] and "cool" in a["condition"]
    bad += not ok
    print("%s  fridge: not getting cold + compressor fault (%s)" % ("PASS" if ok else "FAIL", a["condition"]))
    # generator watt tiers
    a = engines.analyze(item("Generac 7500 watt generator", "runs"))
    ok = a["specs"].get("Watts") == "7500 W" and a["resale_low"] >= 350
    bad += not ok
    print("%s  generator watts parsed (%s, $%d-%d)" % ("PASS" if ok else "FAIL", a["specs"].get("Watts"), a["resale_low"], a["resale_high"]))
    # farther + older must score lower than identical close + fresh
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20)).isoformat()
    near = engines.analyze(item("Toro mower", "runs"))
    far = engines.analyze(item("Toro mower", "runs", lat=38.58, lon=-121.49, posted=old))
    ok = far["score"] < near["score"]
    bad += not ok
    print("%s  far+old scores lower (%d < %d)" % ("PASS" if ok else "FAIL", far["score"], near["score"]))
    print("\n%d failed" % bad)
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
