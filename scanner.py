"""Find .rpp projects under project_root and record them in the database.

Scanning only records each project and its length. Artist, mood and voice effort
are filled in by classifier.py.
"""
import os
from datetime import datetime


def find_projects(root):
    """Yield absolute paths of .rpp files under root. Skips .rpp-bak and other variants."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if os.path.splitext(name)[1].lower() == ".rpp":
                yield os.path.join(dirpath, name)


def project_length(path):
    """Song length in seconds: the end of the last media item in the project.

    Reads POSITION and LENGTH from each top-level <ITEM> block. Returns None
    when the project has no items.
    """
    stack = []
    end = 0.0
    pos = length = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("<"):
                tag = line[1:].split(" ", 1)[0].upper()
                stack.append(tag)
                if tag == "ITEM":
                    pos = length = None
            elif line == ">":
                if stack and stack.pop() == "ITEM" and pos is not None and length is not None:
                    end = max(end, pos + length)
            elif stack and stack[-1] == "ITEM":
                key, _, value = line.partition(" ")
                if key in ("POSITION", "LENGTH"):
                    try:
                        number = float(value.split()[0])
                    except (ValueError, IndexError):
                        continue
                    if key == "POSITION":
                        pos = number
                    else:
                        length = number
    return round(end, 2) if end > 0 else None


def default_title(rel_path):
    return os.path.splitext(os.path.basename(rel_path))[0].replace("_", " ").strip()


def scan(conn, root):
    """Add new projects, refresh lengths of changed ones, flag ones that disappeared."""
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Project folder not found: {root}")

    now =datetime.now().isoformat(timespec="seconds")
    stats = {"found": 0, "added": 0, "updated": 0, "missing": 0, "errors": []}
    existing = {row["rel_path"].lower(): row for row in conn.execute("SELECT * FROM songs")}
    seen = set()

    for path in find_projects(root):
        rel = os.path.relpath(path, root)
        seen.add(rel.lower())
        stats["found"] += 1
        mtime = os.path.getmtime(path)
        row = existing.get(rel.lower())

        if row and row["file_mtime"] == mtime:
            if row["missing"]:
                conn.execute("UPDATE songs SET missing = 0 WHERE id = ?", (row["id"],))
            continue

        try:
            length = project_length(path)
        except OSError as e:
            stats["errors"].append(f"{rel}: {e}")
            length = None

        if row:
            conn.execute(
                "UPDATE songs SET detected_length = ?, file_mtime = ?, last_scanned = ?, missing = 0,"
                " rel_path = ? WHERE id = ?",
                (length, mtime, now, rel, row["id"]),
            )
            stats["updated"] += 1
        else:
            conn.execute(
                "INSERT INTO songs (rel_path, title, detected_length, file_mtime, last_scanned)"
                " VALUES (?, ?, ?, ?, ?)",
                (rel, default_title(rel), length, mtime, now),
            )
            stats["added"] += 1

    for key, row in existing.items():
        if key not in seen and not row["missing"]:
            conn.execute("UPDATE songs SET missing = 1 WHERE id = ?", (row["id"],))
            stats["missing"] += 1

    return stats
