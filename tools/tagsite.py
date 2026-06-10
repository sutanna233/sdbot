class TagSiteTool:
    name = "tagsite"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        names = params.get("names", [])
        if isinstance(names, str):
            names = [names]
        self.host.cmd_tagsite(*names)
