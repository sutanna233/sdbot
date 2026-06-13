class AgentHost:
    """Narrow adapter for AgentPipeline access to the application host."""

    def __init__(self, app):
        self.app = app

    def __getattr__(self, name):
        return getattr(self.app, name)
