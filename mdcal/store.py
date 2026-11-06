"""Day colors (SQLite). Memos are md files (see memofile.py)."""
import sqlite3
from datetime import date


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS memo (day TEXT PRIMARY KEY, text TEXT NOT NULL, updated TEXT NOT NULL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS daycolor (day TEXT PRIMARY KEY, color TEXT NOT NULL)")
        self.conn.commit()

    def colors(self, first: date, last: date):
        """{day: color} for first..last inclusive."""
        return {date.fromisoformat(d): c for d, c in self.conn.execute(
            "SELECT day, color FROM daycolor WHERE day BETWEEN ? AND ?", (first.isoformat(), last.isoformat()))}

    # Legacy memo table: memos now live in md files. Kept only for one-time migration.
    def legacy_memos(self):
        return [(date.fromisoformat(d), t) for d, t in self.conn.execute("SELECT day, text FROM memo ORDER BY day")]

    def delete_legacy_memo(self, day: date):
        self.conn.execute("DELETE FROM memo WHERE day=?", (day.isoformat(),))
        self.conn.commit()

    def set_color(self, day: date, color):
        if color:
            self.conn.execute(
                "INSERT INTO daycolor(day, color) VALUES(?,?) "
                "ON CONFLICT(day) DO UPDATE SET color=excluded.color", (day.isoformat(), color))
        else:
            self.conn.execute("DELETE FROM daycolor WHERE day=?", (day.isoformat(),))
        self.conn.commit()

    def close(self):
        self.conn.close()
