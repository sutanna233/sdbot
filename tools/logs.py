import subprocess
from pathlib import Path
from logging_setup import get_logger

logger = get_logger("tools.logs")


class LogsTool:
    name = "logs"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        action = params.get("action", "tail")
        level = params.get("level", "")
        lines = int(params.get("lines", 50))
        keyword = (params.get("keyword") or "").strip()

        log_dir = getattr(self.host, "_log_dir", Path(__file__).parent.parent / "logs")
        log_file = log_dir / "sdbot.log"

        if not log_file.exists():
            print(f"日志文件不存在: {log_file}")
            print("服务启动后会自动生成日志。")
            return {"ok": False, "text": "log file not found"}

        if action == "path":
            print(f"日志路径: {log_file.resolve()}")
            print(f"日志目录: {log_dir.resolve()}")
            return {"ok": True, "text": str(log_file)}

        # Default: tail the log file
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
        except Exception as e:
            print(f"❌ 读取日志失败: {e}")
            return {"ok": False, "text": str(e)}

        total = len(all_lines)

        # Filter by level
        if level:
            level_upper = level.upper()
            filtered = [ln for ln in all_lines if f"[{level_upper}]" in ln]
        else:
            filtered = all_lines

        # Filter by keyword
        if keyword:
            filtered = [ln for ln in filtered if keyword.lower() in ln.lower()]

        # Take recent lines
        recent = filtered[-lines:] if filtered else []

        if not recent:
            if level or keyword:
                print(f"没有匹配的日志条目（总 {total} 行）。")
            else:
                print(f"日志为空（总 {total} 行）。")
            return {"ok": True, "text": "no matching entries"}

        level_label = f" [{level}]" if level else ""
        keyword_label = f" 关键词={keyword}" if keyword else ""
        print(f"📋 日志 ({log_file.name}) — 最近 {len(recent)} 条{level_label}{keyword_label} (共 {total} 行)")
        print()
        for line in recent:
            print(line.rstrip())

        return {"ok": True, "text": f"shown {len(recent)} lines"}
