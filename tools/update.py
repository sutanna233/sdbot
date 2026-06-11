class UpdateTool:
    name = "update"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        return self.host.cmd_update(
            apply=bool(params.get("apply", False)),
            deps=bool(params.get("deps", False)),
            remote=params.get("remote"),
            branch=params.get("branch"),
        )
