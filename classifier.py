"""Guess title, original artist, mood and voice effort for songs in the database.

The original artist and release year come from MusicBrainz (free, no API key).
Mood and voice effort are estimated with simple rules from the artist's genres,
the release year and the tempo saved in the .rpp project. Treat the results as a
starting point and correct them in the Song library.

MusicBrainz allows one request per second and a song needs about eight, so the
first run over a large library takes a while. Responses are cached, so later
runs are quick.

Only empty fields are filled in, so anything you have edited is kept.

Command line:
    python classifier.py              classify songs with a missing artist, mood or effort
    python classifier.py --dry-run    show what would change without saving
    python classifier.py --force      re-classify every song and overwrite its fields
"""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

import config
import db
from scanner import default_title

OVERRIDES_PATH = os.path.join(config.APP_DIR, "metadata_overrides.json")
CACHE_PATH = os.path.join(config.APP_DIR, "musicbrainz_cache.json")
MB_ROOT = "https://musicbrainz.org/ws/2/"
VERSION = "1.0"

# Genre names as MusicBrainz spells them (lower case). Artists carry many genre
# tags, so most rules only look at an artist's leading genres.
COUNTRY = {"country", "country pop", "contemporary country", "outlaw country", "bluegrass", "trop rock",
           "honky tonk", "gulf and western", "country folk", "neotraditional country"}
DANCE = {"disco", "dance", "dance-pop", "funk", "dance-rock", "post-disco", "electronic dance music",
         "house", "hi-nrg", "new jack swing", "boogie", "eurodance"}
GENTLE = {"soft rock", "folk", "folk rock", "singer-songwriter", "easy listening", "adult contemporary",
          "acoustic", "yacht rock", "trop rock", "folk pop", "chamber pop", "country folk"}
DEMANDING = {"hard rock", "heavy metal", "progressive rock", "glam rock", "arena rock", "art rock",
             "blues rock", "glam metal", "soul", "rhythm and blues", "gospel", "southern rock"}
LEADING = 3  # how many of an artist's top genres count as "leading"

# Words that name a part, take or render rather than the song, in project and media file names.
MEDIA_NOISE = re.compile(
    r"click|vocal|voice|vox|guitar|gtr|bass|drum|piano|keys|kontakt|midi|axe|reverb|delay|stem|"
    r"render|glued|tuned|untitled|track\s*\d|^\d+[-_ ]|\d{6}", re.I)
TRAILING_NOISE = {"live", "old", "tak", "fixed", "imported", "full", "final", "mix", "copy", "master",
                  "backing", "instrumental", "demo", "edit", "new", "v", "version", "audio", "gig"}
DUPLICATE = re.compile(r"conflicted copy|[-_ (]old\)?$|[-_ ]backup$", re.I)
NOT_AN_ARTIST = re.compile(r"various artists|\[unknown]|\[traditional]|anonymous|karaoke|tribute|"
                           r"made famous|in the style of|cover band|sound-?alike|party hits", re.I)

DEFAULT_TEMPO = 120.0  # REAPER's default, so it says nothing about the song


# ---------- reading the project ----------

def project_hints(path):
    """Tempo and the names of media files referenced in an .rpp project."""
    tempo, media = None, []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if tempo is None and line.startswith("TEMPO "):
                    try:
                        tempo = float(line.split()[1])
                    except (IndexError, ValueError):
                        pass
                elif line.startswith("FILE "):
                    name = line[5:].strip().strip('"')
                    media.append(os.path.splitext(os.path.basename(name.replace("\\", "/")))[0])
    except OSError:
        pass
    if tempo is not None and abs(tempo - DEFAULT_TEMPO) < 0.01:
        tempo = None
    return {"tempo": tempo, "media": media}


def clean_title(name):
    """Turn a file or folder name like 'IsSheReallyGoing_1' into 'Is She Really Going'."""
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", name)
    s = re.sub(r"[_\-.]+", " ", s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)          # rocketMan -> rocket Man
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)     # IfIEver -> If I Ever
    s = re.sub(r"(?<=[A-Za-z])(?=\d{3,})", " ", s)      # Miami2017 -> Miami 2017
    words = s.split()
    while words:
        last = words[-1]
        if last.lower().rstrip("0123456789") in TRAILING_NOISE or re.fullmatch(r"\d{1,3}", last):
            words.pop()                                   # "... imported 001", "... GIG"
        elif re.fullmatch(r"[A-Za-z]+\d{1,2}", last):
            words[-1] = last.rstrip("0123456789")         # Tempted2 -> Tempted
            break
        else:
            break
    while words and words[0].lower() in {"o"}:  # sorting prefixes such as "_O Away From It All"
        words.pop(0)
    return " ".join(words)


def title_candidates(rel_path, media_names):
    """Likely song titles for a project, best guesses first."""
    parts = rel_path.replace("/", "\\").split("\\")
    raw = [os.path.splitext(parts[-1])[0], *parts[:-1][::-1]]
    raw += [m for m in media_names if not MEDIA_NOISE.search(m)]
    seen, out = set(), []
    for name in raw:
        title = clean_title(name)
        key = norm(title)
        if len(key) >= 3 and key not in seen:
            seen.add(key)
            out.append(title)
    return out[:4]


def norm(text):
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text.lower().replace("'", "").replace("’", ""))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


# ---------- MusicBrainz ----------

class MusicBrainz:
    """Minimal MusicBrainz client: one request per second, retries, and a local cache."""

    def __init__(self, contact=""):
        self.user_agent = f"reaper-setlist-manager/{VERSION} ( {contact or 'https://github.com'} )"
        self.last = 0.0
        self.cache = {}
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, encoding="utf-8") as f:
                    self.cache = json.load(f)
            except (OSError, ValueError):
                self.cache = {}

    def save_cache(self):
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cache, f)

    def get(self, path, **params):
        url = MB_ROOT + path + "?" + urllib.parse.urlencode({**params, "fmt": "json"})
        if url in self.cache:
            return self.cache[url]
        for attempt in range(5):
            wait = 1.1 - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.load(r)
                self.cache[url] = data
                return data
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(2 ** attempt)
        raise ConnectionError("MusicBrainz is not responding; try again later")

    def artist_genres(self, artist_id):
        """The artist's genres, most-voted first."""
        data = self.get(f"artist/{artist_id}", inc="genres+tags")
        tags = {t["name"].lower(): t.get("count", 0) for t in data.get("tags", []) if t.get("count", 0) > 0}
        tags.update({g["name"].lower(): g.get("count", 0) + 1 for g in data.get("genres", [])})
        return [name for name, _ in sorted(tags.items(), key=lambda kv: -kv[1])][:12]


def same_song(found_title, target):
    """True if a MusicBrainz title is the song we're looking for (allowing subtitles and extra words)."""
    found = norm(found_title)
    return found == target or f" {target} " in f" {found} "


def popularity(mb, title):
    """Which artists recorded a title, and how prominently.

    Counts each artist's recordings and releases among the top search results, plus
    the singles they released under that title (originals of hits are nearly always
    singles, and search ranking sometimes buries them).
    Returns {artist_id: {"name", "weight", "singles"}}.
    """
    target = norm(title)
    artists = defaultdict(lambda: {"name": "", "weight": 0, "singles": 0})

    def credit_of(item):
        credit = (item.get("artist-credit") or [{}])[0]
        name = credit.get("name") or ""
        if not name or NOT_AN_ARTIST.search(name) or not same_song(item.get("title", ""), target):
            return None
        a = artists[credit["artist"]["id"]]
        a["name"] = name
        return a

    for page in range(2):
        data = mb.get("recording", query=f'recording:"{title}"', limit=100, offset=page * 100)
        for rec in data.get("recordings", []):
            a = credit_of(rec)
            if a:
                a["weight"] += 1 + len(rec.get("releases") or [])
        if (page + 1) * 100 >= data.get("count", 0):
            break
    data = mb.get("release-group", query=f'releasegroup:"{title}" AND primarytype:single', limit=100)
    for rg in data.get("release-groups", []):
        a = credit_of(rg)
        if a:
            a["singles"] += 1
    return dict(artists)


def artist_versions(mb, title, artist_id):
    """One artist's recordings of a title: how many, the earliest release and the typical length."""
    data = mb.get("recording", query=f'recording:"{title}" AND arid:{artist_id}', limit=100)
    target = norm(title)
    recs = [r for r in data.get("recordings", []) if same_song(r.get("title", ""), target)]
    dates = sorted(r["first-release-date"] for r in recs if r.get("first-release-date"))
    lengths = sorted(r["length"] / 1000 for r in recs if r.get("length"))
    titles = defaultdict(int)
    for r in recs:
        titles[r["title"]] += 1
    # The total count is only trustworthy if every returned recording really was this song.
    all_matched = len(recs) == len(data.get("recordings", []))
    return {
        "count": data.get("count", len(recs)) if all_matched else len(recs),
        "year": int(dates[0][:4]) if dates and dates[0][:4].isdigit() else None,
        "length": lengths[len(lengths) // 2] if lengths else None,
        "title": max(titles.items(), key=lambda kv: kv[1])[0] if titles else title,
    }


def best_match(mb, title, project_length=None, artists=None):
    """The most likely original recording of a title.

    1. Take the artists most associated with the title (see popularity()).
    2. Look up each one's own recordings of it for a true count, first release and length.
    3. Drop artists whose version is a very different length from the project, which
       separates unrelated songs that share a title.
    4. Of the artists with at least half as many recordings as the leader, the earliest
       release wins, so the original beats a popular cover.
    """
    artists = artists if artists is not None else popularity(mb, title)
    by_weight = sorted(artists, key=lambda a: -artists[a]["weight"])[:3]
    by_singles = sorted((a for a in artists if artists[a]["singles"]), key=lambda a: -artists[a]["singles"])[:3]
    contenders = list(dict.fromkeys(by_weight + by_singles))[:5]
    if not contenders or (artists[by_weight[0]]["weight"] < 4 and not by_singles):
        return None

    versions = {a: artist_versions(mb, title, a) for a in contenders}
    versions = {a: v for a, v in versions.items() if v["count"]}
    if not versions:
        return None
    if project_length:
        similar = {a: v for a, v in versions.items()
                   if v["length"] and 0.7 <= project_length / v["length"] <= 1.5}
        versions = similar or versions
    most = max(v["count"] for v in versions.values())
    leaders = [a for a, v in versions.items() if v["count"] >= most * 0.5]
    chosen = min(leaders, key=lambda a: (versions[a]["year"] or 9999, -versions[a]["count"]))
    v = versions[chosen]
    return {
        "title": v["title"],
        "artist": artists[chosen]["name"],
        "artist_id": chosen,
        "year": v["year"],
        "others": [artists[a]["name"] for a in contenders if a != chosen][:3],
    }


def title_strength(artists):
    """How strongly a candidate title matches something real on MusicBrainz."""
    return sum(a["weight"] + 5 * a["singles"] for a in artists.values())


# ---------- rules ----------

def guess_mood(year, genres, tempo):
    leading = set(genres[:LEADING])
    if tempo and tempo <= 80:
        return "Ballad", f"slow tempo ({tempo:.0f} BPM)"
    if leading & COUNTRY or (genres and genres[0] == "country rock"):
        return "Country", f"genre {(sorted(leading & COUNTRY) or genres)[0]}"
    if year and 1980 <= year <= 1989:
        return "80's", f"first released {year}"
    if leading & DANCE:
        return "Dance", f"genre {sorted(leading & DANCE)[0]}"
    if leading & GENTLE and (tempo is None or tempo <= 100):
        return "Ballad", f"genre {sorted(leading & GENTLE)[0]}" + (f" at {tempo:.0f} BPM" if tempo else "")
    if (year and year < 1980) or any("rock" in g for g in genres[:LEADING]):
        return "Classic Rock", f"first released {year}" if year and year < 1980 else "rock genre"
    return None, "no rule matched"


def guess_effort(genres):
    """Rough voice-effort estimate from genre alone; tempo is too unreliable (many projects use double time)."""
    demanding = set(genres[:5]) & DEMANDING
    gentle = set(genres[:LEADING]) & GENTLE
    if demanding and not gentle:
        return "High", f"genre {sorted(demanding)[0]}"
    if gentle and not demanding:
        return "Low", f"genre {sorted(gentle)[0]}"
    return "Medium", "no strong genre signal"


# ---------- classifying songs ----------

def load_overrides():
    """Optional metadata_overrides.json: {"Folder/Song.rpp": {"title", "artist", "mood", "effort", "hidden", "notes"}}."""
    if not os.path.exists(OVERRIDES_PATH):
        return {}
    with open(OVERRIDES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {k.replace("\\", "/").lower(): v for k, v in data.items() if k != "_comment"}


def classify(song, project_root, mb, overrides):
    """Return the suggested fields for one song (a row from the songs table)."""
    rel = song["rel_path"]
    o = overrides.get(rel.replace("\\", "/").lower())
    if o:
        return {"title": o.get("title"), "original_artist": o.get("artist"), "mood": o.get("mood"),
                "voice_effort": o.get("effort"), "hidden": o.get("hidden"), "notes": o.get("notes"),
                "auto_info": "From metadata_overrides.json"}

    hints = project_hints(os.path.join(project_root, rel))
    tempo = hints["tempo"]
    candidates = title_candidates(rel, hints["media"])
    # Use whichever title guess is best known on MusicBrainz.
    best_title, best_artists, best_strength = None, {}, 0
    for title in candidates:
        artists = popularity(mb, title)
        strength = title_strength(artists)
        if strength > best_strength:
            best_title, best_artists, best_strength = title, artists, strength
        if best_strength >= 150:
            break  # clear winner; skip the remaining guesses
    length = song.get("length_override") or song.get("detected_length")
    match = best_match(mb, best_title, length, best_artists) if best_title else None
    genres = mb.artist_genres(match["artist_id"]) if match else []
    year = match["year"] if match else None
    mood, mood_why = guess_mood(year, genres, tempo)
    effort, effort_why = guess_effort(genres)

    if match:
        info = f'MusicBrainz: "{match["title"]}" by {match["artist"]}' + (f" ({year})" if year else "")
        if match["others"]:
            info += f'; other versions by {", ".join(match["others"])}'
    else:
        info = f"No MusicBrainz match for {', '.join(repr(c) for c in candidates) or 'this project'}"
    info += (f". Genres: {', '.join(genres[:5])}" if genres else "") + (f". Tempo {tempo:.0f} BPM" if tempo else "")
    info += f". Mood: {mood_why}. Effort: {effort_why}."

    return {
        "title": match["title"] if match else (candidates[0] if candidates else None),
        "original_artist": match["artist"] if match else None,
        "mood": mood,
        "voice_effort": effort if match else None,
        "hidden": True if DUPLICATE.search(os.path.splitext(os.path.basename(rel))[0]) else None,
        "notes": None,
        "auto_info": info,
    }


def changes_for(song, suggestion, force):
    """The columns to update: empty fields only, unless force."""
    out = {}
    empty = {
        "title": song["title"] == default_title(song["rel_path"]),
        "original_artist": not song["original_artist"],
        "mood": song["mood"] is None,
        "voice_effort": song["voice_effort"] is None,
        "hidden": song["auto_info"] is None and not song["hidden"],
        "notes": not song["notes"],
    }
    for field, is_empty in empty.items():
        value = suggestion.get(field)
        if value not in (None, "") and (force or is_empty):
            out[field] = (1 if value else 0) if field == "hidden" else value
    out["auto_info"] = suggestion["auto_info"]
    return out


def songs_to_classify(conn, force=False, ids=None):
    sql = "SELECT * FROM songs WHERE missing = 0"
    if ids:
        sql += f" AND id IN ({','.join(str(int(i)) for i in ids)})"
    elif not force:
        sql += " AND (original_artist = '' OR mood IS NULL OR voice_effort IS NULL)"
    return [dict(r) for r in conn.execute(sql + " ORDER BY title COLLATE NOCASE")]


def run(force=False, dry_run=False, ids=None, progress=None):
    """Classify songs in the database. progress(done, total, title) is called before each song."""
    cfg = config.load()
    mb = MusicBrainz(cfg.get("musicbrainz_contact", ""))
    overrides = load_overrides()
    with db.session() as conn:
        songs = songs_to_classify(conn, force, ids)
    results = []
    try:
        for i, song in enumerate(songs):
            if progress:
                progress(i, len(songs), song["title"])
            try:
                suggestion = classify(song, cfg["project_root"], mb, overrides)
            except (ConnectionError, urllib.error.URLError) as e:
                results.append({"id": song["id"], "title": song["title"], "error": str(e)})
                continue
            changes = changes_for(song, suggestion, force)
            if not dry_run:
                with db.session() as conn:
                    conn.execute(f"UPDATE songs SET {', '.join(k + ' = ?' for k in changes)} WHERE id = ?",
                                 (*changes.values(), song["id"]))
            results.append({"id": song["id"], "title": song["title"], "changes": changes})
            mb.save_cache()
    finally:
        mb.save_cache()
    if progress:
        progress(len(songs), len(songs), None)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-classify every song and overwrite its fields")
    parser.add_argument("--dry-run", action="store_true", help="show suggestions without saving them")
    args = parser.parse_args()

    db.init()
    progress = lambda done, total, title: title and print(f"[{done + 1}/{total}] {title}", flush=True)
    for r in run(force=args.force, dry_run=args.dry_run, progress=progress):
        if "error" in r:
            print(f"  ! {r['title']}: {r['error']}")
            continue
        fields = {k: v for k, v in r["changes"].items() if k != "auto_info"}
        print(f"  {r['title']}: {fields or 'no changes'}\n    {r['changes']['auto_info']}")


if __name__ == "__main__":
    main()
