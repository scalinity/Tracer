#!/bin/bash
# One TracerNet seed on a HiPerGator B200, for the v2 work (docs/neural/preregistration-v2.md §7,
# docs/neural/phase2-log.md). Every candidate is this script with its own ARGS, for example:
#
#   sbatch --account=cai6734 --job-name=v2-C2-s0 \
#     --export=ALL,ARGS="train --rung C --seeds 0 --store $HPG/store-v2-r2 --set learning_rate=2.0e-3 --set rho=1.0" \
#     docs/neural/hpg/train-v2.sbatch
#
# A run that hits the time limit resumes exactly from its last epoch: the same ARGS plus --run-id RUN --resume.
# HPG is the project folder on /blue; CODE is a checkout of this repository at the commit being trained. A seed
# peaked at 53 GB of memory in the first artifact's training; each job asks for 60 GB.
#SBATCH --partition=hpg-b200
#SBATCH --gpus=1
#SBATCH --cpus-per-task=6
#SBATCH --mem=60G
#SBATCH --time=10:00:00
#SBATCH --output=/blue/cai6734/daniel.escalante/tracer/v2/logs/%x-%j.out
set -euo pipefail
HPG=${HPG:-/blue/cai6734/daniel.escalante/tracer}
CODE=${CODE:-$HPG/code-v2}
PY=${PY:-/blue/cai6734/daniel.escalante/envs/tracer314/bin/python}
cd "$CODE"
echo "start $(TZ=America/New_York date) on $(hostname), gpu: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
echo "code $(git rev-parse HEAD)"
export TRACER_PROGRESS=$HPG/v2/progress/${SLURM_JOB_NAME}-${SLURM_JOB_ID}.json
export OMP_NUM_THREADS=${TORCH_THREADS:-4} PYTHONUNBUFFERED=1
/usr/bin/time -v $PY -m src.neural.pipeline $ARGS 2>&1
echo "end $(TZ=America/New_York date)"
