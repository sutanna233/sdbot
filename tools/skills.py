class SkillListTool:
    name = "skill_list"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        self.host.cmd_skill_list()


class SkillLoadTool:
    name = "skill_load"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_skill_load(
            name=params.get("name", ""),
            params=params.get("params"),
        )
        if not getattr(self.host, "_in_react_loop", False):
            self.host._consume_pending_skill()


class SkillCreateTool:
    name = "skill_create"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_skill_create(
            name=params.get("name", ""),
            description=params.get("description", ""),
            triggers=params.get("triggers"),
            chain_template=params.get("chain_template"),
            body=params.get("body", ""),
        )
