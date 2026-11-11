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
        "export_folder": None,     # where the last period summary was saved
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
    bad = []
    for k, v in data.items():
        if k not in cfg:
            continue
        if _valid(k, v):
            cfg[k] = v
        else:
            bad.append(f"{k}: {json.dumps(v, ensure_ascii=False)[:60]}")
    if bad:
        return cfg, ("설정 파일의 다음 값이 올바르지 않아 기본값을 사용합니다 "
                     "(다음 설정 저장 때 파일에서도 기본값으로 바뀜).\n" + "\n".join(bad))
    return cfg, None


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _valid(key, v) -> bool:
    """A hand-edited config.json must not reach paint or timer code with a wrong type."""
    if key == "md_folders":
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if key in ("memo_folder", "last_carry", "export_folder"):
        return v is None or isinstance(v, str)
    if key == "geometry":
        return v is None or (isinstance(v, list) and len(v) == 4
                             and all(isinstance(x, int) and not isinstance(x, bool) for x in v))
    if key == "opacity":
        return _number(v) and 0 <= v <= 1
    if key in ("week_starts_sunday", "locked", "pin_debug"):
        return isinstance(v, bool)
    if key == "carry_mode":
        return v in ("auto", "manual", "off")
    if key == "carry_lookback_days":
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0
    if key == "peek_hotkey":
        return isinstance(v, str)
    return True


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
