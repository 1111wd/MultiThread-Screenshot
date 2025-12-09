# config.py

MAX_WORKERS = 5 


HEADLESS = True  # 是否无头模式，True为隐藏浏览器，False为显示浏览器
VIEWPORT = {"width": 1920, "height": 1080}  # 浏览器视口大小
TIMEOUT = 30000  # 超时时间（毫秒）

PROXY_SERVER = "socks5://xxx.xxx.xxx.x:xx"  # 代理服务器，例如 "socks5://192.168.1.1:1080"，如不需要则为 None

RETRY_ATTEMPTS = 3  # 重试次数
RETRY_DELAY = 5  # 重试延迟（秒）

# ==================== 文件设置 ====================
URL_FILE = "1.txt"  # URL列表文件
OUTPUT_FILE = "result_playwright.html"  # 输出报告文件
