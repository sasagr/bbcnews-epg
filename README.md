# bbcnews-epg

A cloud-hosted **XMLTV EPG for the BBC News channel**, built from the BBC's own iPlayer TV guide at
<https://www.bbc.co.uk/iplayer/guide/bbcnews>.

[`generate.py`](generate.py) reads seven days and writes [`bbcnews.xml`](bbcnews.xml). A GitHub
Action re-runs it every 6 hours and commits the file, so any IPTV app can attach the guide with a
plain URL.

## The source moved once

It used to read the schema.org graph out of `bbc.co.uk/schedules/p00fzl6g`. On **21 October 2026**
every path under `/schedules/` and `/programmes/` began answering `302 → /error.html` ("BBC: Service
Temporarily Unavailable") — the undated page and other channels alike, from a GitHub runner as well
as from a home connection, while the rest of bbc.co.uk stayed up. The iPlayer guide carries the same
schedule in `window.__IPLAYER_REDUX_STATE__` and is the better source anyway: UTC timestamps with an
explicit `Z`, and an image template that renders at any size.

The swap was checked against the last file the old scraper produced: of 348 programmes, **346 share
an exact start time**, and of those 345 agree on title and stop time, 346 on description, and every
artwork URL is identical. The handful that differ are the BBC having revised the day's schedule since
that snapshot, not a parsing difference.

## Why, when the IPTV provider already has a BBC News guide

**Artwork.** The provider's feed names a Sky image id for every programme, and Sky's image service
does not hold all of them — every airing of *The World Today with Maryam Moshiri* and *The Context
USA* returns 404, in every crop — so those rows fall back to the channel logo. The BBC publishes its
own images for the same programmes and they exist: in the current build, **348 of 348 programmes
have artwork**, 27 distinct images, every one verified as a real JPEG.

The descriptions and the series/episode split are better too: a daily strand comes through as
`<title>The Travel Show</title><sub-title>France: Bistros Bite Back</sub-title>` rather than one
flattened string.

## Where the data comes from

Not from the visible markup. Each schedule page embeds a schema.org `@graph` of `TVEpisode`
objects, each with a broadcast start and end, its series, its episode, a description and an image.
Nothing here depends on a CSS class or the order of the columns.

**Times are UTC, and that was checked rather than assumed** — an offset of `+00:00` is a claim worth
testing in a country that keeps BST for half the year. Compared against a completely independent
XMLTV feed for the same channel at the same moment, the two agreed to the minute across five
consecutive programmes. `generate.py` re-tests the shape on every run: every timestamp must carry a
timezone, and the week must be plausibly full, or the run **fails** rather than publishing a guide
that is quietly wrong by an hour.

It also refuses to write the file if fewer than 24 broadcasts parse, so a redesign at the BBC leaves
the last good guide in place instead of replacing it with a stub.

## Deploy (once)

1. Public repo `bbcnews-epg` with: `generate.py`, `bbcnews.xml`, `.github/workflows/epg.yml`,
   `README.md`, `.gitignore`.
2. Actions tab → run **"Generate BBC News EPG"** (also runs every 6 hours).
3. Live at `https://raw.githubusercontent.com/<your-username>/bbcnews-epg/main/bbcnews.xml`.

## Add it to the app (Movie4All)

Settings ▸ IPTV ▸ edit the **BBC News** channel:

| Field | Value |
|-------|-------|
| **EPG URL** | `https://raw.githubusercontent.com/<your-username>/bbcnews-epg/main/bbcnews.xml` |
| **EPG channel id / tvg-id** | `BBCNews.uk` |

The channel id is deliberately the same `BBCNews.uk` the provider's feed uses, so this is a drop-in
swap: change the EPG URL and nothing else.

## Run it locally

```
python3 generate.py
```

No dependencies: stdlib only, same as `tgs-epg`, `rds-epg` and `aljazeera-epg`.
