"""App settings, stored in config.json next to this file (edit them from Settings in the app)."""
import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    # Folder scanned (recursively) for .rpp projects.
    "project_root": "",
    # Folder where .rpl setlists are read from and written to.
    "setlist_dir": "",
    # Root used for the paths written *inside* .rpl files. Leave blank to use
    # project_root. Set it when REAPER runs on a machine where the same folder
    # has a different path, e.g. a mapped drive such as V:\Music\Projects
    "rpl_path_root": "",
    "port": 8765,
    # Contact (email or URL) sent to MusicBrainz in the User-Agent, as their API asks.
    "musicbrainz_contact": "",
}


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def is_configured(cfg):
    return bool(cfg["project_root"]) and os.path.isdir(cfg["project_root"]) and bool(cfg["setlist_dir"])


def save(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
