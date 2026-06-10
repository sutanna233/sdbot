import requests, re, time, sys, os
from pathlib import Path

BASE = "https://www.downloadmost.com/NoobAI-XL/danbooru-artist"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
OUT = Path(__file__).parent / "artists.txt"
TEMP = Path("C:\\Users\\User\\AppData\\Local\\Temp\\opencode\\artists_temp.txt")

start = 1
if TEMP.exists():
    with open(TEMP, "r", encoding="utf-8") as f:
        done = len([l for l in f if l.strip()])
    start = done // 24 + 1
    print(f"Resuming from page {start} ({done} artists done)")

all_artists = []
if TEMP.exists():
    with open(TEMP, "r", encoding="utf-8") as f:
        all_artists = [l.strip() for l in f if l.strip()]

for page in range(start, 251):
    url = f"{BASE}/?page={page}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f"  [{page}] HTTP {resp.status_code}")
            break
        artists = re.findall(r'Artist:\s*<span[^>]*>([^<]+)</span>', resp.text)
        if not artists:
            print(f"  [{page}] no artists, stopping")
            break
        all_artists.extend(a.strip() for a in artists)
        print(f"  [{page}] {len(artists)}  total: {len(all_artists)}")
        # save incrementally every 10 pages
        if page % 10 == 0:
            with open(TEMP, "w", encoding="utf-8") as f:
                f.write("\n".join(all_artists) + "\n")
    except requests.Timeout:
        print(f"  [{page}] timeout, retrying...")
        time.sleep(5)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            artists = re.findall(r'Artist:\s*<span[^>]*>([^<]+)</span>', resp.text)
            if artists:
                all_artists.extend(a.strip() for a in artists)
                print(f"  [{page}] {len(artists)} (retry)  total: {len(all_artists)}")
            else:
                break
        except Exception as e2:
            print(f"  [{page}] retry failed: {e2}")
            break
    except Exception as e:
        print(f"  [{page}] {e}")
        break
    time.sleep(0.3)
    sys.stdout.flush()

final = "\n".join(all_artists) + "\n"
with open(TEMP, "w", encoding="utf-8") as f:
    f.write(final)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(final)
print(f"\nDone! {len(all_artists)} artists saved to {OUT}")
