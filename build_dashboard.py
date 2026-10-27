"""Build the dashboard as ONE standalone HTML file: out/ir_event_outcomes.html (+ docs/index.html for GitHub Pages).

Everything the page needs is built into the file: the cleaned data, the world outline, Leaflet's CSS,
and the Chart.js and Leaflet libraries. The build fails if anything required would still load from
the internet. Only two optional extras may reach out, and the page works without both:
  - Google Fonts (falls back to system fonts)
  - OpenStreetMap tiles (falls back to the built-in country outline)

Run build_model.py first, then:  python build_dashboard.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VENDOR = ROOT / "vendor"
LIBS = [("https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js", "chart.umd.js"),
        ("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js", "leaflet.js")]
KEEP = ["as_of", "events", "firms", "attendees", "meetings", "opportunities", "stage_history", "dq_log", "dq_impact"]


class StandaloneError(RuntimeError):
    """The page cannot be built as a working standalone file (missing/invalid model, or an external dependency)."""


def render_page(model):
    """Return the page body (no <html> wrapper) with data, libraries and styles built in.

    Raises StandaloneError if anything required would still load from the internet.
    """
    # "<" is escaped as \u003c so no data value can close the <script> tag or open an HTML comment
    payload = json.dumps({k: model[k] for k in KEEP}, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    html = (ROOT / "dashboard_template.html").read_text(encoding="utf-8")
    html = html.replace("__DATA__", payload)
    html = html.replace("__WORLD__", (VENDOR / "world.json").read_text(encoding="utf-8"))
    html = html.replace("/*__LEAFLET_CSS__*/", (VENDOR / "leaflet.css").read_text(encoding="utf-8"))
    for url, f in LIBS:
        tag = f'<script src="{url}"></script>'
        if tag not in html:
            raise StandaloneError(f"library tag for {f} not found in the template")
        lib = (VENDOR / f).read_text(encoding="utf-8").replace("</script", "<\\/script")
        html = html.replace(tag, f"<script>/* {f} (built in for standalone use) */\n{lib}\n</script>")
    check_standalone(html)
    return html


def check_standalone(html):
    """Fail if anything required is external. Only Google Fonts may be linked (it has a fallback)."""
    problems = []
    if re.search(r"<script[^>]+\bsrc\s*=", html, re.I):
        problems.append("a <script src=...> tag is still present")
    for m in re.finditer(r"<link[^>]+href=\"(https?://[^\"]+)\"", html, re.I):
        if "fonts.googleapis.com" not in m.group(1):
            problems.append(f"external stylesheet/link: {m.group(1)}")
    for marker in ["__DATA__", "__WORLD__", "/*__LEAFLET_CSS__*/"]:
        if marker in html:
            problems.append(f"placeholder {marker} was not filled")
    if problems:
        raise StandaloneError("STANDALONE CHECK FAILED:\n  - " + "\n  - ".join(problems))


def wrap_document(body):
    """Full HTML document for opening locally or from GitHub Pages."""
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n</head>\n<body>\n'
            + body + '\n</body>\n</html>\n')


def build_html(model_path=ROOT / "out" / "model.json", out_dir=ROOT / "out", docs_dir=ROOT / "docs"):
    """Build out_dir/ir_event_outcomes.html (and docs_dir/index.html unless docs_dir is None). Returns the path."""
    model_path = Path(model_path)
    try:
        model = json.loads(model_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StandaloneError(f"{model_path.name} not found. Run build_model.py first.") from None
    except json.JSONDecodeError as e:
        raise StandaloneError(f"{model_path.name} is not valid JSON (line {e.lineno}). Re-run build_model.py.") from None
    missing = [k for k in KEEP if k not in model]
    if missing:
        raise StandaloneError(f"{model_path.name} is missing: {', '.join(missing)}. Re-run build_model.py.")
    body = render_page(model)
    doc = wrap_document(body)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "ir_event_outcomes.html"
    out.write_text(doc, encoding="utf-8")
    (out_dir / ".artifact_body.html").write_text(body, encoding="utf-8")   # claude.ai adds its own wrapper
    if docs_dir is not None:
        Path(docs_dir).mkdir(parents=True, exist_ok=True)
        (Path(docs_dir) / "index.html").write_text(doc, encoding="utf-8")
    return out


if __name__ == "__main__":
    try:
        path = build_html()
    except StandaloneError as e:
        sys.exit(str(e))
    print("wrote", path, f"{path.stat().st_size/1024:.0f} KB")
    print("standalone check: OK (all data and libraries built in; fonts and map tiles are optional)")
