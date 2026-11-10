"""App settings stored as JSON under %APPDATA%\\MdCal."""
import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "MdCal"
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "mdcal.db"
LOCK_PATH = APP_DIR / "mdcal.lock"

DEFAULT_VAULT = r"C:\Obsidian\업무"
DEFAULT_MEMO_VAULT = r"C:\Obsidian\해야할거"


def _defaults():
    return {
        "md_folders": [DEFAULT_VAULT] if Path(DEFAULT_VAULT).is_dir() else [],
        # calendar memos are saved here as YYMMDD.md (also scanned)
        "memo_folder": DEFAULT_MEMO_VAULT if Path(DEFAULT_MEMO_VAULT).is_dir() else None,
        "geometry": None,          # [x, y, w, h]; None = auto place
        "opacity": 0.80,           # background alpha only, text stays opaque
        "week_starts_sunday": True,
        "locked": False,           # disable move/resize
        "carry_mode": "manual",   # auto, manual, or off
        "carry_lookback_days": 14, # past days checked for open tasks
        "last_carry": None,        # last successful auto carry, ISO date
        "peek_hotkey": "ctrl+alt+c",  # lifts the calendar above all windows; "" disables
        "pin_debug": False,        # log every foreground/desktop state change to pin.log
    }


def load_config():
    """Return (config, warning). A broken file is kept as .bad and defaults are used."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    cfg = _defaults()
    if not CONFIG_PATH.exists():
        return cfg, None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config root is not an object")
    except (OSError, ValueError) as e:
        bad = CONFIG_PATH.with_suffix(".json.bad")
        os.replace(CONFIG_PATH, bad)
        return cfg, f"설정 파일을 읽지 못해 기본값으로 시작합니다.\n{e}\n백업: {bad}"
    cfg.update({k: v for k, v in data.items() if k in cfg})
    return cfg, None


def update_config(cfg, key, value):
    """Set one key and save. On failure the old value is restored and OSError is re-raised."""
    missing = object()
    old = cfg.get(key, missing)
    cfg[key] = value
    try:
        save_config(cfg)
    except OSError:
        if old is missing:
            del cfg[key]
        else:
            cfg[key] = old
        raise


def save_config(cfg):
    """Atomic write: tmp file then replace."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
