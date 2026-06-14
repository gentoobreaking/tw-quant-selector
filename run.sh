#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

info()  { echo -e "\033[36mℹ\033[0m $*"; }
ok()    { echo -e "\033[32m✓\033[0m $*"; }
warn()  { echo -e "\033[33m⚠\033[0m $*"; }
err()   { echo -e "\033[31m✗\033[0m $*"; }
header(){ echo -e "\n\033[1;34m══════════════════════════════════════════\033[0m"; echo -e "  \033[1;37m$*\033[0m"; echo -e "\033[1;34m══════════════════════════════════════════\033[0m"; }

cd "$PROJECT_DIR"

up_all() {
  header "Docker Compose Up (all services)"
  docker compose up -d
  ok "http://localhost:8000  |  Frontend http://localhost:5173"
}

up_scheduler() {
  header "Docker Compose Up (scheduler)"
  docker compose --profile scheduler up -d scheduler
  ok "Scheduler started"
}

down() {
  header "Docker Compose Down"
  docker compose down
  ok "All containers stopped"
}

build() {
  header "Docker Build"
  docker compose build
  ok "Images built"
}

logs_app() {
  docker compose logs -f app
}

logs_scheduler() {
  docker compose logs -f scheduler
}

logs_frontend() {
  docker compose logs -f frontend
}

restart_app() {
  header "Restart App"
  docker compose restart app
  ok "App restarted"
}

status() {
  header "Container Status"
  docker compose ps
}

case "${1:-menu}" in
  up|start)        up_all ;;
  scheduler)       up_scheduler ;;
  down|stop)       down ;;
  build)           build ;;
  logs)            shift; docker compose logs -f "$@" ;;
  logs-app)        logs_app ;;
  logs-scheduler)  logs_scheduler ;;
  logs-frontend)   logs_frontend ;;
  restart)         restart_app ;;
  ps|status)       status ;;
  menu)
    echo "用法: $0 {up|down|build|scheduler|logs|logs-app|logs-scheduler|logs-frontend|restart|status}"
    echo ""
    echo "常用指令:"
    echo "  $0 up              啟動所有服務 (app + frontend + postgres)"
    echo "  $0 scheduler       啟動排程器 (docker compose --profile scheduler up -d)"
    echo "  $0 logs-scheduler  查看排程器 log (docker compose logs -f)"
    echo "  $0 logs-app        查看 API log"
    echo "  $0 down            停止所有容器"
    echo "  $0 build           重新 build image"
    echo "  $0 restart         重啟 app 容器"
    echo "  $0 status          查看容器狀態"
    ;;
  *) echo "用法: $0 {up|down|build|scheduler|logs|logs-app|logs-scheduler|logs-frontend|restart|status}"; exit 1 ;;
esac
