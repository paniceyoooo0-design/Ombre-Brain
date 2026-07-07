"""diary/api.py

把日记 + 记忆查看器路由注册到 ombre 的 mcp 实例上。
跟 ombre 现有 custom_route + cookie session 风格一致。

用法（在 server.py 里）：

    from diary import db
    from diary.api import register_routes

    db.init_db()
    register_routes(mcp, bucket_mgr, decay_engine, _require_auth)

鉴权直接复用 ombre 的 _require_auth（cookie session）——日记本和
dashboard 共用一套登录,不再自己造一套 Bearer token。前端 SPA 部署到
同域(或同 tunnel)后浏览器自动带 ombre_session cookie,免登录。
"""
from __future__ import annotations

from . import db


def register_routes(mcp, bucket_mgr, decay_engine, require_auth):
    """
    Args:
        mcp:          FastMCP 实例
        bucket_mgr:   ombre 的 BucketManager
        decay_engine: ombre 的 DecayEngine（算节点权重）
        require_auth: ombre 的 _require_auth 函数,
                      返回 None(已登录) 或 JSONResponse(401)
    """

    # ============================================================
    # 日记
    # ============================================================

    @mcp.custom_route("/api/diary/blocks", methods=["POST"])
    async def diary_create_block(request):
        """🐙 写新 block。HTTP 永远 author='octopus'。"""
        from starlette.responses import JSONResponse
        err = require_auth(request)
        if err: return err

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        content = (body.get("content") or "").strip()
        if not content:
            return JSONResponse({"error": "content 必填"}, status_code=400)

        block = db.create_block(
            content=content,
            author="octopus",
            mood=body.get("mood") or None,
            date=body.get("date") or None,
        )
        return JSONResponse(block)

    @mcp.custom_route("/api/diary/blocks", methods=["GET"])
    async def diary_list_blocks(request):
        """date 单天 / since+until 范围。"""
        from starlette.responses import JSONResponse
        err = require_auth(request)
        if err: return err

        date = request.query_params.get("date")
        since = request.query_params.get("since")
        until = request.query_params.get("until")
        author = request.query_params.get("author")
        try:
            limit = int(request.query_params.get("limit", "50"))
        except ValueError:
            limit = 50
        limit = max(1, min(limit, 100))

        if date and (since or until):
            return JSONResponse(
                {"error": "date 和 since/until 不能同时给"},
                status_code=400,
            )
        if author and author not in ("octopus", "claude"):
            return JSONResponse(
                {"error": "author 必须是 octopus / claude"},
                status_code=400,
            )

        if date:
            blocks = db.get_blocks_by_date(date, author=author)
        elif since and until:
            blocks = db.get_blocks_by_range(
                since, until, author=author, limit=limit
            )
        else:
            return JSONResponse(
                {"error": "需要 date 或 since+until"},
                status_code=400,
            )
        return JSONResponse(blocks)

    @mcp.custom_route("/api/diary/blocks/{block_id}", methods=["PATCH"])
    async def diary_patch_block(request):
        """改 starred / content / mood。"""
        from starlette.responses import JSONResponse
        err = require_auth(request)
        if err: return err

        block_id = request.path_params["block_id"]
        if not db.get_block(block_id):
            return JSONResponse(
                {"error": "block not found"}, status_code=404,
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        # 不传 key = 不动；传 null = 清空（仅 mood 允许）
        allowed = {"content", "mood", "starred"}
        fields = {k: v for k, v in body.items() if k in allowed}

        if "content" in fields:
            if fields["content"] is None or not str(fields["content"]).strip():
                return JSONResponse(
                    {"error": "content 不能清空"}, status_code=400,
                )

        out = db.update_block(block_id, **fields)
        return JSONResponse(out)

    @mcp.custom_route(
        "/api/diary/calendar/{year}/{month}", methods=["GET"]
    )
    async def diary_calendar(request):
        """月历总览：每天的条数 + 主导 mood。"""
        from starlette.responses import JSONResponse
        err = require_auth(request)
        if err: return err

        try:
            year = int(request.path_params["year"])
            month = int(request.path_params["month"])
        except (ValueError, KeyError):
            return JSONResponse(
                {"error": "year/month 必须是整数"}, status_code=400,
            )
        if not (1 <= month <= 12):
            return JSONResponse({"error": "month 1-12"}, status_code=400)

        return JSONResponse(db.get_calendar(year, month))

    @mcp.custom_route("/api/diary/pending-review", methods=["GET"])
    async def diary_pending_review(request):
        """「上周还有 N 条没回看」首页提示用。"""
        from starlette.responses import JSONResponse
        err = require_auth(request)
        if err: return err

        try:
            days = int(request.query_params.get("days", "7"))
        except ValueError:
            days = 7
        days = max(1, min(days, 30))

        return JSONResponse({
            "count": db.get_pending_review(days=days),
            "window_days": days,
        })

    # ============================================================
    # 记忆查看器（只读 ombre 桶）
    # ============================================================

    @mcp.custom_route("/api/memory/graph", methods=["GET"])
    async def memory_graph(request):
        """力导向图数据：节点 + 边。

        节点:
          id, name, weight, domain(主), tags, is_feel, is_pinned, type, preview
        边:
          source -> target  (basis: metadata.source_bucket 关系)
        """
        from starlette.responses import JSONResponse
        err = require_auth(request)
        if err: return err

        try:
            all_buckets = await bucket_mgr.list_all(include_archive=False)
            nodes = []
            edges = []
            valid_ids = {b["id"] for b in all_buckets}

            for b in all_buckets:
                meta = b["metadata"]
                bucket_type = meta.get("type", "")
                tags = meta.get("tags", []) or []
                domain = meta.get("domain", []) or []
                primary_domain = domain[0] if domain else None
                content = b.get("content") or ""

                weight = decay_engine.calculate_score(meta)

                nodes.append({
                    "id": b["id"],
                    "name": meta.get("name") or b["id"],
                    "weight": round(float(weight), 3),
                    "domain": primary_domain,
                    "tags": tags,
                    "type": bucket_type,
                    "is_feel": bucket_type == "feel",
                    "is_pinned": bool(
                        meta.get("pinned") or meta.get("protected")
                    ),
                    "preview": (
                        content[:120] + "..."
                        if len(content) > 120 else content
                    ),
                })

                # source_bucket 关系（feel → 源记忆 等）
                src = meta.get("source_bucket")
                if src and src in valid_ids:
                    edges.append({"source": src, "target": b["id"]})

            return JSONResponse({"nodes": nodes, "edges": edges})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route(
        "/api/memory/buckets/{bucket_id}", methods=["GET"]
    )
    async def memory_bucket_detail(request):
        """点节点看详情。完整 content + 元数据,只读。"""
        from starlette.responses import JSONResponse
        err = require_auth(request)
        if err: return err

        bucket_id = request.path_params["bucket_id"]
        try:
            bucket = await bucket_mgr.get(bucket_id)
            if not bucket:
                return JSONResponse(
                    {"error": "bucket not found"}, status_code=404,
                )
            return JSONResponse({
                "id": bucket["id"],
                "content": bucket.get("content", ""),
                "metadata": bucket["metadata"],
                "weight": round(
                    float(decay_engine.calculate_score(bucket["metadata"])),
                    3,
                ),
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
