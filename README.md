# Tracer

Predictive specimen tracking for the clinical microbiology laboratory. Between the collection scan
and the laboratory receipt scan a specimen has no recorded state. Tracer estimates how likely each
in-flight specimen is to miss its stability window, and where it most likely is. It raises a routed
alert while the specimen can still be recovered.

Course project for CAI 5724, AI in Health Design Studio I (University of Florida).
Team: Daniel Escalante and Karissa Kocourek.

Events come from the project's own simulator, because no public dataset records where a specimen is
mid-transit, when it was received, or that one never arrived at all. Calibrating the simulator against
MIMIC-IV was tested and rejected — it describes a critical-care population whose specimen mix and collection
hours differ systematically from the general laboratory this models, and more data from the same hospital
would not change that. Every parameter is therefore an assumption, drawn from published laboratory
literature and marked as such. The check is in `docs/mimic-demo-findings.md`; no workplace data is used
anywhere.

## Running it

Nothing is runnable yet; the build order is in `docs/architecture.md` §8. The intended flow, once built:

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m src.simulator --seed 0 --days 90 --out data/run-0     # generate an event log
python -m src.pipeline --run data/run-0                         # fit intervals, score, evaluate
uvicorn src.api.app:app --reload                                # live board on localhost:8000
```

Everything regenerates from a seed; no external dataset is required. `notebooks/` holds the one-off
feasibility check that ruled calibration out, which needs the MIMIC-IV demo extract in `data/mimic-demo/`
if you want to re-run it.

The system map opens directly from disk and needs no server: `open visuals/architecture.html`.

## Where things live

| Path | Contents |
|---|---|
| `docs/project-spec.md` | Problem, scope, evaluation plan, division of labor |
| `docs/architecture.md` | Components, data model, models, simulator, interfaces, build order |
| `docs/submissions/` | Each course milestone as `Milestone N - Title`, in `.docx` and `.pdf`; Milestones 2 and 3 also have a Markdown mirror. Milestone 4 is a LaTeX-built `.pdf` with its `.tex` source and a Markdown mirror |
| `docs/course/` | Course guidelines and blank templates, unmodified; kept local, not in the repository |
| `visuals/architecture.html` | Interactive system map; seed of the Week 15 demo |
| `config/` | Karissa's policy table as exported (`policy_table.csv`), sources we added (`policy_sources.csv`), the compiled `breach_policy.yaml` and the workflow graph |
| `src/` | `generator/`, `simulator/`, `pipeline/`, `models/`, `api/` |
| `tests/` | `pytest`; the policy loader and its fail-loudly checks |
| `notes/` | Working notes, questions, meeting capture; kept local, not in the repository |
| `data/` | MIMIC extracts and simulator output (gitignored) |
