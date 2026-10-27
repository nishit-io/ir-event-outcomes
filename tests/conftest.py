"""Shared fixtures: a tiny synthetic dataset written to a temp folder.

Nothing here reads the project's data/ folder. Each test can change rows before writing.
"""
import copy
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = {
    "events": [
        dict(event_id="E1", event_name="Test Summit", event_date="2026-01-10", event_type="Conference", location="New York", cost_usd=100000),
        dict(event_id="E2", event_name="Test Dinner", event_date="2026-02-10", event_type="Hospitality", location="London", cost_usd=50000),
    ],
    "firms": [
        dict(firm_id="F1", firm_name="Alpha Capital", segment="Pension", priority="Tier 1", historical_commitments_usd=1000000, coverage_region="Americas"),
        dict(firm_id="F2", firm_name="Beta Partners", segment="Insurance", priority="Tier 2", historical_commitments_usd=0, coverage_region="EMEA"),
        dict(firm_id="F3", firm_name="Gamma Office", segment="Family Office", priority="Tier 3", historical_commitments_usd=500000, coverage_region="APAC"),
        dict(firm_id="F4", firm_name="Delta Fund", segment="Foundation", priority="Tier 2", historical_commitments_usd=250000, coverage_region="EMEA"),
    ],
    "event_attendees": [
        dict(attendance_id="A1", event_id="E1", firm_id="F1", contact_name="C1", contact_title="CIO", attendance_status="Confirmed"),
        dict(attendance_id="A2", event_id="E1", firm_id="F2", contact_name="C2", contact_title="Director", attendance_status="Tentative"),
        dict(attendance_id="A3", event_id="E2", firm_id="F3", contact_name="C3", contact_title="Principal", attendance_status="Confirmed"),
    ],
    "meetings": [
        dict(meeting_id="M1", firm_id="F1", meeting_date="2026-01-20", meeting_type="Video", internal_attendee="Coverage A", purpose="Fundraising"),
        dict(meeting_id="M2", firm_id="F1", meeting_date="2025-12-20", meeting_type="Phone", internal_attendee="Coverage A", purpose="Relationship"),
    ],
    "opportunity_stage_history": [
        dict(opportunity_id="O1", firm_id="F1", fund_name="Fund A", created_date="2026-01-15", stage_date="2026-01-16", stage="Initial Conversation", opportunity_amount_usd=10000000),
        dict(opportunity_id="O1", firm_id="F1", fund_name="Fund A", created_date="2026-01-15", stage_date="2026-02-01", stage="Due Diligence", opportunity_amount_usd=10000000),
        dict(opportunity_id="O1", firm_id="F1", fund_name="Fund A", created_date="2026-01-15", stage_date="2026-03-01", stage="Committed", opportunity_amount_usd=10000000),
        dict(opportunity_id="O2", firm_id="F4", fund_name="Fund B", created_date="2026-01-20", stage_date="2026-01-21", stage="Initial Conversation", opportunity_amount_usd=5000000),
        dict(opportunity_id="O3", firm_id="F3", fund_name="Fund A", created_date="2026-02-15", stage_date="2026-02-16", stage="Initial Conversation", opportunity_amount_usd=2000000),
        dict(opportunity_id="O3", firm_id="F3", fund_name="Fund A", created_date="2026-02-15", stage_date="2026-03-16", stage="Declined", opportunity_amount_usd=2000000),
    ],
}


def write_dataset(folder, tables):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    import build_model as bm
    for name, rows in tables.items():
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=bm.SCHEMA[name]["cols"])   # empty table: header only
        df.to_csv(folder / f"{name}.csv", index=False, encoding="utf-8")
    return folder


@pytest.fixture
def tables():
    """A fresh, editable copy of the synthetic rows."""
    return copy.deepcopy(BASE)


@pytest.fixture
def dataset(tmp_path, tables):
    """Write the (possibly edited) synthetic rows and return the folder. Call after editing `tables`."""
    def _write(t=None):
        return write_dataset(tmp_path / "data", t or tables)
    return _write
