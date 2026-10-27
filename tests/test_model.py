"""Unit and integration tests for build_model.py (synthetic data only)."""
import json
import time

import pandas as pd
import pytest

import build_model as bm
from conftest import write_dataset

AS_OF = pd.Timestamp("2026-10-26")


def run(folder, window=90, tent=False, as_of=AS_OF):
    d = bm.load(folder)
    att, att_firm, o, log = bm.clean(d, as_of)
    opps = bm.opp_summary(o)
    pe, pairs, port = bm.compute(d, att_firm, opps, window, tent, as_of)
    return dict(d=d, att=att, att_firm=att_firm, o=o, log=log, opps=opps, pe=pe.set_index("event_id"), pairs=pairs, port=port)


def rules(log):
    return {r["rule"]: r for r in log}


# ---------------------------------------------------------------- load(): structural problems stop the build

class TestLoad:
    def test_happy_path(self, dataset):
        d = bm.load(dataset())
        assert set(d) == set(bm.SCHEMA)
        assert pd.api.types.is_datetime64_dtype(d["events"]["event_date"])

    def test_missing_file(self, dataset):
        folder = dataset()
        (folder / "meetings.csv").unlink()
        with pytest.raises(bm.DataError, match="Missing input file: meetings.csv"):
            bm.load(folder)

    def test_missing_column(self, tables, dataset):
        for r in tables["firms"]:
            del r["priority"]
        with pytest.raises(bm.DataError, match="firms.csv is missing column\\(s\\): priority"):
            bm.load(dataset())

    def test_duplicate_master_ids(self, tables, dataset):
        tables["firms"].append(dict(tables["firms"][0]))
        with pytest.raises(bm.DataError, match="duplicate firm_id values: F1"):
            bm.load(dataset())

    def test_empty_master_id(self, tables, dataset):
        tables["events"][1]["event_id"] = None
        with pytest.raises(bm.DataError, match="events.csv, column 'event_id', row 3: ID is empty"):
            bm.load(dataset())

    @pytest.mark.parametrize("bad", ["not a date", "2026-13-45", "12/31/abc"])
    def test_unparseable_date(self, tables, dataset, bad):
        tables["meetings"][1]["meeting_date"] = bad
        with pytest.raises(bm.DataError, match="meetings.csv, column 'meeting_date', row 3"):
            bm.load(dataset())

    def test_missing_date(self, tables, dataset):
        tables["events"][0]["event_date"] = ""
        with pytest.raises(bm.DataError, match="column 'event_date', row 2"):
            bm.load(dataset())

    def test_timezone_aware_dates_become_utc_calendar_dates(self, tables, dataset):
        # 23:30 in New York on 12 Jan is 04:30 UTC on 13 Jan
        tables["events"][0]["event_date"] = "2026-01-12T23:30:00-05:00"
        tables["events"][1]["event_date"] = "2026-02-10T08:00:00+09:00"
        d = bm.load(dataset())
        assert list(d["events"]["event_date"]) == [pd.Timestamp("2026-01-13"), pd.Timestamp("2026-02-09")]
        assert d["events"]["event_date"].dt.tz is None

    def test_non_numeric_money(self, tables, dataset):
        tables["events"][0]["cost_usd"] = "lots"
        with pytest.raises(bm.DataError, match="events.csv, column 'cost_usd', row 2: expected a number"):
            bm.load(dataset())

    def test_negative_event_cost_is_structural(self, tables, dataset):
        tables["events"][0]["cost_usd"] = -5
        with pytest.raises(bm.DataError, match="cannot be negative"):
            bm.load(dataset())

    def test_non_numeric_opportunity_amount(self, tables, dataset):
        tables["opportunity_stage_history"][0]["opportunity_amount_usd"] = "TBD"
        with pytest.raises(bm.DataError, match="opportunity_amount_usd"):
            bm.load(dataset())

    def test_not_utf8(self, dataset):
        folder = dataset()
        (folder / "firms.csv").write_bytes("firm_id,firm_name\nF1,Caf\xe9\n".encode("latin-1"))
        with pytest.raises(bm.DataError, match="not valid UTF-8"):
            bm.load(folder)

    def test_unicode_names_survive(self, tables, dataset):
        tables["firms"][0]["firm_name"] = "Zürich Pensionskasse 日本 <b>&</b>"
        d = bm.load(dataset())
        assert d["firms"]["firm_name"][0] == "Zürich Pensionskasse 日本 <b>&</b>"

    def test_error_messages_do_not_leak_full_paths(self, tmp_path):
        with pytest.raises(bm.DataError) as e:
            bm.load(tmp_path / "secret_location" / "data")
        assert str(tmp_path) not in str(e.value)


# ---------------------------------------------------------------- clean(): row-level problems are set aside and logged

class TestClean:
    def test_clean_data_logs_only_informational_rules(self, dataset):
        r = run(dataset())
        # F2 has $0 history (DQ11); F4 attended nothing, has an opportunity and no meetings (DQ10)
        assert set(rules(r["log"])) == {"DQ10", "DQ11"}

    def test_exact_duplicate_attendance_removed(self, tables, dataset):
        tables["event_attendees"].append(dict(tables["event_attendees"][0]))
        r = run(dataset())
        assert rules(r["log"])["DQ1"]["rows"] == 1 and "A1" in rules(r["log"])["DQ1"]["issue"]
        assert len(r["att"]) == 3

    def test_attendee_without_firm_kept_in_contacts_not_firms(self, tables, dataset):
        tables["event_attendees"].append(dict(attendance_id="A9", event_id="E1", firm_id=None, contact_name="X",
                                              contact_title="CIO", attendance_status="Confirmed"))
        r = run(dataset())
        assert rules(r["log"])["DQ2"]["rows"] == 1
        assert len(r["att"]) == 4 and len(r["att_firm"]) == 3

    def test_unknown_attendance_status_set_aside(self, tables, dataset):
        tables["event_attendees"][0]["attendance_status"] = "Maybe"
        r = run(dataset())
        assert rules(r["log"])["DQ14"]["rows"] == 1
        assert "F1" not in set(r["att_firm"]["firm_id"])

    def test_orphan_attendance_set_aside(self, tables, dataset):
        tables["event_attendees"].append(dict(attendance_id="A8", event_id="E9", firm_id="F1", contact_name="X",
                                              contact_title="CIO", attendance_status="Confirmed"))
        r = run(dataset())
        assert "A8" in rules(r["log"])["DQ15"]["issue"]

    @pytest.mark.parametrize("amount", [0, -10, None])
    def test_bad_opportunity_amount_quarantines_whole_opportunity(self, tables, dataset, amount):
        tables["opportunity_stage_history"][1]["opportunity_amount_usd"] = amount
        r = run(dataset())
        assert rules(r["log"])["DQ12"]["rows"] == 3            # all three O1 rows
        assert "O1" not in set(r["opps"]["opportunity_id"])

    def test_unknown_stage_row_set_aside(self, tables, dataset):
        tables["opportunity_stage_history"][1]["stage"] = "Due Dilligence"   # typo
        r = run(dataset())
        assert rules(r["log"])["DQ13"]["rows"] == 1
        assert r["opps"].set_index("opportunity_id").loc["O1", "status"] == "Committed"

    def test_duplicate_opportunity_under_other_id(self, tables, dataset):
        tables["opportunity_stage_history"] += [dict(r, opportunity_id="O9") for r in tables["opportunity_stage_history"][:3]]
        r = run(dataset())
        assert rules(r["log"])["DQ3"]["issue"].endswith("(O9 duplicates O1)")
        assert set(r["opps"]["opportunity_id"]) == {"O1", "O2", "O3"}

    def test_stage_dated_exactly_on_as_of_is_kept_one_day_later_is_not(self, tables, dataset):
        tables["opportunity_stage_history"][2]["stage_date"] = "2026-10-26"
        r = run(dataset())
        assert "DQ5" not in rules(r["log"])
        tables["opportunity_stage_history"][2]["stage_date"] = "2026-10-27"
        r = run(dataset())
        assert rules(r["log"])["DQ5"]["rows"] == 1
        assert r["opps"].set_index("opportunity_id").loc["O1", "status"] == "Open"

    def test_repeated_stage_keeps_first_date_and_every_first_row(self, tables, dataset):
        # regression: a missing-value comparison once dropped the first stage row of every opportunity
        tables["opportunity_stage_history"].insert(2, dict(tables["opportunity_stage_history"][1], stage_date="2026-02-05"))
        r = run(dataset())
        assert rules(r["log"])["DQ6"]["rows"] == 1
        assert len(r["o"]) == 6                                 # 7 rows - 1 repeat
        assert set(r["opps"]["opportunity_id"]) == {"O1", "O2", "O3"}
        assert r["opps"].set_index("opportunity_id").loc["O1", "dd_date"] == pd.Timestamp("2026-02-01")

    def test_similar_firm_names_flagged(self, tables, dataset):
        tables["firms"].append(dict(tables["firms"][0], firm_id="F5", firm_name="Alpha Capital Holdings"))
        r = run(dataset())
        assert "F5 'Alpha Capital Holdings' may be the same entity as F1" in rules(r["log"])["DQ7"]["issue"]

    def test_similar_name_check_does_not_match_prefixes_inside_words(self, tables, dataset):
        tables["firms"].append(dict(tables["firms"][0], firm_id="F5", firm_name="Alpha Capitalists"))
        assert "DQ7" not in rules(run(dataset())["log"])

    def test_dq_messages_name_the_actual_records(self, tables, dataset):
        tables["firms"][2]["historical_commitments_usd"] = 0
        assert "F2, F3" in rules(run(dataset())["log"])["DQ11"]["issue"]


# ---------------------------------------------------------------- opp_summary()

class TestOppSummary:
    def test_status_and_stages(self, dataset):
        o = run(dataset())["opps"].set_index("opportunity_id")
        assert o.loc["O1", "status"] == "Committed" and o.loc["O1", "max_stage"] == "Committed"
        assert o.loc["O2", "status"] == "Open" and o.loc["O2", "max_stage_rank"] == 0
        assert o.loc["O3", "status"] == "Declined" and o.loc["O3", "max_stage"] == "Initial Conversation"
        assert pd.isna(o.loc["O2", "dd_date"])

    def test_empty_input_returns_empty_frame_with_columns(self):
        empty = pd.DataFrame(columns=bm.SCHEMA["opportunity_stage_history"]["cols"])
        out = bm.opp_summary(empty)
        assert len(out) == 0 and "status" in out.columns


# ---------------------------------------------------------------- compute(): windows, attribution, boundaries

class TestCompute:
    def test_happy_path(self, dataset):
        r = run(dataset())
        e1 = r["pe"].loc["E1"]
        assert e1.firms_attended == 1 and e1.new_opps == 1 and e1.new_pipeline == 10e6
        assert e1.pipeline_per_dollar == pytest.approx(100.0)
        assert e1.funnel_meeting == 1 and e1.funnel_dd == 1 and e1.funnel_commit == 1
        assert r["port"]["control_firms"] == 1                 # F4 attended nothing

    def test_tentative_toggle(self, dataset):
        assert run(dataset(), tent=False)["pe"].loc["E1"].firms_attended == 1
        assert run(dataset(), tent=True)["pe"].loc["E1"].firms_attended == 2

    @pytest.mark.parametrize("created, counted", [
        ("2026-01-10", False),   # same day as the event: not "after"
        ("2026-01-11", True),    # first day after
        ("2026-04-10", True),    # event + 90 days: last day inside
        ("2026-04-11", False),   # event + 91 days: outside
    ])
    def test_window_boundaries(self, tables, dataset, created, counted):
        for row in tables["opportunity_stage_history"][:3]:
            row["created_date"] = created
            row["stage_date"] = max(row["stage_date"], created)
        r = run(dataset(), window=90)
        assert bool(r["pe"].loc["E1"].new_opps == 1) == counted

    def test_pre_window_includes_first_day_excludes_event_day(self, tables, dataset):
        tables["meetings"][1]["meeting_date"] = "2025-10-12"   # event - 90 days
        assert run(dataset())["pe"].loc["E1"].pre_meetings == 1
        tables["meetings"][1]["meeting_date"] = "2026-01-10"   # event day: neither before nor after
        r = run(dataset())["pe"].loc["E1"]
        assert r.pre_meetings == 0 and r.post_meetings == 1

    def test_multi_event_credit_and_dedupe(self, tables, dataset):
        tables["event_attendees"].append(dict(attendance_id="A4", event_id="E2", firm_id="F1", contact_name="C4",
                                              contact_title="CIO", attendance_status="Confirmed"))
        for row in tables["opportunity_stage_history"][:3]:
            row["created_date"] = "2026-02-15"
        r = run(dataset())
        assert r["pe"].loc["E1"].new_pipeline == 10e6 and r["pe"].loc["E2"].new_pipeline == 12e6
        assert r["port"]["multi_event_opps"] == 1
        assert r["port"]["unique_assoc_pipeline"] == 12e6       # O1 counted once
        assert r["port"]["sum_event_pipeline"] == 22e6

    def test_zero_cost_event_has_no_ratio_instead_of_infinity(self, tables, dataset):
        tables["events"][0]["cost_usd"] = 0
        e1 = run(dataset())["pe"].loc["E1"]
        assert e1.pipeline_per_dollar is None or pd.isna(e1.pipeline_per_dollar)

    def test_event_with_no_attendees(self, tables, dataset):
        tables["event_attendees"] = [r for r in tables["event_attendees"] if r["event_id"] != "E2"]
        e2 = run(dataset())["pe"].loc["E2"]
        assert e2.firms_attended == 0 and e2.opp_rate_attendees == 0 and pd.isna(e2.cost_per_attending_firm)

    @pytest.mark.parametrize("bad", [0, -1, 3651, 1.5, True, "90", None])
    def test_invalid_window_rejected(self, dataset, bad):
        r = run(dataset())
        with pytest.raises(ValueError, match="window must be"):
            bm.compute(r["d"], r["att_firm"], r["opps"], bad, False)

    @pytest.mark.parametrize("ok", [1, 3650])
    def test_window_limits_accepted(self, dataset, ok):
        assert len(run(dataset(), window=ok)["pe"]) == 2

    def test_window_fully_observed_uses_as_of(self, dataset):
        r = run(dataset(), window=90, as_of=pd.Timestamp("2026-04-10"))
        assert bool(r["pe"].loc["E1"].window_fully_observed) is True     # 10 Jan + 90 = 10 Apr
        assert bool(r["pe"].loc["E2"].window_fully_observed) is False

    def test_no_events_at_all(self, dataset):
        folder = dataset()
        for name in ("events", "event_attendees"):           # header row only
            pd.DataFrame(columns=bm.SCHEMA[name]["cols"]).to_csv(folder / f"{name}.csv", index=False)
        r = run(folder)
        assert len(r["pe"]) == 0 and r["port"]["unique_assoc_opps"] == 0 and r["port"]["control_firms"] == 4


# ---------------------------------------------------------------- integration: build() and the command line

class TestIntegration:
    def test_build_writes_strict_json(self, tables, dataset, tmp_path):
        tables["events"][0]["cost_usd"] = 0                     # would have produced Infinity before the fix
        tables["firms"][0]["firm_name"] = "Zürich 日本"
        bm.build(dataset(), tmp_path / "out")
        text = (tmp_path / "out" / "model.json").read_text(encoding="utf-8")

        def reject(c):
            raise AssertionError(f"invalid JSON constant {c}")
        model = json.loads(text, parse_constant=reject)
        assert model["firms"][0]["firm_name"] == "Zürich 日本"
        assert {"events", "firms", "attendees", "meetings", "opportunities", "stage_history", "dq_log", "dq_impact"} <= set(model)

    def test_cli_success(self, dataset, tmp_path, capsys):
        assert bm.main(["--data", str(dataset()), "--out", str(tmp_path / "o"), "--window", "30"]) == 0
        assert "Window 30d" in capsys.readouterr().out

    def test_cli_bad_data_exits_2_with_clean_message(self, tables, dataset, tmp_path, capsys):
        tables["events"][0]["event_date"] = "soon"
        assert bm.main(["--data", str(dataset()), "--out", str(tmp_path / "o")]) == 2
        err = capsys.readouterr().err
        assert err.startswith("Input data problem: events.csv, column 'event_date', row 2")
        assert "Traceback" not in err

    @pytest.mark.parametrize("w", ["0", "-5", "abc", "4000"])
    def test_cli_rejects_bad_window(self, dataset, w):
        with pytest.raises(SystemExit):
            bm.main(["--data", str(dataset()), "--window", w])

    def test_large_input_runs_in_reasonable_time(self, tmp_path):
        n_firms, n_opps = 2000, 6000
        t = {
            "events": [dict(event_id=f"E{i}", event_name=f"Event {i}", event_date=f"2026-0{1 + i}-15", event_type="Conference",
                            location="New York", cost_usd=100000) for i in range(5)],
            "firms": [dict(firm_id=f"F{i}", firm_name=f"Firm {i}", segment="Pension", priority="Tier 2",
                           historical_commitments_usd=1, coverage_region="EMEA") for i in range(n_firms)],
            "event_attendees": [dict(attendance_id=f"A{i}", event_id=f"E{i % 5}", firm_id=f"F{i}", contact_name="c",
                                     contact_title="CIO", attendance_status="Confirmed") for i in range(1500)],
            "meetings": [dict(meeting_id=f"M{i}", firm_id=f"F{i % n_firms}", meeting_date=f"2026-0{1 + i % 9}-{1 + i % 28:02d}",
                              meeting_type="Video", internal_attendee="Coverage A", purpose="Fundraising") for i in range(8000)],
            "opportunity_stage_history": [dict(opportunity_id=f"O{i}", firm_id=f"F{i % n_firms}", fund_name="Fund",
                                               created_date=f"2026-0{1 + i % 8}-{1 + i % 27:02d}", stage_date=f"2026-0{1 + i % 8}-{2 + i % 27:02d}",
                                               stage="Initial Conversation", opportunity_amount_usd=1e6) for i in range(n_opps)],
        }
        folder = write_dataset(tmp_path / "big", t)
        start = time.perf_counter()
        model = bm.build(folder, tmp_path / "out")
        assert len(model["opportunities"]) == n_opps
        assert time.perf_counter() - start < 10   # was 156 s before the DQ7 / opp_summary rewrite


def test_real_project_data_headline_numbers():
    """Regression guard on the project's own (synthetic) CSVs: the numbers shown to leadership."""
    r = run(bm.DATA)
    assert r["port"]["unique_assoc_opps"] == 36
    assert r["port"]["unique_assoc_pipeline"] == 1_506_000_000
    assert round(r["pe"].loc["E002"].pipeline_per_dollar) == 2173
    assert [x["rule"] for x in r["log"]] == [f"DQ{i}" for i in range(1, 12)]
