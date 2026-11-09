"""Training reads the observed store only (docs/neural/spec.md §15.2, §17 item 6).

Nothing under src/neural/ or src/decide/ opens the truth file, names a truth table, reads the scenario library
file, or reads the simulator's records directly. src/neural/devlib.py is the one exception: it calls the simulator
to make development data, says so, and no other module imports it."""

import ast
import importlib
import pkgutil
from pathlib import Path

import duckdb
import pytest

import src.neural
from src.pipeline import store

ROOT = Path(store.ROOT)
DIRS = (ROOT / "src" / "neural", ROOT / "src" / "decide")
EXCEPTION = ROOT / "src" / "neural" / "devlib.py"
# The truth file and tables, the library file, the simulator's records and the store functions that read them.
FORBIDDEN = ("open_truth", "truth.duckdb", "truth_name", "truth_specimens", "truth_segments", "truth_disruptions",
             "truth_scenarios", "truth_v2_tables", "scenarios-v2.jsonl", "data/sim", "sim-dev", "read_library",
             "library_bytes", "devlib")
FORBIDDEN_MODULES = ("src.sim", "src.pipeline.evaluate", "src.neural.devlib")


def findings(source: str) -> list[str]:
    """Every name, attribute, string and import that reaches the truth, the library or the simulator."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            texts = [node.id]
        elif isinstance(node, ast.Attribute):
            texts = [node.attr]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            texts = [node.value]
        elif isinstance(node, ast.alias):
            texts = [node.name]
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            texts = [node.name]
        else:
            texts = []
        found += [t for t in texts if any(word in t.lower() for word in FORBIDDEN)]
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [f"{module}.{a.name}" for a in node.names]
            found += [n for n in [module, *names] if any(n == m or n.startswith(m + ".") for m in FORBIDDEN_MODULES)]
        elif isinstance(node, ast.Import):
            found += [a.name for a in node.names if any(a.name == m or a.name.startswith(m + ".")
                                                         for m in FORBIDDEN_MODULES)]
    return found


def modules() -> list[Path]:
    return sorted(p for d in DIRS if d.exists() for p in d.rglob("*.py") if p != EXCEPTION)


def test_no_training_or_decision_module_reaches_the_truth_the_library_or_the_records():
    files = modules()
    assert ROOT / "src" / "neural" / "samplers.py" in files
    for path in files:
        assert findings(path.read_text(encoding="utf-8")) == [], path.relative_to(ROOT)


@pytest.mark.parametrize("snippet", [
    "from src.pipeline import store\ncon = store.open_truth(d)",
    "import duckdb\nduckdb.connect('data/store-v2/truth.duckdb')",
    "q = 'select cause from truth_specimens'",
    "q = 'select * from truth_disruptions'",
    "import gzip\ngzip.open('datasets/scenarios-v2.jsonl.gz')",
    "path = 'data/sim/train/s1/rep-1.jsonl.gz'",
    "from src.sim import run",
    "import src.sim.engine",
    "from src.pipeline import evaluate",
    "from src.pipeline.evaluate import load_specs_v2",
    "from src.neural import devlib",
    "records, sha = store.read_library(p)",
])
def test_the_scanner_catches_every_way_in(snippet):
    assert findings(snippet)


@pytest.mark.parametrize("snippet", [
    "from src.pipeline import store\ncon = store.open_observed_v2()",
    "from src.models.tracernet import features\nt = features.load_table(store.STORE_V2_DIR)",
    "import numpy as np\nx = np.zeros(3)",
    "from src.neural import samplers\ns = samplers.TrainSampler",
])
def test_the_scanner_leaves_training_code_alone(snippet):
    assert findings(snippet) == []


def test_the_development_library_is_the_declared_exception_and_nothing_imports_it():
    doc = ast.get_docstring(ast.parse(EXCEPTION.read_text(encoding="utf-8")))
    assert "one module under src/neural/" in doc and "never imported by training code" in doc
    importers = []
    for path in (ROOT / "src").rglob("*.py"):
        if path == EXCEPTION:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and ("devlib" in (node.module or "")
                                                     or any(a.name == "devlib" for a in node.names)):
                importers.append(path)
            elif isinstance(node, ast.Import) and any("devlib" in a.name for a in node.names):
                importers.append(path)
    assert importers == []


def test_importing_every_training_module_never_opens_the_truth(monkeypatch):
    opened = []
    real = duckdb.connect

    def spy(path=":memory:", *a, **k):
        opened.append(str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(duckdb, "connect", spy)
    for info in pkgutil.walk_packages(src.neural.__path__, "src.neural."):
        importlib.import_module(info.name)
    assert not any(store.TRUTH_NAME in p for p in opened), opened
