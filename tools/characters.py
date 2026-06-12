import json
import re
from datetime import datetime


ALIAS_FILE = "character_aliases.json"


def _now():
    return datetime.now().isoformat(timespec="seconds")


class CharacterResolver:
    def __init__(self, host):
        self.host = host
        self.path = host.script_dir / ALIAS_FILE
        self.aliases = self._load_aliases()

    def resolve(self, request="", characters=None, works=None, tag_hints=None):
        request = str(request or "").strip()
        characters = self._as_list(characters)
        works = self._as_list(works)
        tag_hints = self._as_list(tag_hints)
        inferred_chars, inferred_works = self._infer_from_request(request)
        characters = characters or inferred_chars
        works = works or inferred_works

        resolved = []
        candidates = []
        unresolved = []
        for character in characters:
            item = self._resolve_one(character, works, tag_hints)
            if item.get("status") == "resolved":
                resolved.append(item)
            else:
                unresolved.append(character)
                candidates.extend(item.get("candidates") or [])

        status = "resolved" if resolved and not unresolved else "ambiguous" if candidates else "unresolved"
        return {
            "request": request,
            "characters": characters,
            "works": works,
            "status": status,
            "resolved": resolved,
            "candidates": candidates[:8],
            "unresolved": unresolved,
            "tags": self._merge_tags(resolved),
        }

    def confirm(self, alias, tag, work=None):
        alias = str(alias or "").strip()
        tag = str(tag or "").strip()
        work = str(work or "").strip()
        if not alias or not tag:
            return {"ok": False, "error": "alias and tag are required"}
        data = self._cache_lookup(tag)
        if not data:
            data = self.host.tag_site.search_character(tag)
        if not data:
            return {"ok": False, "error": f"tag not found: {tag}"}
        record = {
            "tag": tag,
            "work": work,
            "source": "user_confirmed",
            "confirmed_at": _now(),
        }
        self.aliases[self._normalize(alias)] = record
        if work:
            self.aliases[self._alias_key(alias, work)] = record
        self._save_aliases()
        return {"ok": True, "alias": alias, "tag": tag, "work": work, "tags": data.get("tags", [])}

    def _resolve_one(self, character, works, tag_hints):
        alias = self._alias_record(character, works)
        if alias:
            data = self._cache_lookup(alias["tag"]) or self.host.tag_site.search_character(alias["tag"])
            return self._resolved(character, alias["tag"], data, "alias", "high", alias.get("work"))

        if self._looks_like_tag(character):
            data = self._cache_lookup(character)
            if data:
                return self._resolved(character, character, data, "exact_input", "high", self._matched_work(data, works))

        candidates = self._cache_candidates(character, works, tag_hints)
        return {"status": "ambiguous" if candidates else "unresolved", "input": character, "candidates": candidates}

    def _resolved(self, character, tag, data, source, confidence, work=None):
        data = data or {}
        tags = data.get("tags") or []
        return {
            "status": "resolved",
            "input": character,
            "tag": tag,
            "name": data.get("name") or tag,
            "work": work,
            "source": source,
            "confidence": confidence,
            "tags": tags,
            "tag_count": len(tags),
        }

    def _cache_candidates(self, character, works, tag_hints):
        cache = getattr(self.host.tag_site, "_cache", {}) or {}
        wanted = {self._normalize(x) for x in [character] + tag_hints if x}
        work_keys = {self._normalize(w) for w in works}
        candidates = []
        for key, item in cache.items():
            data = (item or {}).get("data") or {}
            tags = data.get("tags") or []
            names = {self._normalize(key), self._normalize(data.get("name", ""))}
            names |= {self._normalize(t) for t in tags[:3]}
            exact = bool(wanted & names)
            work_match = self._tags_match_work(tags, work_keys)
            hint_match = self._normalize(key) in {self._normalize(h) for h in tag_hints}
            if not exact and not hint_match:
                continue
            score = 0.7 + (0.2 if work_match else 0) + (0.1 if hint_match else 0)
            candidates.append({
                "tag": key,
                "name": data.get("name") or key,
                "work": self._matched_work(data, works),
                "score": round(min(score, 0.99), 2),
                "reason": "cache_exact" + ("+work" if work_match else ""),
                "tags": tags[:20],
            })
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates[:8]

    def _infer_from_request(self, request):
        text = re.sub(r"^(帮我|请|给我)?(画|生成|出图|来)\s*(一张|一个|个|张)?", "", request).strip()
        match = re.search(r"(.+?)的([^的，,。.!！?？]+)", text)
        if match:
            return [match.group(2).strip()], [match.group(1).strip()]
        return ([text] if text else []), []

    def _alias_record(self, character, works):
        for work in works:
            record = self.aliases.get(self._alias_key(character, work))
            if record:
                return record
        return self.aliases.get(self._normalize(character))

    def _cache_lookup(self, tag):
        cache = getattr(self.host.tag_site, "_cache", {}) or {}
        item = cache.get(self._normalize(tag))
        return (item or {}).get("data")

    def _matched_work(self, data, works):
        work_keys = {self._normalize(w) for w in works}
        for tag in data.get("tags", []) or []:
            if self._normalize(tag) in work_keys:
                return tag
        return None

    def _tags_match_work(self, tags, work_keys):
        return any(self._normalize(t) in work_keys for t in tags or [])

    def _merge_tags(self, resolved):
        result = []
        seen = set()
        for item in resolved:
            for tag in item.get("tags") or []:
                if tag not in seen:
                    result.append(tag)
                    seen.add(tag)
        return result

    def _load_aliases(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_aliases(self):
        self.path.write_text(json.dumps(self.aliases, indent=2, ensure_ascii=False), encoding="utf-8")

    def _as_list(self, value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [str(value).strip()] if str(value).strip() else []

    def _looks_like_tag(self, text):
        return bool(re.search(r"[A-Za-z0-9_]", str(text or "")))

    def _alias_key(self, alias, work):
        return f"{self._normalize(work)}::{self._normalize(alias)}"

    def _normalize(self, text):
        return str(text or "").strip().lower().replace(" ", "_").replace("\\", "")


class CharacterResolveTool:
    name = "character_resolve"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        return self.host.character_resolver.resolve(
            request=params.get("request", ""),
            characters=params.get("characters"),
            works=params.get("works"),
            tag_hints=params.get("tag_hints"),
        )


class CharacterConfirmTool:
    name = "character_confirm"

    def __init__(self, host):
        self.host = host

    def __call__(self, params):
        params = params or {}
        return self.host.character_resolver.confirm(
            alias=params.get("alias", ""),
            tag=params.get("tag", ""),
            work=params.get("work"),
        )
