"""The validation risk-table shard (src/neural/risk_shard.py) never scores a locked set."""
import json
from pathlib import Path

import pytest

from src.neural import risk_shard


@pytest.mark.parametrize("split", ["test", "test_ood"])
def test_a_locked_set_is_refused_before_anything_is_read(split, tmp_path, capsys):
    code = risk_shard.main(["--split", split, "--artifact", str(tmp_path / "none"), "--store", str(tmp_path / "none"),
                            "--out", str(tmp_path / "out")])
    assert code == 2 and "locked" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def _artifact(tmp_path, name, calibration="c" * 64):
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"files": {"calibration.json": calibration}, "name": name}))
    return d


def _store(tmp_path, content="s"):
    d = tmp_path / f"store-{content}"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"content": content}))
    return d


def test_a_folder_takes_parts_only_from_the_inputs_it_recorded(tmp_path):
    """Codex on #85: resuming into a folder made by another artifact or store must refuse, not mix the two."""
    out = tmp_path / "risk"
    a, b, s = _artifact(tmp_path, "a"), _artifact(tmp_path, "b"), _store(tmp_path)
    risk_shard.claim(out, risk_shard.inputs_of("val", a, s))
    risk_shard.claim(out, risk_shard.inputs_of("val", a, s))        # the same inputs resume
    with pytest.raises(SystemExit, match="other inputs"):
        risk_shard.claim(out, risk_shard.inputs_of("val", b, s))
    with pytest.raises(SystemExit, match="other inputs"):
        risk_shard.claim(out, risk_shard.inputs_of("val", a, _store(tmp_path, "t")))


def test_parts_with_no_record_are_refused(tmp_path):
    out = tmp_path / "risk"
    out.mkdir()
    (out / "part-0000.parquet").write_bytes(b"old")
    with pytest.raises(SystemExit, match="no record"):
        risk_shard.claim(out, risk_shard.inputs_of("val", _artifact(tmp_path, "a"), _store(tmp_path)))
    assert not (out / "inputs.json").exists()


def test_a_record_is_published_whole(tmp_path, monkeypatch):
    """Codex on #85: a second shard must never read a record that is created but not yet written."""
    out = tmp_path / "risk"
    inputs = risk_shard.inputs_of("val", _artifact(tmp_path, "a"), _store(tmp_path))
    risk_shard.claim(out, inputs)
    assert json.loads((out / "inputs.json").read_text()) == inputs
    assert [p.name for p in out.iterdir()] == ["inputs.json"]          # no temporary file left behind
    seen = []
    real_link = risk_shard.os.link

    def link(src, dst):                                                 # the record must be complete when it appears
        seen.append(json.loads(Path(src).read_text()))
        return real_link(src, dst)
    monkeypatch.setattr(risk_shard.os, "link", link)
    risk_shard.claim(tmp_path / "risk2", inputs)
    assert seen == [inputs]
