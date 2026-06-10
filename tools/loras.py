class LorasTool:
    name = "loras"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_loras(
            search=params.get("search"),
            action=params.get("lora_action"),
            name=params.get("name"),
            trigger=params.get("trigger"),
        )
