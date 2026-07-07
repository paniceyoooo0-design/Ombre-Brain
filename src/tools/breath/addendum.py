"""
========================================
tools/breath/addendum.py — 二改：breath 返回末尾的附加段
========================================

两个 addendum，都追加在 surface / search 分支的返回文本末尾：

- letters_addendum(is_session_start)：会话开始时把双方各自最近一封信带进
  上下文（「你给我的 / 我给你的」各一封，400 字截断）。非 session start 返回 ""。
- night_fall_addendum(query, valence, arousal, is_session_start)：
  尝试浮现一个梦（晨间窗口掷骰 / 共振通道）。真正的浮现逻辑在
  night_fall 包里；这里通过 rt.night_fall_surface 钩子调用 ——
  server.py 装配 Night-Fall 成功后把钩子写进 _runtime，
  没装上（本地裸跑/测试）时钩子为 None，静默返回 ""。

不做什么（边界）：
- 不直接 import night_fall（避免测试环境强依赖）
- 任何异常都吞掉返回 ""，addendum 永远不能弄坏 breath 主体
========================================
"""

from .. import _runtime as rt
from utils import strip_wikilinks


async def letters_addendum(is_session_start: bool) -> str:
    """会话开始时，把双方各自最近一封信带进上下文（你给我的 / 我给你的）。"""
    if not is_session_start:
        return ""
    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception:
        return ""
    letters = [b for b in all_buckets if b["metadata"].get("type") == "letter"]
    if not letters:
        return ""

    def _ldate(b):
        m = b["metadata"]
        return str(m.get("letter_date") or m.get("created", ""))

    # author 兼容：老数据 "claude"，上游 v2.3.21 起 AI 侧可能是 "ai" 或自定义显示名
    user_side = ("user",)

    def _is_user(b):
        return b["metadata"].get("author") in user_side

    lines = []
    for tag, pick in (("你给我的最近一封", True), ("我给你的最近一封", False)):
        pool = [b for b in letters if _is_user(b) == pick]
        if not pool:
            continue
        pool.sort(key=_ldate, reverse=True)
        latest = pool[0]
        m = latest["metadata"]
        d = str(m.get("letter_date") or m.get("created", ""))[:10]
        title = m.get("title") or m.get("name", "") or ""
        title_tag = f" 《{title}》" if title and title != latest["id"] else ""
        excerpt = strip_wikilinks(latest["content"] or "").strip()[:400]
        lines.append(f"【{tag}】{d}{title_tag} [bucket_id:{latest['id']}]\n{excerpt}")
    if not lines:
        return ""
    return "\n\n=== 最近的信 💌 ===\n" + "\n\n".join(lines)


async def night_fall_addendum(
    query: str, valence: float, arousal: float, is_session_start: bool
) -> str:
    """尝试浮现一个梦。钩子未装配（Night-Fall 未启用）时静默返回 ""。"""
    hook = getattr(rt, "night_fall_surface", None)
    if hook is None:
        return ""
    try:
        surface_result = await hook(
            query=query or "",
            current_valence=valence,
            current_arousal=arousal,
            is_session_start=is_session_start,
        )
        if surface_result and surface_result.startswith("=== 昨夜的梦"):
            return "\n---\n" + surface_result
    except Exception as e:
        log = getattr(rt, "logger", None)
        if log:
            log.warning(f"[Night Fall] surface failed: {e}")
    return ""


async def breath_addendums(
    query: str, valence: float, arousal: float, is_session_start: bool
) -> str:
    """letters + night_fall 两段合并；顺序沿用旧版（信在前，梦在后）。"""
    letters = await letters_addendum(is_session_start)
    nf = await night_fall_addendum(query, valence, arousal, is_session_start)
    return letters + nf
