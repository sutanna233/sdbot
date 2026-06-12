class ChainRunner:
    def __init__(self, host):
        self.host = host

    def run(self, chain, on_step=None, on_success=None, on_error=None, should_stop=None):
        results = []
        total = len(chain or [])
        for index, step in enumerate(chain or [], 1):
            if on_step:
                on_step(index, total, step)
            action_result = self.host._execute_action(step["action"], step.get("params", {}))
            self.host._inject_action_result(step, action_result)
            if step.get("action") == "dream" and isinstance(action_result, dict):
                self._save_generation(step, action_result)
            results.append(action_result)
            if not action_result.get("ok", True):
                if on_error:
                    on_error(step, action_result)
                break
            if on_success:
                on_success(step, action_result)
            if should_stop and should_stop(step, action_result, index, total):
                break
        return results

    def _save_generation(self, step, action_result):
        try:
            _, session = self.host._session_current()
            gen = {
                "description": step.get("params", {}).get("description", ""),
                "prompt": action_result.get("prompt", ""),
                "params": step.get("params", {}),
                "run": action_result.get("run"),
            }
            self.host.agent.state.save_generation(session, gen)
            self.host._save_sessions()
        except Exception:
            pass
