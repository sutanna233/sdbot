import json
import requests
import re
from pathlib import Path


DANBOORU_BASE = "https://danbooru.donmai.us"


class DanbooruTagSearch:
    def __init__(self, config):
        c = config.get("danbooru", {})
        self.base_url = c.get("base_url", DANBOORU_BASE).rstrip("/")
        self.max_results = c.get("max_results", 30)
        self.cache_path = Path(__file__).parent / c.get("cache_file", "tag_cache.json")
        self.cache = self._load_cache()

    def _load_cache(self):
        if not self.cache_path.exists():
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_cache(self):
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=2, ensure_ascii=False)

    def search_tags(self, keyword):
        cache_key = keyword.lower().strip()
        if cache_key in self.cache:
            return self.cache[cache_key]
        try:
            url = f"{self.base_url}/tags.json"
            params = {"search[name_matches]": f"*{keyword}*", "limit": self.max_results}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return []
            results = []
            for t in resp.json():
                tag_type = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "metadata"}.get(t.get("category", 0), "general")
                results.append({
                    "name": t["name"],
                    "type": tag_type,
                    "count": t.get("post_count", 0),
                })
            self.cache[cache_key] = results
            self._save_cache()
            return results
        except requests.RequestException:
            return []

    def search_artists(self, keyword, local_artists):
        kw = keyword.lower()
        matches = [a for a in local_artists if kw in a.lower()]
        return matches[:self.max_results]
