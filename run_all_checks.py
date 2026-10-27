"""Rebuild everything and run every check, in order. Stops at the first failure.

    python run_all_checks.py

0. pytest                unit + integration tests (Python model and dashboard JavaScript, synthetic data)
1. build_model.py        clean the data and compute the model
2. build_dashboard.py    build the single standalone HTML file (fails if anything required is external)
3. test_standalone.py    open ONLY the HTML file, offline, and check every tab
4. verify_dashboard.py   dashboard numbers must equal the Python numbers at 6 settings; saves screenshots
5. test_crossfilter.py   click every chart and check the filters and numbers
6. readout/build_readout.py   1-page PDF + 3-slide deck from the same model; checks page/slide count and figures
"""
import subprocess, sys
from pathlib import Path

root = Path(__file__).parent
steps = ["-m pytest", "build_model.py", "build_dashboard.py", "test_standalone.py", "verify_dashboard.py", "test_crossfilter.py", "readout/build_readout.py"]
for s in steps:
    print(f"\n=== {s} ===", flush=True)
    cmd = [sys.executable, "-m", "pytest", "-o", "addopts=", "-q"] if s == "-m pytest" else [sys.executable, str(root / s)]
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    lines = r.stdout.strip().splitlines()
    print("\n".join(lines if s.startswith("test_") or s.startswith("verify") else lines[-3:]))
    if r.returncode != 0:
        print(r.stderr[-2000:]); sys.exit(f"\nFAILED at {s}")
print("\nALL CHECKS PASSED")
left = (root / "AI_USAGE.md").read_text(encoding="utf-8").count("[Your words]")
if left:
    print(f"\nReminder: AI_USAGE.md still has {left} [Your words] spot(s) to fill in before submitting.")
