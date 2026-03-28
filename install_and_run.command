#!/bin/bash
# HSBC Points Tracker - 安装定时任务并立即运行一次
# 双击此文件即可执行

SCRIPT_DIR="/Users/bruce/claudeSpace/hsbcelite"
PLIST_SRC="$SCRIPT_DIR/com.hsbc.points-tracker.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.hsbc.points-tracker.plist"

cd "$SCRIPT_DIR"

echo "================================================"
echo "  HSBC Points Tracker - 安装定时任务"
echo "================================================"
echo ""

# === 检查并修复虚拟环境 ===
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
if ! "$VENV_PYTHON" --version &>/dev/null; then
    echo "[0/4] 虚拟环境失效，正在重建..."

    # 找可用的 python3
    PYTHON3=$(which python3 2>/dev/null || echo "")
    if [ -z "$PYTHON3" ] || ! "$PYTHON3" --version &>/dev/null; then
        echo "❌ 找不到可用的 python3，请先安装 Python 3"
        echo "按任意键关闭..."; read -n 1; exit 1
    fi
    echo "  使用 Python: $PYTHON3 ($("$PYTHON3" --version))"

    # 删除旧虚拟环境
    rm -rf "$SCRIPT_DIR/.venv"

    # 重建
    echo "  创建虚拟环境..."
    "$PYTHON3" -m venv "$SCRIPT_DIR/.venv"

    echo "  安装依赖（可能需要几分钟）..."
    "$SCRIPT_DIR/.venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

    echo "  安装 Playwright Chromium 浏览器..."
    "$SCRIPT_DIR/.venv/bin/playwright" install chromium

    echo "  ✅ 虚拟环境重建完成！"
    echo ""
fi

# === 安装 LaunchAgent ===
echo "[1/4] 检查旧定时任务..."
if launchctl list | grep -q "com.hsbc.points-tracker"; then
    echo "  卸载旧定时任务..."
    launchctl unload "$PLIST_DST" 2>/dev/null
else
    echo "  无旧定时任务，跳过卸载"
fi

echo "[2/4] 复制 plist 到 ~/Library/LaunchAgents/ ..."
cp "$PLIST_SRC" "$PLIST_DST"

echo "[3/4] 加载定时任务（每天 7:00 AM 运行）..."
launchctl load "$PLIST_DST"

echo ""
echo "✅ 定时任务已安装！每天早上 7:00 AM 自动运行。"
echo ""

# === 手动运行一次 ===
echo "[4/4] 立即手动运行一次脚本..."
echo "--------------------------------------------"
"$SCRIPT_DIR/.venv/bin/python" -m src.main
STATUS=$?
echo "--------------------------------------------"
echo ""

if [ $STATUS -eq 0 ]; then
    echo "✅ 脚本执行成功！"
else
    echo "⚠ 脚本运行结束（退出码: $STATUS）"
    echo "  如有问题，请查看日志: $SCRIPT_DIR/hsbc_points.log"
fi

echo ""
echo "按任意键关闭..."
read -n 1
