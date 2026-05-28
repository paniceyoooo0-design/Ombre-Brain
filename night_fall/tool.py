from __future__ import annotations

import logging
import random

from .config import NightFallConfig
from .imagery import ImageryExtractionError
from .imagery import extract_imagery
from .metadata import choose_dream_mode, new_dream_id, now_iso, now_utc
from .ombre_adapter import JsonModelError
from .ombre_adapter import OmbreAdapter
from .selection import select_buckets
from .storage import DreamStorage
from .surfacing import (
    age_hours,
    evaluate_pending,
    is_eligible_breath,
)
from .writer import DreamWriterError
from .writer import write_dream

logger = logging.getLogger("night_fall.tool")


def _storage(cfg: NightFallConfig) -> DreamStorage:
    return DreamStorage(cfg.dreams_dir, cfg.logs_dir)


def _format_history(cfg: NightFallConfig, limit: int = 20) -> str:
    """Tail the events.jsonl log and format the most recent entries for humans."""
    import json as _json
    log_path = cfg.logs_dir / "events.jsonl"
    if not log_path.exists():
        return "Night Fall history: events.jsonl does not exist yet (no events recorded)."
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return f"Night Fall history: failed to read events.jsonl ({exc})."
    if not lines:
        return "Night Fall history: events.jsonl is empty."
    n = max(1, min(int(limit or 20), 200))
    recent = lines[-n:]
    parsed = []
    for line in recent:
        try:
            parsed.append(_json.loads(line))
        except Exception:
            parsed.append({"event": "_parse_error", "raw": line[:200]})

    out = [f"Night Fall history (last {len(parsed)} of {len(lines)} events):"]
    for entry in parsed:
        event = entry.get("event", "?")
        # Pick a sensible timestamp field per event type
        ts = (
            entry.get("at")
            or entry.get("generated_at")
            or entry.get("surfaced_at")
            or entry.get("deleted_at")
            or "?"
        )
        dream_id = entry.get("dream_id", "")[:20] if entry.get("dream_id") else ""
        # Format extras based on event type
        if event == "generated":
            extra = f"mode={entry.get('dream_mode','?')} frags={entry.get('fragments','?')} cues={entry.get('recall_cues','?')}"
        elif event == "generate_failed":
            extra = f"reason={entry.get('reason','?')} detail={str(entry.get('detail',''))[:80]}"
        elif event == "surfaced":
            extra = f"spontaneous={entry.get('spontaneous','?')}"
        elif event == "deleted":
            extra = f"reason={entry.get('deletion_reason','?')}"
        else:
            extra = ""
        out.append(f"  {ts}  {event:18s}  {dream_id:22s}  {extra}")
    return "\n".join(out)


def _format_surface_response(record, spontaneous: bool) -> str:
    affect = record.metadata.get("core_affect", {})
    cues = record.metadata.get("recall_cues") or []
    cues_text = "｜".join(cues) if cues else "(none)"
    return (
        "=== 昨夜的梦 ===\n"
        f"dream_id: {record.dream_id}\n"
        f"mode: {record.metadata.get('dream_mode')}\n"
        f"spontaneous: {str(bool(spontaneous)).lower()}\n"
        f"core_affect: valence={float(affect.get('valence', 0.5)):.2f}, "
        f"arousal={float(affect.get('arousal', 0.3)):.2f}\n"
        f"recall_cues: {cues_text}\n\n"
        f"{record.body}"
    )


def _emit_and_keep(store: DreamStorage, adapter: OmbreAdapter, record, spontaneous: bool):
    """Mark the dream as surfaced + log the event, but DO NOT delete the file.
    The dream stays in the pool with surfaced=True; subsequent surface
    evaluations filter it out (DreamRecord.surfaced), so it won't re-fire.
    Surfaced dreams remain queryable via night_fall(action='seen') — a
    'dream record book' rather than a one-shot ephemeral.
    """
    surfaced_record = store.update(
        record,
        surfaced=True,
        surfaced_at=now_iso(),
        spontaneous=bool(spontaneous),
    )
    store.log_event(
        "surfaced",
        {
            "dream_id": surfaced_record.dream_id,
            "generated_at": surfaced_record.metadata.get("generated_at"),
            "surfaced_at": surfaced_record.metadata.get("surfaced_at"),
            "spontaneous": bool(spontaneous),
        },
    )
    # Embedding is kept too so future search / re-surfacing logic can reference
    # the dream's cues if needed.
    return _format_surface_response(surfaced_record, spontaneous)


# ---------- morning-window helpers ----------

def _morning_window_id(now_utc, cfg) -> str:
    """Return a stable ID for which 'morning window' the given UTC datetime
    falls into. A window opens at `morning_window_hour` local time; anything
    before that attaches to the previous calendar day's window."""
    from datetime import timedelta
    local = now_utc + timedelta(hours=cfg.morning_window_tz_offset)
    effective = local - timedelta(hours=cfg.morning_window_hour)
    return effective.strftime("%Y-%m-%d")


def _window_marker_path(cfg):
    return cfg.logs_dir / "last_morning_window.json"


def _read_last_window(cfg) -> str | None:
    try:
        import json as _json
        data = _json.loads(_window_marker_path(cfg).read_text(encoding="utf-8"))
        return data.get("window")
    except Exception:
        return None


def _write_window_consumed(cfg, window_id: str):
    try:
        import json as _json
        _window_marker_path(cfg).write_text(
            _json.dumps({"window": window_id, "at": now_iso()}), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning(f"[Night Fall] failed to write window marker: {exc}")


def _arousal_weighted_pick(pending):
    """Pick one record from `pending`, weighted by arousal. Floor of 0.05 so
    even very calm dreams have nonzero chance."""
    weights = []
    for r in pending:
        affect = r.metadata.get("core_affect", {}) or {}
        try:
            a = float(affect.get("arousal", 0.3))
        except Exception:
            a = 0.3
        weights.append(max(0.05, a))
    return random.choices(pending, weights=weights, k=1)[0]


async def _query_embedding(adapter: OmbreAdapter, query: str) -> list[float] | None:
    if not query or not query.strip():
        return None
    engine = getattr(adapter, "embedding_engine", None)
    if engine is None or not getattr(engine, "enabled", False):
        return None
    try:
        emb = await engine._generate_embedding(query)
        return emb or None
    except Exception as exc:
        logger.warning(f"Query embedding failed: {exc}")
        return None


async def night_fall_tool(
    ombre_server,
    cfg: NightFallConfig,
    action: str = "generate",
    query: str = "",
    current_valence: float = -1,
    current_arousal: float = -1,
    current_motifs: str = "",
    is_session_start: bool = False,
    debug: bool = False,
    limit: int = 20,
) -> str:
    action_name = (action or "generate").strip().lower()
    store = _storage(cfg)
    adapter = OmbreAdapter(ombre_server)
    now = now_utc()

    if action_name == "history":
        return _format_history(cfg, limit=limit)

    if action_name == "peek":
        records = [r for r in store.list() if not r.surfaced]
        records.sort(key=lambda r: r.generated_at, reverse=True)
        n = max(1, min(int(limit or 5), 20))
        slice_ = records[:n]
        if not slice_:
            return "Night Fall peek: pool is empty (no pending dreams)."
        out = [f"Night Fall peek: showing {len(slice_)} of {len(records)} pending dreams (newest first).",
               "Note: this is non-destructive inspection. Does not consume attempts or delete.",
               ""]
        for r in slice_:
            meta = r.metadata
            affect = meta.get("core_affect", {}) or {}
            cues = meta.get("recall_cues", []) or []
            try:
                age_h = (now - r.generated_at).total_seconds() / 3600
            except Exception:
                age_h = -1
            out.append(f"━━━ {r.dream_id} ━━━")
            out.append(f"generated_at: {meta.get('generated_at')}  (age {age_h:.1f}h)")
            out.append(f"mode: {meta.get('dream_mode')}")
            try:
                out.append(f"core_affect: valence={float(affect.get('valence', 0.5)):.2f} arousal={float(affect.get('arousal', 0.3)):.2f}")
            except Exception:
                out.append(f"core_affect: {affect}")
            out.append(f"surface_attempts: {meta.get('surface_attempts', 0)} / 4")
            out.append(f"recall_cues: {' ｜ '.join(cues)}")
            out.append("")
            out.append(r.body)
            out.append("")
        return "\n".join(out)

    if action_name == "seen":
        records = [r for r in store.list() if r.surfaced]
        records.sort(key=lambda r: r.metadata.get("surfaced_at") or "", reverse=True)
        n = max(1, min(int(limit or 5), 50))
        slice_ = records[:n]
        if not slice_:
            return "Night Fall seen: no surfaced dreams in the record book yet."
        out = [
            f"Night Fall seen: {len(slice_)} of {len(records)} surfaced dreams (newest first).",
            "These dreams have already been delivered once; they sit in the record book.",
            "",
        ]
        for r in slice_:
            meta = r.metadata
            affect = meta.get("core_affect", {}) or {}
            cues = meta.get("recall_cues", []) or []
            out.append(f"━━━ {r.dream_id} ━━━")
            out.append(f"generated_at: {meta.get('generated_at')}")
            out.append(f"surfaced_at:  {meta.get('surfaced_at')}")
            out.append(f"mode: {meta.get('dream_mode')}  spontaneous: {meta.get('spontaneous')}")
            try:
                out.append(f"core_affect: valence={float(affect.get('valence', 0.5)):.2f} arousal={float(affect.get('arousal', 0.3)):.2f}")
            except Exception:
                out.append(f"core_affect: {affect}")
            out.append(f"recall_cues: {' ｜ '.join(cues)}")
            out.append("")
            out.append(r.body)
            out.append("")
        return "\n".join(out)

    if action_name == "status":
        status = store.status(now)
        oldest = status["oldest_pending_age_hours"]
        oldest_text = "none" if oldest is None else f"{oldest:.1f}h"
        return (
            "Night Fall status:\n"
            f"pending dreams: {status['pending']}\n"
            f"surfaced dreams: {status['surfaced']}\n"
            f"deleted dreams: {status['deleted']}\n"
            f"oldest pending age: {oldest_text}"
        )

    if action_name == "cleanup":
        deleted = store.cleanup_exhausted()
        return f"Night Fall cleanup complete: {deleted} exhausted unsurfaced dream(s) deleted."

    if action_name == "surface":
        # New surface mechanism (v2):
        # Two parallel paths — morning-window roll (primary) + resonance (secondary).
        # No eligibility gate, no per-breath spontaneous, no attempt-counting.
        # Surfaced dreams stay in the pool with surfaced=True (record book), not deleted.

        pending = [
            r for r in store.list()
            if not r.surfaced and age_hours(r, now) >= cfg.min_surface_age_hours
        ]
        if not pending:
            return "No latent dream surfaced."

        # --- Path A: morning window roll ---
        # First breath in a new "morning window" (local time, default 04:00 UTC+8)
        # has cfg.morning_surface_prob chance of producing a dream. If yes, pick
        # one weighted by arousal (more intense → more likely to be chosen).
        current_window = _morning_window_id(now, cfg)
        last_window = _read_last_window(cfg)
        if current_window != last_window:
            _write_window_consumed(cfg, current_window)  # consume the window regardless
            if random.random() < cfg.morning_surface_prob:
                chosen = _arousal_weighted_pick(pending)
                return _emit_and_keep(store, adapter, chosen, spontaneous=False)

        # --- Path B: resonance ---
        # When the user provides query / affect, evaluate whether any dream's
        # cues/affect strongly match. No attempt counting (mechanism retired).
        if query.strip() or (0 <= current_valence <= 1 and 0 <= current_arousal <= 1):
            query_emb = await _query_embedding(adapter, query)
            evaluated = await evaluate_pending(
                pending, cfg, query_emb, current_valence, current_arousal, adapter
            )
            best = None
            for item in evaluated:
                if item["score"] >= cfg.surface_threshold:
                    if best is None or item["score"] > best[0]:
                        best = (item["score"], item)
            if best is not None:
                return _emit_and_keep(store, adapter, best[1]["record"], spontaneous=False)

        return "No latent dream surfaced."

    if action_name != "generate":
        return "Unknown Night Fall action. Use generate, surface, status, or cleanup."

    buckets = await select_buckets(adapter, cfg.selection_limit, current_valence, current_arousal)
    if len(buckets) < 2:
        store.log_event("generate_failed", {
            "reason": "not_enough_buckets",
            "detail": f"only {len(buckets)} candidate bucket(s), need >= 2",
            "at": now_iso(),
        })
        return "Night Fall skipped: not enough memory material to form a dream."

    try:
        fragments = await extract_imagery(adapter, buckets)
    except (ImageryExtractionError, JsonModelError) as exc:
        store.log_event("generate_failed", {
            "reason": "imagery_extraction",
            "detail": str(exc),
            "at": now_iso(),
        })
        return f"Night Fall skipped: imagery extraction failed ({exc})."

    mode = choose_dream_mode()
    try:
        dream_text, core_affect, recall_cues = await write_dream(adapter, buckets, fragments, mode)
    except (DreamWriterError, JsonModelError) as exc:
        store.log_event("generate_failed", {
            "reason": "dream_writing",
            "detail": str(exc),
            "at": now_iso(),
        })
        return f"Night Fall skipped: dream writing failed ({exc})."

    source_ids = []
    for bucket in buckets:
        source_id = str(bucket.get("id") or bucket.get("metadata", {}).get("id") or "").strip()
        if source_id not in source_ids:
            source_ids.append(source_id)

    metadata = {
        "dream_id": new_dream_id(),
        "generated_at": now_iso(),
        "dream_mode": mode,
        "core_affect": core_affect,
        "source_bucket_ids": source_ids,
        "imagery_fragments": fragments,
        "surfaced": False,
        "surfaced_at": None,
        "spontaneous": None,
        "surface_attempts": 0,
        "recall_cues": recall_cues,
    }
    record = store.write(metadata, dream_text)
    store.log_event("generated", {
        "dream_id": record.dream_id,
        "generated_at": metadata["generated_at"],
        "dream_mode": mode,
        "fragments": len(fragments),
        "recall_cues": len(recall_cues),
        "source_bucket_count": len(source_ids),
    })

    # Embed recall_cues so the cue-channel can fire later. Graceful degradation:
    # if embedding fails (no API key, timeout), the dream still lives but only
    # the affect channel will resonate.
    engine = getattr(adapter, "embedding_engine", None)
    if engine is not None and getattr(engine, "enabled", False) and recall_cues:
        cues_text = "；".join(recall_cues)
        try:
            ok = await engine.generate_and_store(record.dream_id, cues_text)
            if not ok:
                logger.warning(f"Embedding generation returned false for {record.dream_id}")
        except Exception as exc:
            logger.warning(f"Embedding generation failed for {record.dream_id}: {exc}")

    if debug:
        return (
            "Night Fall complete: 1 latent dream formed.\n"
            "It has not surfaced.\n"
            f"debug dream_id: {record.dream_id}\n"
            f"debug fragments: {len(fragments)}\n"
            f"debug recall_cues: {len(recall_cues)}"
        )
    return "Night Fall complete: 1 latent dream formed.\nIt has not surfaced."


async def get_surfaceable_dream(ombre_server, cfg: NightFallConfig) -> str | None:
    """v1 auto-surface: pick the newest pending dream past the latency window,
    mark it surfaced, and return the formatted text block. Returns None when
    no dream is eligible. Called directly by Ombre's breath; not an MCP tool.
    """
    store = _storage(cfg)
    adapter = OmbreAdapter(ombre_server)
    now = now_utc()
    pending = [
        r for r in store.list()
        if not r.surfaced and age_hours(r, now) >= cfg.min_surface_age_hours
    ]
    if not pending:
        return None
    pending.sort(key=lambda r: r.generated_at, reverse=True)
    return _emit_and_destroy(store, adapter, pending[0], spontaneous=False)
