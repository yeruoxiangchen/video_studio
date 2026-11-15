#!/usr/bin/env bash
set -euo pipefail
CAMCAT_STUDIO_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export CAMCAT_STUDIO_DATA="${CAMCAT_STUDIO_DATA:-/data/zjr/camcat_studio}"
export TMPDIR="$CAMCAT_STUDIO_DATA/tmp"
export TORCH_HOME="$CAMCAT_STUDIO_DATA/cache/torch"
export HF_HOME="$CAMCAT_STUDIO_DATA/cache/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export TORCH_EXTENSIONS_DIR="$CAMCAT_STUDIO_DATA/cache/torch_extensions"
export MPLCONFIGDIR="$CAMCAT_STUDIO_DATA/cache/matplotlib"
export XDG_CACHE_HOME="$CAMCAT_STUDIO_DATA/cache/xdg"
export PIP_CACHE_DIR="$CAMCAT_STUDIO_DATA/cache/pip"
export npm_config_cache="$CAMCAT_STUDIO_DATA/cache/npm"
export RUFF_CACHE_DIR="$CAMCAT_STUDIO_DATA/cache/ruff"
export MYPY_CACHE_DIR="$CAMCAT_STUDIO_DATA/cache/mypy"
export PLAYWRIGHT_BROWSERS_PATH="$CAMCAT_STUDIO_DATA/cache/playwright"
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:-} -o cache_dir=$CAMCAT_STUDIO_DATA/cache/pytest"
export CUDA_CACHE_PATH="$CAMCAT_STUDIO_DATA/cache/cuda"
export NUMBA_CACHE_DIR="$CAMCAT_STUDIO_DATA/cache/numba"
export MODELSCOPE_CACHE="$CAMCAT_STUDIO_DATA/cache/modelscope"
export HF_DATASETS_CACHE="$CAMCAT_STUDIO_DATA/cache/huggingface/datasets"
export TRITON_CACHE_DIR="$CAMCAT_STUDIO_DATA/cache/triton"
export TORCHINDUCTOR_CACHE_DIR="$CAMCAT_STUDIO_DATA/cache/torchinductor"
export TVM_FFI_CACHE_DIR="$CAMCAT_STUDIO_DATA/cache/tvm-ffi"
export DOCKER_CONFIG="$CAMCAT_STUDIO_DATA/docker/client"
export OMP_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=2
export NUMEXPR_NUM_THREADS=2
export NO_PROXY="127.0.0.1,localhost,::1,api,postgres,minio,milvus,provider-gateway,speech${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"
export PATH="$CAMCAT_STUDIO_DATA/tools/docker:$CAMCAT_STUDIO_DATA/tools/ffmpeg:/usr/sbin:/sbin:$PATH"
if [[ -z "${DOCKER_HOST:-}" && -S "$CAMCAT_STUDIO_DATA/docker/docker.sock" ]]; then
  export DOCKER_HOST="unix://$CAMCAT_STUDIO_DATA/docker/docker.sock"
fi
if [[ -f "$CAMCAT_STUDIO_DATA/config/fonts.conf" ]]; then
  export FONTCONFIG_FILE="$CAMCAT_STUDIO_DATA/config/fonts.conf"
fi
mkdir -p "$TMPDIR" "$TORCH_HOME" "$HF_HUB_CACHE" "$TORCH_EXTENSIONS_DIR" "$MPLCONFIGDIR" \
  "$XDG_CACHE_HOME" "$PIP_CACHE_DIR" "$npm_config_cache" "$RUFF_CACHE_DIR" "$MYPY_CACHE_DIR" \
  "$CAMCAT_STUDIO_DATA/logs" "$CAMCAT_STUDIO_DATA/checks" "$CAMCAT_STUDIO_DATA/web"
cd -- "$CAMCAT_STUDIO_SOURCE"
camcat_offline_contract_environment() {
  export CAMCAT_ENVIRONMENT=test
  export CAMCAT_DATABASE_URL="postgresql+psycopg://camcat:unit@127.0.0.1:1/camcat_unit"
  export CAMCAT_RUNTIME_DIR="$CAMCAT_STUDIO_DATA/checks/unit-runtime"
  for CAMCAT_UNIT_PROVIDER in EMBEDDING RERANKER LLM ASR; do
    export "CAMCAT_${CAMCAT_UNIT_PROVIDER}_BASE_URL=http://127.0.0.1:1"
    export "CAMCAT_${CAMCAT_UNIT_PROVIDER}_API_KEY=unit-schema-only"
  done
  export CAMCAT_VISUAL_ANALYSIS_BASE_URL=""
  export CAMCAT_VISUAL_ANALYSIS_API_KEY=""
  export CAMCAT_VISUAL_ANALYSIS_MODEL=""
  export CAMCAT_NARRATIVE_BASE_URL=""
  export CAMCAT_NARRATIVE_API_KEY=""
  export CAMCAT_NARRATIVE_MODEL=""
  export CAMCAT_TRANSLATION_BASE_URL=""
  export CAMCAT_TRANSLATION_API_KEY=""
  export CAMCAT_TRANSLATION_MODEL=""
  export CAMCAT_TRANSLATION_BACKEND=chat
}
case "${1:-status}" in
  serve-planning-model)
    shift
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/serve_planning_model.py "$@"
    ;;
  start-planning-model)
    shift
    exec systemd-run --user --unit=camcat-studio-planning-model \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=48G --property=CPUQuota=600% --property=TasksMax=512 \
      --property=TimeoutStopSec=30 --property=Restart=no \
      --property=IOSchedulingClass=best-effort --property=IOSchedulingPriority=7 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/planning-model.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/planning-model.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" serve-planning-model "$@"
    ;;
  stop-planning-model)
    exec systemctl --user stop camcat-studio-planning-model
    ;;
  perception-contract)
    shift
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api:$CAMCAT_STUDIO_SOURCE"
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m pytest -q \
      tests/integration/test_source_perception_real.py "$@"
    ;;
  serve-visual-model)
    shift
    exec "$CAMCAT_STUDIO_DATA/quality-models-venv/bin/python" -B -u \
      scripts/serve_visual_model.py "$@"
    ;;
  setup-visual-model)
    exec "$CAMCAT_STUDIO_DATA/local-models-venv/bin/python" -B scripts/setup_visual_model.py
    ;;
  prepare-visual-model)
    exec "$CAMCAT_STUDIO_DATA/quality-models-venv/bin/python" -B scripts/prepare_visual_model.py
    ;;
  prepare-geometry)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/prepare_geometry.py
    ;;
  prepare-planning-model)
    shift
    exec "$CAMCAT_STUDIO_DATA/quality-models-venv/bin/python" -B scripts/prepare_planning_model.py "$@"
    ;;
  start-visual-model)
    shift
    exec systemd-run --user --unit=camcat-studio-visual-model \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=40G --property=CPUQuota=600% --property=TasksMax=512 \
      --property=TimeoutStopSec=30 --property=IOSchedulingClass=best-effort \
      --property=IOSchedulingPriority=7 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/visual-model.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/visual-model.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" serve-visual-model "$@"
    ;;
  stop-visual-model)
    exec systemctl --user stop camcat-studio-visual-model
    ;;
  public-bench)
    shift
    exec bash scripts/public_bench.sh "$@"
    ;;
  setup)
    exec > >(tee -a "$CAMCAT_STUDIO_DATA/logs/setup.log") 2>&1
    if [[ ! -x "$CAMCAT_STUDIO_DATA/venv/bin/python" ]]; then
      python3.12 -B -m venv "$CAMCAT_STUDIO_DATA/venv"
    fi
    "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m pip install -c requirements.studio.txt --disable-pip-version-check --timeout 120 --retries 3 -e '.[dev]'
    if [[ ! -e .venv ]]; then ln -s "$CAMCAT_STUDIO_DATA/venv" .venv; fi
    cp apps/web/package.json apps/web/package-lock.json "$CAMCAT_STUDIO_DATA/web/"
    npm ci --prefix "$CAMCAT_STUDIO_DATA/web" --no-audit --no-fund
    if [[ ! -e apps/web/node_modules ]]; then
      ln -s "$CAMCAT_STUDIO_DATA/web/node_modules" apps/web/node_modules
    fi
    printf '%s\n' 'Development dependencies installed; integration acceptance still requires the real Compose stack.'
    ;;
  unit)
    shift
    if [[ $# -eq 0 ]]; then set -- apps/api/tests/unit; fi
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api:$CAMCAT_STUDIO_SOURCE"
    camcat_offline_contract_environment
    CAMCAT_UNIT_PYTHON="$CAMCAT_STUDIO_DATA/venv/bin/python"
    if [[ -x "$CAMCAT_STUDIO_DATA/localization-eval-venv/bin/python" ]]; then
      CAMCAT_UNIT_PYTHON="$CAMCAT_STUDIO_DATA/localization-eval-venv/bin/python"
    fi
    exec "$CAMCAT_UNIT_PYTHON" -B -m pytest -q -m 'not integration' "$@"
    ;;
  contracts)
    camcat_offline_contract_environment
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api"
    shift
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/generate_openapi_contracts.py "$@"
    ;;
  prepare-speech)
    shift
    "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m pip install -c requirements.studio.txt --timeout 120 --retries 3 'piper-tts==1.3.0'
    exec ionice -c 3 "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/prepare_speech.py "$@"
    ;;
  prepare-deployment)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/prepare_deployment.py
    ;;
  lock-dependencies)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m pip freeze --exclude-editable > requirements.studio.txt
    ;;
  import-translation-provider)
    shift
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/import_translation_provider.py "$@"
    ;;
  translation-contract)
    shift
    camcat_offline_contract_environment
    CAMCAT_TRANSLATION_CHECKS="$CAMCAT_STUDIO_DATA/checks/translation-real"
    if [[ -f "$CAMCAT_STUDIO_DATA/config/active-translation.env" ]]; then
      source "$CAMCAT_STUDIO_DATA/config/active-translation.env"
      CAMCAT_TRANSLATION_CHECKS="$CAMCAT_STUDIO_DATA/checks/translation-shared-real"
    else
      source "$CAMCAT_STUDIO_DATA/config/translation.env"
    fi
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m pytest -q tests/integration/test_translation_real.py --basetemp "$CAMCAT_TRANSLATION_CHECKS" -o "cache_dir=$CAMCAT_STUDIO_DATA/cache/pytest" "$@"
    ;;
  serve-translation)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/serve_translation.py
    ;;
  start-translation)
    exec systemd-run --user --unit=camcat-studio-translation \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=48G --property=CPUQuota=400% --property=TasksMax=512 \
      --property=TimeoutStopSec=30 \
      --property=IOSchedulingClass=idle \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/translation-server.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/translation-server.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" serve-translation
    ;;
  download-core-models)
    export HF_HUB_DISABLE_XET=1
    export HF_HUB_DOWNLOAD_TIMEOUT=120
    export HF_HUB_ETAG_TIMEOUT=60
    exec /home/zjr/anaconda3/envs/chartvlm/bin/python -B -u scripts/download_core_models.py
    ;;
  prepare-local-models)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/setup_local_models.py
    ;;
  prepare-asr)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/prepare_multilingual_asr.py
    ;;
  configure-local)
    shift
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/configure_local_deployment.py "$@"
    ;;
  integration-local)
    shift
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/run_local_integration.py "$@"
    ;;
  setup-evaluation)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/setup_evaluation.py
    ;;
  full-validation)
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api:$CAMCAT_STUDIO_SOURCE"
    exec "$CAMCAT_STUDIO_DATA/localization-eval-venv/bin/python" -B -u -m scripts.run_full_validation
    ;;
  editing-repair)
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api:$CAMCAT_STUDIO_SOURCE"
    exec "$CAMCAT_STUDIO_DATA/local-models-venv/bin/python" -B -u -m scripts.run_editing_repair
    ;;
  full-validation-status)
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api:$CAMCAT_STUDIO_SOURCE"
    exec "$CAMCAT_STUDIO_DATA/localization-eval-venv/bin/python" -B -m scripts.run_full_validation --status
    ;;
  start-full-validation)
    CAMCAT_VALIDATION_SYSTEMD_ENV=()
    if [[ -n "${CAMCAT_VALIDATION_CONFIG:-}" ]]; then
      CAMCAT_VALIDATION_SYSTEMD_ENV+=("--setenv=CAMCAT_VALIDATION_CONFIG=$CAMCAT_VALIDATION_CONFIG")
    fi
    exec systemd-run --user --unit=camcat-studio-full-validation \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property="EnvironmentFile=$CAMCAT_STUDIO_DATA/config/network.env" \
      --property=MemoryMax=4G --property=CPUQuota=400% --property=TasksMax=512 \
      --property=RuntimeMaxSec=7200 --property=TimeoutStopSec=60 --property=Restart=no \
      --property=IOSchedulingClass=best-effort --property=IOSchedulingPriority=7 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/full-validation.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/full-validation.log" \
      "${CAMCAT_VALIDATION_SYSTEMD_ENV[@]}" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" full-validation
    ;;
  stop-full-validation)
    exec systemctl --user stop camcat-studio-full-validation
    ;;
  reliability)
    shift
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/measure_reliability.py "$@"
    ;;
  small-eval)
    shift
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api:$CAMCAT_STUDIO_SOURCE"
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/run_small_eval.py "$@"
    ;;
  start-small-eval)
    shift
    exec systemd-run --user --unit=camcat-studio-small-eval \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=2G --property=CPUQuota=200% --property=TasksMax=128 \
      --property=RuntimeMaxSec=45m --property=TimeoutStopSec=60 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/small-eval.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/small-eval.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" small-eval "$@"
    ;;
  small-eval-status)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -c \
      'import os,pathlib; p=pathlib.Path(os.environ["CAMCAT_STUDIO_DATA"])/"checks/small_eval/latest.json"; print(p.read_text() if p.exists() else "小样本评测尚未启动")'
    ;;
  stop-small-eval)
    exec systemctl --user stop camcat-studio-small-eval
    ;;
  validate-edits)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/validate_edit_regressions.py
    ;;
  start-edit-validation)
    exec systemd-run --user --unit=camcat-studio-edit-validation \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=4G --property=CPUQuota=400% --property=TasksMax=256 \
      --property=RuntimeMaxSec=45m --property=TimeoutStopSec=60 --property=Restart=no \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/edit-validation.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/edit-validation.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" validate-edits
    ;;
  edit-validation-status)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -c \
      'import os,pathlib; p=pathlib.Path(os.environ["CAMCAT_STUDIO_DATA"])/"checks/edit_regressions/latest.json"; print(p.read_text() if p.exists() else "编辑回归尚未启动")'
    ;;
  stop-edit-validation)
    exec systemctl --user stop camcat-studio-edit-validation
    ;;
  validate-delivery)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/validate_edit_regressions.py --public-editing-first
    ;;
  start-delivery-validation)
    exec systemd-run --user --unit=camcat-studio-delivery-validation \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=4G --property=CPUQuota=400% --property=TasksMax=512 \
      --property=RuntimeMaxSec=2h --property=TimeoutStopSec=60 --property=Restart=no \
      --property=IOSchedulingClass=idle \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/delivery-validation.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/delivery-validation.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" validate-delivery
    ;;
  delivery-validation-status)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/validate_edit_regressions.py --public-editing-first --status
    ;;
  stop-delivery-validation)
    exec systemctl --user stop camcat-studio-delivery-validation
    ;;
  profile-reads)
    shift
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api:$CAMCAT_STUDIO_SOURCE"
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/profile_read_routes.py "$@"
    ;;
  profile-source)
    shift
    export PYTHONPATH="$CAMCAT_STUDIO_SOURCE/apps/api:$CAMCAT_STUDIO_SOURCE"
    export CAMCAT_LLM_BASE_URL=http://127.0.0.1:18969
    export CAMCAT_ASR_BASE_URL=http://127.0.0.1:18969
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/profile_source_analysis.py "$@"
    ;;
  start-soak)
    shift
    exec systemd-run --user --unit=camcat-studio-soak \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=2G --property=CPUQuota=200% --property=TasksMax=128 \
      --property=RuntimeMaxSec=45m --property=TimeoutStopSec=60 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/reliability-soak.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/reliability-soak.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" reliability soak "$@"
    ;;
  stop-soak)
    exec systemctl --user stop camcat-studio-soak
    ;;
  reliability-status)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -c \
      'import json,os,pathlib; p=pathlib.Path(os.environ["CAMCAT_STUDIO_DATA"])/"checks/reliability/latest-soak.json"; print(p.read_text() if p.exists() else "稳定性测试尚未启动")'
    ;;
  grammar-contract)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/check_generation_grammar.py
    ;;
  prepare-acceptance-media)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/prepare_acceptance_media.py
    ;;
  bootstrap-local)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/bootstrap_local.py
    ;;
  start-bootstrap)
    exec systemd-run --user --unit=camcat-studio-bootstrap \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property="EnvironmentFile=-$CAMCAT_STUDIO_DATA/config/network.env" \
      --property=MemoryMax=8G --property=CPUQuota=400% --property=TasksMax=512 \
      --property=RuntimeMaxSec=24h --property=TimeoutStopSec=30 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/bootstrap.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/bootstrap.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" bootstrap-local
    ;;
  stop-bootstrap)
    exec systemctl --user stop camcat-studio-bootstrap
    ;;
  prepare-specialist-translation)
    shift
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m scripts.prepare_specialist_translation "$@"
    ;;
  serve-specialist-translation)
    shift
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/serve_specialist_translation.py "$@"
    ;;
  start-specialist-translation)
    shift
    exec systemd-run --user --unit=camcat-studio-specialist-translation \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=8G --property=CPUQuota=400% --property=TasksMax=256 \
      --property=Restart=no --property=TimeoutStopSec=30 \
      --property=IOSchedulingClass=best-effort --property=IOSchedulingPriority=7 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/specialist-translation-controller.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/specialist-translation-controller.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" serve-specialist-translation "$@"
    ;;
  stop-specialist-translation)
    exec systemctl --user stop camcat-studio-specialist-translation
    ;;
  serve-local-models)
    source "$CAMCAT_STUDIO_DATA/config/local-models.env"
    exec "$CAMCAT_STUDIO_DATA/local-models-venv/bin/python" -B -u scripts/serve_local_models.py
    ;;
  start-local-models)
    exec systemd-run --user --unit=camcat-studio-local-models \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property="EnvironmentFile=-$CAMCAT_STUDIO_DATA/config/network.env" \
      --property=MemoryMax=64G --property=CPUQuota=600% --property=TasksMax=512 \
      --property=IOSchedulingClass=best-effort --property=IOSchedulingPriority=7 \
      --property=TimeoutStopSec=30 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/local-models.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/local-models.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" serve-local-models
    ;;
  stop-local-models)
    exec systemctl --user stop camcat-studio-local-models
    ;;
  start-model-download)
    exec systemd-run --user --unit=camcat-studio-model-download \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property="EnvironmentFile=-$CAMCAT_STUDIO_DATA/config/network.env" \
      --property=MemoryMax=4G --property=CPUQuota=200% --property=TasksMax=64 \
      --property=IOSchedulingClass=idle --property=RuntimeMaxSec=12h \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/model-download.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/model-download.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" download-core-models
    ;;
  stop-translation)
    exec systemctl --user stop camcat-studio-translation
    ;;
  docker)
    shift
    exec docker "$@"
    ;;
  compose)
    shift
    if ! command -v docker >/dev/null; then
      printf '%s\n' 'Docker Engine + Compose v2 (or a configured DOCKER_HOST) is required.' >&2
      exit 1
    fi
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/studio_compose.py "$@"
    ;;
  setup-docker)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/setup_rootless_docker.py
    ;;
  capture-network)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/capture_network.py
    ;;
  probe-docker-network)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -u scripts/probe_docker_network.py
    ;;
  serve-docker)
    for CAMCAT_DOCKER_HELPER in newuidmap newgidmap slirp4netns; do
      if ! command -v "$CAMCAT_DOCKER_HELPER" >/dev/null; then
        printf 'Missing rootless Docker prerequisite: %s\n' "$CAMCAT_DOCKER_HELPER" >&2
        exit 1
      fi
    done
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    export DOCKERD_ROOTLESS_ROOTLESSKIT_STATE_DIR="$CAMCAT_STUDIO_DATA/docker/rootlesskit"
    export DOCKERD_ROOTLESS_ROOTLESSKIT_FLAGS='--disable-host-loopback=false'
    exec dockerd-rootless.sh --data-root "$CAMCAT_STUDIO_DATA/docker/data" \
      --exec-root "$CAMCAT_STUDIO_DATA/docker/exec" \
      --pidfile "$CAMCAT_STUDIO_DATA/docker/daemon.pid" \
      --host "unix://$CAMCAT_STUDIO_DATA/docker/docker.sock" \
      --storage-driver overlay2 --exec-opt native.cgroupdriver=systemd
    ;;
  start-docker)
    exec systemd-run --user --unit=camcat-studio-docker \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" --property=Delegate=yes \
      --property="EnvironmentFile=-$CAMCAT_STUDIO_DATA/config/network.env" \
      --property=TasksMax=infinity --property=TimeoutStopSec=60 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/docker.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/docker.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" serve-docker
    ;;
  stop-docker)
    exec systemctl --user stop camcat-studio-docker
    ;;
  serve-speech)
    source "$CAMCAT_STUDIO_DATA/config/speech.env"
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m uvicorn camcat.local_providers.speech:app --host "${CAMCAT_SPEECH_BIND:-127.0.0.1}" --port 18961 --workers 1 --no-access-log
    ;;
  start-speech)
    exec systemd-run --user --unit=camcat-studio-speech \
      --property="WorkingDirectory=$CAMCAT_STUDIO_SOURCE" \
      --property=MemoryMax=1G --property=CPUQuota=200% --property=TasksMax=64 \
      --property="StandardOutput=append:$CAMCAT_STUDIO_DATA/logs/speech.log" \
      --property="StandardError=append:$CAMCAT_STUDIO_DATA/logs/speech.log" \
      /bin/bash "$CAMCAT_STUDIO_SOURCE/scripts/studio.sh" serve-speech
    ;;
  stop-speech)
    exec systemctl --user stop camcat-studio-speech
    ;;
  speech-contract)
    shift
    camcat_offline_contract_environment
    source "$CAMCAT_STUDIO_DATA/config/speech.env"
    if [[ -f "$CAMCAT_STUDIO_DATA/config/active-translation.env" ]]; then
      source "$CAMCAT_STUDIO_DATA/config/active-translation.env"
    elif [[ -f "$CAMCAT_STUDIO_DATA/config/translation.env" ]]; then
      source "$CAMCAT_STUDIO_DATA/config/translation.env"
    fi
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m pytest -q tests/integration/test_localization_media_real.py --basetemp "$CAMCAT_STUDIO_DATA/checks/speech-real" "$@"
    ;;
  media-contract)
    shift
    camcat_offline_contract_environment
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B -m pytest -q tests/integration/test_compiled_ffmpeg_renderer.py --basetemp "$CAMCAT_STUDIO_DATA/checks/native-render" "$@"
    ;;
  browser)
    shift
    export CAMCAT_E2E_BASE_URL="${CAMCAT_E2E_BASE_URL:-http://127.0.0.1:18962}"
    export CAMCAT_E2E_API_BASE="${CAMCAT_E2E_API_BASE:-http://127.0.0.1:18960}"
    export CAMCAT_E2E_LOCALIZATION=1
    cd apps/web
    exec node node_modules/@playwright/test/cli.js test "$@"
    ;;
  prepare-browser)
    cd apps/web
    exec node node_modules/@playwright/test/cli.js install chromium
    ;;
  test-web)
    shift
    cd apps/web
    exec node --test "$@" tests/*.test.mjs
    ;;
  lint)
    "$CAMCAT_STUDIO_DATA/venv/bin/ruff" check apps/api scripts tests services
    exec "$CAMCAT_STUDIO_DATA/venv/bin/mypy" apps/api/camcat services
    ;;
  format)
    mapfile -t CAMCAT_CHANGED_PY < <(git ls-files -m -o --exclude-standard -- '*.py')
    if [[ ${#CAMCAT_CHANGED_PY[@]} -gt 0 ]]; then
      "$CAMCAT_STUDIO_DATA/venv/bin/ruff" format "${CAMCAT_CHANGED_PY[@]}"
      "$CAMCAT_STUDIO_DATA/venv/bin/ruff" check --fix "${CAMCAT_CHANGED_PY[@]}"
    fi
    ;;
  build-web)
    cd apps/web
    node node_modules/typescript/bin/tsc -p tsconfig.json --incremental --tsBuildInfoFile "$CAMCAT_STUDIO_DATA/web/tsconfig.tsbuildinfo"
    exec node node_modules/vite/bin/vite.js build --outDir "$CAMCAT_STUDIO_DATA/web/dist"
    ;;
  status)
    exec "$CAMCAT_STUDIO_DATA/venv/bin/python" -B scripts/studio_status.py
    ;;
  *) printf '%s\n' 'Usage: bash scripts/studio.sh {setup|unit|contracts|test-web|format|lint|build-web|prepare-speech|prepare-deployment|start-speech|stop-speech|speech-contract|start-translation|stop-translation|import-translation-provider|translation-contract|media-contract|compose|prepare-browser|browser|status} [args]' >&2; exit 2 ;;
esac
