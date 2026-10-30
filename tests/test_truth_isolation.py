"""The truth log is an oracle: only src/pipeline/evaluate.py reads it (AGENTS.md)."""

import ast
import importlib
import pkgutil
from pathlib import Path

import duckdb
import pytest

import src.models
from src.pipeline import store

ROOT = Path(store.ROOT)
MODELS = ROOT / "src" / "models"
# Anything a model could use to reach the truth: its tables, its file, the evaluation, or the raw dataset.
FORBIDDEN = ("truth", "evaluate", "planted_fault", "dataset", "synthetic", "data/claude", "jsonl")
ALLOWED_TO_OPEN_TRUTH = {ROOT / "src" / "pipeline" / "store.py", ROOT / "src" / "pipeline" / "evaluate.py"}


def touches_truth(source: str) -> list[str]:
    """Every name, attribute, import, alias and string in a module that points at the truth side."""
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
        elif isinstance(node, ast.ImportFrom):
            texts = [node.module or ""]
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            texts = [node.name]
        else:
            continue
        found += [t for t in texts if any(word in t.lower() for word in FORBIDDEN)]
    return found


def model_files() -> list[Path]:
    return sorted(MODELS.rglob("*.py"))


def test_no_module_under_src_models_refers_to_the_truth():
    files = model_files()
    assert files, "src/models has no modules to check"
    for path in files:
        assert touches_truth(path.read_text(encoding="utf-8")) == [], path.relative_to(ROOT)


def test_importing_every_model_module_never_opens_the_truth(monkeypatch):
    opened = []
    real = duckdb.connect

    def spy(path=":memory:", *a, **k):
        opened.append(str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(duckdb, "connect", spy)
    for info in pkgutil.walk_packages(src.models.__path__, "src.models."):
        importlib.import_module(info.name)
    assert not any(store.TRUTH_NAME in p for p in opened), opened


@pytest.mark.parametrize("snippet", [
    "from src.pipeline.store import open_truth",
    "from src.pipeline import store\nx = store.open_truth()",
    "import duckdb\nduckdb.connect('data/store/truth.duckdb')",
    "import src.pipeline.evaluate",
    "q = 'select breach from truth_specimens'",
    "import gzip\ngzip.open('datasets/tracer-synthetic-v1.jsonl.gz')",
    "from src.pipeline.store import DATASET_PATH",
    "def planted_fault_of(x): return x",
    "rows = brief['planted_fault']",
])
def test_the_scanner_catches_every_way_to_reach_the_truth(snippet):
    assert touches_truth(snippet)


@pytest.mark.parametrize("snippet", [
    "from src.pipeline import store\ncon = store.open_observed()\ncon.sql('select * from events')",
    "import numpy as np\nfrom scipy import stats\nparams = stats.lognorm.fit([1, 2, 3])",
    "from src.generator import config\nconfig.resolve({}, 'x', [])",
])
def test_the_scanner_leaves_ordinary_model_code_alone(snippet):
    assert touches_truth(snippet) == []


def test_a_model_file_that_reads_the_truth_would_fail_the_check(tmp_path):
    bad = tmp_path / "cheat.py"
    bad.write_text("from src.pipeline import store\ncon = store.open_truth()\n")
    assert touches_truth(bad.read_text())


def test_only_the_store_and_the_evaluation_open_the_truth_file():
    users = set()
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "open_truth" in text or store.TRUTH_NAME in text:
            users.add(path)
    assert users <= ALLOWED_TO_OPEN_TRUTH, sorted(str(p.relative_to(ROOT)) for p in users - ALLOWED_TO_OPEN_TRUTH)
