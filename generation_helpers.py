import base64
import hashlib
import itertools
import random
import re
import sys
from datetime import datetime
from io import BytesIO

import yaml
from PIL import Image


def combo_fingerprint(artists):
    return hashlib.sha1(",".join(sorted(artists)).encode()).hexdigest()[:16]


def is_duplicate(history, artists):
    return combo_fingerprint(artists) in history.get("combos", {})


def mark_generated(history, artists, output_path, prompt):
    fingerprint = combo_fingerprint(artists)
    history.setdefault("combos", {})[fingerprint] = {
        "artists": artists,
        "generated_at": datetime.now().isoformat(),
        "output_path": str(output_path),
        "prompt": prompt,
    }
    stats = history.setdefault("stats", {})
    stats["total_generated"] = stats.get("total_generated", 0) + 1


def get_base_name(artist):
    return re.sub(r"_\([^)]+\)$", "", artist).strip()


def filter_similar(candidates, similarity_filter="strict"):
    if similarity_filter == "off":
        return candidates
    seen = set()
    result = []
    for artist in candidates:
        base = get_base_name(artist)
        if base not in seen:
            seen.add(base)
            result.append(artist)
    return result


def sanitize_filename(name):
    return "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in name)


def create_output_dir(script_dir, config, mode):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = script_dir / config["output"]["base_dir"] / f"{timestamp}_{mode}_test"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image(img_data, path):
    Image.open(BytesIO(base64.b64decode(img_data))).save(path, "PNG")


def save_info_txt(config, artists, prompt, negative_prompt, path, seed):
    generation = config["generation"]
    with open(path.with_suffix(".txt"), "w", encoding="utf-8") as f:
        f.write(
            f"Prompt: {prompt}\nNegative: {negative_prompt}\nArtists: {', '.join(artists)}\n"
            f"Seed: {seed}\nSize: {generation['width']}x{generation['height']}\n"
            f"Steps: {generation['steps']}  CFG: {generation['cfg_scale']}  "
            f"Sampler: {generation.get('sampler', 'Euler a')}"
        )


def build_prompt(config, artists):
    artists_str = ", ".join(artist for artist in artists)
    template = config["prompt"]["template"]
    if "{artists}" in template:
        return template.replace("{artists}", artists_str)
    return template + ", " + artists_str


def load_artists(script_dir, config):
    path = script_dir / config["artists"]["list_file"]
    if not path.exists():
        print(f"Error: Artist list not found: {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


class ArtistSampler:
    def __init__(self, host):
        self.host = host

    def select(self, artists_list, num_images):
        selectors = {
            "single": self.select_single,
            "pair": self.select_pair,
            "sequential": self.select_sequential,
            "weighted": self.select_weighted,
            "combo": self.select_combo,
        }
        return selectors.get(self.host.mode, self.select_combo)(artists_list, num_images)

    def select_combo(self, artists, count):
        results = []
        for _ in range(count * 50):
            if len(results) >= count:
                break
            artist_count = min(random.randint(self.host.mode_min, self.host.mode_max), len(artists))
            combo = random.sample(artists, artist_count)
            if self.host.similarity_filter != "off":
                combo = filter_similar(combo, self.host.similarity_filter)
                if len(combo) < self.host.mode_min:
                    continue
            if self._skip_duplicate(combo):
                continue
            results.append(combo)
        if len(results) < count:
            print(f"Warning: Only {len(results)} unique combos out of {count}")
        return results

    def select_single(self, artists, count):
        pool = list(artists)
        random.shuffle(pool)
        if self.host.dedup_enabled and not self.host.allow_resample:
            deduped = [artist for artist in pool if not self.host._is_duplicate([artist])]
            if deduped:
                pool = deduped
        return [[artist] for artist in pool[:count]]

    def select_pair(self, artists, count):
        pairs = list(itertools.combinations(artists, 2))
        random.shuffle(pairs)
        if self.host.dedup_enabled and not self.host.allow_resample:
            pairs = [pair for pair in pairs if not self.host._is_duplicate(list(pair))]
        return [list(pair) for pair in pairs[:count]]

    def select_sequential(self, artists, count):
        chunk_size = self.host.config.get("mode_config", {}).get("sequential", {}).get("chunk_size", 5)
        results = []
        for index in range(0, len(artists), chunk_size):
            chunk = artists[index:index + chunk_size]
            if self._skip_duplicate(chunk):
                continue
            results.append(chunk)
            if len(results) >= count:
                break
        return results[:count]

    def select_weighted(self, artists, count):
        weights = self._load_weights(artists)
        results = []
        for _ in range(count * 50):
            if len(results) >= count:
                break
            artist_count = min(random.randint(self.host.mode_min, self.host.mode_max), len(artists))
            try:
                combo = random.choices(artists, weights=weights, k=artist_count) if weights else random.sample(artists, artist_count)
            except ValueError:
                combo = random.sample(artists, artist_count)
            combo = list(dict.fromkeys(combo))
            if len(combo) < self.host.mode_min:
                continue
            if self._skip_duplicate(combo):
                continue
            results.append(combo)
        return results

    def _load_weights(self, artists):
        weights_file = self.host.config.get("mode_config", {}).get("weighted", {}).get("weights_file")
        if not weights_file:
            return None
        weights_path = self.host.script_dir / weights_file
        if not weights_path.exists():
            return None
        weights_data = yaml.safe_load(open(weights_path, "r", encoding="utf-8"))
        if isinstance(weights_data, dict):
            return [weights_data.get(artist, 1) for artist in artists]
        return None

    def _skip_duplicate(self, artists):
        return self.host.dedup_enabled and not self.host.allow_resample and self.host._is_duplicate(artists)
