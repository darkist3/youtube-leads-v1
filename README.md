# youtube-leads-v1

Scrapes YouTube for mid-tier course creators (coaches, trading/fitness educators) who post regularly and link a course platform (Kajabi, Teachable, Skool, Podia, Gumroad, Circle, Patreon) in their channel description. Output is a CSV ready for a Clay email waterfall.

Pure YouTube Data API v3 + stdlib — no dependencies, no LLM calls. Default config uses ~1.5k of the 10k free daily quota units.

## Usage

```bash
export YOUTUBE_API_KEY="your-key"   # YouTube Data API v3 key
python3 scrape_course_creators.py
```

Writes `course_creator_leads.csv` with channel, sub count, detected platform, course URL, and recency signals.

Tune search terms, subscriber band, and recency filters in the CONFIG block at the top of the script.
