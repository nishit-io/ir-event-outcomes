"""
IR Event Analytics: data model, cleaning rules and KPI computation.

Usage:  python build_model.py [--window 90] [--include-tentative] [--data DIR] [--out DIR]
Outputs: <out>/model.json   (cleaned tables + precomputed KPIs for the dashboard)
         printed summary of key numbers

Agreed methodology
------------------
* Association window  : opportunity CREATED (or meeting held) within (event_date, event_date + W] days.
                        W adjustable; default 90.
* Attendance          : Confirmed only by default; --include-tentative adds Tentative rows.
                        A firm "attended" an event if >=1 qualifying contact attended.
* Multi-event firms   : each event gets full credit; portfolio totals count each opportunity once.
* Baselines           : (a) attendees vs non-attendees (firms at no event) over the same window;
                        (b) attending firms' activity in the W days before vs W days after the event.
* Headline KPI        : associated new pipeline $ per $1 of event cost, plus a conversion funnel.
* This is ASSOCIATION, not causation.

Bad input (agreed rule)
-----------------------
* Structural problems stop the build with a DataError naming the file, column and row:
  missing file or column, duplicate IDs in a master table, unparseable dates, non-numeric or
  negative money values.
* Row-level problems are set aside and logged as data-quality rules (shown on the Data Quality
  tab), and the build continues: zero/negative/missing opportunity amounts, unknown stages,
  unknown attendance statuses, rows that point to a firm or event that does not exist.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "out"
AS_OF = pd.Timestamp("2026-10-26")
STAGES = ["Initial Conversation", "Follow-up / VDR", "Due Diligence", "IC / Documentation", "Committed"]
STAGE_RANK = {s: i for i, s in enumerate(STAGES)}
KNOWN_STAGES = set(STAGES) | {"Declined"}
KNOWN_STATUSES = {"Confirmed", "Tentative"}
MAX_WINDOW_DAYS = 3650

SCHEMA = {
    "events": {"cols": ["event_id", "event_name", "event_date", "event_type", "location", "cost_usd"],
               "key": "event_id", "dates": ["event_date"], "money": ["cost_usd"]},
    "firms": {"cols": ["firm_id", "firm_name", "segment", "priority", "historical_commitments_usd", "coverage_region"],
              "key": "firm_id", "dates": [], "money": ["historical_commitments_usd"]},
    "event_attendees": {"cols": ["attendance_id", "event_id", "firm_id", "contact_name", "contact_title", "attendance_status"],
                        "key": None, "dates": [], "money": []},
    "meetings": {"cols": ["meeting_id", "firm_id", "meeting_date", "meeting_type", "internal_attendee", "purpose"],
                 "key": "meeting_id", "dates": ["meeting_date"], "money": []},
    "opportunity_stage_history": {"cols": ["opportunity_id", "firm_id", "fund_name", "created_date", "stage_date",
                                           "stage", "opportunity_amount_usd"],
                                  "key": None, "dates": ["created_date", "stage_date"], "money": []},
}


class DataError(ValueError):
    """Structural problem in an input file. The message names the file, column and row."""


def _ids(series, limit=6):
    """Format a list of IDs for a data-quality message, e.g. 'A0011, A0042 (+3 more)'."""
    vals = [str(v) for v in series]
    more = f" (+{len(vals) - limit} more)" if len(vals) > limit else ""
    return ", ".join(vals[:limit]) + more


def _csv_row(idx):
    """Row number as a person sees it in the CSV (header is row 1)."""
    return int(idx) + 2


def parse_dates(df, col, table):
    """Parse a date column to timezone-naive UTC calendar dates.

    Timezone-aware values are converted to UTC first, then the time of day is dropped, so
    every date in the model is a plain UTC calendar date (the dashboard uses UTC as well).
    Raises DataError naming the first unparseable value.
    """
    raw = df[col]
    parsed = pd.to_datetime(raw, errors="coerce", utc=True, format="mixed")
    bad = parsed.isna() & raw.notna() & (raw.astype(str).str.strip() != "")
    missing = raw.isna() | (raw.astype(str).str.strip() == "")
    if bad.any() or missing.any():
        i = (bad | missing).idxmax()
        raise DataError(f"{table}.csv, column '{col}', row {_csv_row(i)}: "
                        f"expected a date like 2026-03-12, got {str(raw[i])[:40]!r}")
    return parsed.dt.tz_localize(None).dt.normalize()


def parse_money(df, col, table, allow_negative=False):
    """Parse a money column to float. Raises DataError on non-numeric (or negative) values."""
    num = pd.to_numeric(df[col], errors="coerce")
    bad = num.isna() & df[col].notna()
    if bad.any():
        i = bad.idxmax()
        raise DataError(f"{table}.csv, column '{col}', row {_csv_row(i)}: "
                        f"expected a number, got {str(df[col][i])[:40]!r}")
    if not allow_negative and (num < 0).any():
        i = (num < 0).idxmax()
        raise DataError(f"{table}.csv, column '{col}', row {_csv_row(i)}: amount cannot be negative ({num[i]:,.0f})")
    return num


def load(data_dir=DATA):
    """Read and validate the five CSVs. Returns a dict of DataFrames keyed by table name.

    Raises DataError for structural problems (see module docstring). Row-level problems are
    left in place for clean() to set aside and log.
    """
    data_dir = Path(data_dir)
    d = {}
    for name, spec in SCHEMA.items():
        path = data_dir / f"{name}.csv"
        if not path.is_file():
            raise DataError(f"Missing input file: {name}.csv (looked in {data_dir.name}/)")
        try:
            df = pd.read_csv(path, dtype={c: "string" for c in spec["cols"] if c.endswith("_id") or c in
                                          ("stage", "attendance_status", "firm_name", "event_name")},
                             encoding="utf-8")
        except UnicodeDecodeError:
            raise DataError(f"{name}.csv is not valid UTF-8 text") from None
        except pd.errors.ParserError as e:
            raise DataError(f"{name}.csv could not be read as CSV ({str(e).splitlines()[0][:120]})") from None
        missing = [c for c in spec["cols"] if c not in df.columns]
        if missing:
            raise DataError(f"{name}.csv is missing column(s): {', '.join(missing)}")
        key = spec["key"]
        if key:
            if df[key].isna().any():
                raise DataError(f"{name}.csv, column '{key}', row {_csv_row(df[key].isna().idxmax())}: ID is empty")
            dup = df[key].duplicated(keep=False)
            if dup.any():
                raise DataError(f"{name}.csv has duplicate {key} values: {_ids(df.loc[dup, key].unique())}")
        for col in spec["dates"]:
            df[col] = parse_dates(df, col, name)
        for col in spec["money"]:
            df[col] = parse_money(df, col, name)
        d[name] = df
    # opportunity amounts: must be numeric; zero/negative are row-level (quarantined in clean())
    o = d["opportunity_stage_history"]
    o["opportunity_amount_usd"] = parse_money(o, "opportunity_amount_usd", "opportunity_stage_history",
                                              allow_negative=True)
    return d


def _log(log, rule, table, issue, action, rows):
    """Append a data-quality rule to the log, only when it found something."""
    if rows:
        log.append(dict(rule=rule, table=table, issue=issue, action=action, rows=int(rows)))


def clean(d, as_of=AS_OF):
    """Apply the documented cleaning rules.

    Returns (attendees, attendees_with_firm, stage_history, dq_log). Every rule that changes or
    flags data is recorded in dq_log with the IDs it affected.
    """
    log = []
    events, firms, m = d["events"], d["firms"], d["meetings"]
    firm_ids, event_ids = set(firms["firm_id"]), set(events["event_id"])

    # ---- attendees ----
    att = d["event_attendees"].copy()
    dup = att.duplicated()
    _log(log, "DQ1", "event_attendees", f"Exact duplicate attendance rows ({_ids(att.loc[dup, 'attendance_id'])})",
         "Removed duplicate", dup.sum())
    att = att[~dup]
    unm = att["firm_id"].isna()
    _log(log, "DQ2", "event_attendees", f"Attendee with no firm_id ({_ids(att.loc[unm, 'attendance_id'])})",
         "Excluded from firm-level metrics; kept in contact counts", unm.sum())
    bad_status = ~att["attendance_status"].isin(KNOWN_STATUSES)
    _log(log, "DQ14", "event_attendees",
         f"Unknown attendance status ({_ids(att.loc[bad_status, 'attendance_status'].astype(str).unique())})",
         "Set aside: not counted as attendance", bad_status.sum())
    orphan_att = (att["firm_id"].notna() & ~att["firm_id"].isin(firm_ids)) | ~att["event_id"].isin(event_ids)
    _log(log, "DQ15", "event_attendees",
         f"Attendance rows pointing to an unknown firm or event ({_ids(att.loc[orphan_att, 'attendance_id'])})",
         "Set aside", orphan_att.sum())
    att = att[~bad_status & ~orphan_att]
    att_firm = att[att["firm_id"].notna()].copy()

    # ---- opportunity stage history ----
    o = d["opportunity_stage_history"].copy()
    bad_amt = o["opportunity_amount_usd"].isna() | (o["opportunity_amount_usd"] <= 0)
    _log(log, "DQ12", "opportunity_stage_history",
         f"Missing, zero or negative opportunity amount ({_ids(o.loc[bad_amt, 'opportunity_id'].unique())})",
         "Set aside: whole opportunity excluded", o["opportunity_id"].isin(o.loc[bad_amt, "opportunity_id"]).sum())
    o = o[~o["opportunity_id"].isin(o.loc[bad_amt, "opportunity_id"])]
    bad_stage = ~o["stage"].isin(KNOWN_STAGES)
    _log(log, "DQ13", "opportunity_stage_history",
         f"Unknown stage name ({_ids(o.loc[bad_stage, 'stage'].astype(str).unique())})",
         "Set aside: stage row excluded", bad_stage.sum())
    o = o[~bad_stage]
    orphan_o = ~o["firm_id"].isin(firm_ids)
    _log(log, "DQ15", "opportunity_stage_history",
         f"Opportunities pointing to an unknown firm ({_ids(o.loc[orphan_o, 'opportunity_id'].unique())})",
         "Set aside", orphan_o.sum())
    o = o[~orphan_o]

    # DQ3: the same opportunity recorded under two IDs (identical rows apart from the ID)
    key = ["firm_id", "fund_name", "created_date", "stage_date", "stage", "opportunity_amount_usd"]
    srt = o.sort_values(["opportunity_id", "stage_date", "stage"], kind="stable")
    row_sig = srt[key].astype(str).agg("\x1f".join, axis=1)
    sig = row_sig.groupby(srt["opportunity_id"], sort=True).agg("\x1e".join)
    dupd = sig[sig.duplicated()]
    first_id = sig.reset_index().drop_duplicates(sig.name or 0, keep="first").set_index(sig.name or 0)["opportunity_id"]
    pairs = [f"{oid} duplicates {first_id[s_]}" for oid, s_ in dupd.items()]
    _log(log, "DQ3", "opportunity_stage_history", f"Duplicate opportunity record under a different ID ({_ids(pairs)})",
         "Kept first ID, removed duplicate", len(dupd))
    o = o[~o["opportunity_id"].isin(dupd.index)]

    # DQ4: no stage history before its first recorded stage
    first_stage = o.sort_values("stage_date").groupby("opportunity_id")["stage"].first()
    skip = first_stage[first_stage != "Initial Conversation"].index
    _log(log, "DQ4", "opportunity_stage_history", f"Opportunity with no stage history before {_ids(first_stage[skip].unique())} ({_ids(skip)})",
         "Kept; flagged as possible manual/back-dated entry", len(skip))

    # DQ5: stage dates after the as-of date
    fut = o["stage_date"] > as_of
    _log(log, "DQ5", "opportunity_stage_history",
         f"Stage dates after as-of date {as_of:%Y-%m-%d}: " +
         ", ".join(f"{r.opportunity_id} {r.stage} {r.stage_date:%Y-%m-%d}" for r in o[fut].itertuples()),
         "Treated as not yet happened (rows excluded)", fut.sum())
    o = o[~fut]

    # DQ6: same stage logged twice in a row
    o = o.sort_values(["opportunity_id", "stage_date"], kind="stable")
    # fillna(False): the first stage of each opportunity has no previous stage (NA), which must count as "not repeated"
    rep = o["stage"].eq(o.groupby("opportunity_id")["stage"].shift()).fillna(False).astype(bool)
    _log(log, "DQ6", "opportunity_stage_history", "Same stage logged twice in a row (mostly IC / Documentation)",
         "Kept first occurrence as stage-entry date", rep.sum())
    o = o[~rep]

    # DQ7: a firm name that extends another firm's name by whole words ("Investor Firm 08 Holdings" vs
    # "Investor Firm 08"). Looks up each name's word prefixes in a dict: O(firms x words), not O(firms^2).
    by_name = {}
    for r in firms[["firm_id", "firm_name"]].dropna().itertuples():
        by_name.setdefault(" ".join(str(r.firm_name).split()), r.firm_id)
    similar = []
    for r in firms[["firm_id", "firm_name"]].dropna().itertuples():
        words = str(r.firm_name).split()
        for k in range(len(words) - 1, 0, -1):
            other = by_name.get(" ".join(words[:k]))
            if other is not None and other != r.firm_id:
                similar.append(f"{r.firm_id} '{r.firm_name}' may be the same entity as {other}")
                break
    _log(log, "DQ7", "firms", "; ".join(similar[:3]) + (f" (+{len(similar) - 3} more)" if len(similar) > 3 else ""),
         "Kept separate; flagged", len(similar))

    # ---- meetings (informational) ----
    orphan_m = ~m["firm_id"].isin(firm_ids)
    _log(log, "DQ15", "meetings", f"Meetings pointing to an unknown firm ({_ids(m.loc[orphan_m, 'meeting_id'])})",
         "Kept in the file; never matched to a firm", orphan_m.sum())
    sameday = m.duplicated(["firm_id", "meeting_date"], keep=False)
    same = m[sameday].drop_duplicates(["firm_id", "meeting_date"])
    _log(log, "DQ8", "meetings", "Same firm, same day, multiple meetings (" +
         _ids([f"{r.firm_id} {r.meeting_date:%Y-%m-%d}" for r in same.itertuples()]) + ")",
         "Kept both (different type & purpose)", sameday.sum())
    bulk = (m["internal_attendee"] == "Coverage B") & (m["meeting_type"] == "Video") & (m["purpose"] == "Relationship")
    if bulk.any():
        ids = sorted(m.loc[bulk, "meeting_id"].astype(str))
        _log(log, "DQ9", "meetings",
             f"Block of near-identical 'Coverage B / Video / Relationship' meetings ({ids[0]}–{ids[-1]}): possible bulk logging",
             "Kept; sensitivity shown in dashboard", bulk.sum())
    no_ev = set(firms["firm_id"]) - set(att_firm["firm_id"])
    if no_ev and not m["firm_id"].isin(no_ev).any() and d["opportunity_stage_history"]["firm_id"].isin(no_ev).any():
        _log(log, "DQ10", "meetings",
             f"None of the {len(no_ev)} firms that attended no event has a logged meeting, yet they have opportunities",
             "Kept; caveat added to the attendee vs non-attendee comparison", len(no_ev))
    zero = firms["historical_commitments_usd"].eq(0)
    _log(log, "DQ11", "firms", f"Firms with $0 historical commitments ({_ids(firms.loc[zero, 'firm_id'])})",
         "Kept; treated as new relationships", zero.sum())
    log.sort(key=lambda r: int(r["rule"][2:]))
    return att, att_firm, o, log


def opp_summary(o):
    """Collapse stage history to one row per opportunity.

    Status is the latest stage if it is Committed or Declined, otherwise Open. max_stage ignores
    Declined. dd_date / committed_date are the first dates those stages were reached (NaT if never).
    """
    cols = ["opportunity_id", "firm_id", "fund_name", "created_date", "amount", "max_stage", "max_stage_rank",
            "status", "last_stage", "last_stage_date", "dd_date", "committed_date"]
    if o.empty:
        return pd.DataFrame(columns=cols)
    g = o.sort_values(["opportunity_id", "stage_date"], kind="stable")
    by = g["opportunity_id"]
    first = g.drop_duplicates("opportunity_id", keep="first").set_index("opportunity_id")
    last = g.drop_duplicates("opportunity_id", keep="last").set_index("opportunity_id")
    rank = g[g["stage"] != "Declined"]["stage"].map(STAGE_RANK).groupby(by).max()
    dd = g[g["stage"] == "Due Diligence"].groupby("opportunity_id")["stage_date"].min()
    cm = g[g["stage"] == "Committed"].groupby("opportunity_id")["stage_date"].min()
    out = pd.DataFrame(index=first.index)
    out["firm_id"] = first["firm_id"]
    out["fund_name"] = first["fund_name"]
    out["created_date"] = first["created_date"]
    out["amount"] = first["opportunity_amount_usd"].astype(float)
    r = rank.reindex(out.index)
    out["max_stage"] = r.map(lambda v: STAGES[int(v)] if pd.notna(v) else None)
    out["max_stage_rank"] = r.fillna(-1).astype(int)
    out["status"] = last["stage"].where(last["stage"].isin(["Committed", "Declined"]), "Open").astype(object)
    out["last_stage"] = last["stage"].astype(object)
    out["last_stage_date"] = last["stage_date"]
    out["dd_date"] = dd.reindex(out.index)
    out["committed_date"] = cm.reindex(out.index)
    return out.reset_index()[cols]


PER_EVENT_COLS = ["event_id", "event_name", "cost", "firms_attended", "contacts", "tier1_firms", "post_meetings",
                  "pre_meetings", "funnel_meeting", "funnel_opp", "funnel_dd", "funnel_commit", "new_opps", "pre_opps",
                  "new_pipeline", "committed_usd", "open_usd", "declined_usd", "pipeline_per_dollar",
                  "cost_per_attending_firm", "opp_rate_attendees", "opp_rate_control", "window_fully_observed"]


def compute(d, att_firm, opps, window, include_tentative, as_of=AS_OF):
    """Event KPIs for one window length.

    Returns (per_event DataFrame, (event_id, opportunity_id) association pairs, portfolio dict).
    pipeline_per_dollar and cost_per_attending_firm are None when an event's cost is 0.
    """
    if not isinstance(window, int) or isinstance(window, bool) or not 1 <= window <= MAX_WINDOW_DAYS:
        raise ValueError(f"window must be a whole number of days from 1 to {MAX_WINDOW_DAYS}, got {window!r}")
    ev, firms, m = d["events"], d["firms"], d["meetings"]
    a = att_firm if include_tentative else att_firm[att_firm["attendance_status"] == "Confirmed"]
    attended = a.groupby("event_id")["firm_id"].apply(set).to_dict()
    any_att = set(att_firm["firm_id"])            # firms at any event (any status) → excluded from control
    control = set(firms["firm_id"]) - any_att
    W = pd.Timedelta(days=window)
    per_event, assoc_pairs = [], []
    for e in ev.itertuples():
        fs = attended.get(e.event_id, set())
        d0 = e.event_date
        def post(s, d0=d0): return (s > d0) & (s <= d0 + W)
        def pre(s, d0=d0): return (s >= d0 - W) & (s < d0)
        mf = m[m["firm_id"].isin(fs)]
        new_o = opps[opps["firm_id"].isin(fs) & post(opps["created_date"])]
        pre_o = opps[opps["firm_id"].isin(fs) & pre(opps["created_date"])]
        ctl_o = opps[opps["firm_id"].isin(control) & post(opps["created_date"])]
        assoc_pairs += [(e.event_id, oid) for oid in new_o["opportunity_id"]]
        pipe = float(new_o["amount"].sum())
        cost = float(e.cost_usd)
        per_event.append(dict(
            event_id=e.event_id, event_name=e.event_name, cost=cost,
            firms_attended=len(fs), contacts=int((a["event_id"] == e.event_id).sum()),
            tier1_firms=int(firms[firms["firm_id"].isin(fs)]["priority"].eq("Tier 1").sum()),
            post_meetings=int(post(mf["meeting_date"]).sum()), pre_meetings=int(pre(mf["meeting_date"]).sum()),
            funnel_meeting=len(set(mf[post(mf["meeting_date"])]["firm_id"])), funnel_opp=len(set(new_o["firm_id"])),
            funnel_dd=len(set(new_o[new_o["max_stage_rank"] >= 2]["firm_id"])),
            funnel_commit=len(set(new_o[new_o["status"] == "Committed"]["firm_id"])),
            new_opps=len(new_o), pre_opps=len(pre_o), new_pipeline=pipe,
            committed_usd=float(new_o[new_o["status"] == "Committed"]["amount"].sum()),
            open_usd=float(new_o[new_o["status"] == "Open"]["amount"].sum()),
            declined_usd=float(new_o[new_o["status"] == "Declined"]["amount"].sum()),
            pipeline_per_dollar=pipe / cost if cost > 0 else None,
            cost_per_attending_firm=cost / len(fs) if fs else None,
            opp_rate_attendees=len(set(new_o["firm_id"])) / len(fs) if fs else 0.0,
            opp_rate_control=len(set(ctl_o["firm_id"])) / len(control) if control else 0.0,
            window_fully_observed=bool(d0 + W <= as_of),
        ))
    pairs = pd.DataFrame(assoc_pairs, columns=["event_id", "opportunity_id"])
    uniq = opps[opps["opportunity_id"].isin(pairs["opportunity_id"])]
    portfolio = dict(total_cost=float(ev["cost_usd"].sum()), unique_assoc_opps=len(uniq),
                     unique_assoc_pipeline=float(uniq["amount"].sum()),
                     sum_event_pipeline=float(sum(x["new_pipeline"] for x in per_event)),
                     multi_event_opps=int(pairs["opportunity_id"].duplicated().sum()),
                     control_firms=len(control), attending_firms=len(set().union(*attended.values())))
    return pd.DataFrame(per_event, columns=PER_EVENT_COLS), pairs, portfolio


def cleaning_impact(d, att, att_firm, o, opps, pe, port, window, include_tentative, as_of=AS_OF):
    """The same KPIs on the raw data, for the Data Quality tab's before/after table."""
    raw_att = d["event_attendees"].dropna(subset=["firm_id"])
    raw_hist = d["opportunity_stage_history"]
    raw_opps = opp_summary(raw_hist[raw_hist["stage"].isin(KNOWN_STAGES)])
    pe_raw, _, port_raw = compute(d, raw_att, raw_opps, window, include_tentative, as_of)
    kpis = [("Opportunities, all firms", len(raw_opps), len(opps)),
            ("Committed $, all firms", raw_opps[raw_opps.status == "Committed"].amount.sum(),
             opps[opps.status == "Committed"].amount.sum()),
            ("Associated pipeline $ (unique)", port_raw["unique_assoc_pipeline"], port["unique_assoc_pipeline"])]
    for r, c in zip(pe_raw.itertuples(), pe.itertuples()):
        kpis.append((f"{r.event_id} pipeline $", r.new_pipeline, c.new_pipeline))
    for r, c in zip(pe_raw.itertuples(), pe.itertuples()):
        kpis.append((f"{r.event_id} committed $", r.committed_usd, c.committed_usd))
    return dict(
        rows=[dict(table=t, raw=int(r), clean=int(c)) for t, r, c in [
            ("event_attendees", len(d["event_attendees"]), len(att)),
            ("opportunity_stage_history", len(raw_hist), len(o)),
            ("opportunities (distinct)", raw_hist["opportunity_id"].nunique(), len(opps))]],
        kpis=[dict(kpi=k, raw=float(a), clean=float(b)) for k, a, b in kpis])


def build(data_dir=DATA, out_dir=OUT, window=90, include_tentative=False, as_of=AS_OF):
    """Run the whole model and write <out_dir>/model.json. Returns the model dict."""
    d = load(data_dir)
    att, att_firm, o, log = clean(d, as_of)
    opps = opp_summary(o)
    pe, _, port = compute(d, att_firm, opps, window, include_tentative, as_of)
    impact = cleaning_impact(d, att, att_firm, o, opps, pe, port, window, include_tentative, as_of)

    def ser(df):
        return json.loads(df.to_json(orient="records", date_format="iso"))

    model = dict(as_of=str(as_of.date()), events=ser(d["events"]), firms=ser(d["firms"]), attendees=ser(att),
                 meetings=ser(d["meetings"]), stage_history=ser(o), opportunities=ser(opps),
                 dq_log=log, dq_impact=impact, per_event_default=ser(pe), portfolio_default=port)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "model.json", "w", encoding="utf-8") as fh:
        # allow_nan=False: NaN/Infinity are not valid JSON and would break the dashboard
        json.dump(model, fh, indent=1, allow_nan=False, ensure_ascii=False)
    model["_frames"] = dict(per_event=pe, opps=opps)
    return model


def _window_arg(v):
    """argparse type for --window: a whole number of days from 1 to MAX_WINDOW_DAYS."""
    try:
        n = int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a whole number of days, got {v!r}") from None
    if not 1 <= n <= MAX_WINDOW_DAYS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_WINDOW_DAYS} days, got {n}")
    return n


def main(argv=None):
    """Command-line entry point. Returns 0 on success, 2 on an input data problem (message on stderr)."""
    ap = argparse.ArgumentParser(description="Clean the IR event data and compute the dashboard model.")
    ap.add_argument("--window", type=_window_arg, default=90, help="association window in days (1-3650)")
    ap.add_argument("--include-tentative", action="store_true", help="count Tentative attendees")
    ap.add_argument("--data", default=str(DATA), help="folder with the five input CSVs")
    ap.add_argument("--out", default=str(OUT), help="folder to write model.json to")
    args = ap.parse_args(argv)
    try:
        model = build(args.data, args.out, args.window, args.include_tentative)
    except DataError as e:
        print(f"Input data problem: {e}", file=sys.stderr)
        return 2
    pe, opps = model["_frames"]["per_event"], model["_frames"]["opps"]

    print("Cleaning impact:")
    for k in model["dq_impact"]["kpis"]:
        print("  ", k)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    print(f"\n=== Window {args.window}d | tentative={'incl' if args.include_tentative else 'excl'} ===")
    print(pe.drop(columns=["event_name"]).round(3).T.to_string() if len(pe) else "(no events)")
    port = model["portfolio_default"]
    print("\nPortfolio:", {k: (round(v) if isinstance(v, float) else v) for k, v in port.items()})
    print("\nOpp status (cleaned):", opps["status"].value_counts().to_dict(),
          "| pipeline $", round(opps["amount"].sum()), "| committed $", round(opps[opps.status == "Committed"].amount.sum()))
    print("\nDQ log:")
    for r in model["dq_log"]:
        print(f"  {r['rule']:5} {r['rows']:>3}  {r['issue'][:110]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # output piped into e.g. `head`; the model file is already written
        sys.exit(0)
