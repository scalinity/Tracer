#!/bin/bash
# The Phase 2 candidates of docs/neural/phase2-log.md, each one command (docs/neural/preregistration-v2.md §7).
# Every candidate trains rung C on the coverage-fill store with one fixed budget: 20 epochs of 20 million samples on
# a cosine schedule, run to the end (patience 20), keeping its best validation epoch, so no node's speed and no early
# stop can change how much a candidate trains. Run from a checkout of this repository on HiPerGator:
#
#   bash docs/neural/hpg/candidates-v2.sh C2 0,1,2 cai6734
#
# submits one B200 job per seed. CODE (default $HPG/code-v2) is the checkout the jobs run.
set -euo pipefail
HPG=${HPG:-/blue/cai6734/daniel.escalante/tracer}
STORE=${STORE:-$HPG/store-v2-o4}
BUDGET="--set epoch_samples=20000000 --set max_epochs=20 --set patience=20 --set learning_rate=2.0e-3"
case "$1" in
  C1) EXTRA="" ;;                      # the learning rate and the fixed budget alone
  C2) EXTRA="--set rho=1.0" ;;         # plus the shift ranking term
  *) echo "unknown candidate $1 (C1 or C2)" >&2; exit 2 ;;
esac
HERE=$(cd "$(dirname "$0")" && pwd)
for seed in ${2//,/ }; do
  sbatch --parsable --account="${3:-cai6734}" --mem=60G --job-name="v2-$1-s$seed" ${DEPENDENCY:+--dependency=$DEPENDENCY} \
    --export=ALL,CODE="${CODE:-$HPG/code-v2}",ARGS="train --rung C --seeds $seed --store $STORE $BUDGET $EXTRA" \
    "$HERE/train-v2.sbatch"
done
