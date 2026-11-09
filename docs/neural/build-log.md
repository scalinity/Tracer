# Build log: v2 scenario data and TracerNet

A running diary of the build that `docs/neural/spec.md` describes: what was done, in what order, what was found, and every call made along the way. Newest entries last. Times are US Eastern. The pull-request record is `docs/ai-teaming-log.md`; this log is the day-by-day detail behind it.

Every number this work reports comes from synthetic data with no clinical review.

## Sunday, November 8

**9:03 PM.** Started at commit `8686475` with no pull requests open. Read the spec end to end, then `AGENTS.md`, `CLAUDE.md`, `docs/architecture.md` §4, §5, §7 and §9, and every file the spec's §1 cites: `src/pipeline/evaluate.py`, `store.py`, `src/models/risk.py`, `src/generator/briefs.py`, `validate.py`, `config.py`, the configuration files and both guard tests.

**Environment.** The project venv is Python 3.14.10 on an Apple M5 Pro (18 cores, 48 GB). `pip index versions torch` lists torch 2.15.0 for this interpreter on macOS arm64, so spec §7.11 takes its first branch: torch goes into `requirements.txt` and no separate Python 3.13 venv is needed. The suite collects 599 tests. `.env` holds all three key names (only their lengths were checked). HiPerGator is reachable over SSH with the class allocation, and is held in reserve for training if the Mac is too slow.

**9:15 PM, N0.** Committed the spec. `.env.example` now names `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `OPENROUTER_API_KEY`. `tests/test_no_key_in_site.py` now matches OpenAI keys (plain, project, service-account and admin) and OpenRouter keys, with a look-behind so words such as "risk-adjusted" do not match; adds OpenAI's API host, TypeSafe's API host and OpenRouter's API path to the host list; and checks each of the three keys' values against every tracked file. It gained ten cases and passes, including against the spec itself. Added tasks N0 to N11 to the plan, N0 to N9 under Gate C; the plan check passes.

**A shared contract before parallel work.** N1 (schema and prompt) and N3 (simulator) are built at the same time by separate agents, so `config/scenario.example.json`, the spec's worked example, was written first and both build against it. Writing it settled five gaps in spec §4.3, recorded as assumptions:

- Disruptions with no line, route, unit or instrument target use fixed ids: `lab_station` for an unattended station, `receiving` for short staffing and scanner downtime.
- A `mixed` unit sends by tube with its tube share, otherwise by its courier route if it has one, otherwise on foot.
- One shift clock per scenario, from `receiving.shift_pattern`, governs every day, evening and night field (staffing, station emptying, courier rounds). Under `7-19` the evening values are unused.
- `response` is `none` for every disruption type except a tube line outage, and `noticed_after_min` only matters for outages.
- Tasks N10 and N11 join no gate, since the spec puts only N0 to N9 under Gate C.
