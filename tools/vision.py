class CritiqueTool:
    name = "critique"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_critique(
            path=params.get("path", "last"),
            expected=params.get("expected"),
        )
