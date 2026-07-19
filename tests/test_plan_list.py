"""plan_list —— plan 桶读取口的回归测试。

覆盖：默认只列 active、weight 降序、status 过滤（resolved/abandoned/all）、
空桶与无匹配状态的提示、非 plan 桶不串场、limit 截断。
"""

from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from tools.plan.core import plan_create, plan_list


class NoopDecay:
    async def ensure_started(self):
        return None


def install_plan_runtime(bucket_mgr):
    rt.bucket_mgr = bucket_mgr
    rt.decay_engine = NoopDecay()
    rt.logger = MagicMock()


@pytest.mark.asyncio
async def test_plan_list_defaults_to_active_sorted_by_weight(bucket_mgr):
    install_plan_runtime(bucket_mgr)
    await plan_create(content="轻的计划", weight=0.2)
    await plan_create(content="重的计划", weight=0.9)
    await plan_create(content="做完的计划", status="resolved")

    out = await plan_list()

    assert "active 2" in out and "resolved 1" in out
    assert "轻的计划" in out and "重的计划" in out
    assert "做完的计划" not in out
    # weight 降序：重的在前
    assert out.index("重的计划") < out.index("轻的计划")
    assert "weight 0.9" in out


@pytest.mark.asyncio
async def test_plan_list_status_filter_and_all(bucket_mgr):
    install_plan_runtime(bucket_mgr)
    await plan_create(content="进行中的")
    await plan_create(content="放下的", status="abandoned")
    await plan_create(content="完成的", status="resolved")

    resolved = await plan_list(status="resolved")
    assert "完成的" in resolved
    assert "进行中的" not in resolved and "放下的" not in resolved

    everything = await plan_list(status="all")
    for text in ("进行中的", "放下的", "完成的"):
        assert text in everything


@pytest.mark.asyncio
async def test_plan_list_empty_and_no_match(bucket_mgr):
    install_plan_runtime(bucket_mgr)
    assert "plan 桶是空的" in await plan_list()

    await plan_create(content="只有 active 的")
    none_resolved = await plan_list(status="resolved")
    assert "没有 resolved 状态的 plan" in none_resolved


@pytest.mark.asyncio
async def test_plan_list_ignores_non_plan_buckets(bucket_mgr):
    install_plan_runtime(bucket_mgr)
    await bucket_mgr.create(content="一条普通记忆", domain=["life"])
    await plan_create(content="真正的计划")

    out = await plan_list()
    assert "真正的计划" in out
    assert "一条普通记忆" not in out


@pytest.mark.asyncio
async def test_plan_list_limit_truncates(bucket_mgr):
    install_plan_runtime(bucket_mgr)
    for i in range(4):
        await plan_create(content=f"计划 {i}")

    out = await plan_list(limit=2)
    assert "还有 2 条未显示" in out
