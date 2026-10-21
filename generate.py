#!/usr/bin/env python3
"""Generate an XMLTV EPG for the BBC News channel from bbc.co.uk/iplayer/guide/bbcnews.

The iPlayer TV guide ships each day's schedule as JSON in the page — `window.__IPLAYER_REDUX_STATE__`,
whose `schedule.items` is a list of slots carrying a scheduled start and end, a title, a subtitle, a
synopsis and an image template. So this is a structured read, not a scrape of the visible markup:
nothing depends on a class name or the order of the columns.

**Why this source and not bbc.co.uk/schedules.** It used to be the latter, reading the schema.org
`@graph` out of `/schedules/p00fzl6g`. On 21 October 2026 every path under `/schedules/` and
`/programmes/` began answering `302 → /error.html` ("BBC: Service Temporarily Unavailable") — the
undated page and other channels alike, from a GitHub runner as well as from here, while the rest of
bbc.co.uk stayed up. The iPlayer guide carries the same schedule and is, if anything, the better
source: it gives UTC timestamps with an explicit `Z`, an image template that will render any size,
and 100% artwork coverage.

**Why bother, when the IPTV provider already supplies a BBC News guide.** Artwork. The provider's
feed names a Sky image id for every programme and Sky's image service does not hold all of them —
every airing of "The World Today with Maryam Moshiri" and "The Context USA" 404s, in every crop —
so those rows fall back to the channel logo. The BBC publishes its own images for the same
programmes, and they exist.

**Times are UTC, and that is stated rather than inferred.** `meta.scheduledStart` and
`scheduledEnd` end in `Z`. That matters in a country that keeps BST for half the year, and it shows
in the data: the BBC's broadcast day starts at 05:00Z before the 25 October clock change and 06:00Z
after it, which is 06:00 local on both sides. `sanity_check` re-tests the shape of that on every run.

**An outage upstream is not a failure here, until it is.** The run distinguishes "the BBC is not
answering" from "the BBC answered and we could not read it". The first keeps the committed guide and
exits 0, because a scheduled publisher should not page anyone every six hours for someone else's
outage, and the file already holds a week. The second still fails loudly: that is the case where
nobody would otherwise notice. The amnesty is not open-ended — once the committed guide has less
than `STALE_HOURS` still to run, an outage stops being harmless and fails like anything else.

Stdlib only — runs on a stock GitHub Actions runner. Mirrors tgs-epg / rds-epg / aljazeera-epg.
"""

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

# The tvg-id the app's BBC News channel already uses, so pointing it at this file is a drop-in swap
# rather than an edit on both sides.
CHANNEL_ID = "BBCNews.uk"
CHANNEL_NAME = "BBC News"
CHANNEL_KEY = "bbcnews"                   # BBC News channel in the iPlayer guide
BASE = "https://www.bbc.co.uk/iplayer/guide/" + CHANNEL_KEY
DAYS_AHEAD = 7                            # the guide's own day picker offers this many forward
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
OUT = "bbcnews.xml"
# How much of the committed guide must still lie ahead for an upstream outage to be survivable.
# The job runs every 6 hours and publishes a week, so the BBC would have to be down for six days
# before this trips — at which point the guide really is about to run dry and silence is wrong.
STALE_HOURS = 24

# The image template is a URL with a `{recipe}` placeholder — ichef will render whatever size is
# asked for, so ask for one worth putting on a television.
RECIPE = "1280x720"

_STATE = re.compile(r"window\.__IPLAYER_REDUX_STATE__\s*=\s*(\{.*?\});?\s*</script>", re.S)
# Daily and weekly strands title their editions with the date, sometimes behind a series label:
# "21/10/2026", "2026: 22/10/2026", "Series 6: 21/10/2026" are not subtitles worth showing. A real
# episode name that merely contains a colon ("America's War on Drugs: The Synthetic Wave") does not
# end in a date, so it survives.
_DATE_NAME = re.compile(r"^\s*(?:.*?:\s*)?\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\s*$")


class Unavailable(Exception):
    """The BBC did not serve the guide. Not our bug, and not necessarily worth failing the run."""


def slots(url):
    """The schedule items for one day, or `Unavailable` if the BBC did not serve the page.

    Three ways this is the BBC's problem rather than ours, and all three raise `Unavailable`:
    the request fails outright; the request is diverted somewhere that is not the guide, which is
    how the old source died (`302 → /error.html`, a page that then answers 200, so the status code
    alone would say everything was fine); or the page arrives without the redux state in it.

    A state that parses but holds no items for the day is NOT unavailable — that is an answer, and
    an empty day is a thing that can legitimately happen at the far end of the window.
    """
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            landed = response.geturl()
            html = response.read().decode("utf-8", "ignore")
    except Exception as e:
        raise Unavailable(str(e)) from e
    if urllib.parse.urlsplit(landed).path.rstrip("/") != urllib.parse.urlsplit(url).path.rstrip("/"):
        raise Unavailable(f"diverted to {landed}")
    found = _STATE.search(html)
    if not found:
        raise Unavailable("no __IPLAYER_REDUX_STATE__ in the page")
    schedule = json.loads(found.group(1)).get("schedule") or {}
    items = schedule.get("items")
    return items if isinstance(items, list) else []


def committed_runway():
    """Hours of guide still ahead of now in the `OUT` already committed, or None if there is none.

    Read with a regex rather than an XML parser: all this needs is the last `stop`, and a file too
    damaged to regex is one there is no point falling back to anyway.
    """
    try:
        with open(OUT, encoding="utf-8") as fh:
            stops = re.findall(r'stop="(\d{14})\s*([+-]\d{4})"', fh.read())
    except OSError:
        return None
    if not stops:
        return None
    last = max(datetime.strptime(d + o, "%Y%m%d%H%M%S%z") for d, o in stops)
    return (last - datetime.now(timezone.utc)).total_seconds() / 3600


def timestamp(value):
    """One ISO instant from the guide. `Z` is spelled out for Pythons older than 3.11."""
    if not isinstance(value, str) or not value:
        raise ValueError("missing timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def poster(template):
    """The image at a size worth showing, or "" when the slot carries none."""
    if not isinstance(template, str) or "{recipe}" not in template:
        return ""
    return template.replace("{recipe}", RECIPE)


def convert(slot):
    """One schedule item → (start, stop, title, subtitle, description, icon), or None.

    Every slot counts, whatever its `type`. That field says whether the programme can be streamed
    on iPlayer right now (AVAILABLE, UNAVAILABLE, LIVE, …), which is a fact about iPlayer and not
    about what the channel is broadcasting — an EPG wants the whole schedule.
    """
    meta = slot.get("meta") or {}
    props = slot.get("props") or {}
    try:
        start = timestamp(meta.get("scheduledStart"))
        stop = timestamp(meta.get("scheduledEnd"))
    except (TypeError, ValueError):
        return None
    if stop <= start:
        return None

    title = (props.get("title") or "").strip()
    if not title:
        return None
    name = (props.get("subtitle") or "").strip()
    subtitle = "" if (not name or _DATE_NAME.match(name) or name == title) else name

    return (start, stop, title, subtitle,
            (props.get("synopsis") or "").strip(),
            poster(props.get("imageTemplate")))


def sanity_check(rows):
    """Fail the run rather than publish a guide that is wrong by an hour.

    Two things worth being sure of. Every timestamp must be timezone-aware — a naive one would be
    written without an offset and read as the viewer's local time, which is the classic silent
    hour-out bug. And the week must be plausibly full: the BBC News channel broadcasts around the
    clock, so a span covering only a few hours means the page shape moved.
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
    answered = 0            # days the BBC actually served a schedule for, empty or not
    for offset in range(DAYS_AHEAD):
        day = today + timedelta(days=offset)
        url = f"{BASE}/{day:%Y%m%d}"
        try:
            found = slots(url)
        except Unavailable as e:
            print(f"  ! {day}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            # The page was served and we could not read it. That is ours, and it is the case the
            # amnesty below must not cover, so it counts as answered.
            answered += 1
            print(f"  ! {day}: {e}", file=sys.stderr)
            continue
        answered += 1
        print(f"  {day}: {len(found)} broadcast(s)")
        for slot in found:
            row = convert(slot)
            # Keyed on the start: the BBC's broadcast day runs from 06:00 local to 06:00 local, so
            # consecutive pages overlap at that boundary and return the same broadcast twice.
            if row:
                rows[row[0]] = row

    if len(rows) < 24:
        print(f"! only {len(rows)} broadcasts parsed — refusing to write {OUT}", file=sys.stderr)
        # Nobody answered: the BBC is down, which is not something this repo can fix and not
        # something worth an alert every six hours — as long as the guide already committed still
        # has a usable stretch ahead of it. Say so plainly and leave that file alone.
        if answered == 0:
            runway = committed_runway()
            if runway is None:
                print("! and there is no committed guide to fall back on", file=sys.stderr)
                return 1
            if runway < STALE_HOURS:
                print(f"! and the committed guide runs out in {runway:.0f}h — too close to ignore",
                      file=sys.stderr)
                return 1
            print(f"The BBC served no schedule at all; keeping the committed {OUT}, which still "
                  f"has {runway:.0f}h ({runway / 24:.1f} days) to run.")
            return 0
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
