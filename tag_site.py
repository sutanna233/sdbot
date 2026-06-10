import re, json, time, logging
from pathlib import Path
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.downloadmost.com/NoobAI-XL/danbooru-character/"
SEARCH_URL = BASE_URL + "search.asp"

CACHE_FILE = "tag_cache.json"
CACHE_TTL = 86400 * 36500  # ~100 years, permanent


class TagSite:
    def __init__(self, script_dir):
        self.script_dir = Path(script_dir)
        self.cache_path = self.script_dir / CACHE_FILE
        self._cache = self._load_cache()
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
        })

    def _load_cache(self):
        cache = {}
        if self.cache_path.exists():
            try:
                raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
                # 迁移旧缓存: 归一化 key，合并重复（保留最新 ts）
                for key, val in raw.items():
                    nk = key.strip().lower().replace(" ", "_")
                    if nk in cache:
                        if val.get("ts", 0) > cache[nk].get("ts", 0):
                            cache[nk] = val
                    else:
                        cache[nk] = val
                if cache != raw:
                    self._cache = cache
                    self._save_cache()
                return cache
            except (json.JSONDecodeError, IOError):
                pass
        return cache

    def _save_cache(self):
        self.cache_path.write_text(
            json.dumps(self._cache, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _normalize_key(self, name):
        return name.strip().lower().replace(" ", "_")

    def search_character(self, name, _save=True):
        name = name.strip().lower()
        key = self._normalize_key(name)
        now = time.time()

        cached = self._cache.get(key)
        if cached and now - cached.get("ts", 0) < CACHE_TTL:
            return cached.get("data")

        try:
            resp = self._session.get(SEARCH_URL, params={"charactername": name}, timeout=15)
            resp.encoding = "utf-8"
            html = resp.text
        except Exception as e:
            logger.warning("TagSite search failed for %r: %s", name, e)
            if cached:
                return cached.get("data")
            return None

        char_name, tags = self._parse_page(html)
        if not tags:
            if cached:
                return cached.get("data")
            return None

        result = {"name": char_name or name, "tags": tags}
        self._cache[key] = {"ts": now, "data": result}
        if _save:
            self._save_cache()
        return result

    def _parse_page(self, html):
        m = re.search(
            r'Character:\s*<span[^>]*>([^<]+)</span>',
            html,
        )
        char_name = m.group(1).strip() if m else None

        m = re.search(
            r'Prompt tags:</div><div[^>]*>([^<]+)</div>',
            html,
        )
        tags_str = m.group(1).strip() if m else None
        tags = [t.strip() for t in tags_str.split(",")] if tags_str else []
        return char_name, tags

    def search_characters(self, names):
        seen = set()
        results = []
        changed = False
        now = time.time()
        for name in names:
            key = self._normalize_key(name)
            if key in seen:
                continue
            seen.add(key)
            # 先查缓存
            cached = self._cache.get(key)
            if cached and now - cached.get("ts", 0) < CACHE_TTL:
                if cached.get("data"):
                    results.append(cached["data"])
                continue
            # 缓存未命中，调 API（延迟保存，batch 结束后统一写）
            result = self.search_character(name, _save=False)
            if result:
                results.append(result)
                changed = True
        if changed:
            self._save_cache()
        return results

    def get_tag_text(self, names):
        results = self.search_characters(names)
        parts = []
        seen = set()
        for r in results:
            for t in r["tags"]:
                t = t.strip()
                if t and t not in seen:
                    parts.append(t)
                    seen.add(t)
        return ", ".join(parts)
