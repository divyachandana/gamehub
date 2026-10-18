"""Short run of the QA gate (scripts/qa.py) under pytest: its own server, page checks, pairing, camera pause, perf.

    python -m pytest -q tests/e2e            # ~20 s
    python -m pytest -q tests -m "not e2e"   # skip it
The full gate (escalation timings, all scenarios, ~3 min) is: python scripts/qa.py
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import qa  # noqa: E402


@pytest.mark.e2e
def test_qa_gate_smoke() -> None:
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "qa.py"), "--rules", "--only",
                           "static,pairing,camera,perf", "--perf-calls", "50"],
                          cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert "FAIL " not in done.stdout


def test_unescaped_html_heuristic() -> None:
    js = "el.innerHTML = `<b>${a.name}</b><i>${esc(a.title)}</i><u>${a.kind === 'x' ? 'y' : 'z'}</u><s>${n.toFixed(1)}</s>`;"
    found = qa.unescaped_html(js)
    assert len(found) == 1 and "a.name" in found[0]


def test_page_parser_finds_unlabelled_and_duplicate_ids() -> None:
    p = qa.PageParser()
    p.feed('<head><script src="/assets/auth.js"></script></head><body><input id="a"><label for="b">B</label>'
           '<input id="b"><label>C <select id="c"></select></label><div id="a"></div></body>')
    assert p.head_scripts == ["/assets/auth.js"]
    assert p.ids["a"] == 2
    unlabelled = [c["id"] for c in p.controls if not c["labelled"] and c["id"] not in p.label_for]
    assert unlabelled == ["a"]
