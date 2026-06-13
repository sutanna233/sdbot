import sys
import time

import yaml


class ConfigStore:
    def __init__(self, path):
        self.path = path
        self.data = self.load()

    def load(self):
        if not self.path.exists():
            print(f"Error: Config file not found: {self.path}")
            sys.exit(1)
        with open(self.path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def save(self, data=None):
        if data is not None:
            self.data = data
        exc = None
        for _ in range(3):
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    yaml.dump(self.data, f, allow_unicode=True, default_flow_style=False)
                return
            except PermissionError as e:
                exc = e
                time.sleep(0.2)
        raise exc or RuntimeError("保存 config 失败")
