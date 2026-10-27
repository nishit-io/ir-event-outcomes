"""Tests for readout/build_readout.py: same number formatting as the dashboard, wording built from facts."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "readout"))
import build_readout as br  # noqa: E402


@pytest.mark.parametrize("v, out", [
    (262_500_000, "$263M"),      # half rounds up, like the dashboard (Python's round() would give $262M)
    (1_506_000_000, "$1.51B"), (2_000_000_000, "$2B"), (2_250_000, "$2.3M"), (2_500_000, "$2.5M"),
    (420_000, "$420K"), (950, "$950"), (0, "$0"), (-2_500_000, "-$2.5M"), (None, "n/a"),
])
def test_money_matches_dashboard(v, out):
    assert br.money(v) == out


def test_other_formatters():
    assert [br.pct(0.425), br.pct(0.1743), br.per_dollar(2172.5), br.per_dollar(None)] == ["43%", "17%", "$2,173", "n/a"]
    assert [br.count_word(3), br.count_word(12)] == ["three", "12"]


def test_facts_and_wording_from_project_data():
    f = br.compute_facts()
    n = br.narrative(f)
    assert f["portfolio"]["opps"] == 36 and br.money(f["portfolio"]["committed"]) == "$263M"
    assert f["best"] == "E002" and f["worst"] == "E003"
    assert n["title"] == "Three investor events: results and next steps"
    assert "London" in n["findings"][0][0] and "$2,173" in n["findings"][0][1]
    assert len(n["findings"]) == 3 and len(n["actions"]) == 3


def test_theme_writer_rejects_a_deck_without_theme_slots(tmp_path):
    import zipfile
    bad = tmp_path / "bad.pptx"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("ppt/theme/theme1.xml", "<a:theme><a:clrScheme name='x'></a:clrScheme></a:theme>")
    with pytest.raises(br.ReadoutError, match="theme colour slot"):
        br.apply_theme(bad)
