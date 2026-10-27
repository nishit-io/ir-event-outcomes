"""Optional check: the dashboard's in-browser numbers must match build_model.py exactly.

Opens out/ir_event_outcomes.html in headless Chromium (no network), sets the window and
Tentative toggle through the real controls, reads the page's model, and compares it with
the Python model for the same settings. Also clicks every tab and saves screenshots to
screenshots/.

    pip install playwright && playwright install chromium
    python verify_dashboard.py
"""
import asyncio, sys
from pathlib import Path
import build_model as bm
from playwright.async_api import async_playwright

ROOT = Path(__file__).parent
PAGE = (ROOT / "out" / "ir_event_outcomes.html").resolve()
SETTINGS = [(90, False), (90, True), (30, False), (60, False), (180, False), (180, True)]
TABS = ["summary", "charts", "map", "trends", "details", "quality", "assumptions", "dictionary"]


def python_numbers(window, tent):
    """Per-event and portfolio numbers from build_model for one setting, in the shape the page reports."""
    d = bm.load()
    att, att_firm, o, _ = bm.clean(d)
    opps = bm.opp_summary(o)
    pe, _, port = bm.compute(d, att_firm, opps, window, tent)
    ev = {r.event_id: dict(firms=r.firms_attended, opps=r.new_opps, pipe=round(r.new_pipeline),
                           committed=round(r.committed_usd), meet_after=r.post_meetings, meet_before=r.pre_meetings,
                           funnel=[r.firms_attended, r.funnel_meeting, r.funnel_opp, r.funnel_dd, r.funnel_commit])
          for r in pe.itertuples()}
    return ev, dict(opps=port["unique_assoc_opps"], pipe=round(port["unique_assoc_pipeline"]))


def save_if_changed(png, path):
    """Write a screenshot only if it visibly differs from the saved one.

    Anti-aliasing varies slightly between runs; overwriting on every run would leave git showing
    "modified" screenshots that look identical. A real change (any pixel channel off by more than
    48 levels, over more than 0.05% of pixels, or a different size) replaces the file.
    """
    import io
    from PIL import Image, ImageChops
    if path.exists():
        old, new = Image.open(path).convert("RGB"), Image.open(io.BytesIO(png)).convert("RGB")
        if old.size == new.size:
            diff = ImageChops.difference(old, new).convert("L").point(lambda v: 255 if v > 48 else 0)
            if diff.histogram()[255] <= 0.0005 * old.size[0] * old.size[1]:
                return
    path.write_bytes(png)


JS = """() => { const M = window.__irModel();
  return {ev: Object.fromEntries(M.events.map(x => [x.id, {firms:x.nFirms, opps:x.nOpps, pipe:Math.round(x.pipe),
            committed:Math.round(x.committed), meet_after:x.postMeet, meet_before:x.preMeet, funnel:x.funnel}])),
          port: {opps:M.port.opps, pipe:Math.round(M.port.pipe)}}; }"""


async def main():
    """Compare page vs Python at each setting, then screenshot every tab. Exits 1 on any mismatch or page error."""
    shots = ROOT / "screenshots"; shots.mkdir(exist_ok=True)
    failures, errors = 0, []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.route("**/*", lambda r: r.abort() if r.request.url.startswith("http") else r.continue_())
        await page.goto(PAGE.as_uri()); await page.wait_for_timeout(500)
        await page.evaluate("() => { try { localStorage.clear(); } catch(e) {} }")
        for window, tent in SETTINGS:
            await page.evaluate(f"""() => {{ const w = document.getElementById('win'); w.value = {window};
                w.dispatchEvent(new Event('input'));
                const t = document.getElementById('tent'); if (t.checked !== {str(tent).lower()}) t.click(); }}""")
            js = await page.evaluate(JS)
            ev, port = python_numbers(window, tent)
            ok = js["ev"] == ev and js["port"] == port
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  window={window:>3}  tentative={tent!s:5}  "
                  f"unique opps={port['opps']}  unique pipeline=${port['pipe']/1e6:,.0f}M")
            if not ok:
                print("   python:", ev, port); print("   page:  ", js["ev"], js["port"])
        # reset to defaults and screenshot every tab
        await page.evaluate("() => { const w=document.getElementById('win'); w.value=90; w.dispatchEvent(new Event('input')); const t=document.getElementById('tent'); if(t.checked) t.click(); }")
        for i, tab in enumerate(TABS, 1):
            await page.evaluate(f"location.hash = '{tab}'"); await page.wait_for_timeout(5000 if tab == "map" else 700)
            if tab == "details":
                await page.click('tr[data-firm="F017"]'); await page.wait_for_timeout(300)
            await page.evaluate('scrollTo(0,0)'); save_if_changed(await page.screenshot(full_page=True), shots / f"{i}_{tab}.png")
        await page.emulate_media(color_scheme="dark"); await page.evaluate("location.hash='charts'"); await page.wait_for_timeout(800)
        save_if_changed(await page.screenshot(full_page=True), shots / "9_charts_dark.png")
        await browser.close()
    print("page errors:", errors or "none")
    print("screenshots saved to", shots)
    sys.exit(1 if failures or errors else 0)

asyncio.run(main())
