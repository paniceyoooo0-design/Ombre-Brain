from __future__ import annotations

from .config import NightFallConfig
from .tool import get_surfaceable_dream, night_fall_tool


_NIGHT_FALL_DOC = """Night Fall latent dream lifecycle (v2 — morning-window surface).

Actions:
- generate: Create a new latent dream from emotional Ombre memories. Typically
  called at session end (after grow).
- surface: Try to surface a dream. Two parallel paths:
  (A) Morning-window roll — first breath in a new local-time window
      (default 04:00 UTC+8) has cfg.morning_surface_prob (~50%) chance to
      produce a dream, chosen by arousal-weighted sampling from the pool.
  (B) Resonance — if query/affect provided, dreams whose recall_cues / affect
      match strongly may surface independently.
  Normally invoked indirectly via breath; Claude doesn't need to call
  this manually.
- status: Counts of pending / surfaced / deleted dreams.
- cleanup: (Mostly inactive in v2 since attempts aren't counted) — removes
  dreams whose surface_attempts reached MAX_SURFACE_ATTEMPTS.
- history: Most recent N events from the lifecycle log. Pass limit=N (default 20).
- peek: Non-destructive read of PENDING dreams. limit=N (default 5).
- seen: Non-destructive read of SURFACED dreams (the "record book"). Surfaced
  dreams are kept in the pool with surfaced=True after delivery — they don't
  re-surface, but you can browse them here. limit=N (default 5).

A surfaced dream is delivered once via normal flow. If you want to promote
it into a permanent Ombre bucket (so it participates in regular breath /
weight pool), call hold(content=...) explicitly. Otherwise it stays in the
Night-Fall record book for browsing but does not enter Ombre memory.

Args:
- action: generate | surface | status | cleanup | history | peek | seen
- limit: max items for history (default 20), peek/seen (default 5)
- query: contextual phrase from current conversation (surface only)
- current_valence / current_arousal: 0..1, -1 means unspecified (surface only)
- is_session_start: passed through but no longer required for surface eligibility
- current_motifs: deprecated, retained for compatibility
- debug: include diagnostic info in the response
"""


def register_night_fall(ombre_server, cfg: NightFallConfig) -> None:
    if getattr(ombre_server, "_night_fall_registered", False):
        return

    @ombre_server.mcp.tool()
    async def night_fall(
        action: str = "generate",
        query: str = "",
        current_valence: float = -1,
        current_arousal: float = -1,
        current_motifs: str = "",
        is_session_start: bool = False,
        debug: bool = False,
        limit: int = 20,
    ) -> str:
        return await night_fall_tool(
            ombre_server,
            cfg,
            action=action,
            query=query,
            current_valence=current_valence,
            current_arousal=current_arousal,
            current_motifs=current_motifs,
            is_session_start=is_session_start,
            debug=debug,
            limit=limit,
        )

    night_fall.__doc__ = _NIGHT_FALL_DOC

    async def _auto_surface() -> str | None:
        return await get_surfaceable_dream(ombre_server, cfg)

    ombre_server._night_fall_auto_surface = _auto_surface
    ombre_server._night_fall_registered = True
