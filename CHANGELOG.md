# Changelog

All notable changes to this project. Dates are 2026.

## [1.2.0] – 27 Oct: executive readout

### Added
- `readout/IR_Event_Readout.pdf`: a 1-page executive readout covering headline figures, pipeline per $1, the event scorecard, findings, actions and the association caveat.
- `readout/IR_Event_Readout.pptx`: a 3-slide deck with native charts and speaker notes.
- `readout/build_readout.py` generates both from the model. It self-checks the page count, slide count and key figures, and `run_all_checks.py` runs it.
- `tests/test_readout.py`.

### Fixed
- Python rounded halves to the nearest even number ($262.5M became "$262M") while the dashboard rounds halves up ("$263M"). The readout now rounds the same way as the dashboard.

## [1.1.0] – 27 Oct: QA review

### Added
- **Cross-filtering.** Every bar or line on the *Interactive charts* and *Trends* tabs filters the whole dashboard: event, pipeline status, investor group, funnel step, firm, firms active in a month, firms with a given meeting purpose, or firms by days to first follow-up. Active filters show as removable chips with **Clear all**.
- **Input validation** in `build_model.py`. Structural problems stop the build with one clear message (file, column, row) and exit code 2. Bad rows are set aside and logged as new data-quality rules DQ12–DQ15.
- `--data` and `--out` options for `build_model.py`.
- **Test suite**: 133 pytest tests on synthetic data covering the model, the dashboard's JavaScript in headless Chromium, and the build. `run_all_checks.py` runs them first.
- `test_standalone.py` and a build-time standalone check: the HTML file must work on its own, offline.
- `CHANGELOG.md`.

### Fixed
- **Charts went blank** after clicking a bar or the Opportunities toggle. Replacing Chart.js's default animation settings broke colour animation and crashed its shared animation loop.
- **Script injection (XSS).** About 15 places wrote data values into the page as HTML without escaping. All data is now escaped, and a hostile-data test guards it.
- IDs such as `__proto__` or `constructor` crashed the page.
- Event labels, colours, map positions, coverage regions, Trends months, the firm timeline, the duplicate-firm flag and several summary sentences were hard-coded to the original three events and dates. They now come from the data.
- A zero-cost event produced an infinite ratio. That was invalid JSON, and the page showed "$Infinity". It now shows "n/a".
- Formatters printed "$NaN"/"NaN%", and negative money read "$-2.5M" (now "-$2.5M").
- No events, or filters matching nothing, crashed the summary.
- Data-quality messages hard-coded record IDs. They now name the records actually found.
- "Window fully observed" used a hard-coded date instead of the as-of date.
- Saved browser settings weren't validated, so a stale event ID broke the filters.
- The similar-firm-name check and the opportunity summary were very slow on larger data: 156 s → 0.8 s on 6,000 opportunities.
- Timezone-aware dates crashed the build. They are now converted to UTC calendar dates.

### Changed
- The dashboard always builds with its libraries inside the file. The internet-only build option was removed.
- Embedded data escapes every `<`, so no value can end the page's data block.

## [1.0.0] – 26 Oct: first version
- Interactive standalone dashboard with 8 tabs: executive summary, charts, map, trends, data details, data quality, assumptions and data dictionary.
- Python model with a 90-day adjustable association window, Confirmed-only default, multi-event credit, two baselines, and pipeline per $1 as the headline KPI.
- README, AI_USAGE.md, screenshots and a GitHub Pages copy.
