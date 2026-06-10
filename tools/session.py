class SessionNewTool:
    name = "session_new"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host._session_create(params.get("name"))
        _, session = self.host._session_current()
        print(f"  [OK] 已切换到新对话: {session['name']}")


class SessionListTool:
    name = "session_list"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        print(self.host._session_list_text())


class SessionSwitchTool:
    name = "session_switch"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        if self.host._session_switch(params.get("target", "")):
            _, session = self.host._session_current()
            print(f"  [OK] 已切换到: {session['name']}")
        else:
            print("  [ERR] 未找到该对话")


class SessionRenameTool:
    name = "session_rename"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        sid, _ = self.host._session_current()
        self.host._session_rename(sid, params.get("name", ""))
        print("  [OK] 已重命名")
