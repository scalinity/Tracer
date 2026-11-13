#!/bin/bash
# Score one Phase 2 candidate on validation (docs/neural/preregistration-v2.md §7): its ensemble and calibration on a
# B200, its validation risk table in shards on the burst CPU allocation, then the evaluation that the choice reads.
# Each step waits for the one before it.
#
#   bash docs/neural/hpg/score-candidate-v2.sh C2 RUN_DIR_SEED0 RUN_DIR_SEED1 RUN_DIR_SEED2
#
# writes $HPG/v2/artifacts/NAME, $HPG/v2/risk/NAME/part-*.parquet and $HPG/v2/eval/NAME/evaluation.json.
set -euo pipefail
HPG=${HPG:-/blue/cai6734/daniel.escalante/tracer}
CODE=${CODE:-$HPG/code-p2}
TRAIN=${TRAIN:-$HPG/store-v2-o4}          # the training store (observed only)
EVAL=${EVAL:-$HPG/store-v2-o4-eval}       # the same store with the truth, which only the evaluation reads
SHARDS=${SHARDS:-4}
NAME=$1; shift
RUNS=""; for r in "$@"; do RUNS="$RUNS --run $r"; done
HERE=$(cd "$(dirname "$0")" && pwd)
ART=$HPG/v2/artifacts/$NAME; RISK=$HPG/v2/risk/$NAME; OUT=$HPG/v2/eval/$NAME
cal=$(sbatch --parsable --account=${GPU_ACCOUNT:-cai6734} --mem=60G --time=2:00:00 --job-name=v2-cal-$NAME \
  ${DEPENDENCY:+--dependency=$DEPENDENCY} \
  --export=ALL,CODE=$CODE,CMD="-m src.neural.ensemble calibrate --store $TRAIN$RUNS --out $ART --version tracernet-v2" \
  "$HERE/train-v2.sbatch")
echo "calibrate $cal"
parts=$(( (1091 + 49) / 50 )); per=$(( (parts + SHARDS - 1) / SHARDS )); deps=""
for ((k = 0; k < SHARDS; k++)); do
  j=$(sbatch --parsable --account=cai6734 --qos=cai6734-b --cpus-per-task=32 --mem=120G --job-name=v2-risk-$NAME-$k \
    --dependency=afterok:$cal --export=ALL,CODE=$CODE,CMD="-m src.neural.risk_shard --split val --artifact $ART \
--store $TRAIN --out $RISK --parts $((k * per)):$(((k + 1) * per)) --workers 31" "$HERE/cpu-v2.sbatch")
  deps="$deps:$j"; echo "risk shard $k $j"
done
ev=$(sbatch --parsable --account=cai6734 --qos=cai6734-b --cpus-per-task=32 --mem=160G --job-name=v2-eval-$NAME \
  --dependency=afterok$deps --export=ALL,CODE=$CODE,CMD="-m src.neural.pipeline evaluate --split val \
--tracernet $RISK/*.parquet --store $EVAL --out $OUT --workers 31" "$HERE/cpu-v2.sbatch")
echo "evaluate $ev"
