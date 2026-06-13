import json
import os
import time
from datetime import datetime


def generate_sid():
    import hashlib

    return "sess_" + hashlib.sha1(os.urandom(16)).hexdigest()[:12]


class SessionStore:
    def __init__(self, path, lock):
        self.path = path
        self.lock = lock
        self.data = self.load()

    def load(self):
        if not self.path.exists():
            data = {"current": None, "sessions": {}}
            self.save(data)
            return data
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            data = {"current": None, "sessions": {}}
            self.save(data)
            return data

    def save(self, data=None):
        if data is None:
            data = self.data
        with self.lock:
            exc = None
            tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
            for _ in range(20):
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, self.path)
                    return
                except PermissionError as e:
                    exc = e
                    time.sleep(0.25)
                except OSError as e:
                    exc = e
                    time.sleep(0.25)
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            print(f"  [WARN] sessions.json 暂时无法保存: {exc}")

    def create(self, name=None):
        sid = generate_sid()
        if not name:
            name = f"新对话 {len(self.data['sessions']) + 1}"
        self.data["sessions"][sid] = {
            "name": name,
            "created_at": datetime.now().isoformat(),
            "conversation": [],
        }
        self.data["current"] = sid
        self.save()
        return sid

    def delete(self, sid):
        if sid not in self.data["sessions"]:
            return
        del self.data["sessions"][sid]
        if self.data["current"] == sid:
            remaining = list(self.data["sessions"].keys())
            self.data["current"] = remaining[-1] if remaining else None
        self.save()

    def switch(self, target):
        if target in self.data["sessions"]:
            self.data["current"] = target
            self.save()
            return True
        if str(target).isdigit():
            idx = int(target) - 1
            keys = list(self.data["sessions"].keys())
            if 0 <= idx < len(keys):
                self.data["current"] = keys[idx]
                self.save()
                return True
        return False

    def rename(self, sid, name):
        if sid in self.data["sessions"]:
            self.data["sessions"][sid]["name"] = name
            self.save()

    def list_text(self):
        lines = []
        keys = list(self.data["sessions"].keys())
        for i, sid in enumerate(keys, 1):
            session = self.data["sessions"][sid]
            marker = " <- 当前" if sid == self.data["current"] else ""
            conv_len = len(session.get("conversation", [])) // 2
            lines.append(f"  {i}. [{sid[:8]} {session['name']}] {conv_len}条对话{marker}")
        return "\n".join(lines)

    def current(self):
        sid = self.data["current"]
        if not sid or sid not in self.data["sessions"]:
            sid = self.create()
        return sid, self.data["sessions"][sid]
