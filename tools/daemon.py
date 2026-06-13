import os
import subprocess
from pathlib import Path


SERVICE_NAME = "sdbot-daemon"
PROJECT_DIR = Path(__file__).parent.parent.resolve()
INSTALL_SCRIPT = PROJECT_DIR / "deploy" / "install-service.sh"


def _run(cmd, timeout=30):
    """Run a shell command and return structured result."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": r.returncode == 0,
            "code": r.returncode,
            "out": r.stdout.strip(),
            "err": r.stderr.strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "code": -1, "out": "", "err": "命令未找到"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "out": "", "err": "命令超时"}


def _check_systemd():
    """Check if systemd is available."""
    r = _run(["which", "systemctl"])
    return r["ok"]


def _service_cmd(action):
    """Run systemctl action on the sdbot-daemon service."""
    return _run(["systemctl", action, SERVICE_NAME])


def _result(text, ok=True):
    """Return a result dict. The caller should also have print()ed for the LLM."""
    return {"ok": ok, "text": text}


def cmd_status():
    """Check daemon service status."""
    if not _check_systemd():
        print("当前系统没有 systemd，无法管理后台服务。")
        print("请手动运行: python sdbot.py daemon")
        return _result("no systemd", ok=False)

    r = _service_cmd("is-active")
    active = r["ok"]
    enabled_r = _run(["systemctl", "is-enabled", SERVICE_NAME])
    enabled = enabled_r["ok"]

    lines = []
    if active:
        lines.append("✅ sdbot-daemon 运行中")
    else:
        lines.append("❌ sdbot-daemon 未运行")

    if enabled:
        lines.append("✅ 已设为开机自启")
    else:
        lines.append("❌ 未设开机自启")

    detail = _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"], timeout=10)
    if detail["ok"]:
        for line in detail["out"].split("\n"):
            if any(kw in line for kw in ("Active:", "Main PID:", "Memory:", "Process:")):
                lines.append(line.strip())

    for line in lines:
        print(line)

    return _result("\n".join(lines))


def cmd_start():
    if not _check_systemd():
        print("当前系统没有 systemd。")
        return _result("no systemd", ok=False)
    r = _service_cmd("start")
    if r["ok"]:
        print("✅ sdbot-daemon 已启动")
        print("访问 http://127.0.0.1:7861 使用 WebUI")
        return _result("started")
    else:
        print(f"❌ 启动失败:")
        print(r["err"])
        return _result(r["err"], ok=False)


def cmd_stop():
    if not _check_systemd():
        print("当前系统没有 systemd。")
        return _result("no systemd", ok=False)
    r = _service_cmd("stop")
    if r["ok"]:
        print("✅ sdbot-daemon 已停止")
        return _result("stopped")
    else:
        print(f"❌ 停止失败:")
        print(r["err"])
        return _result(r["err"], ok=False)


def cmd_restart():
    if not _check_systemd():
        print("当前系统没有 systemd。")
        return _result("no systemd", ok=False)
    r = _service_cmd("restart")
    if r["ok"]:
        print("✅ sdbot-daemon 已重启")
        return _result("restarted")
    else:
        print(f"❌ 重启失败:")
        print(r["err"])
        return _result(r["err"], ok=False)


def cmd_enable():
    """Install + enable the daemon service."""
    if not _check_systemd():
        print("当前系统没有 systemd。")
        return _result("no systemd", ok=False)

    if not INSTALL_SCRIPT.exists():
        print(f"❌ 安装脚本不存在: {INSTALL_SCRIPT}")
        print("请确认 deploy/ 目录完整。")
        return _result("install script not found", ok=False)

    print("正在安装 sdbot-daemon 系统服务...")
    print(f"项目路径: {PROJECT_DIR}")

    r = _run(["sudo", str(INSTALL_SCRIPT)], timeout=60)
    if r["ok"]:
        print("✅ 已安装并设为开机自启！")
        print("  WebUI: http://127.0.0.1:7861")
        print("  日志: journalctl -u sdbot-daemon -f")
        return _result("enabled")
    else:
        err = r["err"] or r["out"] or ""
        print(f"❌ 安装失败: {err[:300]}")
        if "a terminal" in err.lower() or "tty" in err.lower() or "password" in err.lower():
            print()
            print("需要 sudo 权限，但在 shell 里无法输入密码。")
            print("请手动在终端执行:")
            print(f"  cd {PROJECT_DIR} && sudo bash deploy/install-service.sh")
        return _result(err, ok=False)


def cmd_disable():
    """Disable and stop the daemon."""
    if not _check_systemd():
        print("当前系统没有 systemd。")
        return _result("no systemd", ok=False)
    _service_cmd("stop")
    r = _run(["systemctl", "disable", SERVICE_NAME])
    if r["ok"]:
        print("✅ 已关闭开机自启")
        return _result("disabled")
    else:
        print(f"❌ 操作失败:")
        print(r["err"])
        return _result(r["err"], ok=False)


class DaemonTool:
    name = "daemon"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        action = params.get("action", "")

        if not action:
            print("请指定操作参数:\n")
            print("  action=enable   → 安装并设为开机自启")
            print("  action=status   → 查看运行状态")
            print("  action=start    → 启动服务")
            print("  action=stop     → 停止服务")
            print("  action=restart  → 重启服务")
            print("  action=disable  → 关闭开机自启")
            print()
            print('例如: {"action": "daemon", "params": {"action": "enable"}}')
            return _result("missing required param: action", ok=False)

        actions = {
            "status": cmd_status,
            "start": cmd_start,
            "stop": cmd_stop,
            "restart": cmd_restart,
            "enable": cmd_enable,
            "disable": cmd_disable,
        }

        handler = actions.get(action)
        if not handler:
            print(f"未知操作: {action}")
            print(f"支持: status / start / stop / restart / enable(安装+开机自启) / disable(关闭自启)")
            return _result(f"unknown action: {action}", ok=False)

        return handler()
