# Reaper Setlist Manager

A small local web app for musicians who play live with [REAPER](https://www.reaper.fm/). It builds
setlists from your song projects and saves them as REAPER project lists (`.rpl`), ready to load
with **File → Project lists**.

- Scans a folder of `.rpp` song projects into a local SQLite database, with each song's length
  read from the project.
- Fills in each song's original artist from [MusicBrainz](https://musicbrainz.org/) and estimates
  a mood (Ballad, Classic Rock, 80's, Country, Dance) and voice effort (Low, Medium, High).
  You can correct anything in the Song library.
- Builds setlists against a target time and mood: running time, voice-effort balance, warnings for
  too many hard songs in a row, drag-and-drop ordering, and auto-fill to the target time.
- Imports your existing `.rpl` files.

## Requirements

Python 3.10 or newer. There are no packages to install. It runs on Windows, macOS and Linux,
although `.rpl` files are written with Windows paths and line endings.

## Getting started

```
git clone https://github.com/gdekhayser/reaper-setlist-manager.git
cd reaper-setlist-manager
python server.py
```

On Windows you can double-click **Start Setlist Manager.bat** instead.

The app opens at http://localhost:8765/. On first run it asks for:

- **Project folder**: where your song projects live. Subfolders are included; `.rpp-bak` backups are skipped.
- **Setlist folder**: where `.rpl` files are read and written.
- **Path written inside .rpl files** (optional): use this if REAPER runs on a machine that reaches
  the same projects through a different path, such as a mapped drive. Each entry is written as this
  folder plus the song's path inside the project folder.

Then open **Song library** and click **Auto-classify**.

## How songs are classified

`classifier.py` runs from the **Auto-classify** button, or from the command line:

```
python classifier.py              # fill in songs with a missing artist, mood or voice effort
python classifier.py --dry-run    # show suggestions without saving
python classifier.py --force      # re-classify every song, overwriting its fields
```

For each project it:

1. **Guesses the title** from the file name, the folder name and the names of audio files in the
   project. `IsSheReallyGoing_1.rpp` becomes "Is She Really Going".
2. **Finds the original artist** on MusicBrainz. It searches for recordings with that title and
   picks the artist who recorded it most often (counting live versions, compilations and reissues).
   When several artists are close, the earliest release wins, so the original beats a well-known cover.
3. **Picks a mood** with simple rules, in this order:
   - Ballad if the project tempo is 80 BPM or slower
   - Country for country-family genres
   - 80's for songs first released 1980–1989
   - Dance for disco, funk and dance genres
   - Ballad for slow, gentle genres
   - Classic Rock for anything earlier than 1980 or tagged rock
4. **Estimates voice effort** from genres and tempo. Hard rock, prog and soul push it up; folk,
   soft rock and slow tempos bring it down. This is the roughest guess, so check it.

Only empty fields are filled in, so your edits are never overwritten (except with `--force`).
Hover over an artist in the Song library to see how the classifier decided. Your own songs won't be
on MusicBrainz, and songs with common titles can match the wrong artist, so check those.

To skip the lookup for songs you already know, copy `metadata_overrides.example.json` to
`metadata_overrides.json` and list them there.

MusicBrainz lookups are limited to one request per second and cached in `musicbrainz_cache.json`,
so a first run over 70 songs takes a few minutes and later runs are fast. MusicBrainz asks API
users to identify themselves; you can set `musicbrainz_contact` in `config.json` to your email
or project URL.

## Building setlists

- **New** creates a setlist with a name, a target time in minutes and a mood (or Mixed).
- Add songs from the panel on the right. It shows the set's mood by default, and can hide songs
  already used in other setlists. **Auto-fill** adds matching songs up to the target time and
  avoids three high-effort songs in a row.
- Drag rows to reorder. The editor shows running and total time, how far you are over or under the
  target, the voice-effort mix, and warnings.
- **Save & write .rpl** saves to the database and writes `<setlist folder>/<name>.RPL`.
  Renaming a setlist renames its file.
- **Import .rpl files** turns existing project lists into setlists. Entries are matched by their
  path below the project folder, so lists written with a different drive letter still match.
  Entries that can't be found are listed in the setlist's notes.

Before the app overwrites or deletes an `.rpl` file, it copies it to `rpl_backups/`.

## Files

| File | Purpose |
| --- | --- |
| `server.py` | Web server and JSON API |
| `scanner.py` | Finds `.rpp` projects and reads their length |
| `classifier.py` | Artist lookup and mood / voice-effort rules |
| `rpl.py` | Reads, writes and matches `.rpl` files |
| `db.py` | SQLite schema |
| `config.py` | Settings (stored in `config.json`) |
| `static/` | The web interface |

Your data stays on your computer: `setlists.db`, `config.json`, `metadata_overrides.json`,
`musicbrainz_cache.json` and `rpl_backups/` are listed in `.gitignore`. The only network requests
are the MusicBrainz lookups.

## License

MIT. Artist data comes from MusicBrainz, which is available under CC0.
