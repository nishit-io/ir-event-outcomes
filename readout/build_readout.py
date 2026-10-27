"""Build the executive readout: a 1-page PDF and a 3-slide PowerPoint deck.

Every number comes from build_model.py at the dashboard's default settings (90-day window,
Confirmed attendees), so the readout always matches the dashboard.

    python readout/build_readout.py

Outputs (in readout/):  facts.json, IR_Event_Readout.pdf, IR_Event_Readout.pptx
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import build_model as bm  # noqa: E402

WINDOW, TENTATIVE = 90, False
PDF = HERE / "IR_Event_Readout.pdf"
PPTX = HERE / "IR_Event_Readout.pptx"
FONTS = HERE / "fonts"
EVENT_COLORS = ["#2F6DB5", "#B8761A", "#25897A", "#8A4F9E", "#C2524A", "#6B7F2A"]   # same as the dashboard


class ReadoutError(RuntimeError):
    """The readout could not be built or failed its own checks."""


# ---------------------------------------------------------------- formatting (same rules as the dashboard)

def _r(v, places=0):
    """Round half up, like the dashboard's JavaScript (Python's round() sends 262.5 to 262, JavaScript to 263)."""
    from decimal import ROUND_HALF_UP, Decimal
    return Decimal(str(v)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def money(v):
    """$1.51B / $889M / $2.5M / $420K / $950; '-' sign before the $. Same output as the dashboard."""
    if v is None:
        return "n/a"
    a, sign = abs(v), "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${_r(a / 1e9, 2)}".rstrip("0").rstrip(".") + "B"
    if a >= 1e6:
        s = f"{_r(a / 1e6)}" if a >= 1e8 else f"{_r(a / 1e6, 1)}".rstrip("0").rstrip(".")
        return f"{sign}${s}M"
    if a >= 1e3:
        return f"{sign}${_r(a / 1e3)}K"
    return f"{sign}${int(_r(a)):,}"


def pct(v):
    return f"{_r(v * 100)}%"


def per_dollar(v):
    return "n/a" if v is None else f"${int(_r(v)):,}"


def count_word(n):
    return ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][n] if 0 <= n < 10 else str(n)


# ---------------------------------------------------------------- facts

def compute_facts():
    """All figures the readout uses, from the model at the default settings."""
    d = bm.load()
    att, att_firm, o, log = bm.clean(d)
    opps = bm.opp_summary(o)
    pe, _, port = bm.compute(d, att_firm, opps, WINDOW, TENTATIVE)
    impact = bm.cleaning_impact(d, att, att_firm, o, opps, pe, port, WINDOW, TENTATIVE)
    if pe.empty:
        raise ReadoutError("No events in the data: nothing to report.")

    events = []
    for i, r in enumerate(pe.itertuples()):
        e = d["events"].set_index("event_id").loc[r.event_id]
        events.append(dict(
            id=r.event_id, name=e.event_name, city=e.location, type=e.event_type, date=f"{e.event_date:%d %b %Y}",
            color=EVENT_COLORS[i % len(EVENT_COLORS)], cost=r.cost, firms=r.firms_attended, tier1=r.tier1_firms,
            cost_per_firm=r.cost_per_attending_firm, met_after=r.funnel_meeting,
            met_rate=r.funnel_meeting / r.firms_attended if r.firms_attended else 0.0,
            no_follow_rate=1 - r.funnel_meeting / r.firms_attended if r.firms_attended else 0.0,
            opps=r.new_opps, pipeline=r.new_pipeline, ppd=r.pipeline_per_dollar, committed=r.committed_usd,
            open=r.open_usd, declined=r.declined_usd, pre_meetings=r.pre_meetings, post_meetings=r.post_meetings,
            opp_rate_attendees=r.opp_rate_attendees, opp_rate_no_event=r.opp_rate_control))

    # window sensitivity: pipeline per $1 at other window lengths
    sens = {}
    for w in (30, 60, 90, 120, 180):
        pw, _, _ = bm.compute(d, att_firm, opps, w, TENTATIVE)
        sens[w] = {r.event_id: dict(ppd=r.pipeline_per_dollar, observed=bool(r.window_fully_observed)) for r in pw.itertuples()}

    ranked = [e for e in events if e["ppd"] is not None]
    best = max(ranked, key=lambda e: e["ppd"])
    worst = min(ranked, key=lambda e: e["ppd"])
    no_event = set(d["firms"]["firm_id"]) - set(att_firm["firm_id"])
    pooled_att = sum(r.funnel_opp for r in pe.itertuples()) / max(sum(r.firms_attended for r in pe.itertuples()), 1)
    avg_no_event = sum(e["opp_rate_no_event"] for e in events) / len(events)
    dup_effect = {k["kpi"].split(" ")[0]: k["raw"] - k["clean"] for k in impact["kpis"] if k["kpi"].endswith("pipeline $")}
    # first window length after which the best long-window event keeps climbing while others are flat
    climbing = max(events, key=lambda e: (sens[180][e["id"]]["ppd"] or 0) - (sens[90][e["id"]]["ppd"] or 0))

    return dict(
        as_of=f"{bm.AS_OF:%d %b %Y}", window=WINDOW, events=events, best=best["id"], worst=worst["id"],
        portfolio=_portfolio(d, att_firm, opps, port),
        no_event=dict(firms=len(no_event), opps=int(opps["firm_id"].isin(no_event).sum()),
                      meetings=int(d["meetings"]["firm_id"].isin(no_event).sum()), avg_rate=avg_no_event),
        attendee_rate=pooled_att, sensitivity={str(k): v for k, v in sens.items()}, climbing=climbing["id"],
        dq=dict(rules=len(log), duplicate_effect=dup_effect),
    )


def _portfolio(d, att_firm, opps, port):
    """Portfolio totals counting each associated opportunity once."""
    _, pairs, _ = bm.compute(d, att_firm, opps, WINDOW, TENTATIVE)
    linked = opps[opps["opportunity_id"].isin(set(pairs["opportunity_id"]))]
    committed = float(linked.loc[linked["status"] == "Committed", "amount"].sum())
    pipe = float(port["unique_assoc_pipeline"])
    return dict(cost=float(port["total_cost"]), pipeline=pipe, opps=int(port["unique_assoc_opps"]),
                ppd=pipe / port["total_cost"] if port["total_cost"] else None,
                committed=committed, committed_share=committed / pipe if pipe else 0.0,
                multi=int(port["multi_event_opps"]), attending_firms=int(port["attending_firms"]))


def narrative(f):
    """The readout's words, built from the facts so they can never drift from the numbers."""
    ev = {e["id"]: e for e in f["events"]}
    b, w, p, ne = ev[f["best"]], ev[f["worst"]], f["portfolio"], f["no_event"]
    up = [e for e in f["events"] if e["post_meetings"] > e["pre_meetings"]]
    down = [e for e in f["events"] if e["post_meetings"] < e["pre_meetings"]]
    s90, s180 = f["sensitivity"]["90"], f["sensitivity"]["180"]
    c = ev[f["climbing"]]
    title = f"{count_word(len(f['events'])).capitalize()} investor events: results and next steps"
    headline = (f"{money(p['cost'])} of event spend sits alongside {money(p['pipeline'])} of new pipeline from attending firms. "
                f"{b['city']} was the most efficient event and {w['city']} the least. The data shows association, not cause.")
    findings = [
        (f"{b['city']} returned the most pipeline per dollar.",
         f"The {b['name']} ({b['type'].lower()}) is associated with {per_dollar(b['ppd'])} of new pipeline per $1, at "
         f"{money(b['cost_per_firm'])} per attending firm. {w['city']} returned {per_dollar(w['ppd'])} per $1 at "
         f"{money(w['cost_per_firm'])} per firm."),
        (f"Follow-up {'fell' if down else 'held'} after {w['city']}.",
         ("Meetings rose after " + " and ".join(f"{e['city']} ({e['pre_meetings']} to {e['post_meetings']})" for e in up) + ". "
          if up else "") +
         (" and ".join(f"They fell after {e['city']} ({e['pre_meetings']} to {e['post_meetings']})" for e in down) + ", " if down else "")
         + f"and {pct(w['no_follow_rate'])} of {w['city']}'s attending firms had no follow-up meeting within {f['window']} days."),
        ("We can't yet say the events caused this pipeline.",
         f"Firms that attended no event opened opportunities more often ({pct(ne['avg_rate'])} vs {pct(f['attendee_rate'])} for "
         f"attendees). Those {ne['firms']} firms have {ne['meetings']} logged meetings but {ne['opps']} opportunities, so meeting "
         f"records look incomplete. {c['city']}'s figure also keeps rising at longer windows ({per_dollar(s90[c['id']]['ppd'])} "
         f"at 90 days, {per_dollar(s180[c['id']]['ppd'])} at 180), which suggests activity unrelated to the event."),
    ]
    actions = [
        (f"Run the {b['type'].lower() if b['type'] != 'Hospitality' else 'hosted-dinner'} format again, as a test.",
         "Hold a second one in another region and track a matched set of firms that were not invited, so the next review "
         "can see whether the event made a difference."),
        (f"Review {w['city']} before renewing.",
         f"It cost {money(w['cost'])} and {pct(w['no_follow_rate'])} of attending firms had no follow-up. "
         "Set a two-week follow-up target for every attending firm."),
        ("Record events so they can be measured.",
         "Tag each meeting and opportunity with the event that sourced it, confirm who actually attended, and remove "
         "duplicate records."),
    ]
    caveat = (f"How to read this: an opportunity counts for an event when a firm that attended opens it within {f['window']} days. "
              "Firms are invited because they are already engaged, so these figures show what happened alongside the events, "
              "not what the events caused. Three events is a small sample.")
    biggest = max(f["dq"]["duplicate_effect"].items(), key=lambda kv: abs(kv[1]), default=(None, 0))
    dq_note = (f" (largest effect: {money(abs(biggest[1]))} removed from {ev[biggest[0]]['city']}'s pipeline)"
               if biggest[0] in ev and biggest[1] else "")
    method = (f"{f['window']}-day window · Confirmed attendees · each opportunity counted once in totals · "
              f"data as of {f['as_of']} · {f['dq']['rules']} data-quality rules applied{dq_note} · synthetic assessment data")
    return dict(title=title, headline=headline, findings=findings, actions=actions, caveat=caveat, method=method)


# ---------------------------------------------------------------- chart for the PDF

def register_fonts():
    from matplotlib import font_manager
    for f in FONTS.glob("*.ttf"):
        font_manager.fontManager.addfont(str(f))


def ppd_chart(f, path):
    """Bar chart: pipeline created per $1 of event cost, one bar per event."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    register_fonts()
    plt.rcParams.update({"font.family": "IBM Plex Sans", "font.size": 8.5})
    ev = f["events"]
    fig, ax = plt.subplots(figsize=(3.55, 2.45), dpi=300)
    vals = [e["ppd"] or 0 for e in ev]
    bars = ax.bar([e["city"] for e in ev], vals, color=[e["color"] for e in ev], width=0.6, zorder=2)
    for b_, e in zip(bars, ev):
        ax.text(b_.get_x() + b_.get_width() / 2, b_.get_height() + max(vals) * 0.02, per_dollar(e["ppd"]),
                ha="center", va="bottom", fontsize=9, fontweight="semibold", color="#15202B")
        ax.text(b_.get_x() + b_.get_width() / 2, -max(vals) * 0.17, f"cost {money(e['cost'])}",
                ha="center", va="top", fontsize=7, color="#5A6776")
    ax.set_ylim(0, max(vals) * 1.18)
    ax.yaxis.set_visible(False)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#D9DFE6")
    ax.tick_params(axis="x", length=0, labelsize=8.5, colors="#15202B", pad=3)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.97, bottom=0.2)
    fig.savefig(path, dpi=300)
    plt.close(fig)


# ---------------------------------------------------------------- PDF

def build_pdf(f, n, chart_png):
    """One US-letter page: headline, KPI tiles, chart + scorecard, findings, actions, caveat."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape as x

    for name in ["IBMPlexSans-Regular", "IBMPlexSans-Medium", "IBMPlexSans-SemiBold", "IBMPlexSans-Bold",
                 "IBMPlexSans-Italic", "IBMPlexSansCondensed-Bold", "IBMPlexSansCondensed-SemiBold", "IBMPlexMono-Regular",
                 "IBMPlexMono-Medium"]:
        pdfmetrics.registerFont(TTFont(name, str(FONTS / f"{name}.ttf")))
    pdfmetrics.registerFontFamily("IBMPlexSans", normal="IBMPlexSans-Regular", bold="IBMPlexSans-SemiBold",
                                  italic="IBMPlexSans-Italic", boldItalic="IBMPlexSans-SemiBold")
    INK, MUTED, LINE, ACCENT, SUNK = HexColor("#15202B"), HexColor("#5A6776"), HexColor("#D9DFE6"), HexColor("#1D4F7A"), HexColor("#EEF1F4")
    WARN = HexColor("#A8650A")

    def st(name, **kw):
        base = dict(fontName="IBMPlexSans-Regular", fontSize=9.6, leading=13.2, textColor=INK, alignment=TA_LEFT)
        base.update(kw)
        return ParagraphStyle(name, **base)
    eyebrow = st("eyebrow", fontName="IBMPlexSans-SemiBold", fontSize=7.8, leading=10, textColor=MUTED)
    h1 = st("h1", fontName="IBMPlexSansCondensed-Bold", fontSize=21, leading=25)
    lead = st("lead", fontSize=10.8, leading=14.4)
    h2 = st("h2", fontName="IBMPlexSansCondensed-Bold", fontSize=13, leading=16, spaceAfter=4)
    body = st("body")
    small = st("small", fontSize=7.8, leading=10, textColor=MUTED)
    kl = st("kl", fontName="IBMPlexSans-SemiBold", fontSize=7.2, leading=9, textColor=MUTED)
    kv = st("kv", fontName="IBMPlexMono-Medium", fontSize=19, leading=23)
    kd = st("kd", fontSize=7.8, leading=10, textColor=MUTED)
    th = st("th", fontName="IBMPlexSans-SemiBold", fontSize=6.6, leading=8.4, textColor=MUTED)
    td = st("td", fontSize=9, leading=11.5)
    tdn = st("tdn", fontName="IBMPlexMono-Regular", fontSize=8.8, leading=11.5, alignment=2)
    thr = ParagraphStyle("thr", parent=th, alignment=2)   # inherit th exactly (st() would reset the font)

    p = f["portfolio"]
    doc = SimpleDocTemplate(str(PDF), pagesize=letter, leftMargin=0.55 * inch, rightMargin=0.55 * inch,
                            topMargin=0.42 * inch, bottomMargin=0.4 * inch,
                            title="IR Event Readout", author="Investor Relations analytics", invariant=True,
                            subject="Executive readout: 2026 investor events")
    W = letter[0] - 1.1 * inch
    story = [Paragraph(x(f"INVESTOR RELATIONS · EXECUTIVE READOUT · DATA AS OF {f['as_of'].upper()}"), eyebrow), Spacer(1, 3),
             Paragraph(x(n["title"]), h1), Spacer(1, 4), Paragraph(x(n["headline"]), lead), Spacer(1, 9)]

    tiles = [("TOTAL EVENT SPEND", money(p["cost"]), f"{len(f['events'])} events · {p['attending_firms']} attending firms"),
             ("NEW PIPELINE ASSOCIATED", money(p["pipeline"]), f"{p['opps']} opportunities within {f['window']} days"),
             ("PIPELINE PER $1 OF COST", per_dollar(p["ppd"]), "across all events"),
             ("COMMITTED SO FAR", money(p["committed"]), f"{pct(p['committed_share'])} of associated pipeline")]
    tile_tbl = Table([[[Paragraph(a, kl), Paragraph(b_, kv), Paragraph(x(c), kd)] for a, b_, c in tiles]],
                     colWidths=[W / 4] * 4)
    tile_tbl.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
                                  ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                  ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [tile_tbl, Spacer(1, 9)]

    # chart (left) + scorecard (right)
    chart_w = 3.2 * inch
    chart = [Paragraph("Pipeline created per $1 of event cost", h2), Image(str(chart_png), width=chart_w, height=chart_w * 2.45 / 3.55)]
    rows = [[Paragraph("EVENT", th), Paragraph("PER FIRM", thr), Paragraph("MET AFTER", thr),
             Paragraph("PIPELINE", thr), Paragraph("COMMITTED", thr)]]
    for e in f["events"]:
        rows.append([Paragraph(f'<font color="{e["color"]}"><b>{x(e["city"])}</b></font><br/>'
                               f'<font size="7.6" color="#5A6776">{e["firms"]} firms</font>', td),
                     Paragraph(money(e["cost_per_firm"]), tdn), Paragraph(pct(e["met_rate"]), tdn),
                     Paragraph(money(e["pipeline"]), tdn), Paragraph(money(e["committed"]), tdn)])
    right_w = W - chart_w - 0.25 * inch
    score = Table(rows, colWidths=[right_w * 0.24] + [right_w * 0.19] * 4)
    score.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    score_cell = [Paragraph("Event scorecard", h2), score, Spacer(1, 5),
                  Paragraph(x(f"Per firm = event cost per attending firm. Met after = share of attending firms with a meeting "
                              f"within {f['window']} days. Committed = linked opportunities that have already committed."), small)]
    mid = Table([[chart, score_cell]], colWidths=[chart_w + 0.25 * inch, right_w])
    mid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [mid, Spacer(1, 10)]

    def numbered(items):
        out = []
        for i, (head, text) in enumerate(items, 1):
            out.append(Paragraph(f'<font name="IBMPlexMono-Medium" color="#1D4F7A">{i}</font>&nbsp;&nbsp;'
                                 f'<b>{x(head)}</b> {x(text)}', st(f"li{i}", leftIndent=12, firstLineIndent=-12, spaceAfter=6)))
        return out
    cols = Table([[[Paragraph("What the data shows", h2)] + numbered(n["findings"]),
                   [Paragraph("What to do next", h2)] + numbered(n["actions"])]],
                 colWidths=[W / 2, W / 2])
    cols.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 0),
                              ("RIGHTPADDING", (0, 0), (0, 0), 10), ("LEFTPADDING", (1, 0), (1, 0), 10),
                              ("RIGHTPADDING", (1, 0), (1, 0), 0), ("LINEBEFORE", (1, 0), (1, 0), 0.5, LINE)]))
    story += [cols, Spacer(1, 4)]

    cav = Table([[Paragraph(f'<font color="#A8650A"><b>Association, not causation.</b></font> {x(n["caveat"])}', body)]],
                colWidths=[W])
    cav.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SUNK), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 6),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [cav, Spacer(1, 5), Paragraph(x(n["method"]), small)]
    del WARN, ACCENT

    from reportlab.platypus.doctemplate import LayoutError
    frame_h = doc.height - 12          # the page frame keeps 6pt padding top and bottom
    try:
        doc.build([KeepInFrame(W, frame_h, story, mode="error")])   # never shrink or spill to page 2
    except LayoutError:
        raise ReadoutError("the readout no longer fits on one page; shorten the text or reduce spacing") from None


# ---------------------------------------------------------------- slides (Node + pptxgenjs)

THEME = dict(name="IR Event Review", major="Calibri", minor="Calibri",
             colors=dict(dk1="15202B", lt1="FFFFFF", dk2="1D4F7A", lt2="F3F5F7", accent1="2F6DB5", accent2="B8761A",
                         accent3="25897A", accent4="8A96A3", accent5="2B7A4B", accent6="B0372E", hlink="1D4F7A",
                         folHlink="5A6776"))


FIXED_DATE = (2026, 10, 26, 0, 0, 0)          # the data's as-of date: keeps rebuilt files byte-identical


def _normalize_zip(data, theme=None):
    """Rewrite an Office zip with fixed timestamps and IDs; apply `theme` to its theme parts if given.

    pptxgenjs stamps the build time into the file, the zip entries and each chart's embedded
    workbook, and gives sections a random ID. Fixing them makes rebuilds byte-identical, so
    re-running the checks never shows the readout as changed in git.
    """
    import hashlib
    import io
    import re
    import zipfile
    iso = "%04d-%02d-%02dT%02d:%02d:%02dZ" % FIXED_DATE
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in sorted(zin.infolist(), key=lambda i: i.filename != "[Content_Types].xml"):
            body = zin.read(item.filename)
            name = item.filename
            if name.endswith(".xlsx"):
                body = _normalize_zip(body)
            elif name == "docProps/core.xml":
                body = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(<)", rb"\g<1>" + iso.encode() + rb"\g<2>", body)
            elif name == "ppt/presentation.xml":
                def fixed_guid(m):
                    h = hashlib.md5(m.group(1)).hexdigest()
                    return b'name="' + m.group(1) + b'" id="{' + f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}".encode() + b'}"'
                body = re.sub(rb'name="([^"]*)" id="\{[0-9a-fA-F-]+\}"', fixed_guid, body)
            elif theme and re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
                xml = body.decode("utf-8")
                for slot, rgb in theme["colors"].items():
                    xml, n = re.subn(rf"(<a:{slot}>)(.*?)(</a:{slot}>)", rf'\g<1><a:srgbClr val="{rgb}"/>\g<3>', xml, count=1, flags=re.S)
                    if n != 1:
                        raise ReadoutError(f"theme colour slot {slot} not found in {name}")
                xml = re.sub(r'(<a:clrScheme name=")[^"]*(")', rf"\g<1>{theme['name']}\g<2>", xml, count=1)
                xml = re.sub(r'(<a:majorFont>\s*<a:latin typeface=")[^"]*(")', rf"\g<1>{theme['major']}\g<2>", xml, count=1)
                xml = re.sub(r'(<a:minorFont>\s*<a:latin typeface=")[^"]*(")', rf"\g<1>{theme['minor']}\g<2>", xml, count=1)
                body = xml.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = item.external_attr
            zout.writestr(info, body)
    return out.getvalue()


def apply_theme(path, theme=THEME):
    """Write the readout's colours and fonts into the deck's theme and make the file reproducible."""
    path = Path(path)
    path.write_bytes(_normalize_zip(path.read_bytes(), theme))


def build_pptx(facts_path):
    """Run make_slides.js (pptxgenjs), then apply the theme colours and fonts."""
    r = subprocess.run(["node", str(HERE / "make_slides.js"), str(facts_path), str(PPTX)],
                       capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0:
        raise ReadoutError("Slide build failed (needs Node with pptxgenjs, react, react-dom, react-icons, sharp):\n"
                           + (r.stderr or r.stdout)[-1500:])
    apply_theme(PPTX)


# ---------------------------------------------------------------- self-checks

def check_outputs(f, n):
    """The PDF must be exactly one page and both files must contain the key numbers."""
    from pypdf import PdfReader
    pages = PdfReader(str(PDF)).pages
    if len(pages) != 1:
        raise ReadoutError(f"PDF has {len(pages)} pages; it must be 1")
    pdf_text = pages[0].extract_text().replace("\n", " ")
    from pptx import Presentation
    deck = Presentation(str(PPTX))
    if len(deck.slides) != 3:
        raise ReadoutError(f"Deck has {len(deck.slides)} slides; it must be 3")
    texts, chart_values = [], []
    for s in deck.slides:
        for sh in s.shapes:
            if sh.has_text_frame:
                texts.append(sh.text_frame.text)
            if sh.has_table:
                texts += [c.text for r in sh.table.rows for c in r.cells]
            if sh.has_chart:
                for plot in sh.chart.plots:
                    texts += list(plot.categories)
                    for ser in plot.series:
                        chart_values.append((sh.name, list(ser.values)))
    deck_text = " ".join(texts)
    p = f["portfolio"]
    must = [money(p["cost"]), money(p["pipeline"]), per_dollar(p["ppd"])] + [e["city"] for e in f["events"]]
    for label, text in (("PDF", pdf_text), ("deck", deck_text)):
        missing = [m for m in must if m not in text]
        if missing:
            raise ReadoutError(f"{label} is missing figures: {missing}")
    missing_pdf = [per_dollar(e["ppd"]) for e in f["events"] if per_dollar(e["ppd"]) not in pdf_text]
    if missing_pdf:
        raise ReadoutError(f"PDF is missing per-event figures: {missing_pdf}")
    ppd_chart_vals = next((v for name, v in chart_values if name == "Pipeline per dollar chart"), None)
    if ppd_chart_vals != [round(e["ppd"] or 0) for e in f["events"]]:
        raise ReadoutError(f"deck chart values {ppd_chart_vals} do not match the model")


def main():
    facts = compute_facts()
    n = narrative(facts)
    facts["narrative"] = n
    facts_path = HERE / "facts.json"
    facts_path.write_text(json.dumps(facts, indent=1, default=float, ensure_ascii=False), encoding="utf-8")
    chart_png = HERE / ".ppd_chart.png"
    ppd_chart(facts, chart_png)
    build_pdf(facts, n, chart_png)
    chart_png.unlink()
    build_pptx(facts_path)
    check_outputs(facts, n)
    print(f"wrote {PDF.relative_to(ROOT)} (1 page) and {PPTX.relative_to(ROOT)} (3 slides); figures checked")


if __name__ == "__main__":
    try:
        main()
    except (ReadoutError, bm.DataError) as e:
        sys.exit(f"Readout not built: {e}")
