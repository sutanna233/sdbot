class ModelsTool:
    name = "models"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_models(
            action=params.get("action", "list"),
            role=params.get("role"),
            model_key=params.get("model"),
        )


class AddProviderTool:
    name = "add_provider"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        self.host.cmd_add_provider(
            base_url=params.get("base_url", ""),
            api_key=params.get("api_key", ""),
            provider_name=params.get("provider_name"),
            capabilities=params.get("capabilities"),
            switch_role=params.get("switch_role"),
        )
