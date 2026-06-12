class DreamTool:
    name = "dream"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        return self.host.cmd_dream(
            description=params.get("description", ""),
            confirm=False,
            num=self.host._to_int(params.get("num"), 1),
            mode=params.get("mode", "combo"),
            max_tags=self.host._to_int(params.get("max_tags"), None),
            steps=self.host._to_int(params.get("steps"), 28),
            cfg_scale=self.host._to_float(params.get("cfg_scale"), 5),
            sampler=params.get("sampler", "Euler"),
            seed=self.host._to_int(params.get("seed"), -1),
            width=self.host._to_int(params.get("width"), 1024),
            height=self.host._to_int(params.get("height"), 1536),
            negative_prompt=params.get("negative_prompt"),
            min_artists=self.host._to_int(params.get("min_artists"), 3),
            max_artists=self.host._to_int(params.get("max_artists"), 5),
            loras=params.get("loras"),
        )


class GenerationInfoTool:
    name = "generation_info"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        return self.host.cmd_generation_info(params.get("detail", "prompt"))
