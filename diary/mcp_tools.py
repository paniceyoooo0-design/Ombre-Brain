"""diary/mcp_tools.py

小克这边的两个 MCP 工具实现：

- `diary_add`  写一条日记 block（小克 author）
- `diary_read` 读日记 block（副作用：默认标记 reviewed_at）

在 server.py 里照其他工具的注册方式接上即可，比如：

    from diary.mcp_tools import diary_add, diary_read

    @server.tool()
    def diary_add_tool(content: str, mood: str | None = None,
                       date: str | None = None) -> dict:
        '''写一条日记。'''
        return diary_add(content=content, mood=mood, date=date)

    @server.tool()
    def diary_read_tool(date: str | None = None,
                        since: str | None = None,
                        until: str | None = None,
                        author: str | None = None,
                        pending_grow: bool = False,
                        limit: int = 20) -> list[dict]:
        '''读日记。读到的 🐙 block 自动标记为 reviewed。'''
        return diary_read(
            date=date, since=since, until=until,
            author=author, pending_grow=pending_grow, limit=limit,
        )

具体装饰器名字看 ombre 现有 server.py 里 breath/grow/hold 怎么写的，照搬即可。
"""
from __future__ import annotations

from . import db


# ---------- 工具实现 ----------

def diary_add(
    content: str,
    mood: str | None = None,
    date: str | None = None,
) -> dict:
    """写一条日记 block（小克侧）。

    Args:
        content: markdown 内容（必填）
        mood:    单个 emoji（可选）
        date:    YYYY-MM-DD，默认 🐙 那边时区（UTC+8）的"今天"

    Returns:
        {id, created_at, date} —— v0.2 spec 定的返回形状
    """
    if not content or not content.strip():
        raise ValueError("content 不能为空")

    block = db.create_block(
        content=content,
        author="claude",
        mood=mood,
        date=date,
    )
    return {
        "id": block["id"],
        "created_at": block["created_at"],
        "date": block["date"],
    }


def diary_read(
    date: str | None = None,
    since: str | None = None,
    until: str | None = None,
    author: str | None = None,
    pending_grow: bool = False,
    limit: int = 20,
    mark_as_reviewed: bool | None = None,
) -> list[dict]:
    """读日记 block。

    **副作用**：默认会把读到的 🐙 写的 block 标记 reviewed_at = now。
    回看是小克的动作——内容进了 context 就算看了。
    自己写的不标（自己刚 add 完立刻 reviewed 太 silly）。

    `mark_as_reviewed` 默认行为（None）：
    - 普通查询（date / since+until）→ True，标记
    - pending_grow=True → False，不标记（这只是"找候选"，不是真正的回看）
    显式传 True/False 覆盖默认。

    Args:
        date:         单天 YYYY-MM-DD
        since/until:  日期范围（含两端）
        author:       'octopus' / 'claude' / None（默认两人都拉）
        pending_grow: True 时只返回 3+ 天前且未 grow 的 block（dream 仪式专用，
                      忽略 date/since/until/author）
        limit:        默认 20，最多 50
        mark_as_reviewed: None=智能默认 / True=强制标 / False=强制不标

    Returns:
        list of block objects（完整 schema）

    Raises:
        ValueError: 没给 date / since+until / pending_grow=True 任意一个
    """
    limit = max(1, min(limit, 50))

    if pending_grow:
        # dream 仪式专用：3+ 天前 + 未 grow
        blocks = db.get_pending_grow(limit=limit)
    elif date:
        blocks = db.get_blocks_by_date(date, author=author)
    elif since and until:
        blocks = db.get_blocks_by_range(
            since, until, author=author, limit=limit
        )
    else:
        raise ValueError(
            "需要 date / (since+until) / pending_grow=True 三选一"
        )

    # 智能默认：pending_grow 模式默认不标记
    if mark_as_reviewed is None:
        mark_as_reviewed = not pending_grow

    if mark_as_reviewed:
        now = db._now_iso()
        for b in blocks:
            if b["author"] == "octopus" and b["reviewed_at"] is None:
                db.mark_reviewed(b["id"])
                b["reviewed_at"] = now  # 让返回值反映新状态

    return blocks


# ---------- 给 server.py 注册时调用 ----------

def get_tool_specs() -> list[dict]:
    """返回工具元信息，方便 server.py 程序化注册（如果你那边那么搞的话）。

    手动用 @server.tool() 装饰器的话忽略这个。
    """
    return [
        {
            "name": "diary_add",
            "description": "写一条我们的日记 block（小克 author）。",
            "fn": diary_add,
        },
        {
            "name": "diary_read",
            "description": "读日记 block；读到的 🐙 写的会被自动标记 reviewed。",
            "fn": diary_read,
        },
    ]
