"""Reaper Setlist Manager: local web app. Run `python server.py`, then open http://localhost:8765/."""
import json
import mimetypes
import os
import re
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import classifier
import config
import db
import rpl
import scanner

STATIC_DIR = os.path.join(config.APP_DIR, "static")
ROUTES = []
SONG_FIELDS = ("title", "original_artist", "mood", "voice_effort", "length_override", "hidden", "notes")


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def route(method, pattern):
    def register(fn):
        ROUTES.append((method, re.compile(pattern + r"\Z"), fn))
        return fn
    return register


# ---------- queries ----------

def song_rows(conn):
    rows = conn.execute(
        """
        SELECT s.*, COALESCE(s.length_override, s.detected_length) AS length,
               (SELECT json_group_array(DISTINCT sl.name) FROM setlist_songs ss
                  JOIN setlists sl ON sl.id = ss.setlist_id WHERE ss.song_id = s.id) AS setlists_json
        FROM songs s ORDER BY s.title COLLATE NOCASE
        """
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["setlists"] = json.loads(d.pop("setlists_json") or "[]")
        d["hidden"] = bool(d["hidden"])
        d["missing"] = bool(d["missing"])
        out.append(d)
    return out


def setlist_summary(conn, sid=None):
    sql = """
        SELECT sl.*, COUNT(ss.song_id) AS song_count,
               COALESCE(SUM(COALESCE(s.length_override, s.detected_length)), 0) AS total_seconds
        FROM setlists sl
        LEFT JOIN setlist_songs ss ON ss.setlist_id = sl.id
        LEFT JOIN songs s ON s.id = ss.song_id
        {where}
        GROUP BY sl.id ORDER BY sl.name COLLATE NOCASE
    """
    if sid is None:
        return [dict(r) for r in conn.execute(sql.format(where=""))]
    row = conn.execute(sql.format(where="WHERE sl.id = ?"), (sid,)).fetchone()
    if not row:
        raise ApiError("Setlist not found", 404)
    d = dict(row)
    d["song_ids"] = [r[0] for r in conn.execute(
        "SELECT song_id FROM setlist_songs WHERE setlist_id = ? ORDER BY position", (sid,))]
    return d


def rpl_lines(conn, cfg, sid):
    root = cfg.get("rpl_path_root") or cfg["project_root"]
    rows = conn.execute(
        "SELECT s.rel_path FROM setlist_songs ss JOIN songs s ON s.id = ss.song_id"
        " WHERE ss.setlist_id = ? ORDER BY ss.position", (sid,))
    return [root.rstrip("\\/") + "\\" + r[0] for r in rows]


def validate_setlist(conn, data, sid=None):
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Setlist name is required")
    clash = conn.execute("SELECT id FROM setlists WHERE name = ? AND id IS NOT ?", (name, sid)).fetchone()
    if clash:
        raise ApiError(f'A setlist named "{name}" already exists')
    try:
        target = float(data.get("target_minutes") or 0)
    except (TypeError, ValueError):
        raise ApiError("Target time must be a number of minutes")
    if target < 0:
        raise ApiError("Target time can't be negative")
    mood = data.get("mood") or None
    if mood and mood not in db.MOODS:
        raise ApiError(f"Unknown mood: {mood}")
    song_ids = [int(x) for x in data.get("song_ids") or []]
    if song_ids:
        known = {r[0] for r in conn.execute(
            f"SELECT id FROM songs WHERE id IN ({','.join('?' * len(set(song_ids)))})", list(set(song_ids)))}
        unknown = set(song_ids) - known
        if unknown:
            raise ApiError(f"Unknown song ids: {sorted(unknown)}")
    return name, target, mood, (data.get("notes") or "").strip(), song_ids


def save_setlist(conn, cfg, data, sid=None):
    """Create or update a setlist in the database, then write its .rpl file."""
    name, target, mood, notes, song_ids = validate_setlist(conn, data, sid)
    new_file = os.path.join(cfg["setlist_dir"], rpl.safe_filename(name) + ".RPL")

    if sid is None:
        cur = conn.execute(
            "INSERT INTO setlists (name, target_minutes, mood, notes, rpl_file) VALUES (?, ?, ?, ?, ?)",
            (name, target, mood, notes, new_file))
        sid = cur.lastrowid
        old_file = None
    else:
        row = conn.execute("SELECT name, rpl_file FROM setlists WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise ApiError("Setlist not found", 404)
        old_file = row["rpl_file"]
        # Keep writing to the original file (e.g. an imported SET1-BR.RPL) unless renamed.
        target_file = old_file if (old_file and row["name"] == name) else new_file
        conn.execute(
            "UPDATE setlists SET name = ?, target_minutes = ?, mood = ?, notes = ?, rpl_file = ?,"
            " updated_at = datetime('now', 'localtime') WHERE id = ?",
            (name, target, mood, notes, target_file, sid))
        new_file = target_file

    conn.execute("DELETE FROM setlist_songs WHERE setlist_id = ?", (sid,))
    conn.executemany(
        "INSERT INTO setlist_songs (setlist_id, position, song_id) VALUES (?, ?, ?)",
        [(sid, i, song) for i, song in enumerate(song_ids)])

    os.makedirs(cfg["setlist_dir"], exist_ok=True)
    rpl.write(new_file, rpl_lines(conn, cfg, sid))
    if old_file and os.path.normcase(old_file) != os.path.normcase(new_file) and os.path.exists(old_file):
        rpl.backup(old_file)
        os.remove(old_file)

    result = setlist_summary(conn, sid)
    result["written_to"] = new_file
    return result


# ---------- API ----------

@route("GET", r"/api/meta")
def get_meta(h):
    cfg = config.load()
    h.send_json({"moods": db.MOODS, "efforts": db.EFFORTS, "config": cfg, "configured": config.is_configured(cfg)})


@route("PUT", r"/api/config")
def put_config(h):
    data = h.read_json()
    cfg = config.load()
    for key in ("project_root", "setlist_dir", "rpl_path_root", "musicbrainz_contact"):
        if key in data:
            cfg[key] = (data[key] or "").strip()
    if not os.path.isdir(cfg["project_root"]):
        raise ApiError(f"Project folder not found: {cfg['project_root']}")
    if not os.path.isdir(cfg["setlist_dir"]):
        raise ApiError(f"Setlist folder not found: {cfg['setlist_dir']}")
    config.save(cfg)
    h.send_json(cfg)


@route("GET", r"/api/songs")
def get_songs(h):
    with db.session() as conn:
        h.send_json(song_rows(conn))


@route("PATCH", r"/api/songs/(\d+)")
def patch_song(h, song_id):
    data = h.read_json()
    updates = {}
    for key, value in data.items():
        if key not in SONG_FIELDS:
            raise ApiError(f"Field can't be edited: {key}")
        if key == "mood" and value not in (None, "", *db.MOODS):
            raise ApiError(f"Unknown mood: {value}")
        if key == "voice_effort" and value not in (None, "", *db.EFFORTS):
            raise ApiError(f"Unknown voice effort: {value}")
        if key == "title" and not (value or "").strip():
            raise ApiError("Title can't be empty")
        if key == "length_override" and value not in (None, ""):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ApiError("Length must be a number of seconds")
            if value <= 0:
                raise ApiError("Length must be greater than zero")
        if key in ("mood", "voice_effort", "length_override") and value == "":
            value = None
        if key == "hidden":
            value = 1 if value else 0
        if isinstance(value, str):
            value = value.strip()
        updates[key] = value
    if not updates:
        raise ApiError("Nothing to update")
    with db.session() as conn:
        cur = conn.execute(
            f"UPDATE songs SET {', '.join(k + ' = ?' for k in updates)} WHERE id = ?",
            (*updates.values(), int(song_id)))
        if cur.rowcount == 0:
            raise ApiError("Song not found", 404)
        song = next(s for s in song_rows(conn) if s["id"] == int(song_id))
    h.send_json(song)


@route("POST", r"/api/scan")
def post_scan(h):
    cfg = config.load()
    with db.session() as conn:
        try:
            stats = scanner.scan(conn, cfg["project_root"])
        except FileNotFoundError as e:
            raise ApiError(str(e))
    h.send_json(stats)


classify_job = {"running": False, "done": 0, "total": 0, "current": None, "changed": 0, "errors": [], "error": None}
classify_lock = threading.Lock()


def run_classify(force, ids):
    def progress(done, total, title):
        classify_job.update(done=done, total=total, current=title)
    try:
        results = classifier.run(force=force, ids=ids, progress=progress)
        classify_job["changed"] = sum(1 for r in results if len(r.get("changes", {})) > 1)
        classify_job["errors"] = [f"{r['title']}: {r['error']}" for r in results if "error" in r]
    except Exception as e:
        traceback.print_exc()
        classify_job["error"] = str(e)
    finally:
        classify_job["running"] = False


@route("POST", r"/api/classify")
def post_classify(h):
    """Start classifier.py in the background; poll GET /api/classify for progress."""
    data = h.read_json()
    if not config.is_configured(config.load()):
        raise ApiError("Set the project folder in Settings first")
    with classify_lock:
        if classify_job["running"]:
            raise ApiError("Auto-classify is already running", 409)
        classify_job.update(running=True, done=0, total=0, current=None, changed=0, errors=[], error=None)
    threading.Thread(target=run_classify, args=(bool(data.get("force")), data.get("ids")), daemon=True).start()
    h.send_json(classify_job)


@route("GET", r"/api/classify")
def get_classify(h):
    h.send_json(classify_job)


@route("GET", r"/api/setlists")
def get_setlists(h):
    with db.session() as conn:
        h.send_json(setlist_summary(conn))


@route("POST", r"/api/setlists")
def post_setlist(h):
    data = h.read_json()
    with db.session() as conn:
        h.send_json(save_setlist(conn, config.load(), data), 201)


@route("GET", r"/api/setlists/(\d+)")
def get_setlist(h, sid):
    with db.session() as conn:
        h.send_json(setlist_summary(conn, int(sid)))


@route("PUT", r"/api/setlists/(\d+)")
def put_setlist(h, sid):
    data = h.read_json()
    with db.session() as conn:
        h.send_json(save_setlist(conn, config.load(), data, int(sid)))


@route("DELETE", r"/api/setlists/(\d+)")
def delete_setlist(h, sid):
    delete_file = parse_qs(urlparse(h.path).query).get("delete_file") == ["1"]
    with db.session() as conn:
        row = conn.execute("SELECT rpl_file FROM setlists WHERE id = ?", (int(sid),)).fetchone()
        if not row:
            raise ApiError("Setlist not found", 404)
        conn.execute("DELETE FROM setlists WHERE id = ?", (int(sid),))
    backup = None
    if delete_file and row["rpl_file"] and os.path.exists(row["rpl_file"]):
        backup = rpl.backup(row["rpl_file"])
        os.remove(row["rpl_file"])
    h.send_json({"deleted": True, "file_removed": bool(backup), "backup": backup})


@route("GET", r"/api/setlists/(\d+)/rpl")
def download_rpl(h, sid):
    with db.session() as conn:
        s = setlist_summary(conn, int(sid))
        content = rpl.render(rpl_lines(conn, config.load(), int(sid))).encode("utf-8")
    filename = rpl.safe_filename(s["name"]) + ".RPL"
    h.send(200, content, "application/octet-stream",
           {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@route("GET", r"/api/rpl-files")
def get_rpl_files(h):
    cfg = config.load()
    folder = cfg["setlist_dir"]
    if not os.path.isdir(folder):
        raise ApiError(f"Setlist folder not found: {folder}")
    with db.session() as conn:
        linked = {os.path.normcase(r[0]) for r in conn.execute(
            "SELECT rpl_file FROM setlists WHERE rpl_file IS NOT NULL")}
    files = []
    for name in sorted(os.listdir(folder), key=str.lower):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.lower().endswith(".rpl"):
            files.append({"name": name, "entries": len(rpl.read(path)),
                          "imported": os.path.normcase(path) in linked})
    h.send_json(files)


@route("POST", r"/api/import")
def post_import(h):
    """Import .rpl files from the setlist folder as setlists in the database."""
    cfg = config.load()
    names = h.read_json().get("files") or []
    results = []
    with db.session() as conn:
        songs = [dict(r) for r in conn.execute("SELECT id, rel_path, hidden FROM songs")]
        lengths = {r[0]: r[1] or 0 for r in conn.execute(
            "SELECT id, COALESCE(length_override, detected_length) FROM songs")}
        for name in names:
            path = os.path.join(cfg["setlist_dir"], os.path.basename(name))
            if not os.path.isfile(path):
                results.append({"file": name, "error": "File not found"})
                continue
            if conn.execute("SELECT 1 FROM setlists WHERE rpl_file = ? COLLATE NOCASE", (path,)).fetchone():
                results.append({"file": name, "error": "Already imported"})
                continue
            set_name = os.path.splitext(os.path.basename(name))[0]
            base, n = set_name, 2
            while conn.execute("SELECT 1 FROM setlists WHERE name = ?", (set_name,)).fetchone():
                set_name, n = f"{base} ({n})", n + 1
            song_ids, unmatched = [], []
            for line in rpl.read(path):
                sid = rpl.match_song(line, songs)
                (song_ids.append(sid) if sid else unmatched.append(line))
            total = sum(lengths.get(s, 0) for s in song_ids)
            cur = conn.execute(
                "INSERT INTO setlists (name, target_minutes, mood, notes, rpl_file) VALUES (?, ?, NULL, ?, ?)",
                (set_name, max(5, round(total / 60)),
                 ("Not found on import:\n" + "\n".join(unmatched)) if unmatched else "", path))
            conn.executemany(
                "INSERT INTO setlist_songs (setlist_id, position, song_id) VALUES (?, ?, ?)",
                [(cur.lastrowid, i, s) for i, s in enumerate(song_ids)])
            results.append({"file": name, "setlist_id": cur.lastrowid, "name": set_name,
                            "matched": len(song_ids), "unmatched": unmatched})
    h.send_json(results)


# ---------- HTTP plumbing ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "SetlistManager/1.0"

    def log_request(self, code="-", size="-"):
        # Only log failed requests.
        if str(code).isdigit() and int(code) >= 400:
            super().log_request(code, size)

    def send(self, code, body, ctype, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise ApiError("Request body is not valid JSON")

    def serve_static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
            return self.send_json({"error": "Not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self.send(200, f.read(), ctype)

    def dispatch(self, method):
        path = urlparse(self.path).path
        try:
            if method != "GET":
                # Only accept writes from this app's own pages, not other websites.
                origin = self.headers.get("Origin")
                if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1"):
                    raise ApiError("Cross-origin request refused", 403)
            for m, pattern, fn in ROUTES:
                match = pattern.match(path)
                if m == method and match:
                    return fn(self, *match.groups())
            if method == "GET" and not path.startswith("/api/"):
                return self.serve_static(path)
            raise ApiError("Not found", 404)
        except ApiError as e:
            self.send_json({"error": str(e)}, e.code)
        except OSError as e:
            traceback.print_exc()
            self.send_json({"error": f"File error: {e}"}, 500)
        except Exception as e:
            traceback.print_exc()
            self.send_json({"error": f"Server error: {e}"}, 500)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")


def main():
    cfg = config.load()
    db.init()
    if not config.is_configured(cfg):
        print("No project folder set yet: choose one in Settings when the app opens.")
    else:
        with db.session() as conn:
            if conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 0:
                print(f"First run: scanning {cfg['project_root']} ...")
                stats = scanner.scan(conn, cfg["project_root"])
                print(f"  {stats['added']} projects added. Use Auto-classify in the Song library to fill in artists and moods.")
    port = int(cfg.get("port", 8765))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}/"
    print(f"Setlist Manager running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
