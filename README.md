# Free fix & flip finds near Placerville, CA

Finds **free** things within 50 miles of Placerville that are worth picking up to
repair, reuse, build with or resell: small engines, appliances, AC units,
electronics and power tools. Each listing comes with a photo, specs, a rough
flip score, and the reasons behind it.

- **Sources:** Craigslist free section (all areas within 50 mi) and Freecycle
  (Placerville, Cameron Park, Auburn, Folsom).
- **Output:** `docs/index.html` (phone-friendly cards plus a table view), `docs/listings.csv`, `docs/listings.json`.
- **Estimates, not prices:** resale and fix costs are rule-of-thumb estimates.
  Every listing links to eBay sold listings so you can check real prices.

```
python engines.py            # fetch + build
python engines.py --offline  # rebuild from cache
python test_engines.py       # classifier checks
```

Python 3.10+, standard library only. See `CLAUDE.md` for full project context.
