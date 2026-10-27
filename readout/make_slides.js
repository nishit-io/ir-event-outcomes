// Build the 3-slide executive readout from facts.json (written by build_readout.py).
// Usage: node make_slides.js facts.json out.pptx   (normally run by build_readout.py)
const fs = require("fs");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const { FaUtensils, FaSearchDollar, FaTags, FaCheck } = require("react-icons/fa");

const [, , factsPath, outPath] = process.argv;
const F = JSON.parse(fs.readFileSync(factsPath, "utf8"));
const N = F.narrative;
const EV = Object.fromEntries(F.events.map(e => [e.id, e]));
const best = EV[F.best], worst = EV[F.worst], P = F.portfolio, NE = F.no_event;

// ---- formatting (same rules as the dashboard and the PDF)
const money = v => { if (v == null) return "n/a"; const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e9) return s + "$" + (a / 1e9).toFixed(2).replace(/\.?0+$/, "") + "B";
  if (a >= 1e6) return s + "$" + (a >= 1e8 ? (a / 1e6).toFixed(0) : (a / 1e6).toFixed(1).replace(/\.0$/, "")) + "M";
  if (a >= 1e3) return s + "$" + Math.round(a / 1e3) + "K";
  return s + "$" + Math.round(a).toLocaleString("en-US"); };
const pct = v => Math.round(v * 100) + "%";   // half-up, same as the dashboard and the PDF
const perDollar = v => v == null ? "n/a" : "$" + Math.round(v).toLocaleString("en-US");
const hex = c => c.replace("#", "").toUpperCase();

// ---- theme (matches the dashboard palette)
const THEME = {
  name: "IR Event Review", headFontFace: "Calibri", bodyFontFace: "Calibri",
  colors: { dk1: "15202B", lt1: "FFFFFF", dk2: "1D4F7A", lt2: "F3F5F7",
            accent1: "2F6DB5", accent2: "B8761A", accent3: "25897A", accent4: "8A96A3",
            accent5: "2B7A4B", accent6: "B0372E", hlink: "1D4F7A", folHlink: "5A6776" },
};
const MUTED = "5A6776", LINE = "D9DFE6", GREY = "8A96A3";

async function icon(Comp, color = "FFFFFF") {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color: "#" + color, size: 256 }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";                                 // 13.33" x 7.5"
  pres.title = "IR Event Readout";
  pres.subject = "Executive readout: 2026 investor events";
  pres.author = "Investor Relations analytics";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;

  // One layout for all three slides: title + lead placeholders, footer and slide number.
  pres.defineSlideMaster({
    title: "Readout content",
    background: { color: "FFFFFF" },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.13, h: 0.85, fontSize: 32, bold: true,
          color: C.text1, valign: "bottom", align: "left", margin: 0 }, text: "Slide title" } },
      { placeholder: { options: { name: "lead", type: "body", x: 0.6, y: 1.25, w: 12.13, h: 0.6, fontSize: 16,
          color: C.text2, valign: "top", align: "left", margin: 0 }, text: "Lead sentence" } },
      { text: { text: `IR executive readout · ${F.window}-day window · Confirmed attendees · data as of ${F.as_of} · synthetic data`,
          options: { x: 0.6, y: 7.0, w: 10.5, h: 0.3, fontSize: 10, color: MUTED, margin: 0, isTextBox: true } } },
    ],
    slideNumber: { x: 12.33, y: 7.0, w: 0.4, h: 0.3, fontSize: 10, color: MUTED, align: "right" },
  });
  pres.addSection({ title: "Readout" });
  const newSlide = () => pres.addSlide({ masterName: "Readout content", sectionTitle: "Readout" });

  const chartText = { catAxisLabelColor: MUTED, valAxisLabelColor: MUTED, catAxisLabelFontFace: "+mn-lt",
    valAxisLabelFontFace: "+mn-lt", dataLabelFontFace: "+mn-lt", legendFontFace: "+mn-lt", titleFontFace: "+mn-lt",
    catAxisLabelFontSize: 12, valAxisLabelFontSize: 11, dataLabelFontSize: 12, legendFontSize: 12, titleFontSize: 14,
    titleColor: "15202B", dataLabelColor: "15202B", legendColor: MUTED,
    valGridLine: { color: LINE, size: 0.75 }, catGridLine: { style: "none" }, catAxisLineShow: false, valAxisLineShow: false };

  // ================= Slide 1: what we got for the money
  {
    const s = newSlide();
    s.addText(`${best.city}'s ${best.type === "Hospitality" ? "dinner" : "event"} returned the most pipeline per dollar`, { placeholder: "title" });
    s.addText(N.headline, { placeholder: "lead" });
    const tiles = [
      ["Total event spend", money(P.cost), `${F.events.length} events · ${P.attending_firms} attending firms`],
      ["New pipeline associated", money(P.pipeline), `${P.opps} opportunities within ${F.window} days`],
      ["Pipeline per $1 of cost", perDollar(P.ppd), "across all events"],
      ["Committed so far", money(P.committed), `${pct(P.committed_share)} of associated pipeline`],
    ];
    tiles.forEach(([lab, val, det], i) => {
      const x = 0.6 + i * 3.1;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 2.05, w: 2.83, h: 1.3, fill: { color: C.background2 }, line: { color: C.background2 },
        rectRadius: 0.08, objectName: `KPI card ${i + 1}` });
      s.addText(lab.toUpperCase(), { x: x + 0.2, y: 2.15, w: 2.45, h: 0.3, fontSize: 11, bold: true, color: C.text2, charSpacing: 1, margin: 0, isTextBox: true, objectName: `KPI label ${i + 1}` });
      s.addText(val, { x: x + 0.2, y: 2.43, w: 2.45, h: 0.55, fontSize: 30, bold: true, color: C.text1, margin: 0, isTextBox: true, objectName: `KPI value ${i + 1}` });
      s.addText(det, { x: x + 0.2, y: 2.98, w: 2.45, h: 0.3, fontSize: 11, color: MUTED, margin: 0, isTextBox: true, objectName: `KPI detail ${i + 1}` });
    });
    s.addChart(pres.charts.BAR, [{ name: "Pipeline per $1", labels: F.events.map(e => e.city), values: F.events.map(e => Math.round(e.ppd || 0)) }], {
      x: 0.6, y: 3.6, w: 6.3, h: 3.2, barDir: "col", chartColors: F.events.map(e => hex(e.color)), barGapWidthPct: 70,
      showTitle: true, title: "Pipeline created per $1 of event cost", showValue: true, dataLabelPosition: "outEnd",
      dataLabelFormatCode: "$#,##0", valAxisLabelFormatCode: "$#,##0", valAxisHidden: true, showLegend: false,
      ...chartText, objectName: "Pipeline per dollar chart" });
    const hdr = ["Event", "Cost", "Cost / firm", "Met after", "Committed"].map((t, i) => ({ text: t, options: { bold: true, color: MUTED, fontSize: 12, align: i ? "right" : "left" } }));
    const rows = F.events.map(e => [
      { text: [{ text: "● ", options: { color: hex(e.color) } }, { text: e.city, options: { bold: true } }] },
      { text: money(e.cost), options: { align: "right" } }, { text: money(e.cost_per_firm), options: { align: "right" } },
      { text: pct(e.met_rate), options: { align: "right" } }, { text: money(e.committed), options: { align: "right" } }]);
    s.addTable([hdr, ...rows], { x: 7.3, y: 3.75, w: 5.43, colW: [1.55, 0.95, 1.03, 0.95, 0.95], fontSize: 14, color: C.text1,
      border: { type: "solid", pt: 0.75, color: LINE }, rowH: 0.5, valign: "middle", margin: [0.03, 0.06, 0.03, 0.06], objectName: "Event scorecard" });
    s.addText(`Met after = attending firms with a meeting within ${F.window} days.`, { x: 7.3, y: 5.95, w: 5.43, h: 0.3, fontSize: 11, color: MUTED, margin: 0, isTextBox: true, objectName: "Scorecard note" });
    s.addNotes(`Headline: ${money(P.cost)} across ${F.events.length} events sits alongside ${money(P.pipeline)} of new pipeline from attending firms within ${F.window} days, `
      + `${perDollar(P.ppd)} per dollar. ${best.city} (${best.name}) was the most efficient at ${perDollar(best.ppd)} per $1 and ${money(best.cost_per_firm)} per attending firm. `
      + `${worst.city} was the least efficient at ${perDollar(worst.ppd)} per $1 and ${money(worst.cost_per_firm)} per firm. Only ${pct(P.committed_share)} has committed so far, `
      + `so treat pipeline as a leading indicator.`);
  }

  // ================= Slide 2: association, not causation
  {
    const s = newSlide();
    s.addText("We can't yet say the events caused this pipeline", { placeholder: "title" });
    s.addText("Firms that attended no event did at least as well, and their records look incomplete. Read the event figures as association.", { placeholder: "lead" });
    const cities = F.events.map(e => e.city);
    s.addChart(pres.charts.BAR, [
      { name: "Attending firms", labels: cities, values: F.events.map(e => Math.round(e.opp_rate_attendees * 100)) },
      { name: "Firms at no event", labels: cities, values: F.events.map(e => Math.round(e.opp_rate_no_event * 100)) }], {
      x: 0.6, y: 2.0, w: 6.0, h: 3.0, barDir: "col", barGrouping: "clustered", chartColors: [THEME.colors.dk2, GREY], barGapWidthPct: 60,
      showTitle: true, title: `Share of firms opening an opportunity within ${F.window} days`, showValue: true, dataLabelPosition: "outEnd",
      dataLabelFormatCode: '0"%"', valAxisHidden: true, valAxisMaxVal: 100, showLegend: true, legendPos: "b", ...chartText,
      objectName: "Attendees vs no event chart" });
    s.addChart(pres.charts.BAR, [
      { name: `${F.window} days before`, labels: cities, values: F.events.map(e => e.pre_meetings) },
      { name: `${F.window} days after`, labels: cities, values: F.events.map(e => e.post_meetings) }], {
      x: 6.9, y: 2.0, w: 5.83, h: 3.0, barDir: "col", barGrouping: "clustered", chartColors: [GREY, THEME.colors.dk2], barGapWidthPct: 60,
      showTitle: true, title: "Meetings with attending firms, before vs after", showValue: true, dataLabelPosition: "outEnd",
      valAxisHidden: true, showLegend: true, legendPos: "b", ...chartText, objectName: "Before vs after chart" });
    const c = EV[F.climbing], s90 = F.sensitivity["90"][c.id].ppd, s180 = F.sensitivity["180"][c.id].ppd;
    const stats = [
      [`${NE.meetings}`, `logged meetings for the ${NE.firms} firms at no event, yet they opened ${NE.opps} opportunities`],
      [pct(worst.no_follow_rate), `of ${worst.city}'s attending firms had no follow-up meeting within ${F.window} days`],
      [perDollar(s180), `${c.city}'s pipeline per $1 at 180 days, against ${perDollar(s90)} at ${F.window}: likely activity unrelated to the event`],
    ];
    stats.forEach(([big, text], i) => {
      const x = 0.6 + i * 4.11;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 5.3, w: 3.88, h: 1.45, fill: { color: C.background2 }, line: { color: C.background2 }, rectRadius: 0.08, objectName: `Evidence card ${i + 1}` });
      s.addText(big, { x: x + 0.2, y: 5.38, w: 3.5, h: 0.6, fontSize: 28, bold: true, color: C.text2, margin: 0, isTextBox: true, objectName: `Evidence value ${i + 1}` });
      s.addText(text, { x: x + 0.2, y: 5.98, w: 3.5, h: 0.7, fontSize: 13, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: `Evidence text ${i + 1}` });
    });
    s.addNotes(N.findings.map(([h, t]) => `${h} ${t}`).join("\n\n") + `\n\nHow to read this: ${N.caveat}`);
  }

  // ================= Slide 3: what to do next
  {
    const s = newSlide();
    s.addText("Three actions for the next event cycle", { placeholder: "title" });
    s.addText("Keep what worked, fix what didn't, and make the next review able to measure cause, not just association.", { placeholder: "lead" });
    const icons = [await icon(FaUtensils), await icon(FaSearchDollar), await icon(FaTags)];
    const circle = [hex(best.color), hex(worst.color), THEME.colors.dk2];
    N.actions.forEach(([head, body], i) => {
      const x = 0.6 + i * 4.11;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 2.05, w: 3.88, h: 3.05, fill: { color: C.background2 }, line: { color: C.background2 }, rectRadius: 0.08, objectName: `Action card ${i + 1}` });
      s.addShape(pres.shapes.OVAL, { x: x + 0.25, y: 2.3, w: 0.75, h: 0.75, fill: { color: circle[i] }, line: { color: circle[i] }, objectName: `Action icon circle ${i + 1}` });
      s.addImage({ data: icons[i], x: x + 0.43, y: 2.48, w: 0.39, h: 0.39, altText: ["Dinner", "Review", "Tagging"][i], objectName: `Action icon ${i + 1}` });
      s.addText(head.replace(/\.$/, ""), { x: x + 0.25, y: 3.2, w: 3.4, h: 0.7, fontSize: 18, bold: true, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: `Action heading ${i + 1}` });
      s.addText(body, { x: x + 0.25, y: 3.9, w: 3.4, h: 1.1, fontSize: 14, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: `Action text ${i + 1}` });
    });
    s.addText("At the next review, we should be able to show", { x: 0.6, y: 5.35, w: 12.13, h: 0.4, fontSize: 18, bold: true, color: C.text2, margin: 0, isTextBox: true, objectName: "Targets heading" });
    const check = await icon(FaCheck, THEME.colors.accent5);
    ["Every attending firm met within 14 days", "A matched group of uninvited firms to compare against", "Every opportunity tagged with the event that sourced it"]
      .forEach((t, i) => {
        const x = 0.6 + i * 4.11;
        s.addImage({ data: check, x, y: 5.95, w: 0.3, h: 0.3, altText: "Check", objectName: `Target check ${i + 1}` });
        s.addText(t, { x: x + 0.42, y: 5.88, w: 3.45, h: 0.6, fontSize: 14, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: `Target ${i + 1}` });
      });
    s.addNotes(N.actions.map(([h, t], i) => `${i + 1}. ${h} ${t}`).join("\n\n")
      + "\n\nThe three targets turn the next review from association into something closer to attribution.");
  }

  await pres.writeFile({ fileName: outPath });   // build_readout.py then writes THEME's colours into the deck
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
