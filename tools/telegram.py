class TelegramTool:
    name = "telegram"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_telegram(
            action=params.get("subaction", "start"),
            token=params.get("token"),
        )
