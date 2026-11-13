#!/bin/bash
# The fresh test set's one look (docs/neural/preregistration-v2.md §8) on HiPerGator's default partition: simulate
# and store the frozen fresh library, then score it once with the frozen artifact. Run from a checkout of this
# repository at the commit that froze the fresh library; its data/sim-fresh and data/store-v2-fresh become links to
# SIM and STORE, outside the checkout. TRAIN_EVAL is the v2 store whose train split gives the global fits.
#
#   CODE=$HPG/code-fresh bash docs/neural/hpg/fresh-v2.sh
#
# The evaluation writes results/neural/test_fresh/ in the checkout: the lock, the run log, the evaluation and
# looked.json, after which no look runs again. A run that fails before its evaluation is written may resume on the
# same locked inputs: submit the evaluation job again unchanged (the run log records each attempt).
set -euo pipefail
HPG=${HPG:-/blue/cai6734/daniel.escalante/tracer}
CODE=${CODE:?a checkout at the commit that froze the fresh library}
SIM=${SIM:-$HPG/sim-fresh}
STORE=${STORE:-$HPG/store-v2-fresh}
TRAIN_EVAL=${TRAIN_EVAL:-$HPG/store-v2-o4-eval}
ACCOUNT=${ACCOUNT:-cai6734}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$SIM" "$STORE" "$CODE/data"
[ -e "$CODE/data/sim-fresh" ] || ln -s "$SIM" "$CODE/data/sim-fresh"
[ -e "$CODE/data/store-v2-fresh" ] || ln -s "$STORE" "$CODE/data/store-v2-fresh"
run() {   # name, cpus, memory, time, dependency, command
  sbatch --parsable --account="$ACCOUNT" --qos="$ACCOUNT-b" --job-name="v2-fresh-$1" --cpus-per-task="$2" \
    --mem="$3" --time="$4" ${5:+--dependency=afterok:$5} --export=ALL,CODE="$CODE",CMD="$6" "$HERE/cpu-v2.sbatch"
}
sim=$(run simulate 64 192G 4:00:00 "" "-m src.neural.pipeline simulate --fresh --workers 63")
sto=$(run store 64 192G 4:00:00 "$sim" "-m src.neural.pipeline store --fresh --workers 63")
look=$(run look 32 600G 24:00:00 "$sto" "-m src.neural.pipeline evaluate --split test_fresh \
--artifact artifacts/tracernet/v2 --store data/store-v2-fresh --train-store $TRAIN_EVAL --site-map --workers 31")
echo "simulate $sim, store $sto, the one look $look"
