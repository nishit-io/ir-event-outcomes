"""Unit tests for the dashboard's JavaScript, run against the shipped page in headless Chromium.

Each test builds a standalone page from SYNTHETIC data (tests/conftest.py), opens it with all network
access blocked, and calls the page's own functions through the read-only window.__irTest hook.

Needs:  pip install playwright && playwright install chromium
"""
import copy

import pytest

import build_dashboard as bd
import build_model as bm
from conftest import BASE, write_dataset

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="session")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def make_page(tmp_path, browser):
    """make_page(tables=None, timezone=None) -> (page, errors). Builds a page from synthetic rows and opens it offline."""
    opened = []

    def _make(tables=None, timezone=None, n=[0]):
        n[0] += 1
        root = tmp_path / f"site{n[0]}"
        bm.build(write_dataset(root / "data", tables or copy.deepcopy(BASE)), root / "out")
        html = bd.build_html(root / "out" / "model.json", root / "out", docs_dir=None)
        ctx = browser.new_context(timezone_id=timezone) if timezone else browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", lambda r: r.abort() if r.request.url.startswith("http") else r.continue_())
        page.goto(html.as_uri())
        page.evaluate("() => { try { localStorage.clear() } catch (e) {} }")
        page.reload()
        page.wait_for_function("window.__irTest !== undefined")
        opened.append(ctx)
        return page, errors

    yield _make
    for c in opened:
        c.close()


def js(page, expr, *args):
    """Evaluate `expr` with T = window.__irTest in scope."""
    return page.evaluate(f"(args) => {{ const T = window.__irTest; return ({expr}); }}", list(args))


def visit_all_tabs(page):
    for tab in ["summary", "charts", "map", "trends", "details", "quality", "assumptions", "dictionary"]:
        page.evaluate(f"location.hash = '{tab}'")
        page.wait_for_timeout(250 if tab != "map" else 600)


# ---------------------------------------------------------------- formatters

class TestFormatters:
    @pytest.mark.parametrize("v, out", [
        (0, "$0"), (950, "$950"), (1500, "$2K"), (2_500_000, "$2.5M"), (150_000_000, "$150M"),
        (1_506_000_000, "$1.51B"), (2_000_000_000, "$2B"), (-2_500_000, "-$2.5M"),
        (None, "–"), (float("nan"), "–"), ("abc", "–"),
    ])
    def test_money(self, make_page, v, out):
        page, _ = make_page()
        assert js(page, "T.money(args[0])", v) == out

    def test_money_infinity(self, make_page):
        page, _ = make_page()
        assert js(page, "[T.money(Infinity), T.money(-Infinity), T.money(undefined)]") == ["–", "–", "–"]

    def test_pct_int_perdollar(self, make_page):
        page, _ = make_page()
        assert js(page, "[T.pct(0.5), T.pct(0), T.pct(null), T.pct(NaN)]") == ["50%", "0%", "–", "–"]
        assert js(page, "[T.int(1234567.4), T.int(-3), T.int(NaN)]") == ["1,234,567", "-3", "–"]
        assert js(page, "[T.perDollar(2172.97), T.perDollar(null), T.perDollar(0)]") == ["$2,173", "n/a", "$0"]

    @pytest.mark.parametrize("raw, out", [
        ('<img src=x onerror="alert(1)">', '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'),
        ("Tom & Jerry's", "Tom &amp; Jerry&#39;s"),
        ("Zürich 日本 🚀", "Zürich 日本 🚀"),
        (None, ""),
    ])
    def test_esc(self, make_page, raw, out):
        page, _ = make_page()
        assert js(page, "T.esc(args[0])", raw) == out


# ---------------------------------------------------------------- compute(): same rules as the Python model

class TestCompute:
    def test_happy_path(self, make_page):
        page, _ = make_page()
        r = js(page, "(() => { const M = T.compute(90, false, {}); return {e: M.events.map(x => [x.id, x.nFirms, x.nOpps, x.pipe, x.ppd]), port: M.port}; })()")
        assert r["e"] == [["E1", 1, 1, 10e6, 100], ["E2", 1, 1, 2e6, 40]]
        assert r["port"]["opps"] == 2 and r["port"]["pipe"] == 12e6

    @pytest.mark.parametrize("window", [15, 30, 90, 180])
    @pytest.mark.parametrize("tent", [False, True])
    def test_matches_python_model(self, make_page, tmp_path, window, tent):
        page, _ = make_page()
        d = bm.load(write_dataset(tmp_path / "py", copy.deepcopy(BASE)))
        _, att_firm, o, _ = bm.clean(d)
        pe, _, port = bm.compute(d, att_firm, bm.opp_summary(o), window, tent)
        expect = [[r.event_id, r.firms_attended, r.new_opps, r.new_pipeline,
                   [r.firms_attended, r.funnel_meeting, r.funnel_opp, r.funnel_dd, r.funnel_commit]] for r in pe.itertuples()]
        got = js(page, "T.compute(args[0], args[1], {}).events.map(x => [x.id, x.nFirms, x.nOpps, x.pipe, x.funnel])", window, tent)
        assert got == expect
        assert js(page, "T.compute(args[0], args[1], {}).port.pipe", window, tent) == port["unique_assoc_pipeline"]

    def test_results_do_not_depend_on_viewer_timezone(self, make_page):
        expr = "JSON.stringify(T.compute(90, true, {}).events.map(x => [x.id, x.nOpps, x.postMeet, x.preMeet, x.observed]))"
        results = {tz: js(make_page(timezone=tz)[0], expr) for tz in ["UTC", "Pacific/Kiritimati", "America/Los_Angeles"]}
        assert len(set(results.values())) == 1, results

    @pytest.mark.parametrize("filt, firms", [
        ({"status": "Declined"}, ["F3"]),
        ({"attr": {"key": "priority", "val": "Tier 1"}}, ["F1"]),
        ({"firm": "F3"}, ["F3"]),
    ])
    def test_filters(self, make_page, filt, firms):
        page, _ = make_page()
        got = js(page, "[...new Set(T.compute(90, false, args[0]).uniq.map(o => o.firm_id))]", filt)
        assert got == firms

    def test_firm_set_filter_applies_to_comparison_group_too(self, make_page):
        page, _ = make_page()
        assert js(page, "T.compute(90, false, {firmSet: new Set(['F1'])}).control.size") == 0
        assert js(page, "T.compute(90, false, {firmSet: new Set(['F4'])}).control.size") == 1

    def test_zero_cost_event_shows_na_not_infinity(self, make_page):
        t = copy.deepcopy(BASE)
        t["events"][0]["cost_usd"] = 0
        page, errors = make_page(t)
        assert js(page, "T.compute(90, false, {}).events[0].ppd") is None
        visit_all_tabs(page)
        page.evaluate("location.hash = 'summary'")
        text = page.inner_text("body")
        assert "Infinity" not in text and "NaN" not in text and "No cost" in text
        assert not errors

    def test_event_without_attendees(self, make_page):
        t = copy.deepcopy(BASE)
        t["event_attendees"] = [r for r in t["event_attendees"] if r["event_id"] != "E2"]
        page, errors = make_page(t)
        assert js(page, "(() => { const x = T.compute(90, false, {}).events[1]; return [x.nFirms, x.costPerFirm, x.rateAtt]; })()") == [0, None, 0]
        visit_all_tabs(page)
        assert not errors


# ---------------------------------------------------------------- cross-filters

class TestCrossFilters:
    def test_click_toggles(self, make_page):
        page, _ = make_page()
        js(page, "T.applyClick({ev: 'E1', status: 'Open'})")
        assert js(page, "[T.state().ev, T.filters().status]") == ["E1", "Open"]
        js(page, "T.applyClick({ev: 'E1', status: 'Open'})")          # same click again clears it
        assert js(page, "[T.state().ev, T.filters().status]") == ["ALL", None]

    def test_trend_filters(self, make_page):
        page, _ = make_page()
        base = "T.compute(90, false, {})"
        jan = js(page, f"[...T.trendFirms({{kind:'month', month: Date.UTC(2026,0,1), group:'att', measure:'meet'}}, {base}, 90)]")
        assert jan == ["F1"]
        fund = js(page, f"[...T.trendFirms({{kind:'purpose', purpose:'Fundraising', when:'post'}}, {base}, 90)]")
        assert fund == ["F1"]
        none = js(page, f"[...T.trendFirms({{kind:'first', ev:'E2', none:true}}, {base}, 90)]")
        assert none == ["F3"]                                          # F3 attended E2 and has no meetings
        assert js(page, f"T.trendFirms({{kind:'first', ev:'E9', none:true}}, {base}, 90).size") == 0

    def test_step_filter_resolves_to_firms_that_reached_it(self, make_page):
        page, _ = make_page()
        js(page, "T.applyClick({ev: 'E1', step: {ev: 'E1', idx: 3}})")   # reached Due Diligence
        assert js(page, "[...T.resolveXF(90, false).stepSet]") == ["F1"]


# ---------------------------------------------------------------- saved settings

class TestRestoreState:
    def test_invalid_saved_values_are_ignored(self, make_page):
        page, _ = make_page()
        before = js(page, "T.state()")
        js(page, "T.restoreState({W: 999, ev: 'E999', tent: 'yes', mix: '<script>', pp: 1, tr: null, map: 'ppd ', bulk: 0})")
        assert js(page, "T.state()") == before

    @pytest.mark.parametrize("junk", [None, [], "string", 42, [1, 2]])
    def test_non_object_ignored(self, make_page, junk):
        page, _ = make_page()
        before = js(page, "T.state()")
        js(page, "T.restoreState(args[0])", junk)
        assert js(page, "T.state()") == before

    def test_valid_values_applied(self, make_page):
        page, _ = make_page()
        js(page, "T.restoreState({W: 30, ev: 'E2', tent: true, mix: 'priority'})")
        assert {k: v for k, v in js(page, "T.state()").items() if k in ("W", "ev", "tent", "mix")} == \
               {"W": 30, "ev": "E2", "tent": True, "mix": "priority"}

    def test_corrupt_storage_does_not_break_the_page(self, make_page):
        page, errors = make_page()
        page.evaluate("localStorage.setItem('ir-dash', '{not json')")
        page.reload()
        page.wait_for_function("window.__irTest !== undefined")
        assert js(page, "T.state().W") == 90 and not errors


# ---------------------------------------------------------------- data-driven labels, colours and map positions

class TestDataDriven:
    def test_labels_and_colours_come_from_the_data(self, make_page):
        page, _ = make_page()
        assert js(page, "T.SHORT") == {"E1": "New York", "E2": "London"}
        assert js(page, "T.EVCOL") == {"E1": "--e1", "E2": "--e2"}

    def test_two_events_in_one_city_get_distinct_labels(self, make_page):
        t = copy.deepcopy(BASE)
        t["events"][1]["location"] = "New York"
        page, _ = make_page(t)
        labels = js(page, "Object.values(T.SHORT)")
        assert len(set(labels)) == 2 and labels[0] == "New York"

    def test_unknown_city_gets_no_pin_and_a_note_instead_of_crashing(self, make_page):
        t = copy.deepcopy(BASE)
        t["events"][1]["location"] = "Atlantis"
        page, errors = make_page(t)
        page.evaluate("location.hash = 'map'")
        page.wait_for_timeout(4500)
        assert "No map position for: Test Dinner (Atlantis)" in page.inner_text("#map-mode")
        assert js(page, "Object.keys(T.CITY)") == ["E1"]
        assert not errors

    def test_accented_city_name_is_found(self, make_page):
        t = copy.deepcopy(BASE)
        t["events"][1]["location"] = "São Paulo"
        page, _ = make_page(t)
        assert js(page, "Object.keys(T.CITY)") == ["E1", "E2"]

    def test_more_than_six_events_cycle_colours(self, make_page):
        t = copy.deepcopy(BASE)
        t["events"] = [dict(BASE["events"][0], event_id=f"E{i}", event_name=f"Event {i}") for i in range(1, 9)]
        page, errors = make_page(t)
        cols = js(page, "Object.values(T.EVCOL)")
        assert len(cols) == 8 and cols[6] == cols[0]
        visit_all_tabs(page)
        assert not errors


# ---------------------------------------------------------------- whole page with unusual data

class TestWholePage:
    def test_every_tab_renders_without_errors(self, make_page):
        page, errors = make_page()
        visit_all_tabs(page)
        assert not errors

    def test_no_events_at_all(self, make_page):
        t = copy.deepcopy(BASE)
        t["events"], t["event_attendees"] = [], []          # header-only CSVs: an empty export
        page, errors = make_page(t)
        assert js(page, "T.compute(90, false, {}).events.length") == 0
        visit_all_tabs(page)
        page.evaluate("location.hash = 'summary'")
        page.wait_for_timeout(200)
        assert "nothing to compare" in page.inner_text("#findings")
        body = page.inner_text("body")
        assert "NaN" not in body and "Infinity" not in body and "undefined" not in body
        assert not errors

    def test_unicode_and_long_names_render(self, make_page):
        t = copy.deepcopy(BASE)
        t["firms"][0]["firm_name"] = "Zürich Pensionskasse 日本 " + "Very Long Name " * 20
        page, errors = make_page(t)
        page.evaluate("location.hash = 'details'")
        page.wait_for_timeout(300)
        assert "Zürich Pensionskasse 日本" in page.inner_text("#dt-tbl")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert not errors


# ---------------------------------------------------------------- security: hostile text in every field

XSS = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
ATTR = '"><svg onload="window.__xss=(window.__xss||0)+1">'


def hostile_tables():
    """Synthetic data where every free-text field carries an HTML/script injection payload."""
    t = copy.deepcopy(BASE)
    for e in t["events"]:
        e.update(event_name=XSS + e["event_name"], event_type=ATTR, location=XSS)
    for f in t["firms"]:
        f.update(firm_name=XSS + f["firm_name"], segment=ATTR, coverage_region=XSS)
    for a in t["event_attendees"]:
        a.update(contact_name=XSS, contact_title=ATTR)
    for m in t["meetings"]:
        m.update(meeting_type=XSS, internal_attendee=ATTR, purpose=XSS)
    for o in t["opportunity_stage_history"]:
        o.update(fund_name=XSS)
    return t


class TestSecurity:
    def test_no_script_runs_from_data_anywhere(self, make_page):
        page, errors = make_page(hostile_tables())
        visit_all_tabs(page)
        # open the parts that render data on demand: firm timeline, opportunity list, map popups, chips
        page.evaluate("location.hash = 'details'"); page.wait_for_timeout(200)
        page.click('#dt-tbl tbody tr[data-firm="F1"]'); page.wait_for_timeout(200)
        page.click('#dt-seg button[data-v="opps"]'); page.wait_for_timeout(200)
        page.evaluate("location.hash = 'map'"); page.wait_for_timeout(600)
        page.evaluate("""() => { document.querySelectorAll('#map path.leaflet-interactive').forEach(p =>
            p.dispatchEvent(new MouseEvent('click', {bubbles: true}))); }""")
        page.wait_for_timeout(300)
        js(page, "T.applyClick({attr: {key: 'segment', val: args[0]}, firm: 'F1'})", ATTR)
        js(page, "T.applyClick({trend: {kind: 'purpose', purpose: args[0], when: 'post'}})", XSS)
        visit_all_tabs(page)
        assert page.evaluate("window.__xss") is None
        assert page.evaluate("document.querySelectorAll('img[src=\"x\"], svg[onload]').length") == 0
        assert not errors

    def test_payload_is_shown_as_text(self, make_page):
        page, _ = make_page(hostile_tables())
        page.evaluate("location.hash = 'details'"); page.wait_for_timeout(200)
        assert '<img src=x onerror=' in page.inner_text("#dt-tbl")

    @pytest.mark.parametrize("fid, oid", [("__proto__", "constructor"), ("toString", "__proto__"), ("hasOwnProperty", "valueOf")])
    def test_ids_that_clash_with_javascript_names(self, make_page, fid, oid):
        t = copy.deepcopy(BASE)
        for table in ("firms", "event_attendees", "meetings", "opportunity_stage_history"):
            for r in t[table]:
                if r.get("firm_id") == "F1":
                    r["firm_id"] = fid
        for r in t["opportunity_stage_history"]:
            if r["opportunity_id"] == "O1":
                r["opportunity_id"] = oid
        page, errors = make_page(t)
        assert js(page, "T.compute(90, false, {}).events[0].nOpps") == 1
        visit_all_tabs(page)
        page.evaluate("location.hash = 'details'"); page.wait_for_timeout(200)
        page.click(f'#dt-tbl tbody tr[data-firm="{fid}"]'); page.wait_for_timeout(200)
        assert not errors


def test_new_quarantine_rules_show_on_data_quality_tab(make_page):
    t = copy.deepcopy(BASE)
    t["opportunity_stage_history"][3]["opportunity_amount_usd"] = -1        # DQ12
    t["opportunity_stage_history"][4]["stage"] = "Lost"                     # DQ13
    t["event_attendees"][0]["attendance_status"] = "Maybe"                  # DQ14
    page, errors = make_page(t)
    page.evaluate("location.hash = 'quality'")
    page.wait_for_timeout(300)
    text = page.inner_text("#dq-tbl")
    for rule in ("DQ12", "DQ13", "DQ14"):
        assert rule in text
    assert "undefined" not in text and not errors


class TestCodeReviewFixes:
    def test_regions_come_from_the_data(self, make_page):
        t = copy.deepcopy(BASE)
        t["firms"][3]["coverage_region"] = "Middle East"
        t["firms"][2]["coverage_region"] = "Mars"
        page, errors = make_page(t)
        assert set(js(page, "T.REGIONS")) == {"Americas", "EMEA", "Middle East", "Mars"}
        assert "Mars" not in js(page, "Object.keys(T.REG)") and "Middle East" in js(page, "Object.keys(T.REG)")
        page.evaluate("location.hash = 'map'"); page.wait_for_timeout(4500)
        assert "region Mars" in page.inner_text("#map-mode")
        assert "Mars" in page.inner_text("#region-tbl")          # still listed in the table
        assert not errors

    def test_months_follow_the_data(self, make_page):
        t = copy.deepcopy(BASE)
        t["meetings"][1]["meeting_date"] = "2024-06-15"
        page, _ = make_page(t)
        months = js(page, "T.MONTHS.map(t => new Date(t).toISOString().slice(0,7))")
        assert months[0] == "2024-06" and months[-1] == "2026-10" and len(months) == len(set(months))

    def test_firm_timeline_covers_old_activity(self, make_page):
        t = copy.deepcopy(BASE)
        t["meetings"][1]["meeting_date"] = "2023-01-10"
        page, errors = make_page(t)
        page.evaluate("location.hash = 'details'"); page.wait_for_timeout(200)
        page.click('#dt-tbl tbody tr[data-firm="F1"]'); page.wait_for_timeout(200)
        xs = page.evaluate("[...document.querySelectorAll('#dt-tbl tr.detail svg circle')].map(c => +c.getAttribute('cx'))")
        assert xs and min(xs) >= 100 and max(xs) <= 800          # every point inside the drawing
        assert not errors

    def test_duplicate_firm_flag_uses_the_name_rule(self, make_page):
        t = copy.deepcopy(BASE)
        t["firms"].append(dict(t["firms"][1], firm_id="F5", firm_name="Beta Partners Holdings"))
        page, _ = make_page(t)
        assert sorted(js(page, "T.SIMILAR")) == ["F2", "F5"]
        page.evaluate("location.hash = 'details'"); page.wait_for_timeout(200)
        page.select_option("#dt-att", "flag"); page.wait_for_timeout(200)
        assert "Possible duplicate firm" in page.inner_text("#dt-tbl")
