# AI usage

I built this with Claude (Anthropic) as a working partner. I set the working method, made or approved every decision, tested the dashboard myself, and directed what to fix or build next. Claude profiled the data, proposed options with a recommendation for each, wrote the code and documents, and ran the automated checks.

I kept the final say on architecture, system design, UI/UX and security-sensitive decisions: Claude proposed options, and nothing went in without my approval. I made sure every decision was visible and reversible.


## Where AI helped

- **Data profiling.** It found the duplicate attendance row and duplicate opportunity, the attendee with no firm, the future-dated stages, the repeated stages, and the block of near-identical meetings that may be bulk logging.
- **Code.** It wrote:
  - the pandas cleaning and KPI pipeline
  - the single-file dashboard (Chart.js and Leaflet)
  - the build, verification and test scripts
  - the generator for the PDF and slide readout
- **Wording.** It drafted the findings, assumptions, data dictionary, README and readout. I reviewed them.

## How it sped things up

Without AI, building this dashboard would take me roughly two weeks to get results that are accurate and complete. The work happens in three phases:
1.  **Data preparation**, the most intensive phase:
    -   Profiling: understanding the structure and content of the source data
    -   Cleansing: fixing errors, duplicates, and inconsistencies
    -   Validation: confirming the data is correct and complete
    -   Transformation: reshaping the data for analysis
    -   Quality monitoring: checking for issues on an ongoing basis
2.  **Data modeling**: organizing the prepared data so it supports the required metrics and views
3.  **Visualization design**: building the final dashboard layout and charts

With AI, the build took about a day of working sessions. AI sped up two key parts of the process, and I reviewed and refined its work at each step:
-   **Data transformation:** AI handled much of the heavy transformation work, which cut the time needed to get the data ready. I approved each cleaning and business rule and checked its effect in the dashboard and on the Data Quality tab.
-   **Initial visualization draft:** AI produced the first version of the dashboard. I then directed refinements to make it more interactive and meaningful for the people who will use it.

Two things made the process faster overall:
-   Each build step ended in something I could open and test, so I could redirect early instead of reworking at the end.
-   Sensitivity checks (window length, Tentative on or off, raw vs cleaned data) are built into the dashboard, so testing a conclusion takes a click rather than a rerun.

## How the output was checked

**By me**
- I used the dashboard myself in my browser, tab by tab. That's how I found the blank-chart bug (below), which none of the automated checks had caught.
- I reviewed each step's numbers and screenshots before letting it continue, and asked for progress checklists at key points.

**By Claude, at my direction**
- **The same numbers, calculated twice.** The KPIs are calculated in Python and again in the browser. A script drives the real dashboard controls and confirms the two match exactly at six settings.
- **Spot checks against the raw CSVs.** For example, London's committed $35M is O9998 ($25M) plus O0048 ($10M).
- **Automated tests.** 146 tests on synthetic data, plus scripts that:
  - open the HTML file on its own with no internet
  - click every chart
  - check that the readout is exactly one page and three slides with matching figures
- **Visual checks.** Every tab rendered in light and dark themes with the internet blocked, and every readout page and slide rendered and inspected.

## Where I changed or challenged the work

1. **I found a bug the automated checks missed.** On the Interactive charts tab, clicking a bar or the Opportunities toggle blanked every chart. I reported it and asked for the root cause, not a workaround. The cause was a chart-animation setting that crashed the chart library's shared drawing loop. The earlier checks had passed because they never clicked a chart. There is now a test that clicks every chart.
2. **I asked for more than a fix: every chart became a filter.** Clicking a bar now filters the whole dashboard. I then asked for the same on the Trends tab.
3. **I questioned the plan to include screenshots.** I asked whether someone who downloads the project gets a working dashboard or just pictures. That showed the first build loaded its chart libraries from the internet. The libraries are now built into the file.
4. **I made "works standalone" a rule, not a one-off.** The build now refuses to produce a file that needs the internet, and a test opens the file alone with no connection.
5. **I ordered a senior-engineer QA pass** with rules: synthetic test data only, never read secrets, flag anything touching login or payments. It found and fixed:
   - a script-injection hole (an injected payload ran 115 times before the fix, 0 after)
   - assumptions hard-coded to this dataset
   - a cleaning step that took 156 seconds on larger data
6. **I chose differently from the recommendation on the readout.** Claude recommended a one-page PDF only. I asked for both the PDF and a three-slide deck.
