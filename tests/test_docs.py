"""The documents say what the code and the data say (plan task W6)."""

import importlib.util
import json
import math
import re

from src.generator import config
from src.pipeline import store

ROOT = store.ROOT
README = (ROOT / "README.md").read_text(encoding="utf-8")
ARCH = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "datasets" / "manifest.json").read_text(encoding="utf-8"))


def section(text: str, start: str, stop: str) -> str:
    i = text.index(start)
    return text[i:text.index(stop, i)]


def test_every_module_the_readme_runs_exists():
    modules = set(re.findall(r"python -m (src[\w.]+)", README))
    assert {"src.pipeline.store", "src.pipeline.evaluate", "src.pipeline.location_eval", "src.generator.briefs"} <= modules
    for name in modules:
        assert importlib.util.find_spec(name) is not None, name


def test_no_document_calls_a_simulator_the_event_source():
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        text = path.read_text(encoding="utf-8").lower()
        assert "simulator is the event source" not in text.replace("*", ""), path.name
    assert "simulator" not in README.lower() and "simpy" not in README.lower()
    assert ARCH.count("SimPy") == 1  # only as the alternative the generator replaced
    assert "Claude-generated" in ARCH and "## 6. The generator" in ARCH


def test_the_generator_section_quotes_the_manifest():
    text = section(ARCH, "## 6. The generator", "### 6.1")
    assert f"{MANIFEST['requested']:,} requested" in text and f"{MANIFEST['accepted']:,} accepted" in text
    assert f"{MANIFEST['dropped']} dropped" in text and f"{MANIFEST['breach_accepted']} breaches" in text
    assert f"${MANIFEST['cost_usd']:.2f}" in text and MANIFEST["model"] in text
    assert "no clinical review" in text.lower()


def test_the_fault_table_has_the_configs_rates():
    cfg = config.load()["config"]["faults"]
    table = section(ARCH, "### 6.3 Planted faults", "The last is a")
    for name, fault in cfg.items():
        row = next(line for line in table.splitlines() if line.startswith(f"| `{name}`"))
        assert f"{fault['rate']:.1%}" in row, name
        if "delay_min" in fault:
            lo, hi = fault["delay_min"]
            assert f"{lo} to {hi} min" in row, name
    assert "`faults{7 modes}`" in ARCH and len(cfg) == 7


def test_the_specimen_types_section_matches_the_policy_table():
    loaded = config.load()
    policies = loaded["policy"]["policies"]
    variants = {p["specimen_type"] for p in policies.values()}
    text = section(ARCH, "### 6.1 Specimen types", "### 6.2")
    assert f"{len(variants)} specimen variants and {len(policies)} specimen-and-test rows" in text
    assert len(variants) == 12 and len(policies) == 44
    families = {"Urine": 3, "Body fluid": 4, "Bronchoalveolar lavage": 2, "Culture swab": 2, "Tissue": 1}
    rows = [line for line in text.splitlines() if line.startswith("| ") and line.split("|")[1].strip() in families]
    assert sum(families.values()) == len(variants) and len(rows) == len(families)
    mix = loaded["config"]["specimen_mix"]["entries"]
    in_mix = {policies[pid]["specimen_type"] for entry in mix.values() for pid in entry["policies"]}
    assert in_mix == variants  # the generator's mix names every variant the table has
    for window in sorted({p["window_min"] for p in policies.values()}):
        if window in (60, 120, 1440, 2880, 4320, 7200):
            assert str(window) in text, window


def test_the_worked_example_uses_the_exhibits_numbers():
    text = section(ARCH, "### One specimen, minute by minute", "## 4. Data model")
    for needle in ("window 120 minutes", "P(breach) = 0.06", "0.47", "0.64 on the destination tube station and 0.36 on lost",
                   "60 minutes remaining", "52 minutes inside the window"):
        assert needle in text, needle


def test_the_worked_example_numbers_come_from_the_model():
    from test_location import JS_LEGS
    from test_risk import JS_MIX

    from src.models import location, risk
    assert round(risk.risk_unreceived(1, JS_MIX, 120), 2) == 0.06
    assert round(risk.risk_unreceived(60, JS_MIX, 120), 2) == 0.47 and risk.risk_unreceived(55, JS_MIX, 120) < 0.45
    names, post = location.filter_series(JS_LEGS, 60)
    station, lost = post[60, 2] + post[60, 3], post[60, names.index("lost")]
    assert (round(station, 2), round(lost, 2)) == (0.64, 0.36)
    assert round(1 - 0.5 * (1 + math.erf(math.log(60 / 24) / 0.5 / 2 ** 0.5)), 2) == 0.03  # about three percent of normal transits


def test_the_interfaces_section_describes_the_bundle_that_exists():
    from src.api import export
    text = section(ARCH, "## 7. Interfaces", "**The board needs four things")
    size = export.DEFAULT_OUT.stat().st_size
    import gzip
    packed = len(gzip.compress(export.DEFAULT_OUT.read_bytes()))
    assert "about 3.3 MB (480 KB compressed)" in text and abs(size / 1e6 - 3.3) < 0.25 and abs(packed / 1e3 - 480) < 60
    assert "docs/demo/bundle.schema.json" in text and "python -m src.api.export" in text
    assert "src.api.export" in README and "FastAPI" not in ARCH and "uvicorn" not in README
