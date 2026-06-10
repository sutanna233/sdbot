class WebFetchTool:
    name = "web_fetch"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_web_fetch(
            url=params.get("url", ""),
            format=params.get("format", "text"),
            max_length=params.get("max_length"),
        )
