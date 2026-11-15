#!/usr/bin/env bash
set -euo pipefail
CAMCAT_BENCH_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export CAMCAT_STUDIO_DATA="${CAMCAT_STUDIO_DATA:-/data/zjr/camcat_studio}"
export TMPDIR="$CAMCAT_STUDIO_DATA/tmp"
export TORCH_HOME="$CAMCAT_STUDIO_DATA/cache/torch"
export HF_HOME="$CAMCAT_STUDIO_DATA/cache/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export TORCH_EXTENSIONS_DIR="$CAMCAT_STUDIO_DATA/cache/torch_extensions"
export MPLCONFIGDIR="$CAMCAT_STUDIO_DATA/cache/matplotlib"
export XDG_CACHE_HOME="$CAMCAT_STUDIO_DATA/cache/xdg"
export CUDA_CACHE_PATH="$CAMCAT_STUDIO_DATA/cache/cuda"
export PYTHONDONTWRITEBYTECODE=1
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=2
export PYTHONPATH="$CAMCAT_BENCH_SOURCE:$CAMCAT_BENCH_SOURCE/apps/api"
export PATH="$CAMCAT_STUDIO_DATA/tools/ffmpeg:$PATH"
cd -- "$CAMCAT_BENCH_SOURCE"
case "${1:-status}" in
  prepare|retrieval|editing|verify-editing|holdout)
    CAMCAT_BENCH_ACTION="$1"
    shift
    case "$CAMCAT_BENCH_ACTION" in
      prepare) CAMCAT_BENCH_MODULE=scripts.prepare_public_benchmarks ;;
      retrieval) CAMCAT_BENCH_MODULE=scripts.run_public_retrieval ;;
      editing) CAMCAT_BENCH_MODULE=scripts.run_public_editing ;;
      verify-editing) CAMCAT_BENCH_MODULE=scripts.verify_public_editing_run ;;
      holdout) CAMCAT_BENCH_MODULE=scripts.run_public_holdout ;;
    esac
    exec "$CAMCAT_STUDIO_DATA/local-models-venv/bin/python" -B -u -m "$CAMCAT_BENCH_MODULE" "$@"
    ;;
  start-retrieval|start-editing|start-verify-editing|start-holdout)
    CAMCAT_BENCH_KIND="${1#start-}"
    shift
    CAMCAT_BENCH_MEMORY=4G
    if [[ "$CAMCAT_BENCH_KIND" == retrieval ]]; then CAMCAT_BENCH_MEMORY=32G; fi
    exec systemd-run --user --unit="camcat-public-$CAMCAT_BENCH_KIND" \
      --property="WorkingDirectory=$CAMCAT_BENCH_SOURCE" \
      --property="EnvironmentFile=$CAMCAT_STUDIO_DATA/config/network.env" \
      --property="MemoryMax=$CAMCAT_BENCH_MEMORY" --property=CPUQuota=400% \
      --property=TasksMax=512 --property=RuntimeMaxSec=7200 \
      --property=TimeoutStopSec=30 --property=Restart=no --property=IOSchedulingClass=idle \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/public-$CAMCAT_BENCH_KIND.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/public-$CAMCAT_BENCH_KIND.log" \
      /bin/bash "$CAMCAT_BENCH_SOURCE/scripts/public_bench.sh" "$CAMCAT_BENCH_KIND" "$@"
    ;;
  stop-retrieval|stop-editing|stop-verify-editing|stop-holdout)
    exec systemctl --user stop "camcat-public-${1#stop-}"
    ;;
  holdout-status)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -c '
import json,os,pathlib
p=pathlib.Path(os.environ["CAMCAT_STUDIO_DATA"])/"benchmarks/runs/editing_holdout/latest.json"
s=json.loads(p.read_text()) if p.exists() else {"state":"not_started"}
keys=("run_id","pid","state","phase","elapsed_seconds","updated_at","job_progress","summary","error","output")
view={k:s[k] for k in keys if k in s}
view["cases"]=[{k:r[k] for k in ("id","state","reward","seconds","error","harness_error") if k in r} for r in s.get("cases",[])]
print(json.dumps(view,ensure_ascii=False,indent=2))'
    ;;
  status)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -c 'import json,os,pathlib; r=pathlib.Path(os.environ["CAMCAT_STUDIO_DATA"])/"benchmarks"; print(json.dumps({k:json.loads(p.read_text()) if p.exists() else {"state":"not_started"} for k in ("retrieval","editing","editing_validation","editing_holdout") for p in [r/"runs"/k/"latest.json"]},ensure_ascii=False,indent=2))'
    ;;
  *) printf '%s\n' 'Usage: bash scripts/public_bench.sh {prepare|retrieval|editing|holdout|start-retrieval|start-editing|start-holdout|holdout-status|status|stop-retrieval|stop-editing|stop-holdout}'; exit 2 ;;
esac
