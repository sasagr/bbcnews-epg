#!/usr/bin/env python3
"""Generate an XMLTV EPG for the BBC News channel from bbc.co.uk/schedules/p00fzl6g.

The BBC publishes each day's schedule as a schema.org `@graph` inside the page — a list of
`TVEpisode` objects carrying a broadcast start and end, the series, the episode, a description and
an image. So this is a structured read, not a scrape of the visible markup: nothing depends on a
class name or the order of the columns.

**Why bother, when the IPTV provider already supplies a BBC News guide.** Artwork. The provider's
feed names a Sky image id for every programme and Sky's image service does not hold all of them —
every airing of "The World Today with Maryam Moshiri" and "The Context USA" 404s, in every crop —
so those rows fall back to the channel logo. The BBC publishes its own images for the same
programmes, and they exist.

**Times are UTC, and that was checked rather than assumed.** The graph gives explicit offsets
(`+00:00`), which is a claim worth testing in a country that keeps BST for half the year. Compared
against the provider's own XMLTV for the same channel at the same moment, the two agreed to the
minute across five consecutive programmes — 17:00-17:30 "BBC News at Six", three "The World Today"
slots, then "The Context USA". `sanity_check` re-tests the shape of that on every run.

Stdlib only — runs on a stock GitHub Actions runner. Mirrors tgs-epg / rds-epg / aljazeera-epg.
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

# The tvg-id the app's BBC News channel already uses, so pointing it at this file is a drop-in swap
# rather than an edit on both sides.
CHANNEL_ID = "BBCNews.uk"
CHANNEL_NAME = "BBC News"
SERVICE_PID = "p00fzl6g"                  # BBC News channel on bbc.co.uk/schedules
BASE = "https://www.bbc.co.uk/schedules/" + SERVICE_PID
DAYS_AHEAD = 7                            # the BBC publishes at least this far
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
OUT = "bbcnews.xml"

# The graph hands out a thumbnail; the same image exists at a size worth putting on a television.
THUMB_RECIPE = re.compile(r"/images/ic/[^/]+/")
POSTER_RECIPE = "/images/ic/1280x720/"

_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
# Daily strands title their episodes with the date — "07/08/2026" is not a subtitle worth showing.
_DATE_NAME = re.compile(r"^\s*\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\s*$")


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "ignore")


def episodes(html):
    """The `TVEpisode` list out of the page's schema.org graph."""
    for block in _LD.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        graph = data.get("@graph")
        if isinstance(graph, list) and graph:
            return [e for e in graph if isinstance(e, dict) and e.get("publication")]
    return []


def poster(image):
    """The same image at a size worth showing. Falls back to whatever was given."""
    if not image:
        return ""
    return THUMB_RECIPE.sub(POSTER_RECIPE, image, count=1)


def convert(entry):
    """One `TVEpisode` → (start, stop, title, subtitle, description, icon), or None."""
    publication = entry.get("publication") or {}
    try:
        start = datetime.fromisoformat(publication["startDate"])
        stop = datetime.fromisoformat(publication["endDate"])
    except (KeyError, TypeError, ValueError):
        return None
    if stop <= start:
        return None

    series = ((entry.get("partOfSeries") or {}).get("name") or "").strip()
    name = (entry.get("name") or "").strip()

    # The series is the programme; the episode name is a subtitle, unless it is just the date, which
    # is how every daily strand names its editions.
    if series:
        title = series
        subtitle = "" if (not name or _DATE_NAME.match(name) or name == series) else name
    else:
        title = name
        subtitle = ""
    if not title:
        return None

    return (start, stop, title, subtitle,
            (entry.get("description") or "").strip(),
            poster(entry.get("image") or (entry.get("partOfSeries") or {}).get("image")))


def sanity_check(rows):
    """Fail the run rather than publish a guide that is wrong by an hour.

    Two things worth being sure of. Every timestamp must be timezone-aware — a naive one would be
    written without an offset and read as the viewer's local time, which is the classic silent
    hour-out bug. And the day must be plausibly full: the BBC News channel broadcasts around the
    clock, so a day covering only a few hours means the page shape moved.
    """
    for start, stop, *_ in rows:
        if start.tzinfo is None or stop.tzinfo is None:
            raise SystemExit("! a broadcast has no timezone — refusing to guess at the offset")
    span = rows[-1][1] - rows[0][0]
    hours = span.total_seconds() / 3600
    if hours < 20 * DAYS_AHEAD * 0.5:
        raise SystemExit(f"! only {hours:.0f}h of schedule across {DAYS_AHEAD} days — the page shape "
                         f"has probably changed")
    print(f"  {len(rows)} broadcasts spanning {hours:.0f}h, all timezone-aware")


def xmltv_ts(dt):
    return dt.strftime("%Y%m%d%H%M%S %z")


def main():
    today = datetime.now(timezone.utc).date()
    rows = {}
    for offset in range(DAYS_AHEAD):
        day = today + timedelta(days=offset)
        url = f"{BASE}/{day:%Y/%m/%d}"
        try:
            found = episodes(fetch(url))
        except Exception as e:
            print(f"  ! {day}: {e}", file=sys.stderr)
            continue
        print(f"  {day}: {len(found)} broadcast(s)")
        for entry in found:
            row = convert(entry)
            # Keyed on the start: consecutive days overlap at the boundary (a day's page runs past
            # midnight into the next), so the same broadcast is returned twice.
            if row:
                rows[row[0]] = row

    if len(rows) < 24:
        print(f"! only {len(rows)} broadcasts parsed — refusing to write {OUT}", file=sys.stderr)
        return 1

    ordered = [rows[k] for k in sorted(rows)]
    sanity_check(ordered)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<tv generator-info-name="bbcnews-epg">',
             f'  <channel id="{CHANNEL_ID}">',
             f'    <display-name>{escape(CHANNEL_NAME)}</display-name>',
             '  </channel>']

    for start, stop, title, subtitle, desc, icon in ordered:
        lines.append(f'  <programme start="{xmltv_ts(start)}" stop="{xmltv_ts(stop)}" channel="{CHANNEL_ID}">')
        lines.append(f'    <title lang="en">{escape(title)}</title>')
        if subtitle:
            lines.append(f'    <sub-title lang="en">{escape(subtitle)}</sub-title>')
        if desc:
            lines.append(f'    <desc lang="en">{escape(desc)}</desc>')
        if icon:
            lines.append(f'    <icon src="{escape(icon)}" />')
        lines.append('  </programme>')
    lines.append('</tv>')

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    with_art = sum(1 for r in ordered if r[5])
    print(f"Wrote {OUT}: {len(ordered)} programmes, {with_art} with artwork, "
          f"{ordered[0][0]:%Y-%m-%d %H:%M} → {ordered[-1][1]:%Y-%m-%d %H:%M} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
