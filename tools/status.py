class StatusTool:
    name = "status"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        self.host.cmd_status()


class HistoryTool:
    name = "history"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_history(
            last=int(params.get("last", 20)),
            search=params.get("search"),
        )


class ArtistsTool:
    name = "artists"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_artists(
            search=params.get("search"),
            count_only=bool(params.get("count_only", False)),
        )


class GalleryTool:
    name = "gallery"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_gallery(
            run_name=params.get("run"),
            list_only=bool(params.get("list_only", False)),
            regenerate=bool(params.get("regenerate", False)),
            open_after=bool(params.get("open_after", True)),
        )


class WebUITool:
    name = "webui"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_webui(
            host=params.get("host", "127.0.0.1"),
            port=int(params.get("port", 7861)),
            background=bool(params.get("background", True)),
        )
