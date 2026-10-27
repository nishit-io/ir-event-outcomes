"""Interaction test for the Interactive charts tab: clicks every chart, checks the cross-filter it
sets, checks the numbers obey the filter, and checks that no chart is left blank.

    python test_crossfilter.py
"""
import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright

PAGE = (Path(__file__).parent / "out" / "ir_event_outcomes.html").resolve().as_uri() + "#charts"
CHARTS = ["c-ppd", "c-funnel", "c-status", "c-ctl", "c-prepost", "c-sens", "c-mix", "c-firms"]
BLANK = """() => Object.fromEntries(%s.map(id => { const c = document.getElementById(id), x = c.getContext('2d');
  const d = x.getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 16) if (d[i]) n++;
  return [id, n]; }))""" % CHARTS
fails = []

def check(name, cond, detail=""):
    """Print PASS/FAIL for one check and remember failures."""
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond: fails.append(name)

async def click_bar(pg, chart, ds, idx):
    """Click the centre of a given bar/point using Chart.js's own element positions."""
    pos = await pg.evaluate(f"""() => {{ const ch = Chart.getChart('{chart}'); const el = ch.getDatasetMeta({ds}).data[{idx}];
        const r = ch.canvas.getBoundingClientRect(); const p = el.getCenterPoint ? el.getCenterPoint() : el;
        ch.canvas.scrollIntoView({{block:'center'}}); const r2 = ch.canvas.getBoundingClientRect();
        return {{x: r2.left + p.x, y: r2.top + p.y}}; }}""")
    await pg.mouse.move(pos["x"], pos["y"]); await pg.wait_for_timeout(120)
    await pg.mouse.click(pos["x"], pos["y"]); await pg.wait_for_timeout(700)

async def main():
    """Run every check in this file; exit 1 if any failed."""
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.route("**/*", lambda r: r.abort() if r.request.url.startswith("http") else r.continue_())
        await pg.goto(PAGE); await pg.wait_for_timeout(500)
        await pg.evaluate("() => { try { localStorage.clear() } catch(e) {} }"); await pg.reload(); await pg.wait_for_timeout(800)
        F = lambda: pg.evaluate("window.__irFilters()")
        M = lambda js: pg.evaluate(f"(() => {{ const M = window.__irModel(); return {js}; }})()")
        base_pipe = await M("M.port.pipe")

        async def not_blank(step):
            px = await pg.evaluate(BLANK)
            empty = [k for k, v in px.items() if v < 50]
            check(f"{step}: no blank charts", not empty, f"blank: {empty}" if empty else "")

        # 1 ppd bar -> event
        await click_bar(pg, "c-ppd", 0, 2); f = await F()
        check("Pipeline per $1: click Berlin bar sets event filter", f["ev"] == "E003", str(f)); await not_blank("after ppd click")
        await click_bar(pg, "c-ppd", 0, 2); f = await F()
        check("Pipeline per $1: clicking it again clears it", f["ev"] == "ALL")

        # 2 funnel step
        dd_london = await M("M.events[1].funnel[3]")
        await click_bar(pg, "c-funnel", 1, 3); f = await F()
        check("Funnel: click London 'Reached DD' sets step + event", f["ev"] == "E002" and f["step"] == {"ev": "E002", "idx": 3}, str(f))
        n = await M("M.events[1].nFirms")
        check("Funnel: London now counts only firms that reached DD", n == dd_london, f"{n} firms vs {dd_london} expected")
        await not_blank("after funnel click")
        await pg.click("#chips-clear"); await pg.wait_for_timeout(400)

        # 3 status
        await click_bar(pg, "c-status", 2, 0); f = await F()
        check("Status: click New York 'Declined' sets status + event", f["status"] == "Declined" and f["ev"] == "E001", str(f))
        bad = await M("M.uniq.filter(o => o.status !== 'Declined').length")
        check("Status: every linked opportunity is now Declined", bad == 0)
        ny = await M("M.events[0].pipe")
        check("Status: New York pipeline = its declined amount ($150M)", ny == 150e6, str(ny))
        await not_blank("after status click")
        await pg.click("#chips-clear"); await pg.wait_for_timeout(400)

        # 4 ctl + prepost + sens -> event
        for chart, ds, idx, ev in [("c-ctl", 1, 1, "E002"), ("c-prepost", 1, 0, "E001"), ("c-sens", 2, 5, "E003")]:
            await click_bar(pg, chart, ds, idx); f = await F()
            check(f"{chart}: click sets event {ev}", f["ev"] == ev, str(f)); await not_blank(f"after {chart} click")
            await pg.click("#chips-clear"); await pg.wait_for_timeout(400)

        # 5 mix -> investor group (+ event)
        await pg.click('#mix-seg button[data-v="priority"]'); await pg.wait_for_timeout(500)
        await click_bar(pg, "c-mix", 0, 0); f = await F()
        check("Mix (Tier): click New York 'Tier 1' sets tier + event", f["attr"] == {"key": "priority", "val": "Tier 1"} and f["ev"] == "E001", str(f))
        tiers = await pg.evaluate("""(() => { const M = window.__irModel();
              const F = Object.fromEntries(JSON.parse(document.getElementById('data').textContent).firms.map(f => [f.firm_id, f.priority]));
              const ids = [...new Set([...M.events.flatMap(x => [...x.firms]), ...M.uniq.map(o => o.firm_id), ...M.control])];
              return ids.map(id => F[id]); })()""")
        check("Mix: every firm and opportunity left is Tier 1", len(tiers) > 0 and set(tiers) == {"Tier 1"}, f"{len(tiers)} firms, tiers {sorted(set(tiers))}")
        await not_blank("after mix click")
        await pg.click('#mix-seg button[data-v="segment"]'); await pg.wait_for_timeout(300)
        await pg.click("#chips-clear"); await pg.wait_for_timeout(400)

        # 6 firms -> single firm
        await click_bar(pg, "c-firms", 0, 0); f = await F()
        check("Top firms: click top bar sets firm filter", bool(f["firm"]), str(f))
        firms = await M("[...new Set(M.uniq.map(o => o.firm_id))]")
        check("Top firms: only that firm's opportunities remain", firms == [f["firm"]], str(firms))
        await not_blank("after firm click")

        # 7 stacking + chips + clear all
        await click_bar(pg, "c-ppd", 0, 0); f = await F()
        check("Filters stack: firm filter kept when event clicked", bool(f["firm"]) and f["ev"] == "E001", str(f))
        chips = await pg.locator("#chips .fchip").count()
        check("Chips: one chip per active filter", chips == 2, f"{chips} chips")
        await pg.locator("#chips .fchip button").first.click(); await pg.wait_for_timeout(400); f = await F()
        check("Chips: × removes just that filter", f["ev"] == "ALL" and bool(f["firm"]), str(f))
        await pg.click("#chips-clear"); await pg.wait_for_timeout(400); f = await F()
        check("Clear all resets every filter", f == {"ev": "ALL", "attr": None, "status": None, "step": None, "firm": None, "trend": None}, str(f))
        check("Clear all restores the full pipeline", await M("M.port.pipe") == base_pipe)
        hidden = await pg.evaluate("document.getElementById('chips').hidden")
        check("Chips bar hides when no filters", hidden)

        # 8 toggles after clicks (the original bug)
        await click_bar(pg, "c-ppd", 0, 1)
        await pg.click('#pp-seg button[data-v="opp"]'); await pg.wait_for_timeout(500); await not_blank("after Opportunities toggle")
        await pg.click('#pp-seg button[data-v="meet"]'); await pg.wait_for_timeout(500); await not_blank("after Meetings toggle")
        for t in ["summary", "map", "trends", "details", "charts"]:
            await pg.evaluate(f"location.hash='{t}'"); await pg.wait_for_timeout(600)
        await not_blank("after visiting every tab with a filter on")
        await pg.click("#chips-clear"); await pg.wait_for_timeout(300)

        # ---------------- Trends tab ----------------
        await pg.evaluate("location.hash='trends'"); await pg.wait_for_timeout(800)
        TRENDS = ["t-monthly", "t-cum", "t-speed", "t-purpose", "t-first"]
        async def trends_not_blank(step):
            px = dict(await pg.evaluate(BLANK.replace("%s" % CHARTS, str(TRENDS))))
            empty = [k for k, v in px.items() if v < 50]
            check(f"Trends {step}: no blank charts", not empty, f"blank: {empty}" if empty else "")
        DATA = "JSON.parse(document.getElementById('data').textContent)"

        # monthly: May 2026 (index 5), attending firms, meetings
        await click_bar(pg, "t-monthly", 0, 5); f = await F()
        check("Monthly: click May bar sets 'active that month' filter", (f["trend"] or {}).get("kind") == "month", str(f["trend"]))
        ok = await pg.evaluate(f"""(() => {{ const M = window.__irModel(), D = {DATA};
            const may = Date.UTC(2026,4,1), ids = [...new Set(M.events.flatMap(x => [...x.firms]))];
            return ids.length > 0 && ids.every(id => D.meetings.some(m => m.firm_id === id && m.meeting_date.slice(0,7) === '2026-05')); }})()""")
        check("Monthly: every remaining firm had a meeting in May 2026", ok)
        await trends_not_blank("after monthly click")
        await pg.click("#chips-clear"); await pg.wait_for_timeout(300)

        # cumulative line -> event
        await click_bar(pg, "t-cum", 1, 3); f = await F()
        check("Pipeline build-up: click London line sets event", f["ev"] == "E002", str(f))
        await trends_not_blank("after build-up click")
        await pg.click("#chips-clear"); await pg.wait_for_timeout(300)

        # speed bar -> event; 'Not linked' does nothing
        await click_bar(pg, "t-speed", 0, 2); f = await F()
        check("Stage speed: click Berlin bar sets event", f["ev"] == "E003", str(f))
        await pg.click("#chips-clear"); await pg.wait_for_timeout(300)
        await click_bar(pg, "t-speed", 0, 3); f = await F()
        check("Stage speed: 'Not linked' bar sets no filter", f["ev"] == "ALL" and not f["trend"], str(f))

        # purpose: Fundraising after
        await click_bar(pg, "t-purpose", 0, 1); f = await F()
        check("Purpose: click 'Fundraising · After' sets filter", f["trend"] == {"kind": "purpose", "purpose": "Fundraising", "when": "post"}, str(f["trend"]))
        ok = await pg.evaluate(f"""(() => {{ const M = window.__irModel(), D = {DATA}, W = M.W*864e5;
            const ts = s => Date.parse(s.slice(0,10)+'T00:00:00Z');
            const ids = [...new Set(M.events.flatMap(x => [...x.firms]))];
            return ids.length > 0 && ids.every(id => M.events.some(x => x.firms.has(id) && D.meetings.some(m => m.firm_id === id &&
                m.purpose === 'Fundraising' && ts(m.meeting_date) > x.e.t && ts(m.meeting_date) <= x.e.t + W))); }})()""")
        check("Purpose: every remaining firm had a fundraising meeting after an event it attended", ok)
        await trends_not_blank("after purpose click")
        await pg.click("#chips-clear"); await pg.wait_for_timeout(300)

        # first follow-up: Berlin 'None'
        last = await pg.evaluate("Chart.getChart('t-first').data.labels.length - 1")
        await click_bar(pg, "t-first", 2, last); f = await F()
        check("Follow-up: click Berlin 'None' sets event + no-follow-up filter", f["ev"] == "E003" and (f["trend"] or {}).get("none"), str(f))
        berlin = await M("({n: M.events[2].nFirms, met: M.events[2].funnel[1]})")
        check("Follow-up: Berlin now shows only its 8 firms with no follow-up meeting", berlin == {"n": 8, "met": 0}, str(berlin))
        await trends_not_blank("after follow-up click")

        # a Trends filter must also drive the Charts tab
        await pg.evaluate("location.hash='charts'"); await pg.wait_for_timeout(700)
        await not_blank("Charts tab with a Trends filter on")
        # settings change keeps the Trends filter recalculated
        await pg.evaluate("location.hash='trends'"); await pg.wait_for_timeout(500)
        await pg.click("#tr-bulk"); await pg.wait_for_timeout(500); await trends_not_blank("after bulk toggle with filter on")
        await pg.click("#tr-bulk"); await pg.click("#chips-clear"); await pg.wait_for_timeout(300)
        check("No JavaScript errors", not errors, "; ".join(errors[:3]))
        await b.close()
    print(f"\n{len(fails)} failed" if fails else "\nall checks passed"); sys.exit(1 if fails else 0)

asyncio.run(main())
