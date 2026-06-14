"""
Chrome 启动辅助脚本

用途：一键启动带远程调试端口的 Chrome，供 Playwright 连接

使用方法：
  python3 start_chrome.py

启动后 Chrome 会打开，保持该终端窗口不要关闭。
然后在另一个终端运行主程序：
  python3 main.py

⚠️  如果 Chrome 已经在运行，需要先完全退出（Cmd+Q）再运行本脚本
"""

import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

# Chrome 可执行文件路径
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 用户数据目录（使用拷贝的 profile，避免锁定默认 profile）
CHROME_USER_DATA = "/tmp/chrome-automation-profile"

# 远程调试端口
DEBUG_PORT = 9222


def copy_profile_if_needed():
    """如果临时 profile 不存在，从默认 profile 复制"""
    dest = Path(CHROME_USER_DATA)
    if dest.exists():
        logger.info(f"临时 profile 已存在: {dest}")
        return

    src = Path.home() / "Library/Application Support/Google/Chrome"
    if not src.exists():
        logger.error("未找到 Chrome 默认 profile")
        return

    logger.info("首次运行，复制 Chrome profile（约需1分钟）...")
    dest.mkdir(parents=True, exist_ok=True)
    dest_default = dest / "Default"
    dest_default.mkdir(parents=True, exist_ok=True)

    src_default = src / "Default"
    # 复制关键文件
    items = [
        "Extensions", "Extension State", "Extension Rules",
        "Local Storage", "Session Storage", "IndexedDB",
        "Cookies", "Cookies-journal",
        "Login Data", "Login Data-journal",
        "Web Data", "Web Data-journal",
        "Preferences", "Secure Preferences",
        "Local Extension Settings",
        "Favicons", "Favicons-journal",
        "History", "History-journal",
        "Bookmarks",
    ]

    for item in items:
        src_item = src_default / item
        if src_item.exists():
            try:
                subprocess.run(["cp", "-r", str(src_item), str(dest_default / item)],
                               capture_output=True, timeout=30)
                logger.debug(f"  ✓ {item}")
            except Exception as e:
                logger.warning(f"  ✗ {item}: {e}")

    # 复制父级配置
    for item in ["Local State", "First Run", "Last Browser"]:
        src_item = src / item
        if src_item.exists():
            subprocess.run(["cp", str(src_item), str(dest / item)],
                           capture_output=True, timeout=5)

    logger.success("Profile 复制完成 ✓")


def check_port_available() -> bool:
    """检查调试端口是否已被占用"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        result = s.connect_ex(('localhost', DEBUG_PORT))
        if result == 0:
            logger.warning(f"端口 {DEBUG_PORT} 已被占用，Chrome 可能已在运行")
            return False
    return True


def start_chrome():
    """启动带调试端口的 Chrome"""
    copy_profile_if_needed()

    if not check_port_available():
        logger.info("Chrome 可能已经在运行，尝试连接...")
        return True

    logger.info("启动 Chrome...")
    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={CHROME_USER_DATA}",
    ]

    # 在后台启动
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等待启动
    logger.info("等待 Chrome 启动...")
    time.sleep(5)

    # 检查是否成功
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"http://localhost:{DEBUG_PORT}/json/version", timeout=3)
        if resp.status == 200:
            logger.success(f"✓ Chrome 已启动，调试端口: {DEBUG_PORT}")
            logger.info("保持此窗口不要关闭，在另一个终端运行：python3 main.py")
            return True
    except Exception:
        pass

    logger.error("Chrome 启动失败，请检查是否有其他 Chrome 实例在运行")
    logger.info("请先完全退出 Chrome（Cmd+Q），然后重新运行本脚本")
    return False


if __name__ == "__main__":
    start_chrome()
