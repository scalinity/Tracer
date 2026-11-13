"""The validation risk-table shard (src/neural/risk_shard.py) never scores a locked set."""
from pathlib import Path

import pytest

from src.neural import risk_shard


@pytest.mark.parametrize("split", ["test", "test_ood"])
def test_a_locked_set_is_refused_before_anything_is_read(split, tmp_path, capsys):
    code = risk_shard.main(["--split", split, "--artifact", str(tmp_path / "none"), "--store", str(tmp_path / "none"),
                            "--out", str(tmp_path / "out")])
    assert code == 2 and "locked" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
