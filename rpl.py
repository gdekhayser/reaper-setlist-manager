"""Reading and writing REAPER project lists (.rpl): one full .rpp path per line."""
import os
import re
import shutil
from datetime import datetime

from config import APP_DIR

BACKUP_DIR = os.path.join(APP_DIR, "rpl_backups")


def read(path):
    with open(path, "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def render(project_paths):
    return "".join(p + "\r\n" for p in project_paths)


def backup(path):
    """Copy an existing .rpl into rpl_backups/ before it is overwritten or removed."""
    if not os.path.exists(path):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    dest = os.path.join(BACKUP_DIR, f"{stem}.{datetime.now():%Y%m%d-%H%M%S}{ext}")
    shutil.copy2(path, dest)
    return dest


def write(path, project_paths):
    content = render(project_paths)
    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == content.encode("utf-8"):
                return
        backup(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def safe_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "setlist"


def match_song(line, songs):
    """Find the song a .rpl line refers to, whatever drive or root it was written with.

    songs: list of rows with id and rel_path. Tries, in order: the path ends
    with the song's relative path; then the file name; then the file stem
    equals the song's folder name (old entries like "PianoVoiceGuitar_1.rpp"
    that have since moved into a folder of that name).
    """
    norm = line.replace("/", "\\").lower()
    best = None
    for s in songs:
        rel = s["rel_path"].lower()
        if norm == rel or norm.endswith("\\" + rel):
            if best is None or len(rel) > len(best["rel_path"]):
                best = s
    if best:
        return best["id"]

    def single(candidates):
        if len(candidates) > 1:
            candidates = [s for s in candidates if not s.get("hidden")]
        return candidates[0]["id"] if len(candidates) == 1 else None

    base = os.path.basename(norm)
    found = single([s for s in songs if os.path.basename(s["rel_path"]).lower() == base])
    if found:
        return found

    stem = os.path.splitext(base)[0]
    return single([s for s in songs if s["rel_path"].lower().split("\\", 1)[0] == stem])
