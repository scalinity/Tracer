#!/bin/bash
# A CPU job for the v2 work on HiPerGator's default partition (AMD processors, where every committed risk table was
# scored): a validation risk-table shard, an evaluation, a calibration on CPU or the test suite. For example:
#
#   sbatch --account=cai6734 --qos=cai6734-b --job-name=v2-score-C2-0 --cpus-per-task=32 --mem=120G \
#     --export=ALL,CMD="-m src.neural.risk_shard --split val --artifact $HPG/v2/artifacts/C2 \
#       --store $HPG/store-v2-r2 --out $HPG/v2/risk/C2 --parts 0:6 --workers 31" docs/neural/hpg/cpu-v2.sbatch
#
#   sbatch ... --export=ALL,CMD="-m src.neural.pipeline evaluate --split val --tracernet $HPG/v2/risk/C2/*.parquet \
#       --store $HPG/store-v2-r2-eval --out $HPG/v2/eval/C2 --workers 31" docs/neural/hpg/cpu-v2.sbatch
#SBATCH --partition=hpg-default
#SBATCH --cpus-per-task=32
#SBATCH --mem=120G
#SBATCH --time=12:00:00
#SBATCH --output=/blue/cai6734/daniel.escalante/tracer/v2/logs/%x-%j.out
set -euo pipefail
HPG=${HPG:-/blue/cai6734/daniel.escalante/tracer}
CODE=${CODE:-$HPG/code-v2}
PY=${PY:-/blue/cai6734/daniel.escalante/envs/tracer314/bin/python}
cd "$CODE"
echo "start $(TZ=America/New_York date) on $(hostname), $(nproc) cpus, $(lscpu | grep 'Model name' | sed 's/  */ /g')"
echo "code $(git rev-parse HEAD)"
export TRACER_PROGRESS=$HPG/v2/progress/${SLURM_JOB_NAME}-${SLURM_JOB_ID}.json
export OMP_NUM_THREADS=${TORCH_THREADS:-1} PYTHONUNBUFFERED=1
/usr/bin/time -v $PY $CMD 2>&1
echo "end $(TZ=America/New_York date)"
