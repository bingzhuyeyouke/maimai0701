"""自动修复 TOPIC_TAGS 中的英文引号为中文引号

用法: python3 fix_topic_quotes.py <script.py>
原理: 扫描脚本中 TOPIC_TAGS 的值，将英文引号 " (U+0022) 替换为中文引号 "" (U+201C/U+201D)

背景: Write 工具会将中文引号 "" 转成英文引号 ""，导致脉脉搜索匹配不到。
      此脚本在 Write 创建脚本后运行，修复引号问题。
"""
import re
import sys
from pathlib import Path


def fix_quotes(filepath: str) -> bool:
    """修复脚本中 TOPIC_TAGS 的英文引号，返回是否修改"""
    data = Path(filepath).read_bytes()

    # 找 TOPIC_TAGS 块: 从 "TOPIC_TAGS = {" 到下一个 "}"
    # 在这个范围内，将英文引号替换为中文引号
    text = data.decode('utf-8')

    # 匹配 TOPIC_TAGS = { ... } 块
    match = re.search(r'(TOPIC_TAGS\s*=\s*\{)(.+?)(\})', text, re.DOTALL)
    if not match:
        print("⚠️ 未找到 TOPIC_TAGS 块")
        return False

    prefix = match.group(1)
    body = match.group(2)
    suffix = match.group(3)

    # 在 TOPIC_TAGS 的值中，替换英文引号为中文引号
    # 匹配模式: "xxx" 中文内容中的英文引号
    # 策略：找到所有中文文本中的英文引号对
    original_body = body

    # 匹配 "中文内容" 中的英文引号（前面是中文/冒号等非ASCII字符）
    # 英文引号出现在中文字符中间的，替换为中文引号
    body = re.sub(
        r'([一-鿿：，？])"([^"]+)"([一-鿿：，？])',
        r'\1"\2"\3',
        body
    )

    if body != original_body:
        new_text = text[:match.start()] + prefix + body + suffix + text[match.end():]
        Path(filepath).write_bytes(new_text.encode('utf-8'))
        print(f"✓ 已修复 {filepath} 中的英文引号 → 中文引号")

        # 验证
        verify = Path(filepath).read_bytes()
        if b'\xe2\x80\x9c' in verify and b'\xe2\x80\x9d' in verify:
            print("✓ 验证通过：中文引号已存在")
        else:
            print("⚠️ 验证失败：中文引号未写入，尝试字节级替换...")
            _fix_by_bytes(filepath)
        return True
    else:
        print("ℹ️ 未发现需要修复的英文引号")
        return False


def _fix_by_bytes(filepath: str):
    """字节级修复：扫描所有 "中文"英文引号"中文" 模式"""
    data = Path(filepath).read_bytes()

    # 在中文Unicode范围内的字节后跟 0x22(英文引号) 的模式
    # 中文字符UTF-8: 3字节 E4-E9 开头
    import re as bre

    # 简单策略：找所有在中文字节序列内的 0x22
    # 替换: 第一个 22 -> E2 80 9C (左引号), 第二个 22 -> E2 80 9D (右引号)
    text = data.decode('utf-8')
    result = []
    in_chinese_context = False
    quote_count = 0

    i = 0
    while i < len(text):
        ch = text[i]
        # 检测中文字符（U+4E00-U+9FFF）或中文标点
        if '一' <= ch <= '鿿' or ch in '：，？、；！':
            in_chinese_context = True
            result.append(ch)
        elif ch == '"' and in_chinese_context:
            # 在中文上下文中的英文引号，替换为中文引号
            quote_count += 1
            if quote_count % 2 == 1:
                result.append('“')  # 左引号 "
            else:
                result.append('”')  # 右引号 "
        elif ch in ' \n\r\t':
            result.append(ch)
        elif ch in ':=':
            result.append(ch)
        else:
            # 非中文字符，重置上下文
            in_chinese_context = False
            result.append(ch)
        i += 1

    if quote_count > 0:
        new_data = ''.join(result).encode('utf-8')
        Path(filepath).write_bytes(new_data)
        print(f"✓ 字节级修复完成，替换了 {quote_count} 个英文引号")
    else:
        print("⚠️ 字节级修复也未发现引号")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 fix_topic_quotes.py <script.py>")
        sys.exit(1)
    fix_quotes(sys.argv[1])
