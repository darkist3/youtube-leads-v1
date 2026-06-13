#!/usr/bin/env python3
"""
Course-creator lead scraper for subscription video-editing outreach.

Signal stack:
  - Channel posts ~weekly (last 4 uploads within RECENT_DAYS)
  - Channel description / links point to a course platform (Kajabi/Teachable/Skool/Podia/Gumroad)
  - Sub count in target band (mid-tier, has budget but not enterprise)

Output: course_creator_leads.csv  ->  drop into Clay for email waterfall.

No LLM calls. Pure YouTube Data API v3 + regex. Runs on free quota (~10k units/day).
"""

import csv
import os
import re
import time
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import urllib.request
import urllib.parse
import json

# ============================================================
# CONFIG  -- edit these
# ============================================================
API_KEY = os.environ.get("YOUTUBE_API_KEY", "")   # export YOUTUBE_API_KEY=... ; never hardcode/commit a key

# Seed search terms per niche. Add/remove freely.
SEARCH_TERMS = [
    # sales / offers / personal brand
    "high ticket closing course", "appointment setting course", "personal branding course",
    "linkedin lead generation course", "email marketing course", "sales funnel course",
    # money / local business models
    "wholesaling real estate course", "airbnb arbitrage course", "credit repair business course",
    "trucking business course", "cleaning business course", "vending machine business course",
    # content / creator economy
    "faceless youtube channel course", "ai content creation course", "podcast launch course",
    "notion course creator", "freelance writing course", "voiceover course",
    # health / skills
    "weight loss coaching program", "hormone health coach", "marathon training coach",
    "guitar lessons course online",
]

# Qualification filters
SUB_MIN = 10_000        # ignore tiny channels with no budget
SUB_MAX = 800_000       # ignore mega channels (different buyer, in-house teams)
RECENT_DAYS = 45        # last 4 uploads must fall within this window = posts regularly
MIN_RECENT_UPLOADS = 2  # of last 4 videos, at least this many within RECENT_DAYS

RESULTS_PER_TERM = 50   # max 50 per search call (1 page). Bump pages below if needed.
PAGES_PER_TERM = 2      # each page = 100 quota units. 22 terms x 2 pages = 4400 units.

OUTPUT = "course_creator_leads.csv"

# ============================================================
# Platform detection
# ============================================================
COURSE_PLATFORMS = {"Kajabi", "Teachable", "Skool", "Podia", "Gumroad"}

PLATFORM_PATTERNS = {
    "Kajabi":   re.compile(r"(?:[\w-]+\.)?mykajabi\.com|kajabi\.com", re.I),
    "Teachable":re.compile(r"(?:[\w-]+\.)?teachable\.com|thinkific\.com", re.I),
    "Skool":    re.compile(r"skool\.com", re.I),
    "Podia":    re.compile(r"podia\.com", re.I),
    "Gumroad":  re.compile(r"gumroad\.com", re.I),
    "Circle":   re.compile(r"circle\.so", re.I),
    "Patreon":  re.compile(r"patreon\.com", re.I),
}
URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)

API_BASE = "https://www.googleapis.com/youtube/v3"


def api_get(endpoint, params):
    params["key"] = API_KEY
    url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ! API error {e.code}: {body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ! request failed: {e}", file=sys.stderr)
        return None


def search_channels(term, pages):
    """Return list of channelIds from search."""
    ids = []
    page_token = None
    for _ in range(pages):
        params = {
            "part": "snippet", "q": term, "type": "channel",
            "maxResults": RESULTS_PER_TERM, "order": "relevance",
        }
        if page_token:
            params["pageToken"] = page_token
        data = api_get("search", params)
        if not data:
            break
        for item in data.get("items", []):
            cid = item.get("snippet", {}).get("channelId") or \
                  item.get("id", {}).get("channelId")
            if cid:
                ids.append(cid)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return ids


def get_channel_details(channel_ids):
    """Batch up to 50 channel IDs per call. Returns list of channel dicts."""
    out = []
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i+50]
        data = api_get("channels", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch), "maxResults": 50,
        })
        if data:
            out.extend(data.get("items", []))
    return out


def get_recent_uploads(uploads_playlist_id, n=4):
    """Return (dates, video_ids) for the last n uploads."""
    data = api_get("playlistItems", {
        "part": "snippet", "playlistId": uploads_playlist_id, "maxResults": n,
    })
    if not data:
        return [], []
    dates, video_ids = [], []
    for item in data.get("items", []):
        snip = item.get("snippet", {})
        pub = snip.get("publishedAt")
        if pub:
            try:
                dates.append(datetime.fromisoformat(pub.replace("Z", "+00:00")))
            except ValueError:
                pass
        vid = snip.get("resourceId", {}).get("videoId")
        if vid:
            video_ids.append(vid)
    return dates, video_ids


def get_video_descriptions(video_ids):
    """Fetch snippet for up to 50 video IDs; return list of description strings."""
    if not video_ids:
        return []
    data = api_get("videos", {
        "part": "snippet", "id": ",".join(video_ids[:50]),
    })
    if not data:
        return []
    return [item.get("snippet", {}).get("description", "") for item in data.get("items", [])]


def detect_platforms(text):
    found = []
    for name, pat in PLATFORM_PATTERNS.items():
        if pat.search(text or ""):
            found.append(name)
    return found


def extract_course_url(text):
    """First URL that matches a known course platform, else first URL."""
    urls = URL_RE.findall(text or "")
    for u in urls:
        for pat in PLATFORM_PATTERNS.values():
            if pat.search(u):
                return u
    return urls[0] if urls else ""


def main():
    if not API_KEY:
        print("ERROR: set the YOUTUBE_API_KEY environment variable first.", file=sys.stderr)
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    print(f"Seeding {len(SEARCH_TERMS)} search terms...")

    all_ids = set()
    for term in SEARCH_TERMS:
        ids = search_channels(term, PAGES_PER_TERM)
        print(f"  '{term}': {len(ids)} channels")
        all_ids.update(ids)
        time.sleep(0.2)
    print(f"Unique channels found: {len(all_ids)}")

    channels = get_channel_details(list(all_ids))
    print(f"Fetched details for {len(channels)} channels. Qualifying...")

    leads = []
    for ch in channels:
        snip = ch.get("snippet", {})
        stats = ch.get("statistics", {})
        content = ch.get("contentDetails", {}).get("relatedPlaylists", {})

        # sub band
        if stats.get("hiddenSubscriberCount"):
            continue
        subs = int(stats.get("subscriberCount", 0))
        if subs < SUB_MIN or subs > SUB_MAX:
            continue

        # activity signal + collect video IDs for description check below
        uploads_pl = content.get("uploads")
        if not uploads_pl:
            continue
        dates, video_ids = get_recent_uploads(uploads_pl, n=4)
        recent = sum(1 for d in dates if d >= cutoff)
        if recent < MIN_RECENT_UPLOADS:
            continue

        # platform signal: channel description first, then fall back to video descriptions
        ch_desc = snip.get("description", "")
        platforms = detect_platforms(ch_desc)
        platform_source = ch_desc  # text to pull course URL from

        if not platforms:
            video_descs = get_video_descriptions(video_ids[:3])
            combined = "\n".join(video_descs)
            platforms = detect_platforms(combined)
            platform_source = combined

        if not platforms:
            continue  # no monetization signal anywhere -> skip

        # Patreon alone is not a qualifying signal (community/tip jar, not a course platform)
        if not (COURSE_PLATFORMS & set(platforms)):
            continue

        last_upload = max(dates).date().isoformat() if dates else ""
        leads.append({
            "channel": snip.get("title", ""),
            "channel_url": f"https://youtube.com/channel/{ch.get('id','')}",
            "subscribers": subs,
            "videos": stats.get("videoCount", ""),
            "platform": ", ".join(platforms),
            "course_url": extract_course_url(platform_source),
            "last_upload": last_upload,
            "recent_uploads_45d": recent,
            "country": snip.get("country", ""),
            "description_snippet": ch_desc[:160].replace("\n", " "),
        })
        time.sleep(0.1)

    leads.sort(key=lambda x: x["subscribers"])
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        if leads:
            w = csv.DictWriter(f, fieldnames=list(leads[0].keys()))
            w.writeheader()
            w.writerows(leads)
    print(f"\nDONE. {len(leads)} qualified leads written to {OUTPUT}")
    print("Next: import to Clay, run email waterfall (Prospeo) + website scrape for contact@/hello@.")


if __name__ == "__main__":
    main()
