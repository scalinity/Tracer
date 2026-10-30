# Tracer

Predictive specimen tracking for the clinical microbiology laboratory. Between the collection scan
and the laboratory receipt scan a specimen has no recorded state. Tracer estimates how likely each
in-flight specimen is to miss its stability window, and where it most likely is. It raises a routed
alert while the specimen can still be recovered.

Course project for CAI 5724, AI in Health Design Studio I (University of Florida).
Team: Daniel Escalante and Karissa Kocourek.

Events come from a Claude-generated synthetic dataset, because no public dataset records where a specimen
is mid-transit, when it was received, or that one never arrived at all. Seeded code draws each specimen's
brief and every timing, `claude-haiku-5-5` writes the scan events and the hidden true path, a nine-check
validator accepts or rejects each trail, and the accepted output is frozen with a manifest in `datasets/`.
The dataset is synthetic and had no clinical review. Calibrating against MIMIC-IV was tested and
rejected — it describes a critical-care population whose specimen mix and collection
hours differ systematically from the general laboratory this models, and more data from the same hospital
would not change that. Every parameter is therefore an assumption, drawn from published laboratory
literature and marked as such. The check is in `docs/mimic-demo-findings.md`; no workplace data is used
anywhere.

## Running it

Everything below runs from the frozen dataset, with no API key and no network.

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                                    # the unit and integration tests, all offline

python -m src.pipeline.store              # load datasets/ into data/store/: an observed file and a truth file
python -m src.pipeline.intervals          # fit the transit-time distributions -> results/interval_params.json
python -m src.pipeline.recovery           # does the fit recover what the generator planted?
python -m src.pipeline.evaluate           # baselines against the risk model on the held-out runs, and the alert list
python -m src.pipeline.location_eval      # score the location filter against the hidden truth
python -m src.api.export --run test       # write docs/demo/bundle.json for the demo
python -m src.pipeline.sensitivity      # reweight the held-out runs for other volumes, hours and mixes

node visuals/plan.check.cjs               # the plan's data is consistent
(cd visuals/exhibit-3d && npm install && npm test)   # the 3D exhibit's tests; `npm run dev` serves it
```

Generating a dataset needs `ANTHROPIC_API_KEY` in `.env` and spends money. The first command prints a table
of briefs and calls nothing; the second prices the full job and stops, and a real run requires `--budget-usd`:

```sh
python -m src.generator.briefs --seed 0 --run 0 --n 200
python -m src.generator.run --seed 1 --runs 30 --n 400 --batch --dry-run --budget-usd 5
```

The first end-to-end results are saved in `results/mvp-nov8.json` (tag `v0.1-mvp`) and are never edited.

Everything regenerates from a seed; no external dataset is required. `notebooks/` holds the one-off
feasibility check that ruled calibration out, which needs the MIMIC-IV demo extract in `data/mimic-demo/`
if you want to re-run it.

The system map opens directly from disk and needs no server: `open visuals/architecture.html`.

## Where things live

| Path | Contents |
|---|---|
| `docs/project-spec.md` | Problem, scope, evaluation plan, division of labor |
| `docs/architecture.md` | Components, data model, models, generator, interfaces, build order |
| `docs/submissions/` | Each course milestone as `Milestone N - Title`, in `.docx` and `.pdf`; Milestones 2 and 3 also have a Markdown mirror. Milestone 4 is a LaTeX-built `.pdf` with its `.tex` source and a Markdown mirror |
| `docs/ai-teaming-log.md` | How Claude Code and the Claude API were used: one dated entry per pull request, the API jobs with their costs, and what worked |
| `docs/course/` | Course guidelines and blank templates, unmodified. Only `ai-design-project-guidelines.pdf` (the revised project guidelines) is in the repository; the rest stays local |
| `visuals/architecture.html` | Interactive system map; seed of the Week 15 demo |
| `visuals/exhibit-3d/` | The 3D bench, "The Silent Specimen", with its built `dist/` |
| `config/` | Karissa's policy table as exported (`policy_table.csv`), sources we added (`policy_sources.csv`), the compiled `breach_policy.yaml`, the workflow graph and the generator parameters (`config.yaml`) |
| `datasets/` | The frozen synthetic dataset and its manifest (model, hashes, seed, counts, cost) |
| `src/` | `generator/` (briefs, prompt, validator, batch runs), `pipeline/` (store, fit, evaluate, triage), `models/` (risk, location), `api/` |
| `tests/` | `pytest`: the policy and config loaders, the generator and its validator, the store, the fit, the models, the evaluation and the midterm snapshot |
| `docs/demo/` | The demo's data: `bundle.json` (the held-out specimens with their risk, alerts and truth, and a grid of 20 operating points) and its `bundle.schema.json`, plus `briefs.json`, a template and a Claude search brief for each alert; `python -m src.api.export` and `python -m src.api.search_briefs` write them |
| `results/` | Fitted parameters, the evaluation, the alert list, the location scores, the recovery plot and the midterm snapshot `mvp-nov8.json` |
| `notes/` | Working notes, questions, meeting capture; kept local, not in the repository |
| `data/` | Claude output, the two-file store in `data/store/` and MIMIC extracts (gitignored) |
