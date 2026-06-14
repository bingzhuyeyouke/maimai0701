"""
内容合规处理模块

功能：
  1. 文字打码：公司名 → "某大厂"，人名 → "某员工" 等
  2. 图片打码：OCR 检测公司名区域 → 模糊处理

核心原则：合规修改后才能通过 MultiPost 发布到其他平台
"""

import io
from pathlib import Path
from typing import Optional, List, Tuple

from loguru import logger

from config import settings


# ========== 文字打码 ==========

COMPLIANCE_PROMPT = """你是一个内容合规改写专家。请对以下自媒体帖子进行合规改写，规则如下：

1. 所有具体公司名（如华为、阿里、腾讯、字节跳动、百度、京东、美团、拼多多、快手、小米、网易、360、科大讯飞等）统一替换为"某大厂"或"某互联网公司"
2. 如果上下文能区分是哪家，可以用"某电商大厂"、"某社交大厂"、"某搜索大厂"等带特征的描述
3. 具体人名替换为"某员工"、"某高管"、"某大佬"等
4. 具体薪资数字可以保留，但要模糊化如"15k"改为"1.5万左右"
5. 保持原文的吐槽风格和语气，不要改得太正式
6. 不要添加任何解释说明，直接输出改写后的内容

原文：
{content}

请直接输出改写后的内容："""


def text_compliance(content: str) -> str:
    """
    文字合规改写：用 DeepSeek AI 将公司名/人名替换为合规表述

    参数:
        content: 原始帖子文字

    返回:
        合规改写后的文字
    """
    logger.info("文字合规改写...")

    from openai import OpenAI

    client = OpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
    )

    prompt = COMPLIANCE_PROMPT.format(content=content)

    try:
        response = client.chat.completions.create(
            model=settings.ai_model,
            messages=[
                {"role": "system", "content": "你是内容合规改写专家，只输出改写结果，不解释。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,  # 低温度，改写要准确
            max_tokens=1000,
        )

        result = response.choices[0].message.content
        if result:
            logger.success(f"文字合规改写完成 ✓ ({len(content)}字 → {len(result)}字)")
            return result.strip()
        else:
            logger.error("AI 返回空结果，使用原文")
            return content

    except Exception as e:
        logger.error(f"合规改写失败: {e}，使用原文")
        return content


# ========== AI 全文改写（自定义帖子模式） ==========

REWRITE_PROMPT = """你是一位资深的今日头条和微信公众号创作者。请将以下脉脉帖子改写为一篇适合在今日头条和微信公众号发布的文章。

改写要求：

1. **标题**：生成一个吸引人的标题，风格参考今日头条爆款标题：
   - 可以用数字、疑问句、对比等手法
   - 标题长度15-30字
   - 不要标题党，但要有吸引力
   - 不要用"震惊"、"竟然"等低质标题词

2. **正文改写**：
   - 将原始帖子内容改写为完整的文章，保留核心观点和事实
   - 文章风格：口语化、接地气、有观点、有活人感
   - 段落要短（每段2-3句），适合手机阅读
   - 开头3秒抓住读者注意力
   - 结尾要有互动引导（提问/评论引导）
   - 字数300-800字

3. **合规要求**（必须严格遵守）：
   - 所有具体公司名替换为"某大厂"或"某互联网公司"
   - 如果上下文能区分公司类型，用"某电商大厂"、"某社交大厂"、"某搜索大厂"等
   - 具体人名替换为"某员工"、"某高管"等
   - 具体薪资数字模糊化，如"15k"改为"1.5万左右"

4. **格式**：
   - 第一行输出标题（不要加#号）
   - 空一行后输出正文
   - 正文不要用Markdown格式，用纯文本

5. **禁止**：
   - 不要编造原文中没有的事实和数据
   - 不要大段搬运原文，要真正改写
   - 不要写成新闻稿风格，要有个人观点和感受
   - 不要使用比喻句和排比句
   - 不要添加"作为一名职场人"等套话开头

原始帖子内容：
标题：{title}
正文：{content}

请输出改写后的标题和正文："""


def text_rewrite(content: str, title: str = "") -> tuple:
    """
    AI 全文改写：将脉脉帖子改写为适合今日头条/公众号的文章风格

    与 text_compliance() 的区别：
    - text_compliance() 只做公司名/人名打码替换，保持原文结构和语气
    - text_rewrite() 做完整改写，生成新标题、新文章结构、新表达方式，
      同时也包含合规打码

    参数:
        content: 原始帖子正文
        title:   原始帖子标题（可选，帮助 AI 更好理解内容）

    返回:
        (new_title, new_body) 元组
        如果改写失败，返回 (原title, 原content)
    """
    logger.info("AI 全文改写...")

    from openai import OpenAI

    client = OpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
    )

    prompt = REWRITE_PROMPT.format(content=content, title=title or "无标题")

    try:
        response = client.chat.completions.create(
            model=settings.ai_model,
            messages=[
                {
                    "role": "system",
                    "content": "你是今日头条和微信公众号的资深创作者，擅长将职场爆料改写为有吸引力的自媒体文章。只输出改写结果，不解释。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=1500,
        )

        result = response.choices[0].message.content
        if not result:
            logger.error("AI 返回空结果，使用原文")
            return (title, content)

        result = result.strip()

        # 解析 AI 输出：首行标题 + 空行 + 正文
        new_title, new_body = _split_rewrite_result(result, title)

        logger.success(f"全文改写完成 ✓ 标题: {title[:20]}→{new_title[:20]}, 正文: {len(content)}→{len(new_body)}字")
        return (new_title, new_body)

    except Exception as e:
        logger.error(f"全文改写失败: {e}，使用原文")
        return (title, content)


def _split_rewrite_result(result: str, fallback_title: str) -> tuple:
    """
    解析 AI 改写输出为 (标题, 正文)

    AI 输出格式：第一行标题，空一行，然后正文
    """
    lines = result.split("\n")

    if not lines:
        return (fallback_title, result)

    # 找到第一个非空行作为标题
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip():
            title = line.strip()
            body_start = i + 1
            break

    if not title:
        return (fallback_title, result)

    # 跳过标题和正文之间的空行
    while body_start < len(lines) and not lines[body_start].strip():
        body_start += 1

    body = "\n".join(lines[body_start:]).strip()

    if not body:
        return (title, result)

    return (title, body)


# ========== 图片打码 ==========

def image_compliance(image_path: str, company_names: Optional[List[str]] = None) -> str:
    """
    图片合规处理：OCR 检测文字区域 → 识别公司名 → 模糊打码 + 固定位置打码

    参数:
        image_path:    图片文件路径
        company_names: 需要打码的公司名列表（可选，不传则自动检测所有文字区域打码）

    返回:
        打码后的图片保存路径（原文件名加 _masked 后缀）
    """
    logger.info(f"图片合规处理: {image_path}")

    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError:
        logger.error("缺少依赖：pip install opencv-python-headless Pillow")
        return image_path

    # 1. OCR 检测文字区域
    text_regions = _ocr_detect(image_path)

    # 2. 筛选需要打码的区域（包含公司名的）
    regions_to_mask = _filter_mask_regions(text_regions, company_names)

    # 3. 添加固定位置打码：脉脉水印（右下角 "maimai.cn"）
    watermark_region = _detect_watermark_region(image_path)
    if watermark_region:
        regions_to_mask.append(watermark_region)

    if not regions_to_mask:
        logger.info("无需打码的文字区域")
        return image_path

    # 4. 对选中区域进行模糊处理
    masked_path = _apply_blur(image_path, regions_to_mask)

    logger.success(f"图片打码完成 ✓ 保存: {masked_path}")
    return masked_path


def _detect_watermark_region(image_path: str) -> Optional[dict]:
    """
    检测脉脉固定位置水印区域（右下角 "maimai.cn"）

    脉脉截图的水印始终在图片右下角，OCR 通常检测不到（太淡），
    所以直接按图片尺寸计算固定区域进行打码。

    返回:
        水印区域 dict，或 None（图片太小没有水印）
    """
    try:
        from PIL import Image
        img = Image.open(image_path)
        w, h = img.size
    except Exception:
        return None

    # 水印只在大图（手机截图）上出现
    if w < 200 or h < 400:
        return None

    # 脉脉水印在右下角，大约占宽度40%、高度3%的区域
    x1 = int(w * 0.55)
    x2 = int(w * 0.98)
    y1 = int(h * 0.93)
    y2 = int(h * 0.98)

    logger.debug(f"  添加脉脉水印打码区域: ({x1},{y1})-({x2},{y2})")
    return {
        "text": "maimai.cn",
        "bbox": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        "confidence": 1.0,
    }


def _ocr_detect(image_path: str) -> List[dict]:
    """
    用 EasyOCR 检测图片中的文字及其位置

    返回:
        [{"text": "华为", "bbox": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], "confidence": 0.95}, ...]
    """
    logger.debug("OCR 检测文字...")

    try:
        import easyocr
    except ImportError:
        logger.error("缺少 easyocr：pip install easyocr")
        return []

    # 首次运行会下载模型（约 100MB），之后会缓存
    reader = easyocr.Reader(['ch_sim', 'en'], verbose=False)

    results = reader.readtext(image_path)

    regions = []
    for bbox, text, confidence in results:
        if confidence > 0.3 and len(text.strip()) > 1:
            regions.append({
                "text": text.strip(),
                "bbox": bbox,  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                "confidence": round(confidence, 2),
            })

    logger.debug(f"OCR 检测到 {len(regions)} 个文字区域")
    for r in regions[:10]:
        logger.debug(f"  '{r['text']}' (置信度:{r['confidence']})")

    return regions


# 常见公司名关键词（用于匹配 OCR 结果）
COMPANY_KEYWORDS = [
    # 大厂
    "华为", "阿里", "阿里巴巴", "腾讯", "字节", "字节跳动", "百度", "京东", "美团",
    "拼多多", "快手", "小米", "网易", "360", "科大讯飞", "滴滴", "小红书", "微博",
    "苹果", "微软", "谷歌", "亚马逊", "Meta", "OpenAI", "商汤", "旷视",
    # 大厂子品牌
    "钉钉", "飞猪", "蚂蚁", "闲鱼", "携程", "哔哩哔哩", "B站", "知乎",
    # 金融/银行
    "微众", "微众银行", "网商", "网商银行", "百信银行", "招商银行", "工行", "建行",
    # 脉脉平台相关（必须打码）
    "脉脉", "maimai", "成就职业梦想", "同事圈",
    # 人名
    "余承东", "马云", "马化腾", "刘强东", "雷军", "张一鸣", "李彦宏", "王兴",
    "周靖人", "陈航", "无招", "陈宇森",
]


def _filter_mask_regions(
    text_regions: List[dict],
    extra_names: Optional[List[str]] = None,
) -> List[dict]:
    """
    筛选需要打码的文字区域

    规则：包含公司名/人名的区域需要打码
    """
    keywords = COMPANY_KEYWORDS.copy()
    if extra_names:
        keywords.extend(extra_names)

    mask_regions = []
    for region in text_regions:
        text = region["text"]
        # 检查是否包含公司名/人名
        for kw in keywords:
            if kw in text:
                mask_regions.append(region)
                logger.debug(f"  需打码: '{text}' (匹配: {kw})")
                break

    return mask_regions


def _apply_blur(image_path: str, regions: List[dict]) -> str:
    """
    对指定文字区域进行高斯模糊打码

    参数:
        image_path: 原图路径
        regions:    需要打码的区域列表

    返回:
        打码后的图片路径
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    if img is None:
        logger.error(f"无法读取图片: {image_path}")
        return image_path

    h, w = img.shape[:2]

    for region in regions:
        bbox = region["bbox"]

        # bbox 是四个角点的坐标，转换为矩形
        pts = np.array(bbox, dtype=np.int32)
        x_min = max(0, pts[:, 0].min())
        x_max = min(w, pts[:, 0].max())
        y_min = max(0, pts[:, 1].min())
        y_max = min(h, pts[:, 1].max())

        # 扩大打码区域（确保完全覆盖文字）
        padding_x = max(5, int((x_max - x_min) * 0.1))
        padding_y = max(3, int((y_max - y_min) * 0.15))
        x_min = max(0, x_min - padding_x)
        x_max = min(w, x_max + padding_x)
        y_min = max(0, y_min - padding_y)
        y_max = min(h, y_max + padding_y)

        # 对该区域进行高斯模糊
        roi = img[y_min:y_max, x_min:x_max]
        if roi.size > 0:
            # 模糊强度取决于区域大小
            ksize = max(15, min(51, ((x_max - x_min) // 2) * 2 + 1))
            blurred = cv2.GaussianBlur(roi, (ksize, ksize), 0)
            img[y_min:y_max, x_min:x_max] = blurred

    # 保存打码后的图片
    src = Path(image_path)
    masked_path = str(src.parent / f"{src.stem}_masked{src.suffix}")
    cv2.imwrite(masked_path, img)

    logger.info(f"已打码 {len(regions)} 个区域")
    return masked_path
