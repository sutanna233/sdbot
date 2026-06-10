class RunTool:
    name = "run"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        config = self.host.config
        old = {
            "mode": self.host.mode,
            "width": config["generation"]["width"],
            "height": config["generation"]["height"],
            "steps": config["generation"]["steps"],
            "cfg": config["generation"]["cfg_scale"],
            "seed": config["generation"]["seed"],
            "sampler": config["generation"]["sampler"],
        }
        try:
            if params.get("mode"):
                self.host.mode = params["mode"]
            if params.get("num"):
                self.host.cli_args["num"] = int(params["num"])
            if params.get("width"):
                config["generation"]["width"] = int(params["width"])
            if params.get("height"):
                config["generation"]["height"] = int(params["height"])
            if params.get("steps"):
                config["generation"]["steps"] = int(params["steps"])
            if params.get("cfg_scale"):
                config["generation"]["cfg_scale"] = float(params["cfg_scale"])
            if params.get("seed") is not None:
                config["generation"]["seed"] = int(params["seed"])
            if params.get("sampler"):
                config["generation"]["sampler"] = params["sampler"]
            self.host.run(loras=self.host._resolve_loras(params.get("loras")))
        finally:
            self.host.mode = old["mode"]
            config["generation"]["width"] = old["width"]
            config["generation"]["height"] = old["height"]
            config["generation"]["steps"] = old["steps"]
            config["generation"]["cfg_scale"] = old["cfg"]
            config["generation"]["seed"] = old["seed"]
            config["generation"]["sampler"] = old["sampler"]
