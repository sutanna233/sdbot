from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


ACCENT = "bold #D88CFF"
ACCENT_SOFT = "#D8B4FE"
CYAN = "bold #7DD3FC"
WHITE = "#F3F4F6"
MUTED = "#9CA3AF"
OK = "bold #86EFAC"
WARN = "bold #FDE68A"
ERR = "bold #FCA5A5"


class TUIController:
    """Small, stable themed CLI renderer.

    This intentionally avoids alternate-screen mode and live redraws so command
    output stays readable in Windows Terminal, PowerShell, and plain terminals.
    """

    def __init__(self):
        self.console = Console(highlight=False)
        self.header = {"model": "-", "session": "-"}
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        self._print_banner()

    def stop(self):
        # Keep terminal history visible; no alternate-screen cleanup needed.
        pass

    def _print_banner(self):
        model = self.header.get("model") or "-"
        session = self.header.get("session") or "-"
        body = Group(
            Text("苏丹娜 SD Assistant", style=ACCENT),
            Text(f"Session: {session}    Model: {model}", style=WHITE),
            Text("/help 查看命令    /exit 退出", style=MUTED),
        )
        self.console.print()
        self.console.print(Panel(body, border_style=ACCENT_SOFT, padding=(1, 2)))

    def chat(self, text: str):
        self.console.print()
        self.console.print(Text("苏丹娜", style=ACCENT))
        for line in str(text).splitlines() or [""]:
            self.console.print(Text(f"  {line}", style=WHITE))

    def system(self, text: str, msg_type: str = "info"):
        labels = {
            "ok": ("OK", OK),
            "err": ("ERR", ERR),
            "warn": ("WARN", WARN),
            "info": ("INFO", CYAN),
        }
        label, style = labels.get(msg_type, labels["info"])
        lines = str(text).splitlines() or [""]
        self.console.print(Text.assemble((f"[{label}] ", style), (lines[0], WHITE)))
        for line in lines[1:]:
            self.console.print(Text(f"       {line}", style=WHITE))

    def ask(self, prompt: str = "你") -> str:
        label = "你" if prompt in ("input", "你", "") else prompt
        return self.console.input(f"[bold #D88CFF]{label} > [/]").strip()

    def confirm(self, steps: list[tuple], phrase: str = "", risk: str = "info") -> str:
        title = "危险操作" if risk == "high" else "行动计划"
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style=MUTED)
        table.add_column(style=CYAN)
        table.add_column(style=WHITE)
        table.add_column(style=MUTED)

        risk_labels = {
            "destructive": ("删除", ERR),
            "write": ("写入", WARN),
            "read": ("读取", CYAN),
            "low": ("低风险", OK),
            "info": ("信息", CYAN),
        }
        for idx, (action, desc, rl) in enumerate(steps, 1):
            risk_label, risk_style = risk_labels.get(rl, risk_labels["info"])
            table.add_row(
                f"{idx}.",
                Text(f"[{risk_label}]", style=risk_style),
                action,
                desc,
            )

        self.console.print()
        self.console.print(Text(title, style=ACCENT))
        self.console.print(table)
        self.console.print()

        if phrase:
            result = self.console.input(
                f"[bold #D88CFF]输入“{phrase}”执行，或 n 取消 > [/]")
            return "yes" if result.strip() == phrase else "no"

        result = self.console.input(
            "[bold #D88CFF]确认执行？ [Enter=执行 / n=取消 / e=修改] > [/]")
        result = result.strip().lower()
        if result in ("n", "no"):
            return "no"
        if result in ("e", "edit"):
            return "edit"
        return "yes"

    def table(self, title: str, columns: list[str], rows: list[list]):
        tbl = Table(title=title, border_style=ACCENT_SOFT, header_style=ACCENT,
                    title_style=ACCENT, padding=(0, 1))
        for col in columns:
            tbl.add_column(col)
        for row in rows:
            tbl.add_row(*[str(c) for c in row])
        self.console.print(tbl)

    def update_header(self, **kwargs):
        self.header.update(kwargs)

    def update_status(self, **kwargs):
        pass
