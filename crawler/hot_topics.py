"""
热点话题输入模块 —— 由你手动输入热点话题

不爬虫、不反爬、不封号 👍
你只需要把热点话题粘贴进来，程序自动存入数据库。

后续如果想加自动爬取，再写一个 crawler 就行，互不影响。
"""

from datetime import datetime

from loguru import logger

from db.database import save_topics


def input_topics() -> list[dict]:
    """
    交互式输入热点话题

    你可以一行一条直接粘贴，输入空行结束。
    支持两种格式：
      - 纯文本：         人工智能新突破
      - 带热度/分类：     人工智能新突破 | 热度:500万 | 科技
    """
    print("\n📝 请输入热点话题（一行一条，输入空行结束）：")
    print("   格式1: 关键词")
    print("   格式2: 关键词 | 热度值 | 分类标签")
    print("-" * 50)

    topics = []
    rank = 1

    while True:
        line = input(f"  话题 {rank}: ").strip()

        # 空行 → 结束输入
        if not line:
            break

        # 解析这一行
        parts = [p.strip() for p in line.split("|")]

        keyword = parts[0] if len(parts) >= 1 else ""
        hot_value = parts[1] if len(parts) >= 2 else ""
        category = parts[2] if len(parts) >= 3 else ""

        if keyword:
            topics.append({
                "keyword": keyword,
                "hot_value": hot_value,
                "category": category,
                "rank": rank,
            })
            rank += 1
        else:
            print("    ⚠️  关键词不能为空，请重新输入")

    return topics


def quick_add_topics(topic_lines: str) -> list[dict]:
    """
    快速批量添加话题（适合从别处复制粘贴一大段文字）

    参数:
        topic_lines: 多行文本，一行一条话题

    用法:
        topics = quick_add_topics('''
        人工智能新突破 | 500万 | 科技
        世界杯决赛 | 300万 | 体育
        ''')
    """
    topics = []
    rank = 1

    for line in topic_lines.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split("|")]
        keyword = parts[0] if len(parts) >= 1 else ""
        hot_value = parts[1] if len(parts) >= 2 else ""
        category = parts[2] if len(parts) >= 3 else ""

        if keyword:
            topics.append({
                "keyword": keyword,
                "hot_value": hot_value,
                "category": category,
                "rank": rank,
            })
            rank += 1

    return topics


def save_manual_topics(topics: list[dict]):
    """
    将手动输入的话题保存到数据库
    来源标记为 "manual"（手动输入）
    """
    if topics:
        save_topics(topics, source="manual")
        logger.info(f"已保存 {len(topics)} 条手动话题 ✓")
    else:
        logger.warning("没有话题需要保存")
