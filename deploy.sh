#!/usr/bin/env bash
# StockPilot 部署脚本
#
# 用法:
#   ./deploy.sh              # 全量首次部署
#   ./deploy.sh code         # ★ 日常: 构建镜像 → 导入 → 滚动重启 app
#   ./deploy.sh code --fresh # 跳过缓存完整重建 (改了 requirements.txt 时用)
#   ./deploy.sh secrets      # 只更新 k8s Secret 并重启
#   ./deploy.sh config       # 只更新 ConfigMap (watchlist/grafana) 并重启
#   ./deploy.sh restart      # 只滚动重启所有 Pod (不重建镜像)
#   ./deploy.sh status       # 查看当前 Pod / Job 状态
#   ./deploy.sh logs [app|tsdb|grafana]  # 实时日志 (默认 app)
#   ./deploy.sh all          # 全量部署 (同无参数)

set -euo pipefail

# ─── 配置 ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KUBECTL="/snap/bin/microk8s kubectl"
NS=stockpilot
# 每次构建生成唯一 tag，避免 imagePullPolicy:Never 使用旧缓存
IMAGE_TAG="v$(date +%Y%m%d-%H%M%S)"
IMAGE="stockpilot-app:${IMAGE_TAG}"
TAR=/tmp/stockpilot-app.tar

# ─── 颜色 ──────────────────────────────────────────────────────────────────
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[0;33m'
C_CYAN='\033[0;36m'
C_RED='\033[0;31m'
C_GRAY='\033[0;90m'

step()  { echo -e "\n${C_CYAN}${C_BOLD}▶ $*${C_RESET}"; }
ok()    { echo -e "${C_GREEN}  ✓ $*${C_RESET}"; }
warn()  { echo -e "${C_YELLOW}  ⚠ $*${C_RESET}"; }
die()   { echo -e "${C_RED}  ✗ $*${C_RESET}" >&2; exit 1; }
info()  { echo -e "${C_GRAY}  $*${C_RESET}"; }

# ─── 计时工具 ────────────────────────────────────────────────────────────────
_T0=0
timer_start() { _T0=$SECONDS; }
timer_end()   { echo -e "${C_GRAY}  ↳ 耗时 $(( SECONDS - _T0 ))s${C_RESET}"; }

# ─── 核心操作 ────────────────────────────────────────────────────────────────

build_image() {
  local fresh="${1:-}"
  step "构建镜像 ${IMAGE}"
  timer_start
  local build_args=""
  [[ "$fresh" == "--fresh" ]] && build_args="--no-cache" && info "跳过缓存 (--fresh)"
  # --network=host：让构建期(pip/npm)走宿主机解析器，规避 Docker 桥接网默认 DNS
  # 在本机 IPv6 优先/不可达环境下的外网解析失败。
  docker build --network=host $build_args -t "${IMAGE}" "$SCRIPT_DIR/app"
  timer_end
  ok "镜像构建完成"
}

import_image() {
  step "导入镜像到 microk8s"
  timer_start
  info "保存到 ${TAR} ..."
  docker save "${IMAGE}" -o "${TAR}"
  info "导入 containerd ..."
  /snap/bin/microk8s ctr image import "${TAR}"
  rm -f "${TAR}"
  timer_end
  ok "镜像导入完成"
}

restart_app() {
  step "更新镜像并重启 stockpilot-app → ${IMAGE}"
  $KUBECTL set image deployment/stockpilot-app app="docker.io/library/${IMAGE}" -n $NS
  $KUBECTL rollout status deployment/stockpilot-app -n $NS --timeout=120s
  ok "App 已就绪"
}

prune_images() {
  step "清理旧版 stockpilot-app 镜像 (保留当前: ${IMAGE})"
  # Docker: 删除所有旧 tag（当前 tag 不在其中）
  local old_docker
  old_docker=$(docker images --format '{{.Repository}}:{{.Tag}}' \
    | grep '^stockpilot-app:' | grep -v "^${IMAGE}$" || true)
  if [[ -n "$old_docker" ]]; then
    echo "$old_docker" | xargs docker rmi -f 2>/dev/null && info "Docker 旧镜像已删除" || true
  else
    info "Docker 无旧镜像"
  fi

  # microk8s containerd: 删除旧 tag
  local old_ctr
  old_ctr=$(/snap/bin/microk8s ctr images list -q \
    | grep 'stockpilot-app:' | grep -v "${IMAGE}" || true)
  if [[ -n "$old_ctr" ]]; then
    echo "$old_ctr" | xargs /snap/bin/microk8s ctr images rm 2>/dev/null && info "containerd 旧镜像已删除" || true
  else
    info "containerd 无旧镜像"
  fi
}

restart_all() {
  step "滚动重启所有 Deployment"
  for dep in stockpilot-app grafana tsdb; do
    if $KUBECTL get deployment/$dep -n $NS &>/dev/null; then
      $KUBECTL rollout restart deployment/$dep -n $NS
    fi
  done
  for dep in stockpilot-app grafana tsdb; do
    if $KUBECTL get deployment/$dep -n $NS &>/dev/null; then
      $KUBECTL rollout status deployment/$dep -n $NS --timeout=120s
    fi
  done
  ok "所有 Pod 已就绪"
}

apply_secrets() {
  step "更新 Secret"
  [[ -f "$SCRIPT_DIR/secrets.env" ]] || die "未找到 secrets.env"
  $KUBECTL create secret generic stockpilot-secrets \
    --from-env-file="$SCRIPT_DIR/secrets.env" \
    -n $NS --dry-run=client -o yaml | $KUBECTL apply -f -
  ok "Secret 已更新"
}

apply_configmaps() {
  step "更新 ConfigMap"
  $KUBECTL create configmap stockpilot-watchlist \
    --from-file=watchlist.yaml="$SCRIPT_DIR/config/watchlist.yaml" \
    -n $NS --dry-run=client -o yaml | $KUBECTL apply -f -

  $KUBECTL create configmap grafana-provisioning-datasources \
    --from-file="$SCRIPT_DIR/grafana/provisioning/datasources/" \
    -n $NS --dry-run=client -o yaml | $KUBECTL apply -f -

  $KUBECTL create configmap grafana-provisioning-dashboards \
    --from-file="$SCRIPT_DIR/grafana/provisioning/dashboards/dashboards.yaml" \
    -n $NS --dry-run=client -o yaml | $KUBECTL apply -f -

  $KUBECTL create configmap grafana-dashboards-json \
    --from-file="$SCRIPT_DIR/grafana/dashboards/" \
    -n $NS --dry-run=client -o yaml | $KUBECTL apply -f -
  ok "ConfigMap 已更新"
}

show_status() {
  echo ""
  echo -e "${C_BOLD}─── Pods ────────────────────────────────────────────${C_RESET}"
  $KUBECTL get pods -n $NS -o wide
  echo ""
  echo -e "${C_BOLD}─── 最近 Job 运行 ────────────────────────────────────${C_RESET}"
  curl -sf http://localhost:30810/api/v1/jobs 2>/dev/null | \
    python3 -c "
import sys,json
d=json.load(sys.stdin)
runs=d.get('recent_runs',[])[:8]
for r in runs:
    s = r.get('status','?')
    mark = '✓' if s=='ok' else '✗' if s=='failed' else '…'
    print(f\"  {mark} {r.get('job_name','?'):<22s} {s:<8s} {str(r.get('started_at',''))[:16]}  {str(r.get('detail',''))[:50]}\")
" 2>/dev/null || info "App 未响应 (可能正在启动)"
  echo ""
  echo -e "${C_BOLD}─── 集成状态 ─────────────────────────────────────────${C_RESET}"
  curl -sf http://localhost:30810/health 2>/dev/null | \
    python3 -c "
import sys,json
h=json.load(sys.stdin)
print(f\"  DB={'✓' if h.get('db') else '✗'}  Scheduler={'✓' if h.get('scheduler') else '✗'}\")
for k,v in h.get('integrations',{}).items():
    print(f\"  {k:<12s} {'✓ 已连接' if v else '✗ 未配置'}\")
" 2>/dev/null || true
}

# ─── 全量部署 ────────────────────────────────────────────────────────────────

full_deploy() {
  local fresh="${1:-}"
  echo -e "\n${C_BOLD}╔══════════════════════════════════════╗${C_RESET}"
  echo -e "${C_BOLD}║   StockPilot — 全量部署               ║${C_RESET}"
  echo -e "${C_BOLD}╚══════════════════════════════════════╝${C_RESET}"

  step "准备数据目录"
  mkdir -p /appHome/data/stockpilot/{tsdb,grafana,backups}
  chown -R 472:472 /appHome/data/stockpilot/grafana 2>/dev/null || true
  ok "目录就绪"

  build_image "$fresh"
  import_image

  step "创建命名空间"
  $KUBECTL apply -f "$SCRIPT_DIR/k8s/namespace.yaml"

  apply_secrets
  apply_configmaps

  step "应用 k8s 资源"
  $KUBECTL apply -f "$SCRIPT_DIR/k8s/persistent-volumes.yaml"
  $KUBECTL apply -f "$SCRIPT_DIR/k8s/tsdb.yaml"
  $KUBECTL apply -f "$SCRIPT_DIR/k8s/grafana.yaml"
  $KUBECTL apply -f "$SCRIPT_DIR/k8s/app.yaml"
  $KUBECTL apply -f "$SCRIPT_DIR/k8s/backup-cronjob.yaml"
  # 更新 app 镜像为本次构建的唯一 tag
  $KUBECTL set image deployment/stockpilot-app app="docker.io/library/${IMAGE}" -n $NS
  ok "资源已应用"

  step "等待所有服务就绪"
  $KUBECTL rollout status deployment/tsdb           -n $NS --timeout=300s
  $KUBECTL rollout status deployment/grafana        -n $NS --timeout=300s
  $KUBECTL rollout status deployment/stockpilot-app -n $NS --timeout=300s

  prune_images
  echo -e "\n${C_GREEN}${C_BOLD}✓ 部署完成${C_RESET}"
  show_status
}

# ─── 入口 ────────────────────────────────────────────────────────────────────

CMD="${1:-all}"
ARG2="${2:-}"

case "$CMD" in
  code)
    build_image "$ARG2"
    import_image
    restart_app
    prune_images
    echo ""
    show_status
    ;;
  secrets)
    apply_secrets
    restart_app
    ;;
  config)
    apply_configmaps
    restart_all
    ;;
  restart)
    restart_all
    ;;
  status)
    show_status
    ;;
  logs)
    target="${ARG2:-app}"
    case "$target" in
      app)     $KUBECTL logs -f -n $NS deployment/stockpilot-app --tail=50 ;;
      tsdb)    $KUBECTL logs -f -n $NS deployment/tsdb --tail=50 ;;
      grafana) $KUBECTL logs -f -n $NS deployment/grafana --tail=50 ;;
      *)       die "未知目标: $target (可选: app tsdb grafana)" ;;
    esac
    ;;
  all)
    full_deploy "$ARG2"
    ;;
  *)
    echo -e "用法: $0 [code|secrets|config|restart|status|logs|all] [--fresh]"
    exit 1
    ;;
esac
