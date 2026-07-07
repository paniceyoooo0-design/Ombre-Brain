"""diary/db.py

我们日记本的数据层。SQLite 单文件，和 Ombre 的 buckets/ 同级。

v0.2 schema:
  diary_blocks(id, date, author, created_at, content, mood,
               starred, grown_at, promoted_bucket_id)
  index: idx_date(date), idx_pending(grown_at, date)
"""
from __future__ import annotations

import os
import sqlite3
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ---------- 配置 ----------

# 默认走 Zeabur Volume；本地跑用环境变量覆盖
DB_PATH = Path(os.getenv("DIARY_DB_PATH", "/data/diary.db"))

# 🐙 那边时区（UTC+8）
TZ_OCTOPUS = timezone(timedelta(hours=8))

# grow 冷却期：当天和昨天不算，>=3 天前才进视野
GROW_COOLDOWN_DAYS = 3


# ---------- 工具 ----------

def _new_id() -> str:
    """短 hash，例 '4a7b9c2e'"""
    return secrets.token_hex(4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_octopus() -> str:
    """🐙 那边时区的'今天'"""
    return datetime.now(TZ_OCTOPUS).strftime("%Y-%m-%d")


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": r["id"],
        "date": r["date"],
        "author": r["author"],
        "created_at": r["created_at"],
        "content": r["content"],
        "mood": r["mood"],
        "starred": bool(r["starred"]),
        "reviewed_at": r["reviewed_at"],
        "grown_at": r["grown_at"],
        "promoted_bucket_id": r["promoted_bucket_id"],
    }


# ---------- 建表 ----------

def init_db() -> None:
    """server.py 启动时调一次。"""
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS diary_blocks (
                id                  TEXT PRIMARY KEY,
                date                TEXT NOT NULL,
                author              TEXT NOT NULL
                                    CHECK(author IN ('octopus', 'claude')),
                created_at          TEXT NOT NULL,
                content             TEXT NOT NULL,
                mood                TEXT,
                starred             INTEGER NOT NULL DEFAULT 0,
                reviewed_at         TEXT,
                grown_at            TEXT,
                promoted_bucket_id  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_date
                ON diary_blocks(date);
            CREATE INDEX IF NOT EXISTS idx_pending
                ON diary_blocks(grown_at, date);
        """)


# ---------- 写 ----------

def create_block(
    content: str,
    author: str,
    mood: str | None = None,
    date: str | None = None,
) -> dict:
    if author not in ("octopus", "claude"):
        raise ValueError(f"unknown author: {author}")
    bid = _new_id()
    now = _now_iso()
    d = date or _today_octopus()
    with _conn() as c:
        c.execute(
            """INSERT INTO diary_blocks
               (id, date, author, created_at, content, mood, starred)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (bid, d, author, now, content, mood),
        )
        row = c.execute(
            "SELECT * FROM diary_blocks WHERE id = ?", (bid,)
        ).fetchone()
    return _row(row)  # type: ignore[return-value]


def update_block(block_id: str, **fields) -> dict | None:
    """仅允许改 content / mood / starred。其他字段（grown_at 等）走专用方法。"""
    allowed = {"content", "mood", "starred"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    if not payload:
        return get_block(block_id)
    # starred 强制成 0/1
    if "starred" in payload:
        payload["starred"] = 1 if payload["starred"] else 0
    sets = ", ".join(f"{k} = ?" for k in payload)
    values = list(payload.values()) + [block_id]
    with _conn() as c:
        c.execute(f"UPDATE diary_blocks SET {sets} WHERE id = ?", values)
        row = c.execute(
            "SELECT * FROM diary_blocks WHERE id = ?", (block_id,)
        ).fetchone()
    return _row(row)


def mark_reviewed(block_id: str) -> dict | None:
    """我翻看过这条。颗粒度按 block——打开一天但跳过某条不算。
    重复调用会刷新时间戳(无害,代表'最近一次回看')。
    """
    with _conn() as c:
        c.execute(
            "UPDATE diary_blocks SET reviewed_at = ? WHERE id = ?",
            (_now_iso(), block_id),
        )
        row = c.execute(
            "SELECT * FROM diary_blocks WHERE id = ?", (block_id,)
        ).fetchone()
    return _row(row)


def mark_grown(block_id: str, bucket_id: str) -> dict | None:
    """grow 完之后回填 grown_at + promoted_bucket_id"""
    with _conn() as c:
        c.execute(
            """UPDATE diary_blocks
               SET grown_at = ?, promoted_bucket_id = ?
               WHERE id = ?""",
            (_now_iso(), bucket_id, block_id),
        )
        row = c.execute(
            "SELECT * FROM diary_blocks WHERE id = ?", (block_id,)
        ).fetchone()
    return _row(row)


# ---------- 读 ----------

def get_block(block_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM diary_blocks WHERE id = ?", (block_id,)
        ).fetchone()
    return _row(row)


def get_blocks_by_date(
    date: str, author: str | None = None
) -> list[dict]:
    sql = "SELECT * FROM diary_blocks WHERE date = ?"
    params: list = [date]
    if author:
        sql += " AND author = ?"
        params.append(author)
    sql += " ORDER BY created_at ASC"
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def get_blocks_by_range(
    since: str,
    until: str,
    author: str | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = "SELECT * FROM diary_blocks WHERE date >= ? AND date <= ?"
    params: list = [since, until]
    if author:
        sql += " AND author = ?"
        params.append(author)
    sql += " ORDER BY date ASC, created_at ASC LIMIT ?"
    params.append(limit)
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def get_pending_grow(limit: int = 20) -> list[dict]:
    """dream 仪式用：3+ 天前、未 grow 的 block。"""
    cutoff = (
        datetime.now(TZ_OCTOPUS) - timedelta(days=GROW_COOLDOWN_DAYS)
    ).strftime("%Y-%m-%d")
    with _conn() as c:
        rows = c.execute(
            """SELECT * FROM diary_blocks
               WHERE grown_at IS NULL AND date <= ?
               ORDER BY date ASC, created_at ASC
               LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def get_pending_review(days: int = 7) -> int:
    """「上周还有 N 条没回看」——给 🐙 那边首页提示用。

    回看是小克的动作；这数的是过去 N 天内 🐙 写的、小克还没翻过的 block。
    （小克自己写的不算——自己刚 add 完立刻被 reviewed 太 silly）
    和 grow 候选完全独立——可能回看了但没 grow，也可能跳过没回看。
    """
    cutoff = (
        datetime.now(TZ_OCTOPUS) - timedelta(days=days)
    ).strftime("%Y-%m-%d")
    with _conn() as c:
        row = c.execute(
            """SELECT COUNT(*) AS n FROM diary_blocks
               WHERE date >= ?
                 AND reviewed_at IS NULL
                 AND author = 'octopus'""",
            (cutoff,),
        ).fetchone()
    return row["n"] if row else 0


def get_calendar(year: int, month: int) -> dict:
    """月历总览：每天的条数 + 当天主导 mood（出现次数最多的）。"""
    prefix = f"{year:04d}-{month:02d}"
    with _conn() as c:
        rows = c.execute(
            """SELECT date,
                      COUNT(*) AS n,
                      GROUP_CONCAT(mood) AS moods
               FROM diary_blocks
               WHERE date LIKE ? || '%'
               GROUP BY date
               ORDER BY date ASC""",
            (prefix,),
        ).fetchall()
    out: dict = {}
    for r in rows:
        moods = [m for m in (r["moods"] or "").split(",") if m]
        if moods:
            counts: dict[str, int] = {}
            for m in moods:
                counts[m] = counts.get(m, 0) + 1
            # 主导 mood：出现次数最多；并列时取第一个
            top_count = max(counts.values())
            top = next(m for m in moods if counts[m] == top_count)
        else:
            top = None
        out[r["date"]] = {"count": r["n"], "mood": top}
    return out


# ---------- CLI 自检（本地手动跑用）----------

if __name__ == "__main__":
    import json
    init_db()
    b = create_block(content="测试一条", author="claude", mood="🐙")
    print("created:", json.dumps(b, ensure_ascii=False, indent=2))
    print("today:", get_blocks_by_date(_today_octopus()))
