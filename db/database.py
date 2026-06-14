"""
数据库模块 —— 用 SQLite 存储热点话题和生成的文章
为什么用 SQLite？因为它不需要安装数据库服务，一个文件就是数据库，
对小白最友好，以后需要迁移到 MySQL/PostgreSQL 也很方便。
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings


def get_connection() -> sqlite3.Connection:
    """
    获取数据库连接
    每次操作都打开新连接，用完关闭，避免多线程问题
    """
    db_path = settings.db_full_path
    # 确保数据库所在目录存在
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    # 让查询结果可以用列名访问（而不是下标）
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    初始化数据库表
    只在首次运行时创建表，后续运行会跳过（IF NOT EXISTS）
    """
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hot_topics (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword     TEXT    NOT NULL,       -- 热搜关键词
                hot_value   TEXT    DEFAULT '',     -- 热度值
                category    TEXT    DEFAULT '',     -- 分类标签（如"影视"、"社会"）
                source      TEXT    NOT NULL,       -- 来源（如"weibo"）
                rank        INTEGER DEFAULT 0,      -- 排名
                fetched_at  TEXT    NOT NULL,       -- 抓取时间（ISO 格式字符串）
                UNIQUE(keyword, source, fetched_at) -- 同一关键词同一来源同一天不重复
            )
        """)

        # 文章表：存储 AI 生成的文章
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword     TEXT    NOT NULL,       -- 对应的热点关键词
                title       TEXT    NOT NULL,       -- 文章标题
                body        TEXT    NOT NULL,       -- 文章正文（Markdown）
                style       TEXT    DEFAULT '通用', -- 写作风格
                ai_model    TEXT    DEFAULT '',     -- 使用的 AI 模型
                status      TEXT    DEFAULT 'draft',-- 状态：draft/approved/published
                created_at  TEXT    NOT NULL        -- 生成时间
            )
        """)

        conn.commit()
        logger.info("数据库初始化完成 ✓")
    finally:
        conn.close()


def save_topics(topics: list[dict], source: str):
    """
    批量保存热点话题到数据库

    参数:
        topics: 话题列表，每项是 dict，包含 keyword / hot_value / category / rank
        source:  来源标识，如 "weibo"
    """
    if not topics:
        logger.warning("没有话题需要保存")
        return

    conn = get_connection()
    now = datetime.now().isoformat()

    try:
        inserted = 0
        for topic in topics:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO hot_topics
                        (keyword, hot_value, category, source, rank, fetched_at)
                    VALUES
                        (:keyword, :hot_value, :category, :source, :rank, :fetched_at)
                    """,
                    {
                        "keyword": topic["keyword"],
                        "hot_value": topic.get("hot_value", ""),
                        "category": topic.get("category", ""),
                        "source": source,
                        "rank": topic.get("rank", 0),
                        "fetched_at": now,
                    },
                )
                inserted += 1
            except sqlite3.Error as e:
                logger.error(f"保存话题失败: {topic.get('keyword', '?')} → {e}")

        conn.commit()
        logger.info(f"保存了 {inserted} 条话题 (来源: {source}) ✓")
    finally:
        conn.close()


def get_recent_topics(limit: int = 20) -> list[dict]:
    """
    查询最近抓取的热点话题

    参数:
        limit: 最多返回多少条
    返回:
        dict 列表，按抓取时间倒序
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT keyword, hot_value, category, source, rank, fetched_at
            FROM hot_topics
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ---------- 文章相关操作 ----------

def save_article(article_data: dict) -> int:
    """
    保存一篇 AI 生成的文章

    参数:
        article_data: 包含 keyword/title/body/style/ai_model 的字典
    返回:
        插入的记录 ID
    """
    conn = get_connection()
    now = datetime.now().isoformat()

    try:
        cursor = conn.execute(
            """
            INSERT INTO articles (keyword, title, body, style, ai_model, status, created_at)
            VALUES (:keyword, :title, :body, :style, :ai_model, 'draft', :created_at)
            """,
            {
                "keyword": article_data["keyword"],
                "title": article_data["title"],
                "body": article_data["body"],
                "style": article_data.get("style", "通用"),
                "ai_model": article_data.get("ai_model", ""),
                "created_at": now,
            },
        )
        conn.commit()
        article_id = cursor.lastrowid
        logger.info(f"文章已保存 (id={article_id}, 标题: {article_data['title']}) ✓")
        return article_id
    finally:
        conn.close()


def get_articles(
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """
    查询文章列表

    参数:
        keyword: 按关键词筛选（模糊匹配）
        status:  按状态筛选（draft/approved/published）
        limit:   最多返回多少条
    """
    conn = get_connection()
    try:
        sql = """
            SELECT id, keyword, title, style, ai_model, status, created_at
            FROM articles
            WHERE 1=1
        """
        params = []

        if keyword:
            sql += " AND keyword LIKE ?"
            params.append(f"%{keyword}%")
        if status:
            sql += " AND status = ?"
            params.append(status)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_article_by_id(article_id: int) -> Optional[dict]:
    """
    根据 ID 查询单篇文章（含正文）
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_article_status(article_id: int, status: str):
    """
    更新文章状态
    draft → approved → published
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE articles SET status = ? WHERE id = ?",
            (status, article_id),
        )
        conn.commit()
        logger.info(f"文章 {article_id} 状态更新为: {status} ✓")
    finally:
        conn.close()
