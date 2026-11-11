"""scripts/key_history_check.py finds a key value anywhere in the history and prints only names (spec §17 item 1)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE = "sk-fake-" + "0123456789abcdef" * 3


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def run(repo, env):
    return subprocess.run([sys.executable, str(ROOT / "scripts/key_history_check.py"), "--repo", str(repo),
                           "--env", str(env)], capture_output=True, text=True)


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    return repo


def test_a_key_value_in_an_old_commit_is_found_and_only_names_are_printed(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "notes.txt").write_text(f"token {FAKE}\n")
    git(repo, "add", "notes.txt")
    git(repo, "commit", "-qm", "add")
    (repo / "notes.txt").write_text("cleaned\n")                     # gone from the tree, still in the history
    git(repo, "commit", "-qam", "clean")
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={FAKE}\nANTHROPIC_API_KEY=\n")
    r = run(repo, env)
    assert r.returncode == 1
    assert "FOUND OPENAI_API_KEY in notes.txt" in r.stdout
    assert FAKE not in r.stdout + r.stderr


def test_a_clean_history_finds_none(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("nothing secret\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "add")
    env = tmp_path / ".env"
    env.write_text(f'OPENROUTER_API_KEY="{FAKE}"\n')
    r = run(repo, env)
    assert r.returncode == 0 and "none found" in r.stdout and FAKE not in r.stdout
