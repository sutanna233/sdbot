import sys
from io import StringIO


class ToolExecutor:
    def __init__(self, host, registry=None):
        self.host = host
        self.registry = registry

    def execute(self, action, params=None):
        params = params or {}
        return self._capture(action, params, lambda: self.execute_raw(action, params))

    def execute_raw(self, action, params=None):
        params = params or {}
        if self.registry:
            handler = self.registry.get(action)
            if handler:
                return handler(params)
        self.host._execute_action_raw(action, params)
        return None

    def execute_chain(self, chain):
        results = []
        for step in chain:
            result = self.execute(step.get("action"), step.get("params", {}))
            results.append(result)
            if not result.get("ok", True):
                break
        return results

    def _capture(self, action, params, fn):
        buf = StringIO()
        result = {
            "action": action,
            "ok": True,
            "summary": "",
            "output": [],
            "result": {},
            "error": None,
        }

        class TeeStdout:
            def __init__(self, *targets):
                self.targets = targets
            def write(self, s):
                for t in self.targets:
                    t.write(s)
            def flush(self):
                for t in self.targets:
                    try:
                        t.flush()
                    except Exception:
                        pass

        orig_stdout = sys.stdout
        orig_stderr = sys.stderr
        try:
            sys.stdout = TeeStdout(orig_stdout, buf)
            sys.stderr = TeeStdout(orig_stderr, buf)
            raw = fn()
            if isinstance(raw, dict):
                result["result"] = raw
        except Exception as e:
            result["ok"] = False
            result["error"] = str(e)
        finally:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr
        output = buf.getvalue()
        lines = [ln for ln in output.splitlines() if ln]
        result["output"] = lines
        result["summary"] = lines[0] if lines else ""
        return result
