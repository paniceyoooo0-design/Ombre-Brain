#!/usr/bin/env python3
"""
批量补 embedding：遍历所有记忆桶，为没有向量的桶生成 embedding。
用法：在 Zeabur 容器里运行 python reembed_all.py
"""

import asyncio
import os
import sys
import yaml
import glob
import sqlite3
import json

# 加载项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embedding_engine import EmbeddingEngine


def load_config():
    """加载配置文件"""
    for name in ["config.yaml", "config.example.yaml"]:
        if os.path.exists(name):
            with open(name) as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("找不到 config.yaml 或 config.example.yaml")


def get_bucket_content(filepath: str) -> tuple[str, str]:
    """读取桶文件，返回 (bucket_id, 内容文本)"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    # 从文件名提取 bucket_id
    basename = os.path.basename(filepath).replace(".md", "")

    # 跳过 YAML frontmatter，取正文
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            content = parts[2].strip()
        else:
            content = text
    else:
        content = text

    return basename, content


async def main():
    config = load_config()
    engine = EmbeddingEngine(config)

    if not engine.enabled:
        print("❌ Embedding 未启用或 API key 无效")
        return

    # 找到所有桶文件
    buckets_dir = config.get("buckets_dir", "./buckets")
    patterns = [
        os.path.join(buckets_dir, "*.md"),
        os.path.join(buckets_dir, "**", "*.md"),
    ]

    files = set()
    for pattern in patterns:
        files.update(glob.glob(pattern, recursive=True))

    # 排除特殊文件
    files = [f for f in files if not os.path.basename(f).startswith("_")]

    print(f"📁 找到 {len(files)} 个桶文件")

    # 检查哪些已经有 embedding
    conn = sqlite3.connect(engine.db_path)
    existing = set(
        row[0] for row in conn.execute("SELECT bucket_id FROM embeddings").fetchall()
    )
    conn.close()

    print(f"✅ 已有 embedding：{len(existing)} 个")

    # 补缺的
    to_embed = []
    for f in files:
        bucket_id, content = get_bucket_content(f)
        if bucket_id not in existing and content:
            to_embed.append((bucket_id, content))

    print(f"🔧 需要补 embedding：{len(to_embed)} 个")

    if not to_embed:
        print("🎉 全部桶都有 embedding，无需操作")
        return

    success = 0
    fail = 0
    for i, (bucket_id, content) in enumerate(to_embed):
        try:
            result = await engine.generate_and_store(bucket_id, content)
            if result:
                success += 1
                print(f"  ✅ [{i+1}/{len(to_embed)}] {bucket_id}")
            else:
                fail += 1
                print(f"  ❌ [{i+1}/{len(to_embed)}] {bucket_id} - 生成失败")
        except Exception as e:
            fail += 1
            print(f"  ❌ [{i+1}/{len(to_embed)}] {bucket_id} - {e}")

        # 每 5 个暂停一下，避免 API 限速
        if (i + 1) % 5 == 0:
            await asyncio.sleep(1)

    print(f"\n🏁 完成！成功 {success}，失败 {fail}")


if __name__ == "__main__":
    asyncio.run(main())
