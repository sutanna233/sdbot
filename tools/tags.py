class TagsTool:
    name = "tags"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_tags(params.get("keyword", ""), params.get("type"))
