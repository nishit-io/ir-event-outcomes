"""Standalone test: the HTML file must work on its own, with no internet and nothing else beside it.

Copies ONLY out/ir_event_outcomes.html into an empty temporary folder, opens it in headless
Chromium with every network request blocked, and checks that every tab renders its content.
It also lists every outside request the page attempted; only the two optional extras
(Google Fonts, OpenStreetMap tiles) are allowed.

    python test_standalone.py
"""
import asyncio, shutil, sys, tempfile
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

SRC = Path(__file__).parent / "out" / "ir_event_outcomes.html"
OPTIONAL_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com", "tile.openstreetmap.org"}
fails = []

def check(name, cond, detail=""):
    """Print PASS/FAIL for one check and remember failures."""
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond: fails.append(name)

PAINTED = """ids => ids.map(id => { const c = document.getElementById(id); if (!c || !c.width) return [id, 0];
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
  for (let i = 3; i < d.length; i += 16) if (d[i]) n++; return [id, n]; })"""

async def main():
    """Run every check in this file; exit 1 if any failed."""
    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / SRC.name
        shutil.copy(SRC, page_file)
        check("Folder contains only the HTML file", [p.name for p in Path(tmp).iterdir()] == [SRC.name])
        attempted, errors = set(), []
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page(viewport={"width": 1280, "height": 900})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            async def route(r):
                u = r.request.url
                if u.startswith("file://") and Path(urlparse(u).path) == page_file: await r.continue_()
                elif u.startswith("data:") or u.startswith("blob:"): await r.continue_()
                else:
                    attempted.add(urlparse(u).hostname or u[:40]); await r.abort()
            await pg.route("**/*", route)
            await pg.goto(page_file.as_uri()); await pg.wait_for_timeout(800)
            await pg.evaluate("() => { try { localStorage.clear() } catch(e) {} }")
            await pg.reload(); await pg.wait_for_timeout(800)

            check("Chart library is built in", await pg.evaluate("typeof Chart === 'function'"))
            check("Map library is built in", await pg.evaluate("typeof L === 'object' && !!L.map"))
            check("Data is built in", await pg.evaluate("JSON.parse(document.getElementById('data').textContent).firms.length") == 60)

            # Executive summary
            await pg.evaluate("location.hash='summary'"); await pg.wait_for_timeout(400)
            rows = await pg.locator("#scorecard tbody tr").count()
            kpi = await pg.locator("#kpis .kpi").count()
            check("Summary: KPI tiles and scorecard render", kpi == 4 and rows == 3, f"{kpi} tiles, {rows} rows")

            # Charts
            await pg.evaluate("location.hash='charts'"); await pg.wait_for_timeout(900)
            ids = ["c-ppd", "c-funnel", "c-status", "c-ctl", "c-prepost", "c-sens", "c-mix", "c-firms"]
            painted = dict(await pg.evaluate(PAINTED, ids))
            check("Charts: all 8 charts are drawn", all(v > 50 for v in painted.values()), str({k: v for k, v in painted.items() if v <= 50}) or "")

            # Map (tiles blocked, so the built-in outline must show)
            await pg.evaluate("location.hash='map'"); await pg.wait_for_timeout(5000)
            shapes = await pg.locator("#map path.leaflet-interactive, #map svg path").count()
            note = await pg.inner_text("#map-mode")
            check("Map: outline, pins and region bubbles render", shapes > 150, f"{shapes} shapes")
            check("Map: falls back to the built-in outline", "built-in" in note, note)
            check("Map: region table renders", await pg.locator("#region-tbl tbody tr").count() == 3)

            # Trends
            await pg.evaluate("location.hash='trends'"); await pg.wait_for_timeout(900)
            painted = dict(await pg.evaluate(PAINTED, ["t-monthly", "t-cum", "t-speed", "t-purpose", "t-first"]))
            check("Trends: all 5 charts are drawn", all(v > 50 for v in painted.values()), str(painted))

            # Data details
            await pg.evaluate("location.hash='details'"); await pg.wait_for_timeout(500)
            n = await pg.locator("#dt-tbl tbody tr").count()
            await pg.click('#dt-tbl tbody tr[data-firm="F017"]'); await pg.wait_for_timeout(300)
            tl = await pg.locator("#dt-tbl tr.detail svg").count()
            check("Details: firm table and timeline render", n == 60 and tl == 1, f"{n} rows, timeline {tl}")

            # Quality, assumptions, dictionary
            await pg.evaluate("location.hash='quality'"); await pg.wait_for_timeout(400)
            check("Data quality: issue log renders", await pg.locator("#dq-tbl tbody tr").count() == 11)
            await pg.evaluate("location.hash='assumptions'"); await pg.wait_for_timeout(400)
            check("Assumptions: sections and metrics render",
                  await pg.locator("#as-grid .card").count() == 6 and await pg.locator("#as-metrics tbody tr").count() == 10)
            await pg.evaluate("location.hash='dictionary'"); await pg.wait_for_timeout(400)
            check("Dictionary: diagram and 6 table cards render",
                  await pg.locator("#er rect").count() == 5 and await pg.locator("#dict .card").count() == 6)

            await b.close()
        extra = attempted - OPTIONAL_HOSTS
        check("Only optional extras tried the internet", not extra, f"tried: {sorted(attempted) or 'nothing'}")
        check("No JavaScript errors", not errors, "; ".join(errors[:3]))
    print(f"\n{len(fails)} failed" if fails else "\nall standalone checks passed"); sys.exit(1 if fails else 0)

asyncio.run(main())
