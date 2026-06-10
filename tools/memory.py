class MemorySetTool:
    name = "memory_set"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_memory_set(params.get("key"), params.get("value"))


class MemoryGetTool:
    name = "memory_get"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_memory_get(params.get("key"))


class MemoryForgetTool:
    name = "memory_forget"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_memory_forget(params.get("key"), params.get("confirm"))


class MemoryListTool:
    name = "memory_list"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        self.host.cmd_memory_list()
