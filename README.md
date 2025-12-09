# MultiThread-Screenshot-Tool
一个基于 Playwright 构建的可多线程的网页批量截图工具，支持自动重试和生成详细的 HTML 报告。实现方式非常简单

1. 克隆项目

git clone https://github.com/your-username/WebScreenshot-Pro.git

cd WebScreenshot-Pro

2. 安装依赖

pip install playwright tqdm

playwright install chromium

3.准备url列表

可在config.py中自行设置

其他选项例如线程数量、是否隐藏浏览器、代理服务器地址、超时时间、重试次数也可在config.py中进行配置

4.运行

python main.py
