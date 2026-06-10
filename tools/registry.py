class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self._schemas = {}

    def register(self, name, handler, schema=None):
        self._tools[name] = handler
        if schema is not None:
            self._schemas[name] = schema

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return sorted(self._tools.keys())

    def schema(self, name):
        return self._schemas.get(name)

    def schemas(self):
        return dict(self._schemas)
