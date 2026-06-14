"""
一键全流程：DeepSeek 生成 → MultiPost 发布

适用场景：
  你从微信群拿到热点话题，想要一键完成：生成文章 → 发布到头条/公众号

用法：
  终端1：python3 start_chrome.py
  终端2：python3 quick_publish.py

流程：
  1. 输入热点话题
  2. 选择 DeepSeek 工作流对话
  3. DeepSeek 自动生成文章
  4. 预览文章，确认无误
  5. 自动发布到 MultiPost（头条/公众号）
"""

import sys
from typing import Optional
from loguru import logger

from config import settings, PROJECT_ROOT
from generator.deepseek_web import DeepSeekWeb
from publisher.multipost import MultiPostPublisher


def setup_logger():
    """配置日志"""
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
    )
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.add(
        str(log_dir / "app_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{line} | {message}",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
    )


# 你常用的工作流列表（显示用）
COMMON_WORKFLOWS = [
    "热点话题吐槽文章生成",
    "职场爆料评论贴创作",
    "小蜜蜂",
    "话题冲刺",
    "脉穗计划",
    "AI科技热点职场热点吐槽文章",
]


def step_input_topic() -> str:
    """第1步：输入热点话题"""
    print("\n" + "=" * 55)
    print("📌 输入热点话题")
    print("=" * 55)
    topic = input("\n  话题: ").strip()
    if not topic:
        print("  ⚠️  话题不能为空！")
        return step_input_topic()
    return topic


def step_select_workflow() -> str:
    """第2步：选择 DeepSeek 工作流"""
    print("\n" + "=" * 55)
    print("🎯 选择 DeepSeek 工作流")
    print("=" * 55)
    for i, wf in enumerate(COMMON_WORKFLOWS, start=1):
        print(f"  {i}. {wf}")
    print(f"  0. 其他（手动输入名称）")

    choice = input("\n  选择 (1-6): ").strip()

    if choice.isdigit() and 1 <= int(choice) <= len(COMMON_WORKFLOWS):
        return COMMON_WORKFLOWS[int(choice) - 1]
    elif choice == "0":
        name = input("  工作流名称: ").strip()
        return name if name else COMMON_WORKFLOWS[0]
    else:
        print("  使用默认工作流")
        return COMMON_WORKFLOWS[0]


def step_deepseek_generate(topic: str, workflow: str) -> Optional[str]:
    """第3步：用 DeepSeek 网页端生成文章"""
    print("\n" + "=" * 55)
    print("🤖 DeepSeek 生成文章")
    print("=" * 55)
    print(f"  话题: {topic}")
    print(f"  工作流: {workflow}")

    # 询问是否开启深度思考
    think = input("\n  开启深度思考？(y=开启/n=关闭，回车=关闭): ").strip().lower()
    use_deep_think = (think == "y")

    ds = DeepSeekWeb()
    if not ds.connect():
        logger.error("❌ 无法连接 Chrome")
        return None

    try:
        # 打开 DeepSeek
        ds.open_deepseek()

        # 打开工作流对话
        if not ds.open_conversation(workflow):
            return None

        # 发送话题并等待回复
        print(f"\n  ⏳ DeepSeek 正在生成文章，请耐心等待...")
        reply = ds.send_and_wait(
            message=topic,
            max_wait=180,  # 最长等3分钟
            use_deep_think=use_deep_think,
        )

        if reply:
            print(f"\n  ✓ 生成完成！文章长度: {len(reply)} 字")
        else:
            logger.error("❌ 生成失败")

        return reply

    finally:
        ds.disconnect()


def step_preview_and_edit(title: str, body: str) -> tuple:
    """第4步：预览文章，可选择修改"""
    print("\n" + "=" * 55)
    print("📋 文章预览")
    print("=" * 55)
    print(f"  标题: {title}")
    print(f"  正文 ({len(body)} 字):")
    print("-" * 40)
    # 显示前500字
    preview = body[:500] + ("..." if len(body) > 500 else "")
    print(preview)
    print("-" * 40)

    print("\n  选项:")
    print("  1. 直接发布")
    print("  2. 修改标题")
    print("  3. 重新生成")

    choice = input("\n  选择 (1-3): ").strip()

    if choice == "2":
        new_title = input(f"  新标题 (回车保持原样): ").strip()
        if new_title:
            title = new_title
    elif choice == "3":
        return None, None  # 信号：需要重新生成

    return title, body


def step_publish(title: str, body: str) -> bool:
    """第5步：发布到 MultiPost"""
    print("\n" + "=" * 55)
    print("🚀 发布到 MultiPost")
    print("=" * 55)

    confirm = input("\n  确认发布到 头条/公众号？(y/dry/n): ").strip().lower()

    if confirm not in ("y", "dry", "d"):
        logger.info("已取消发布")
        return False

    publisher = MultiPostPublisher()
    if not publisher.connect():
        logger.error("❌ 无法连接 Chrome")
        return False

    try:
        dry_run = (confirm in ("dry", "d"))

        result = publisher.publish(
            title=title,
            body=body,
            platforms=["微信公众号", "今日头条"],
            dry_run=dry_run,
        )

        if dry_run and result:
            real = input("\n  干跑通过，真正发布？(y/n): ").strip().lower()
            if real == "y":
                result = publisher.publish(
                    title=title,
                    body=body,
                    platforms=["微信公众号", "今日头条"],
                    dry_run=False,
                )

        return result

    finally:
        publisher.disconnect()


def extract_title_from_body(body: str) -> str:
    """
    从正文提取标题
    如果正文以 # 标题 开头，就用它
    否则取第一行
    """
    lines = body.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith('#'):
            return line.lstrip('#').strip()
        if line and len(line) < 80:
            return line
    return "无标题"


def main():
    """主流程"""
    setup_logger()
    logger.info("🚀 一键全流程启动")

    # 第1步：输入热点话题
    topic = step_input_topic()

    # 第2步：选择工作流
    workflow = step_select_workflow()

    # 第3步：DeepSeek 生成（可重试）
    while True:
        body = step_deepseek_generate(topic, workflow)
        if not body:
            retry = input("\n  生成失败，重试？(y/n): ").strip().lower()
            if retry == "y":
                continue
            logger.info("退出")
            return

        # 提取标题
        title = extract_title_from_body(body)

        # 第4步：预览
        title, body = step_preview_and_edit(title, body)
        if title is None:
            # 需要重新生成
            continue
        break

    # 第5步：发布
    step_publish(title, body)

    logger.info("🏁 一键全流程结束")


if __name__ == "__main__":
    main()
