class FileReadTool:
    name = "file_read"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_file_read(
            path=params.get("path", ""),
            start_line=params.get("start_line"),
            max_lines=params.get("max_lines"),
        )


class FileWriteTool:
    name = "file_write"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_file_write(
            path=params.get("path", ""),
            content=params.get("content", ""),
            confirm_yes=params.get("confirm_yes", False),
        )


class FileListTool:
    name = "file_list"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_file_list(
            path=params.get("path", "."),
            pattern=params.get("pattern"),
            max_count=params.get("max_count", 100),
        )


class FileDeleteTool:
    name = "file_delete"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_file_delete(
            path=params.get("path", ""),
            confirm_yes=params.get("confirm_yes", False),
        )


class FileFindTool:
    name = "file_find"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_file_find(
            pattern=params.get("pattern", "*"),
            contains=params.get("contains"),
            max_count=params.get("max_count", 20),
        )
