#!/bin/bash
# Simulate the frozen library and build store v2 from it (docs/neural/spec.md §5, §6.4) on HiPerGator, for the v2
# work (docs/neural/preregistration-v2.md). CODE is a checkout at the commit that froze the library; its data/sim and
# data/store-v2 are links to SIM and EVAL, so the 11 GB simulation and the truth stay outside the checkout. TRAIN gets
# the observed file alone (a hard link, plus the manifest): training reads TRAIN and never sees the truth.
#
#   sbatch --account=cai6734 --qos=cai6734-b --job-name=v2-sim-store \
#     --export=ALL,CODE=$HPG/code-o4,SIM=$HPG/sim-o4,EVAL=$HPG/store-v2-o4-eval,TRAIN=$HPG/store-v2-o4 \
#     docs/neural/hpg/sim-store-v2.sbatch
#SBATCH --partition=hpg-default
#SBATCH --cpus-per-task=64
#SBATCH --mem=192G
#SBATCH --time=4:00:00
#SBATCH --output=/blue/cai6734/daniel.escalante/tracer/v2/logs/%x-%j.out
set -euo pipefail
HPG=${HPG:-/blue/cai6734/daniel.escalante/tracer}
PY=${PY:-/blue/cai6734/daniel.escalante/envs/tracer314/bin/python}
export TRACER_PROGRESS=$HPG/v2/progress/${SLURM_JOB_NAME}-${SLURM_JOB_ID}.json
export OMP_NUM_THREADS=1 PYTHONUNBUFFERED=1
mkdir -p "$SIM" "$EVAL" "$TRAIN" "$CODE/data"
[ -e "$CODE/data/sim" ] || ln -s "$SIM" "$CODE/data/sim"
[ -e "$CODE/data/store-v2" ] || ln -s "$EVAL" "$CODE/data/store-v2"
cd "$CODE"
echo "start $(TZ=America/New_York date) on $(hostname), ${SLURM_CPUS_PER_TASK} cpus; code $(git rev-parse HEAD)"
sha256sum datasets/scenarios-v2.jsonl.gz
/usr/bin/time -v $PY -m src.neural.pipeline simulate --workers $((SLURM_CPUS_PER_TASK - 1)) 2>&1
echo "simulate end $(TZ=America/New_York date)"
/usr/bin/time -v $PY -m src.neural.pipeline store --workers $((SLURM_CPUS_PER_TASK - 1)) 2>&1
echo "store end $(TZ=America/New_York date)"
ln -f "$EVAL/observed.duckdb" "$TRAIN/observed.duckdb"
cp "$EVAL/manifest.json" "$TRAIN/manifest.json"
sha256sum "$EVAL/observed.duckdb" "$EVAL/truth.duckdb"
echo "end $(TZ=America/New_York date)"
