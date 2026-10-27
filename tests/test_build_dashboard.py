"""Tests for build_dashboard.py: standalone guarantee and clear errors (synthetic data only)."""
import copy
import json

import pytest

import build_dashboard as bd
import build_model as bm
from conftest import BASE, write_dataset


@pytest.fixture
def model_file(tmp_path):
    bm.build(write_dataset(tmp_path / "data", copy.deepcopy(BASE)), tmp_path / "out")
    return tmp_path / "out" / "model.json"


def test_builds_a_standalone_page(model_file, tmp_path):
    out = bd.build_html(model_file, tmp_path / "site", docs_dir=tmp_path / "docs")
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "<script src=" not in html
    assert (tmp_path / "docs" / "index.html").read_text(encoding="utf-8") == html


def test_external_script_fails_the_build():
    with pytest.raises(bd.StandaloneError, match="script src"):
        bd.check_standalone('<script src="https://cdn.example.com/x.js"></script>')


def test_external_stylesheet_fails_but_google_fonts_allowed():
    bd.check_standalone('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=X">')
    with pytest.raises(bd.StandaloneError, match="external stylesheet"):
        bd.check_standalone('<link rel="stylesheet" href="https://evil.example.com/a.css">')


def test_unfilled_placeholder_fails():
    with pytest.raises(bd.StandaloneError, match="__DATA__"):
        bd.check_standalone("<script>__DATA__</script>")


def test_data_cannot_close_the_script_block(model_file):
    model = json.loads(model_file.read_text(encoding="utf-8"))
    model["firms"][0]["firm_name"] = "</script><script>alert(1)</script><!--"
    body = bd.render_page(model)
    data_block = body[body.index('id="data">'):body.index("</script>", body.index('id="data">'))]
    assert "</script" not in data_block[10:] and "<!--" not in data_block
    assert "\\u003c/script>" in data_block


@pytest.mark.parametrize("content, msg", [(None, "not found"), ("{oops", "not valid JSON"), ('{"as_of": "x"}', "is missing")])
def test_bad_model_file_gives_clear_error(tmp_path, content, msg):
    path = tmp_path / "model.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(bd.StandaloneError, match=msg) as e:
        bd.build_html(path, tmp_path / "site", docs_dir=None)
    assert str(tmp_path) not in str(e.value)
