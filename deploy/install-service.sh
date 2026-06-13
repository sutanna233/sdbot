#!/bin/bash
set -euo pipefail

# ─────────────────────────────────────────────
#  sdbot-daemon systemd 服务安装脚本
#  用法: sudo bash install-service.sh
# ─────────────────────────────────────────────

SERVICE_NAME="sdbot-daemon"
SERVICE_FILE="$(dirname "$0")/sdbot-daemon.service"

# 检查是否 root
if [ "$EUID" -ne 0 ]; then
    echo "  [ERR] 请用 sudo 运行: sudo bash $0"
    exit 1
fi

# 获取项目实际路径（解析 symlink）
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
VENV_DIR="$PROJECT_DIR/.venv"
RUN_USER="${SUDO_USER:-$(whoami)}"

echo "  ──────────────────────────────────────"
echo "  安装 sdbot-daemon 系统服务"
echo "  项目路径: $PROJECT_DIR"
echo "  运行用户: $RUN_USER"
echo "  ──────────────────────────────────────"

# 检查是否有虚拟环境
PYTHON_BIN="$PROJECT_DIR/sdbot.py"
if [ -f "$VENV_DIR/bin/python" ]; then
    PYTHON_BIN="$VENV_DIR/bin/python"
    echo "  [OK] 使用虚拟环境: $VENV_DIR"
else
    echo "  [..] 未找到虚拟环境，使用系统 python3"
    PYTHON_BIN="$(which python3)"
    echo "  [OK] 使用: $PYTHON_BIN"
fi

# 写入服务文件
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=sdbot - SD WebUI Agent Daemon
Documentation=https://github.com/sutanna233/sdbot
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_BIN $PROJECT_DIR/sdbot.py daemon --host 127.0.0.1 --port 7861
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=5
UMask=0022
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

echo "  [OK] 服务文件已写入 /etc/systemd/system/$SERVICE_NAME.service"

# 重载、启用、启动
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME

echo "  [OK] 服务已启动并设为开机自启"
echo ""
echo "  ─── 常用命令 ─────────────────────────"
echo "  查看状态:  systemctl status $SERVICE_NAME"
echo "  查看日志:  journalctl -u $SERVICE_NAME -f -n 50"
echo "  重启服务:  systemctl restart $SERVICE_NAME"
echo "  停止服务:  systemctl stop $SERVICE_NAME"
echo "  ──────────────────────────────────────"
