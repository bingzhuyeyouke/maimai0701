"""
内容生成模块 —— 调用 DeepSeek AI，根据热点话题生成文章草稿

设计思路：
    1. 输入：一个热点关键词
    2. AI 根据关键词生成一篇文章草稿（标题 + 正文）
    3. 输出：结构化的文章对象，存入数据库

⚠️  风险提示：
    - AI 可能生成不准确的内容，所有输出都需要人工审核
    - API 调用有费用，deepseek-chat 很便宜但仍需注意用量
    - 不要把 AI 输出直接发布，一定要人工过一遍
"""

from datetime import datetime
from typing import Optional

from openai import OpenAI
from loguru import logger
from pydantic import BaseModel, Field

from config import settings


# ---------- 数据模型 ----------

class Article(BaseModel):
    """一篇生成的文章"""
    keyword: str = Field(description="热点关键词")
    title: str = Field(description="文章标题")
    body: str = Field(description="文章正文（Markdown 格式）")
    style: str = Field(default="通用", description="写作风格")
    model: str = Field(default="", description="使用的 AI 模型")
    created_at: str = Field(default="", description="生成时间")


# ---------- AI 客户端 ----------

def create_client() -> OpenAI:
    """
    创建 DeepSeek 客户端
    DeepSeek 兼容 OpenAI SDK，只需要改 base_url 就行
    """
    if not settings.ai_api_key:
        raise ValueError(
            "❌ AI_API_KEY 未配置！\n"
            "请在 .env 文件中设置 AI_API_KEY=sk-xxxxxx"
        )

    return OpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
    )


# ---------- 提示词模板 ----------

DEFAULT_SYSTEM_PROMPT = """你是一位资深自媒体内容创作者。你的任务是：

1. 根据用户提供的热点关键词，写一篇吸引人的自媒体文章
2. 文章要求：
   - 标题要有吸引力，适合在社交媒体传播
   - 开头要抓住读者注意力（3秒内决定是否继续看）
   - 内容要有信息量，不要空洞废话
   - 结尾要有互动引导（提问/投票/评论引导）
3. 格式要求：
   - 用 Markdown 格式输出
   - 标题用 # 开头
   - 适当使用加粗、列表等格式
4. 不要编造具体数据和引用来源，如果不确定就说"据相关报道"
"""

DEFAULT_USER_PROMPT_TEMPLATE = """请根据以下热点话题写一篇文章：

热点关键词：{keyword}
{extra}

请直接输出文章内容（标题 + 正文）。"""


# ---------- 生成函数 ----------

def generate_article(
    keyword: str,
    style: str = "通用",
    extra_instructions: str = "",
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> Optional[Article]:
    """
    根据热点关键词，调用 AI 生成一篇文章

    参数:
        keyword:           热点关键词（必填）
        style:             写作风格描述，如"幽默"、"专业"、"感性"
        extra_instructions: 额外的写作要求（可选）
        system_prompt:      系统提示词（可选，有默认值）

    返回:
        Article 对象，如果生成失败返回 None
    """
    logger.info(f"开始生成文章，关键词: {keyword}")

    # 拼装用户提示词
    extra = ""
    if style and style != "通用":
        extra += f"\n写作风格：{style}"
    if extra_instructions:
        extra += f"\n额外要求：{extra_instructions}"

    user_prompt = DEFAULT_USER_PROMPT_TEMPLATE.format(
        keyword=keyword,
        extra=extra,
    )

    try:
        client = create_client()

        # 调用 AI 接口
        response = client.chat.completions.create(
            model=settings.ai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,     # 稍有创造性，但不太离谱
            max_tokens=2000,     # 文章不需要太长
        )

        # 提取 AI 返回的文本
        content = response.choices[0].message.content
        if not content:
            logger.error("AI 返回了空内容")
            return None

        # 从内容中分离标题和正文
        title, body = _split_title_body(content)

        article = Article(
            keyword=keyword,
            title=title,
            body=body,
            style=style,
            model=settings.ai_model,
            created_at=datetime.now().isoformat(),
        )

        logger.success(f"文章生成完成 ✓  标题: {title}")
        return article

    except ValueError as e:
        logger.error(str(e))
        return None
    except Exception as e:
        logger.error(f"❌ AI 调用失败: {e}")
        return None


def _split_title_body(content: str) -> tuple[str, str]:
    """
    从 AI 输出中分离标题和正文

    AI 通常会在第一行用 # 写标题，我们把它拆出来
    如果没有 # 标题，就把第一行当标题
    """
    lines = content.strip().splitlines()

    if not lines:
        return ("无标题", content)

    # 第一行是 # 标题
    if lines[0].startswith("#"):
        title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        return (title, body)

    # 没有标题标记，第一行当标题
    title = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    return (title, body)
