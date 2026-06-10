class ConfigTool:
    name = "config"

    def __init__(self, host, action):
        self.host = host
        self.action = action

    def __call__(self, params):
        params = params or {}
        self.host.cmd_config(self.action, params.get("key"), params.get("value"))


class ClearTool:
    name = "clear"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_clear(params.get("target", "history"))


class LLMTool:
    name = "llm"

    def __init__(self, host, action):
        self.host = host
        self.action = action

    def __call__(self, params):
        params = params or {}
        self.host.cmd_llm(self.action, params.get("key"), params.get("value"))
