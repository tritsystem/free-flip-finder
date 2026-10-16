"""Free fix-and-flip finder for Placerville, CA.

Pulls free listings from Craigslist (free section, all areas within RADIUS_MI
of Placerville) and the nearby Freecycle groups, keeps the things worth
repairing - small engines, electronics, appliances, AC units, power tools -
scores each one for fix-and-resell value, and writes a static site:

    docs/index.html     phone-friendly spreadsheet (works on iOS 15 Safari)
    docs/listings.csv   same rows, opens in Numbers/Excel
    docs/listings.json  machine-readable

Stdlib only.  Run:  python engines.py            (fetch + build)
                    python engines.py --offline  (rebuild from cached state)

The resale/fix numbers are rule-of-thumb ESTIMATES written into the tables
below, not measured prices.  Every row links to eBay sold listings so the
estimate can be checked before driving anywhere.
"""
import csv
import datetime as dt
import html
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "data", "state.json")
SITE = os.path.join(HERE, "docs")

HOME = (38.7296, -120.7985)  # Placerville, CA 95667
RADIUS_MI = 50
COST_PER_MILE = 0.25         # rough gas cost per driven mile (estimate)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"}

CL_TERMS = [
    # small engines
    "mower", "lawnmower", "generator", "tiller", "chainsaw", "pressure washer",
    "snowblower", "go kart", "minibike", "weed eater", "trimmer", "blower", "edger",
    "outboard", "small engine", "briggs", "log splitter", "chipper", "lawn tractor", "engine",
    # AC / climate
    "air conditioner", "ac unit", "window ac", "portable ac", "swamp cooler",
    "dehumidifier", "mini split", "pellet stove",
    # appliances
    "washer", "dryer", "refrigerator", "fridge", "freezer", "dishwasher", "stove", "oven",
    "microwave", "vacuum", "dyson", "kitchenaid", "espresso", "sewing machine",
    # electronics
    "tv", "television", "xbox", "playstation", "nintendo", "receiver", "amplifier", "stereo",
    "speakers", "turntable", "laptop", "computer", "monitor", "3d printer", "camera",
    "projector", "guitar amp", "synthesizer", "drone",
    # tools
    "air compressor", "welder", "table saw", "miter saw", "power tools", "drill",
]
FREECYCLE_GROUPS = ["PlacervilleCA", "CameronParkCA", "AuburnCA", "FolsomCA"]

GROUPS = {"engine": "Small engines", "climate": "AC & climate", "appliance": "Appliances",
          "electronics": "Electronics", "tools": "Power tools"}

# --- item table ---------------------------------------------------------------
# (group, key, label, title regex, resale_low, resale_high, heavy) - resale is
# the typical local used price when WORKING.  Rule-of-thumb estimates.
# Order matters: specific patterns first ("pressure washer" before "washer").
EQUIP = [
    # small engines
    ("engine", "riding", "Riding mower / lawn tractor",
     r"riding mower|lawn ?tractor|garden tractor|rider\b|zero[- ]turn|ztr\b", 300, 900, True),
    ("engine", "logsplit", "Log splitter", r"log ?splitter|wood ?splitter", 400, 1000, True),
    ("engine", "chipper", "Chipper / shredder", r"chipper|shredder(?!.*paper)", 200, 600, True),
    ("engine", "gokart", "Go-kart / minibike", r"go[- ]?kart|go[- ]?cart|mini ?bike|pit bike", 150, 500, False),
    ("engine", "outboard", "Outboard motor", r"outboard", 200, 900, False),
    ("engine", "generator", "Generator", r"generator|genny", 150, 500, False),
    ("engine", "pwasher", "Pressure washer", r"pressure ?washer|power ?washer", 80, 220, False),
    ("engine", "snow", "Snowblower", r"snow ?blower|snow ?thrower", 150, 450, False),
    ("engine", "tiller", "Tiller / cultivator", r"tiller|rototiller|cultivator|mantis", 100, 350, False),
    ("engine", "chainsaw", "Chainsaw", r"chain ?saw", 60, 220, False),
    ("engine", "bblower", "Backpack blower", r"backpack blower", 80, 200, False),
    ("engine", "blower", "Leaf blower", r"leaf ?blower|\bblower\b", 30, 100, False),
    ("engine", "trimmer", "String trimmer / edger",
     r"weed ?eater|weed ?wacker|weed ?whacker|string trimmer|\btrimmer\b|\bedger\b|brush ?cutter", 30, 100, False),
    ("engine", "pump", "Water / trash pump", r"water pump|trash pump|transfer pump", 80, 200, False),
    ("engine", "mower", "Push / self-propelled mower", r"lawn ?mower|\bmower\b|mower deck", 60, 200, False),
    ("engine", "engine", "Bare small engine",
     r"small engine|horizontal shaft|vertical shaft|\bgx\s?\d{3}|\bgcv\s?\d{3}|briggs|tecumseh|predator \d+|kohler", 40, 150, False),
    # AC / climate
    ("climate", "minisplit", "Mini-split AC / heat pump", r"mini[- ]?split|ductless", 200, 600, True),
    ("climate", "portac", "Portable AC", r"portable (ac|a/c|air ?conditioner)", 100, 250, False),
    ("climate", "windowac", "Window AC", r"window (ac|a/c|air ?conditioner|unit)", 60, 180, False),
    ("climate", "ac", "Air conditioner", r"air ?conditioner|\ba/?c\b(?! adapter| charger| power)|\bac unit", 60, 200, False),
    ("climate", "swamp", "Swamp / evaporative cooler", r"swamp cooler|evaporative cooler", 40, 150, False),
    ("climate", "dehum", "Dehumidifier", r"dehumidifier", 50, 150, False),
    ("climate", "pellet", "Pellet stove", r"pellet stove", 300, 900, True),
    # appliances
    ("appliance", "wdset", "Washer + dryer set", r"washer\s*(and|&|/|\+)\s*dryer|dryer\s*(and|&|/|\+)\s*washer", 200, 500, True),
    ("appliance", "washer", "Washing machine", r"washing machine|\bwasher\b(?! fluid)(?!s? and)", 100, 300, True),
    ("appliance", "dryer", "Clothes dryer", r"\bdryer\b(?! sheets)(?!.*hair)", 80, 250, True),
    ("appliance", "minifridge", "Mini fridge / beverage cooler",
     r"mini ?fridge|dorm fridge|compact fridge|kegerator|wine (fridge|cooler)|beverage (fridge|cooler)", 40, 150, False),
    ("appliance", "fridge", "Refrigerator", r"refrigerator|\bfridge\b(?! magnet)", 100, 400, True),
    ("appliance", "freezer", "Freezer", r"freezer", 100, 300, True),
    ("appliance", "dishwasher", "Dishwasher", r"dishwasher", 75, 200, True),
    ("appliance", "microwave", "Microwave", r"microwave", 20, 60, False),
    ("appliance", "range", "Range / stove / oven", r"\bstove\b|\brange\b(?! hood)|\boven\b|cooktop", 100, 350, True),
    ("appliance", "vacuum", "Vacuum", r"vacuum|dyson|roomba|kirby|shop ?vac", 30, 150, False),
    ("appliance", "mixer", "Stand mixer", r"kitchen ?aid|stand mixer", 80, 250, False),
    ("appliance", "blender", "Pro blender", r"vitamix|blendtec", 100, 250, False),
    ("appliance", "espresso", "Espresso / coffee machine", r"espresso|breville|coffee maker|keurig", 30, 200, False),
    ("appliance", "sewing", "Sewing machine", r"sewing machine|serger", 40, 200, False),
    # electronics
    ("electronics", "console", "Game console",
     r"xbox|playstation|\bps[1-5]\b|nintendo|\bwii\b|gamecube|game cube|\bsega\b|\bn64\b|game ?boy|\bswitch (console|lite|oled)", 60, 300, False),
    ("electronics", "organ", "Electric organ", r"\borgan\b|electone", 0, 50, True),
    ("electronics", "plasma", "Plasma TV", r"plasma", 20, 80, True),
    ("electronics", "crt", "CRT / tube TV", r"\bcrt\b|tube tv|tube television", 10, 80, True),
    ("electronics", "tv", "Flat-screen TV", r"\btvs?\b|television|flat ?screen|smart tv|\b\d{2}\s?(\"|in|inch)\s?(lcd|led|tv)", 50, 250, False),
    ("electronics", "projector", "Projector", r"projector", 40, 200, False),
    ("electronics", "audio", "Stereo / hi-fi audio",
     r"receiver|amplifier|\bamp\b|stereo|turntable|record player|speakers?|subwoofer|cassette deck|tape deck|"
     r"reel[- ]to[- ]reel|equalizer|cd player|hi-?fi", 40, 300, False),
    ("electronics", "music", "Music gear", r"guitar amp|amp head|synth|digital piano|mixing board|mixer board|pa system|keyboard amp", 60, 300, False),
    ("electronics", "monitor", "Computer monitor", r"\bmonitors?\b", 30, 120, False),
    ("electronics", "laptop", "Laptop", r"laptop|macbook|chromebook|notebook computer", 60, 300, False),
    ("electronics", "computer", "Desktop computer", r"computer|desktop pc|\bimac\b|gaming pc|\bpc\b", 50, 300, False),
    ("electronics", "3dprint", "3D printer", r"3d printer|ender ?3|prusa", 100, 300, False),
    ("electronics", "tablet", "Tablet / phone", r"\bipad\b|tablet|iphone|smart ?phone", 40, 200, False),
    ("electronics", "camera", "Camera", r"camera|dslr|camcorder|\blens\b", 40, 250, False),
    ("electronics", "drone", "Drone / RC", r"drone|rc car|rc truck|quadcopter", 40, 200, False),
    # tools
    ("tools", "compressor", "Air compressor", r"air compressor|\bcompressor\b", 60, 250, False),
    ("tools", "welder", "Welder", r"welder|welding machine|mig|plasma cutter", 100, 400, False),
    ("tools", "bigsaw", "Table / miter saw", r"table saw|miter saw|mitre saw|band ?saw|radial arm saw", 60, 300, True),
    ("tools", "powertool", "Power tool",
     r"\bdrill\b|circular saw|sander|grinder|\brouter\b(?!.*wifi)|nail gun|impact driver|jig ?saw|sawzall|reciprocating saw|power tools?", 20, 120, False),
]
# Things that look like engines but are not small engines.
NOT_SMALL = re.compile(
    r"\b(v6|v8|v-8|350|5\.7|4\.0l|transmission|chevy|chevrolet|ford|dodge|jeep|toyota|"
    r"nissan|subaru|pilot|civic|accord|suburban|silverado|truck|sedan|car parts|"
    r"for metal or parts|title|tittle|vin)\b", re.I)
# Not repair-and-resell items even though a keyword matches.
JUNK_TITLE = re.compile(r"\b(stands?|cabinets?|mounts?|brackets?|covers?|manuals?|box(es)? only|tv tray|entertainment center|"
                        r"remote only|cables?|cords?|wanted|iso|looking for|scam(mer)?s?|beware|warning|repair service|"
                        r"computer repair|antenna|rotator|filters?|bags|attachments?|accessor(y|ies)|"
                        r"moving boxes|boxes|parts for sale)\b", re.I)
GENERIC_TITLE = re.compile(r"free stuff|misc|garage|yard|curb|moving|clean ?out|everything|lots? of|tools|equipment|stuff", re.I)
ELECTRIC = re.compile(r"\b(electric|cordless|corded|battery[- ]powered|\d{2}v\b|\d{2} volt|ego\b|greenworks|worx)", re.I)
GAS = re.compile(r"\b(gas|gasoline|2[- ]?cycle|2[- ]?stroke|4[- ]?cycle|4[- ]?stroke|cc\b|\d+\s?cc|briggs|honda|tecumseh|kohler|kawasaki)", re.I)

# --- brands ---------------------------------------------------------------------
BRANDS = [  # (regex, name, multiplier on resale) - first match wins
    # vintage hi-fi (the big multipliers)
    (r"mcintosh", "McIntosh", 3.0), (r"marantz", "Marantz", 1.8), (r"sansui", "Sansui", 1.8),
    (r"technics", "Technics", 1.4), (r"pioneer", "Pioneer", 1.3), (r"klipsch", "Klipsch", 1.5),
    (r"\bbose\b", "Bose", 1.1), (r"kenwood", "Kenwood", 1.1), (r"onkyo", "Onkyo", 1.0), (r"denon", "Denon", 1.1),
    (r"\bjbl\b", "JBL", 1.2), (r"\bfender\b", "Fender", 1.3), (r"marshall", "Marshall", 1.3), (r"roland", "Roland", 1.3),
    # engines / yard
    (r"\bhonda\b", "Honda", 1.35), (r"\bstihl\b", "Stihl", 1.4), (r"husqvarna", "Husqvarna", 1.25),
    (r"\btoro\b", "Toro", 1.2), (r"kawasaki", "Kawasaki", 1.25), (r"\bechos?\b", "Echo", 1.2),
    (r"john deere|\bdeere\b", "John Deere", 1.3), (r"kubota", "Kubota", 1.3), (r"yamaha", "Yamaha", 1.3),
    (r"shindaiwa", "Shindaiwa", 1.2), (r"redmax", "RedMax", 1.2), (r"ariens", "Ariens", 1.15),
    (r"cub cadet", "Cub Cadet", 1.05), (r"snapper", "Snapper", 1.05), (r"troy[- ]?bilt", "Troy-Bilt", 1.0),
    (r"craftsman", "Craftsman", 1.0), (r"generac", "Generac", 1.05), (r"champion", "Champion", 0.95),
    (r"briggs", "Briggs & Stratton", 1.0), (r"kohler", "Kohler", 1.05), (r"tecumseh", "Tecumseh", 0.85),
    (r"poulan", "Poulan", 0.75), (r"homelite", "Homelite", 0.7),
    (r"\bmtd\b|yard machines?", "MTD / Yard Machines", 0.75), (r"predator", "Predator", 0.85),
    (r"murray", "Murray", 0.7), (r"simplicity", "Simplicity", 1.05), (r"powermate", "Powermate", 0.8),
    # tools
    (r"milwaukee", "Milwaukee", 1.3), (r"dewalt", "DeWalt", 1.2), (r"makita", "Makita", 1.2),
    (r"festool", "Festool", 2.0), (r"lincoln", "Lincoln", 1.3), (r"miller", "Miller", 1.4),
    (r"ryobi", "Ryobi", 0.85), (r"husky", "Husky", 0.85), (r"harbor freight|chicago electric", "Harbor Freight", 0.6),
    # appliances
    (r"speed queen", "Speed Queen", 1.5), (r"maytag", "Maytag", 1.1), (r"whirlpool", "Whirlpool", 1.1),
    (r"kenmore", "Kenmore", 1.0), (r"\bge\b|general electric", "GE", 1.0), (r"frigidaire", "Frigidaire", 0.95),
    (r"\bbosch\b", "Bosch", 1.2), (r"sub[- ]?zero", "Sub-Zero", 2.5), (r"viking", "Viking", 1.8),
    (r"kitchen ?aid", "KitchenAid", 1.2), (r"vitamix", "Vitamix", 1.3), (r"breville", "Breville", 1.3),
    (r"dyson", "Dyson", 1.5), (r"kirby", "Kirby", 1.2), (r"roomba|irobot", "iRobot", 1.1),
    (r"\blg\b", "LG", 1.0), (r"samsung", "Samsung", 0.95), (r"haier|midea|hisense", "Haier/Midea/Hisense", 0.8),
    (r"bernina", "Bernina", 1.8), (r"singer", "Singer", 0.9), (r"\bjuki\b", "Juki", 1.4),
    # electronics
    (r"\bapple\b|macbook|\bimac\b|\bipad\b|iphone", "Apple", 1.5), (r"\bsony\b", "Sony", 1.2),
    (r"panasonic", "Panasonic", 1.0), (r"vizio", "Vizio", 0.85), (r"\btcl\b|insignia|element|onn\b", "Budget TV brand", 0.7),
    (r"nintendo", "Nintendo", 1.3), (r"\bdell\b", "Dell", 0.9), (r"\bhp\b|hewlett", "HP", 0.85),
    (r"lenovo|thinkpad", "Lenovo", 0.95), (r"\bcanon\b", "Canon", 1.1), (r"\bnikon\b", "Nikon", 1.1),
    (r"\bdji\b", "DJI", 1.4),
]

# --- condition / faults ------------------------------------------------------------
# (key, label, regex, fix_low, fix_high, severity, groups)  fix = parts cost, estimate.
E, C, A, L, T = "engine", "climate", "appliance", "electronics", "tools"
ALL = (E, C, A, L, T)
FAULTS = [
    ("fatal", "Engine damage (seized / no compression / thrown rod)",
     r"seized|no compression|low compression|thr[eo]w a rod|thrown rod|hole in (the )?block|cracked block|"
     r"locked up|broken crank|bent crank|blown (engine|motor)", 0, 0, "fatal", (E,)),
    ("panel", "Cracked / broken screen",
     r"cracked (screen|panel)|broken (screen|panel)|screen (is )?(cracked|broken|shattered)|lines on (the )?screen", 0, 0, "fatal", ("tv",)),
    ("water", "Water / fire damage", r"water damage|flood(ed)?|fire damage|burnt|smells? burn", 0, 0, "fatal", (L, T)),
    ("sealed", "Sealed refrigerant system (leak / needs recharge / compressor)",
     r"needs? (freon|refrigerant|recharge|charged)|low (on )?(freon|refrigerant)|refrigerant leak|freon leak|"
     r"compressor (is )?(bad|dead|broken|out)|bad compressor", 0, 0, "sealed", (C, A)),
    ("parts", "Sold as parts / missing pieces",
     r"for parts|parts only|missing|no engine|no motor|frame only|just the deck|untested", 0, 0, "parts", ALL),
    # engines
    ("carb", "Fuel / carb (sat with old gas)",
     r"carb|carburet|old gas|sat for|sitting|been sitting|ran when parked|needs (a )?tune|tune[- ]?up|surges|bogs|"
     r"primer|fuel line|gummed|varnish", 15, 40, "easy", (E,)),
    ("nostart", "Doesn't start / run",
     r"(won'?t|wont|doesn'?t|does not|will not|not|never)\s+(start|run|running)|non[- ]?running|not running", 15, 50, "unknown", (E,)),
    ("recoil", "Pull cord / recoil", r"pull ?cord|recoil|starter rope", 10, 25, "easy", (E,)),
    ("ignition", "Spark / ignition", r"no spark|coil|ignition|spark plug", 10, 30, "easy", (E,)),
    ("smoke", "Smokes / oil burning", r"smokes|smoking|burns oil|blue smoke", 20, 80, "moderate", (E,)),
    # electrical, any group
    ("nopower", "No power / won't turn on",
     r"(won'?t|wont|doesn'?t|does not|will not)\s+(turn|power) on|no power|not powering|dead\b|(doesn'?t|does not|won'?t) work",
     5, 40, "unknown", (C, A, L, T)),
    ("backlight", "No picture / backlight", r"no picture|black screen|backlight|sound but no picture|screen (is )?dark|no display",
     20, 45, "moderate", ("tv", "monitor", "laptop")),
    ("hdmi", "Bad port / no signal", r"hdmi|no signal|port (is )?broken|disc drive|drive (doesn'?t|won'?t) read|red ring|blue light", 10, 40, "moderate", (L,)),
    ("battery", "Battery", r"battery|won'?t hold (a )?charge|clicks", 20, 70, "easy", (E, L, T)),
    ("remote", "Missing remote / cord / charger", r"no (remote|cord|charger|power cord)|missing (remote|cord|charger)", 5, 20, "easy", (L, C, T)),
    ("noise", "Noisy (bearing / belt / fan)", r"noisy|\bloud\b|grinding|squeal|rattl|hum(s|ming)\b", 15, 60, "moderate", (C, A, L, T)),
    ("error", "Error code / blinking light", r"error code|\b[ef]\d{1,2}\b|blinking|flashing (light|code)", 10, 80, "moderate", (A, C, L)),
    # appliances / climate
    ("compstart", "Compressor not starting (start relay / capacitor first - can be a dead compressor)",
     r"compressor (doesn'?t|don'?t|not|won'?t|isn'?t|never)\s+(seem to be\s+)?(run|work|start|kick|come on)|"
     r"compressor (clicks|hums)", 15, 40, "moderate", (C, A)),
    ("nocool", "Doesn't cool (fan / capacitor / thermostat first)",
     r"(won'?t|wont|doesn'?t|does not|not|isn'?t)\s+((get|getting|stay|staying|blow|blowing|keep|keeping)\s+)?(cool|cold|cooling|freez)|"
     r"blows warm|warm air|stopped (cooling|working)", 10, 60, "moderate", (C, A)),
    ("noheat", "Doesn't heat (fuse / element / igniter)",
     r"(won'?t|wont|doesn'?t|does not|not)\s+heat|no heat|not heating|igniter", 10, 60, "moderate", (A, C)),
    ("leak", "Leaks water", r"leaks?\b|leaking", 15, 60, "moderate", (A, C)),
    ("drain", "Won't drain / spin", r"(won'?t|wont|doesn'?t|does not)\s+(drain|spin|agitate)", 15, 60, "moderate", (A,)),
    ("door", "Door / latch / seal", r"door (latch|seal|gasket|switch)|latch|gasket|lid switch", 10, 50, "easy", (A,)),
    # tools / engines shared
    ("belt", "Belt / drive", r"\bbelt\b|self[- ]?propel(led)? (doesn'?t|not)|drive (doesn'?t|not)", 15, 60, "moderate", (E, A, T)),
    ("pump", "Pump problem", r"pump (leaks?|bad|broken)|no pressure|unloader|won'?t build pressure", 30, 120, "moderate", (E, T)),
    ("tires", "Tires / wheels", r"flat tire|tires? (flat|bad)", 10, 50, "easy", (E,)),
    ("rust", "Rust / deck damage", r"\brust|deck (is )?(rusted|cracked|bad)", 0, 40, "moderate", (E, A)),
    ("cosmetic", "Cosmetic only (dents / scratches)", r"\bdents?\b|scratch|cosmetic|scuff", 0, 0, "easy", ALL),
]
WORKS = re.compile(r"(?<!n't )(?<!not )(?<!wont )\b(runs|running|starts (right|first)|works( great| fine| good| well| perfectly)?|"
                   r"working|turns on|powers on|cools (great|well|good)|tested|fully functional|in working order|"
                   r"runs (good|great|fine)|ran (last|this))\b", re.I)
NOT_WORKS = re.compile(r"(n't|not|never|no longer|stopped)\s+(work|working|run|running|turn on|power)", re.I)
DEFAULT_UNKNOWN_FIX = {E: (15, 50), C: (10, 60), A: (15, 70), L: (10, 50), T: (10, 40)}

# Group-specific advice shown with each listing.
TIPS = {
    "engine": "Ask if it has compression (the pull cord should resist). Stale-gas carb jobs are the usual free-engine fault.",
    "climate": "Test the fan and listen for the compressor click. A bad start/run capacitor ($10-25) is the common cheap fix; "
               "anything involving refrigerant needs an EPA 608 certified tech, so skip those for flipping.",
    "appliance": "Most appliance faults are a $10-60 part (lid switch, thermal fuse, pump, igniter). Search the model "
                 "number + symptom on YouTube before you go.",
    "electronics": "Power-supply capacitors, backlights and fuses are the cheap fixes. Bring a phone charger / HDMI cable to test on the spot if you can.",
    "tools": "Corded tools usually need brushes, a switch or a cord ($5-25). Cordless tools without a battery are worth less - batteries cost $40-150.",
}
HEAVY_NOTE = "Heavy: needs a truck or trailer and two people."


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf8", "replace")


def miles(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# --- sources -----------------------------------------------------------------
def craigslist_search(term):
    q = urllib.parse.urlencode({"batch": "12-0-360-0-0", "cc": "US", "lang": "en", "postal": "95667",
                                "query": term, "searchPath": "zip", "search_distance": RADIUS_MI})
    d = json.loads(get("https://sapi.craigslist.org/web/v8/postings/search/full?" + q))["data"]
    dec = d["decode"]
    out = []
    for it in d["items"]:
        tags = {x[0]: x[1:] for x in it if isinstance(x, list) and x}
        loc_part = it[4].split("~")
        li, di = (loc_part[0].split(":") + ["0"])[:2]
        lat, lon = float(loc_part[1]), float(loc_part[2])
        loc = dec["locations"][int(li)]
        sub = (loc[2] + "/") if len(loc) > 2 else ""
        pid = dec["minPostingId"] + it[0]
        slug = (tags.get(6) or ["item"])[0]
        out.append({
            "id": "cl-%d" % pid, "source": "Craigslist",
            "title": it[-1] if isinstance(it[-1], str) else "",
            "url": "https://%s.craigslist.org/%szip/d/%s/%d.html" % (loc[1], sub, slug, pid),
            "lat": lat, "lon": lon,
            "place": dec["locationDescriptions"][int(di)] if int(di) else loc[1],
            "posted": dt.datetime.fromtimestamp(dec["minPostedDate"] + it[1], dt.timezone.utc).isoformat(timespec="seconds"),
            "images": ["https://images.craigslist.org/%s_600x450.jpg" % c.split(":", 1)[1] for c in tags.get(4, [])][:4],
        })
    return out


def craigslist_detail(url):
    b = get(url)
    if "This posting has been deleted" in b or "This posting has expired" in b or "flagged for removal" in b:
        return None
    m = re.search(r'<section id="postingbody">(.*?)</section>', b, re.S)
    body = m.group(1) if m else ""
    body = re.sub(r'<div class="print-information.*?</div>\s*</div>', "", body, flags=re.S)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body.replace("<br>", "\n")))
    body = re.sub(r"[ \t]+", " ", body).strip()
    attrs = {}
    for lab, val in re.findall(r'<span class="labl">(.*?)</span>\s*<span class="valu">(.*?)</span>', b, re.S):
        attrs[html.unescape(re.sub(r"<[^>]+>", "", lab)).strip(" :")] = \
            html.unescape(re.sub(r"<[^>]+>", "", val)).strip()
    return {"body": body[:3000], "attrs": attrs}


def freecycle_group(group):
    b = get("https://www.freecycle.org/town/" + group)
    m = re.search(r'data="(\{&quot;group[^"]*)"', b)
    if not m:
        raise RuntimeError("no post data on page")
    j = json.loads(html.unescape(m.group(1)))
    out = []
    for p in j.get("posts", []):
        if (p.get("type") or {}).get("name") != "OFFER":
            continue
        g = p.get("group") or {}
        out.append({
            "id": "fc-%d" % p["id"], "source": "Freecycle (%s)" % g.get("name", group),
            "title": p.get("subject", ""), "url": "https://www.freecycle.org/posts/%d" % p["id"],
            "lat": g.get("latitude"), "lon": g.get("longitude"),
            "place": p.get("location") or g.get("name", ""),
            "posted": "%sT%s-07:00" % (p.get("date"), p.get("time") or "12:00:00"),
            "images": (p.get("images") or [])[:4],
            "body": (p.get("description") or "").strip()[:3000], "attrs": {},
        })
    return out


# --- analysis ---------------------------------------------------------------
def find_specs(text, group="engine"):
    t = text
    s = {}
    m = re.search(r"\b(g[cx]v?\s?\d{3}|gx\s?\d{2,3})\b", t, re.I)
    if m:
        s["Engine"] = "Honda " + m.group(1).upper().replace(" ", "")
    m = re.search(r"\b(\d{2,3})\s?cc\b", t, re.I)
    if m:
        s["Displacement"] = m.group(1) + " cc"
    m = re.search(r"\b(\d{1,2}(?:\.\d)?)\s?(hp|horse ?power)\b", t, re.I)
    if m:
        s["Power"] = m.group(1) + " hp"
    m = re.search(r"\b(\d{1,2},?\d{3})\s?(w|watts?)\b", t, re.I)
    if m:
        s["Watts"] = m.group(1).replace(",", "") + " W"
    m = re.search(r"\b(\d,?\d{3})\s?psi\b", t, re.I)
    if m:
        s["Pressure"] = m.group(1).replace(",", "") + " psi"
    m = re.search(r"\b(\d{1,2},?\d{3})\s?btus?\b", t, re.I)
    if m:
        s["Cooling"] = m.group(1).replace(",", "") + " BTU"
    m = re.search(r"\b(\d{1,2}(?:\.\d)?)\s?(cu\.? ?ft|cubic f(oo|ee)t)", t, re.I)
    if m:
        s["Capacity"] = m.group(1) + " cu ft"
    m = re.search(r"\b(\d{1,3})\s?(gal(lon)?s?)\b", t, re.I)
    if m and group == "tools":
        s["Tank"] = m.group(1) + " gal"
    m = re.search(r"\b(\d{2})\s?(\"|''|in\b|inch)", t, re.I)
    if m:
        n = int(m.group(1))
        if group == "electronics" and 13 <= n <= 90:
            s["Screen"] = "%d in" % n
        elif group == "engine" and 14 <= n <= 60:
            s["Size"] = "%d in" % n
    m = re.search(r"\b(model|mod\.?|m/n)\s*(#|no\.?|number)?\s*:?\s*([A-Z0-9][A-Z0-9-]{3,})", t, re.I)
    if m and re.search(r"\d", m.group(3)):
        s["Model"] = m.group(3).upper()
    if group == "engine":
        if re.search(r"2[- ]?(cycle|stroke)", t, re.I):
            s["Cycle"] = "2-stroke"
        elif re.search(r"4[- ]?(cycle|stroke)|ohv|briggs|honda|kohler|tecumseh|kawasaki", t, re.I):
            s["Cycle"] = "4-stroke"
        if re.search(r"self[- ]?propel", t, re.I):
            s["Drive"] = "Self-propelled"
        if re.search(r"electric start|key start", t, re.I):
            s["Start"] = "Electric start"
    m = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", t)
    if m and re.search(r"\b(from|year|bought|made|built|purchased|circa|vintage)\b.{0,20}" + m.group(1), t, re.I):
        s["Year"] = m.group(1)
    return s


def classify(item):
    """Return the EQUIP row for an item, or None.  The type must come from the
    title: bodies say things like "bring a chain saw" or "you could put an
    outboard on it".  Only a generic "free stuff" title lets the body decide."""
    title = item["title"].lower()
    if JUNK_TITLE.search(title):
        return None
    row = next((e for e in EQUIP if re.search(e[3], title)), None)
    if not row and GENERIC_TITLE.search(title):
        row = next((e for e in EQUIP if re.search(e[3], item.get("body", "").lower())), None)
    return row


def analyze(item):
    text = "%s\n%s\n%s" % (item["title"], item.get("body", ""),
                           " ".join("%s: %s" % kv for kv in item.get("attrs", {}).items()))
    # Craigslist bodies arrive with curly or mangled apostrophes ("don\ufffdt")
    low = re.sub("[\u2018\u2019\u02bc\ufffd`]", "'", text.lower())
    row = classify(item)
    if not row:
        return None
    group, key, label, _, r_lo, r_hi, heavy = row
    if group == "engine" and key == "engine" and NOT_SMALL.search(low):
        return None  # automotive engine, not a small engine
    notes = []
    if group == "engine" and ELECTRIC.search(low) and not GAS.search(low):
        label = "Electric / battery " + label.lower()
        r_lo, r_hi = int(r_lo * 0.6), int(r_hi * 0.6)
        notes.append("Electric/battery model - no engine to fix; batteries cost $80-200, check one is included.")
    brand = next(((n, mult) for rx, n, mult in BRANDS if re.search(rx, low)), None)
    mult = brand[1] if brand else 1.0
    specs = find_specs(text, group)
    if key == "generator" and "Watts" in specs:
        w = int(re.sub(r"\D", "", specs["Watts"]))
        r_lo, r_hi = (100, 250) if w < 3000 else (200, 450) if w < 6000 else (350, 800)
    if specs.get("Drive") == "Self-propelled" and key == "mower":
        r_lo, r_hi = 100, 250
    if key == "tv" and "Screen" in specs:
        inch = int(re.sub(r"\D", "", specs["Screen"]))
        r_lo, r_hi = (30, 80) if inch < 40 else (60, 180) if inch < 55 else (120, 350)
    resale = (round(r_lo * mult / 5) * 5, round(r_hi * mult / 5) * 5)

    def applies(f):
        return group in f[6] or key in f[6]
    faults = [f for f in FAULTS if applies(f) and re.search(f[2], low)]
    not_working = bool(NOT_WORKS.search(low))
    works = bool(WORKS.search(low)) and not not_working and not any(
        f[5] in ("fatal", "sealed") or f[0] in ("nostart", "nopower") for f in faults)
    if works:
        # "works" also matches inside fault text like "works but leaks" - keep real faults
        faults = [f for f in faults if f[5] != "unknown"]
    dead = any(f[5] in ("fatal", "sealed", "parts") for f in faults)
    fixable = [f for f in faults if f[5] not in ("fatal", "sealed", "parts")]
    # "won't drain, otherwise works" is a drain job, not a working washer
    if any(f[3] or f[4] for f in fixable):
        works = False
    if works and not fixable:
        fix = (0, 15)  # cleaning, oil/plug, maybe a cord
    elif fixable:
        fix = (sum(f[3] for f in fixable), sum(f[4] for f in fixable))
    else:
        fix = DEFAULT_UNKNOWN_FIX[group]

    dist = miles(HOME, (item["lat"], item["lon"])) if item.get("lat") is not None else None
    trip = round(dist * 2 * COST_PER_MILE) if dist is not None else 10
    try:
        age_days = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(item["posted"])).days
    except Exception:
        age_days = None

    mid_resale = sum(resale) / 2
    if dead:
        mid_resale *= 0.25  # parts / scrap value only
    net = mid_resale - sum(fix) / 2 - trip
    score = 20 + net * 0.3
    score += 8 if works else 0
    score -= 5 if heavy else 0
    score -= 10 if (age_days or 0) > 14 else 5 if (age_days or 0) > 5 else 0
    score = int(max(0, min(100, round(score))))
    if dead:
        verdict = "Parts / scrap"
    elif score >= 65:
        verdict = "Grab it"
    elif score >= 45:
        verdict = "Worth a look"
    elif score >= 25:
        verdict = "Marginal"
    else:
        verdict = "Skip"

    why = ["%s: working ones typically resell ~$%d-%d locally%s (estimate - check sold comps)."
           % (label, resale[0], resale[1], " with the %s name" % brand[0] if brand else "")]
    if brand and brand[1] >= 1.2:
        why.append("%s is a sought-after brand - buyers pay more and parts are easy to find." % brand[0])
    elif brand and brand[1] < 0.9:
        why.append("%s is a budget brand - lower resale, keep the fix cheap." % brand[0])
    why.extend(notes)
    if works:
        why.append("Listing says it works - likely just cleaning and testing before resale.")
    for f in faults:
        if f[5] == "fatal":
            why.append("Mentions '%s' - usually costs more to fix than it's worth; value is in parts." % f[1].lower())
        elif f[5] == "sealed":
            why.append("%s - needs an EPA 608 certified tech; flip for parts/scrap or skip." % f[1])
        elif f[5] == "parts":
            why.append("Listed as parts / incomplete / untested - worth it for parts, builds or scrap metal.")
        elif f[3] or f[4]:
            why.append("%s: usually $%d-%d in parts." % (f[1], f[3], f[4]))
        else:
            why.append("%s - lowers the price a bit but doesn't need fixing." % f[1])
    if not faults and not works:
        why.append("Condition not stated - ask what's wrong before driving out.")
    why.append(TIPS[group])
    if heavy:
        why.append(HEAVY_NOTE)
    if key == "microwave":
        why.append("Safety: microwave capacitors hold a lethal charge even unplugged - don't open one without discharging it.")
    if dist is not None:
        why.append("~%d mi from Placerville (~$%d gas round trip, est.)." % (round(dist), trip))
    if age_days is not None and age_days > 5:
        why.append("Posted %d days ago - may be gone; message before driving." % age_days)
    why.append("Est. profit if fixed: ~$%d (mid resale - mid fix - gas)." % round(net))

    if works:
        cond = "Works"
    elif dead:
        cond = "Parts / dead" if not any(f[5] == "sealed" for f in faults) else "Sealed-system fault"
    else:
        cond = ", ".join(f[1] for f in fixable) or "Unknown"
    return {
        "group": GROUPS[group], "type": label, "brand": brand[0] if brand else "", "specs": specs,
        "condition": cond, "heavy": heavy,
        "resale_low": resale[0], "resale_high": resale[1], "fix_low": fix[0], "fix_high": fix[1],
        "distance_mi": round(dist, 1) if dist is not None else None, "age_days": age_days,
        "net_est": round(net), "score": score, "verdict": verdict, "why": why,
    }


# --- pipeline ----------------------------------------------------------------
def load_state():
    try:
        with open(STATE, encoding="utf8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"items": {}, "runs": []}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE)


def fetch(st):
    status = {}
    found = {}
    n = 0
    for term in CL_TERMS:
        try:
            for it in craigslist_search(term):
                found.setdefault(it["id"], it)
            n += 1
        except Exception as e:
            status.setdefault("Craigslist", []).append("%s: %s" % (term, e))
        time.sleep(1.5)
    status["Craigslist"] = "ok (%d searches)" % n if n else "FAILED " + "; ".join(status.get("Craigslist", []))[:200]
    fc_ok = []
    for g in FREECYCLE_GROUPS:
        try:
            for it in freecycle_group(g):
                found.setdefault(it["id"], it)
            fc_ok.append(g)
        except Exception as e:
            status["Freecycle " + g] = "FAILED: %s" % e
        time.sleep(1.5)
    status["Freecycle"] = "ok (%s)" % ", ".join(fc_ok) if fc_ok else "FAILED"

    # radius filter, then a cheap title prefilter before fetching bodies
    keep = {}
    for k, it in found.items():
        if it.get("lat") is None or miles(HOME, (it["lat"], it["lon"])) > RADIUS_MI:
            continue
        keep[k] = it
    items = st["items"]
    for k, it in keep.items():
        old = items.get(k)
        if old:
            old.update({kk: it[kk] for kk in ("title", "images", "posted") if it.get(kk)})
            old["last_seen"] = now_iso()
            continue
        it["first_seen"] = it["last_seen"] = now_iso()
        # only open listings whose title could be a candidate
        wanted = classify(it) or GENERIC_TITLE.search(it["title"])
        if it["source"] == "Craigslist" and not wanted:
            it["body"], it["attrs"] = "", {}
        elif it["source"] == "Craigslist":
            try:
                d = craigslist_detail(it["url"])
                time.sleep(1.0)
                if d is None:
                    continue
                it.update(d)
            except Exception as e:
                it["body"], it["attrs"] = "", {}
                it["detail_error"] = str(e)[:120]
        items[k] = it
    # drop listings that vanished from search results (deleted / expired)
    for k in list(items):
        if k not in keep:
            del items[k]
    st["runs"] = (st.get("runs", []) + [{"at": now_iso(), "status": status, "raw": len(found),
                                          "in_radius": len(keep)}])[-20:]
    return status


def build(st):
    rows = []
    for it in st["items"].values():
        a = analyze(it)
        if a:
            r = dict(it)
            r.update(a)
            rows.append(r)
    rows.sort(key=lambda r: -r["score"])
    os.makedirs(SITE, exist_ok=True)
    for r in rows:
        q = (r["brand"] + " " + r["type"].split(" /")[0]).strip()
        r["comps"] = "https://www.ebay.com/sch/i.html?_nkw=%s&LH_Sold=1&LH_Complete=1" % urllib.parse.quote_plus(q)
    with open(os.path.join(SITE, "listings.json"), "w", encoding="utf8") as f:
        json.dump({"updated": now_iso(), "rows": rows}, f, indent=1)
    cols = ["verdict", "score", "title", "group", "type", "brand", "condition", "specs", "resale_low", "resale_high",
            "fix_low", "fix_high", "net_est", "heavy", "distance_mi", "place", "age_days", "source", "url", "photo",
            "comps", "why"]
    with open(os.path.join(SITE, "listings.csv"), "w", encoding="utf8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([("; ".join("%s: %s" % kv for kv in r["specs"].items()) if c == "specs" else
                         (r["images"][0] if r["images"] else "") if c == "photo" else
                         " ".join(r["why"]) if c == "why" else r.get(c, "")) for c in cols])
    last = (st.get("runs") or [{}])[-1]
    with open(os.path.join(HERE, "template.html"), encoding="utf8") as f:
        page = f.read()
    page = page.replace("/*DATA*/null", json.dumps({"updated": now_iso(), "status": last.get("status", {}),
                                                     "rows": rows}).replace("</", "<\\/"))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf8") as f:
        f.write(page)
    return rows


def main():
    st = load_state()
    if "--offline" in sys.argv and not st["items"]:
        # a fresh clone has no data/ (git-ignored); don't overwrite docs/ with an empty page
        sys.exit("No cached listings in data/state.json - run `python engines.py` (live fetch) first.")
    if "--offline" not in sys.argv:
        print(json.dumps(fetch(st), indent=1))
        save_state(st)
    rows = build(st)
    print("%d listings written to %s" % (len(rows), SITE))
    for r in rows:
        print("%3d %-13s %-13s %5s mi  %-28s %s" % (r["score"], r["verdict"], r["group"], r["distance_mi"],
                                                   r["type"][:28], r["title"][:50]))


if __name__ == "__main__":
    main()
