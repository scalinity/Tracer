"""Look for every key value in .env across the full git history (spec §17 item 1).

    python scripts/key_history_check.py [--env PATH] [--repo PATH]

Every blob reachable from any ref is read once through `git cat-file --batch` and searched for each non-empty value
in .env. It prints only the names of the files and keys where a value appears, never a value, and exits 1 if any
appears, 0 if none does.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def env_values(path: Path) -> dict[str, bytes]:
    """KEY=value lines of a .env file, without quotes; empty values are left out."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip().strip("'\"")
        if value:
            out[name.strip().removeprefix("export ").strip()] = value.encode()
    return out


def blobs(repo: Path) -> dict[str, set[str]]:
    """Every blob reachable from any ref, with the paths it appears under."""
    listing = subprocess.run(["git", "-C", str(repo), "rev-list", "--all", "--objects"],
                             capture_output=True, text=True, check=True).stdout.splitlines()
    paths: dict[str, set[str]] = {}
    for line in listing:
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, set()).add(path)
    return paths


def scan(repo: Path, values: dict[str, bytes]) -> list[tuple[str, str]]:
    """(path, key name) for every blob holding a value."""
    paths = blobs(repo)
    found = []
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    try:
        for sha in paths:
            proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if len(header) < 3 or header[1] != b"blob":
                if len(header) >= 3:
                    proc.stdout.read(int(header[2]) + 1)
                continue
            data = proc.stdout.read(int(header[2]))
            proc.stdout.read(1)
            for name, value in values.items():
                if value in data:
                    found += [(p, name) for p in sorted(paths[sha])]
    finally:
        proc.stdin.close()
        proc.wait()
    return sorted(set(found))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--env", default=None, help="default: .env in the repository")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    env = Path(args.env) if args.env else repo / ".env"
    if not env.exists():
        print(f"no {env}: nothing to look for", file=sys.stderr)
        return 2
    values = env_values(env)
    found = scan(repo, values)
    commits = subprocess.run(["git", "-C", str(repo), "rev-list", "--all", "--count"], capture_output=True, text=True,
                             check=True).stdout.strip()
    print(f"{len(values)} key values from {env.name}, searched in every blob of {commits} commits")
    for path, name in found:
        print(f"FOUND {name} in {path}")
    print("none found" if not found else f"{len(found)} file(s) hold a key value")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
