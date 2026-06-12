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
        if not candidates:
            candidates = self._verified_llm_candidates(character, works)
        resolved = self._auto_resolve_candidate(character, candidates, works)
        if resolved:
            return resolved
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

    def _verified_llm_candidates(self, character, works):
        tags = self._llm_candidate_tags(character, works)
        candidates = []
        seen = set()
        work_keys = {self._normalize(w) for w in works}
        for tag in tags:
            key = self._normalize(tag)
            if not key or key in seen:
                continue
            seen.add(key)
            data = self._cache_lookup(tag)
            if not data:
                data = self.host.tag_site.search_character(tag)
            if not data:
                continue
            tag_list = data.get("tags") or []
            work_hint = self._candidate_work_hint(tag)
            work_match = self._tags_match_work(tag_list, work_keys) or (work_hint and self._normalize(work_hint) in {self._normalize(t) for t in tag_list})
            score = 0.82 + (0.15 if work_match else 0)
            candidates.append({
                "tag": self._normalize(tag),
                "name": data.get("name") or tag,
                "work": self._matched_work(data, works) or work_hint,
                "score": round(min(score, 0.99), 2),
                "reason": "llm_candidate_verified" + ("+work" if work_match else ""),
                "tags": tag_list[:20],
            })
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates[:8]

    def _auto_resolve_candidate(self, character, candidates, works):
        if not candidates:
            return None
        if works:
            matched = [c for c in candidates if c.get("work")]
            if len(matched) != 1:
                return None
            item = matched[0]
        else:
            return None
        return self._resolved(
            character,
            item.get("tag"),
            {"name": item.get("name"), "tags": item.get("tags") or []},
            item.get("reason", "candidate_verified"),
            "high",
            item.get("work"),
        )

    def _llm_candidate_tags(self, character, works):
        llm_getter = getattr(self.host, "_llm", None)
        if not callable(llm_getter):
            return []
        system = (
            "你是 Danbooru 角色 tag 候选生成器。只输出 JSON，不要解释。"
            "根据角色名和作品名给出最多 5 个可能的 Danbooru character tag 候选。"
            "这些只是候选，不是事实；不要编造外观描述。"
            "输出格式: {\"candidates\": [\"character_(copyright)\"]}"
        )
        payload = {"character": character, "works": works}
        try:
            result = llm_getter().agent_chat(system, [], json.dumps(payload, ensure_ascii=False))
        except Exception:
            return []
        if isinstance(result, dict):
            values = result.get("candidates") or result.get("tags") or []
            if isinstance(values, str):
                values = [values]
            return [str(v).strip() for v in values if str(v).strip()]
        if isinstance(result, list):
            return [str(v).strip() for v in result if str(v).strip()]
        return []

    def _candidate_work_hint(self, tag):
        match = re.search(r"\(([^)]+)\)\s*$", str(tag or ""))
        return match.group(1).strip() if match else None

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
