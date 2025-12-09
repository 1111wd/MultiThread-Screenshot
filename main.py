import base64
from playwright.sync_api import sync_playwright
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from tqdm import tqdm
import config
import queue
import sys

def read_urls_from_file(filename=config.URL_FILE):
    urls = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                url = line.strip()
                if url and not url.startswith('#'):
                    if not url.startswith(('http://', 'https://')):
                        url = 'https://' + url
                    urls.append(url)
        print(f"Read {len(urls)} URLs from {filename}")
        return urls
    except Exception as e:
        print(f"Error reading file: {e}")
        return []

def worker_task(args_queue, result_queue, worker_id, progress_queue):
    """每个工作线程独立运行的函数"""
    screenshot_base64 = None
    title = "No title"
    
    try:
        with sync_playwright() as p:
            browser_args = [
                '--disable-gpu',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
            ]
            
            browser = p.chromium.launch(
                headless=config.HEADLESS,
                args=browser_args
            )
            
            while True:
                try:
                    task = args_queue.get(timeout=1)
                except queue.Empty:
                    break
                
                url, index, total_count, retry_count = task
                
                if retry_count > 0:
                    print(f"Worker {worker_id}: Retry attempt {retry_count} for {url[:50]}...")
                
                success = False
                error_msg = ""
                
                try:
                    browser_context_args = {
                        "viewport": config.VIEWPORT,
                        "ignore_https_errors": True,
                        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    }
                    
                    if config.PROXY_SERVER:
                        browser_context_args["proxy"] = {"server": config.PROXY_SERVER}
                    
                    context = browser.new_context(**browser_context_args)
                    page = context.new_page()
                    
                    try:
                        page.set_default_timeout(config.TIMEOUT)
                        page.set_default_navigation_timeout(config.TIMEOUT)
                        
                        response = page.goto(url, wait_until="load", timeout=config.TIMEOUT)
                        
                        if response and response.status >= 400:
                            error_msg = f"HTTP {response.status}"
                            print(f"Worker {worker_id}: HTTP error {response.status} for {url[:50]}...")
                        else:
                            time.sleep(1)
                            
                            screenshot_bytes = page.screenshot(full_page=True, timeout=10000)
                            screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                            title = page.title() or "No title"
                            success = True
                            print(f"Worker {worker_id}: Success for {url[:50]}...")
                    
                    except Exception as e:
                        error_msg = str(e)
                        if retry_count < config.RETRY_ATTEMPTS:
                            print(f"Worker {worker_id}: Attempt {retry_count + 1} failed: {str(e)[:80]}")
                        else:
                            print(f"Worker {worker_id}: Final failure: {str(e)[:80]}")
                    
                    finally:
                        try:
                            page.close()
                            context.close()
                        except:
                            pass
                
                except Exception as e:
                    error_msg = str(e)
                    print(f"Worker {worker_id}: Unexpected error: {e}")
                
                result_queue.put((url, screenshot_base64, title, success, error_msg))
                progress_queue.put(1)
                
                args_queue.task_done()
            
            browser.close()
    
    except Exception as e:
        print(f"Worker {worker_id}: Failed to initialize: {e}")
        result_queue.put(("", None, "Worker failed", False, str(e)))

def capture_screenshots_with_playwright(urls):
    """使用工作线程池进行批量截图"""
    all_success_data = []
    all_failed_urls = []
    
    print(f"Starting multi-threaded screenshot capture, workers: {config.MAX_WORKERS}")
    
    args_queue = queue.Queue()
    result_queue = queue.Queue()
    progress_queue = queue.Queue()
    
    for i, url in enumerate(urls):
        args_queue.put((url, i, len(urls), 0))
    
    total_urls = len(urls)
    pbar = tqdm(total=total_urls, desc="Overall progress", unit="URL", file=sys.stdout)
    
    workers = []
    for i in range(min(config.MAX_WORKERS, total_urls)):
        worker = threading.Thread(
            target=worker_task,
            args=(args_queue, result_queue, i+1, progress_queue),
            daemon=True
        )
        worker.start()
        workers.append(worker)
    
    completed = 0
    retry_urls = []
    retry_info = {}
    
    while completed < total_urls:
        try:
            result = result_queue.get(timeout=30)
            url, screenshot_base64, title, success, error_msg = result
            
            if url:
                if success and screenshot_base64:
                    all_success_data.append((url, screenshot_base64, title))
                else:
                    retry_count = retry_info.get(url, 0)
                    if retry_count < config.RETRY_ATTEMPTS:
                        retry_info[url] = retry_count + 1
                        retry_urls.append((url, retry_count + 1))
                        print(f"Queued for retry: {url[:50]}... (attempt {retry_count + 1})")
                    else:
                        all_failed_urls.append(url)
                        print(f"Permanently failed: {url[:50]}... - {error_msg[:50]}")
            
            completed += 1
            pbar.update(1)
            pbar.set_description(f"Progress: {completed}/{total_urls}")
            pbar.set_postfix(success=len(all_success_data), failed=len(all_failed_urls))
        
        except queue.Empty:
            if all(w.is_alive() for w in workers):
                continue
            else:
                print("All workers finished but not all tasks completed")
                break
    
    pbar.close()
    
    if retry_urls and config.RETRY_ATTEMPTS > 0:
        print(f"\nStarting retry phase for {len(retry_urls)} URLs")
        time.sleep(config.RETRY_DELAY)
        
        for url, retry_count in retry_urls:
            args_queue.put((url, 0, 1, retry_count))
        
        retry_pbar = tqdm(total=len(retry_urls), desc="Retry progress", unit="URL")
        
        for _ in range(len(retry_urls)):
            try:
                result = result_queue.get(timeout=30)
                url, screenshot_base64, title, success, error_msg = result
                
                if url and success and screenshot_base64:
                    all_success_data.append((url, screenshot_base64, title))
                    print(f"Retry successful: {url[:50]}...")
                else:
                    all_failed_urls.append(url)
                    print(f"Retry failed: {url[:50]}...")
                
                retry_pbar.update(1)
            
            except queue.Empty:
                break
        
        retry_pbar.close()
    
    for worker in workers:
        worker.join(timeout=5)
    
    print(f"\nCompleted: {len(all_success_data)} successful, {len(all_failed_urls)} failed")
    return all_success_data, all_failed_urls

def generate_html_report(success_data, failed_urls, output_file=config.OUTPUT_FILE):
    try:
        print(f"Generating HTML report: {output_file}")
        
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        
        html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Webpage Screenshot Report</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
            line-height: 1.6;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 20px rgba(0,0,0,0.1);
        }}
        h1, h2 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        .success-item {{
            margin: 25px 0;
            padding: 20px;
            border: 2px solid #27ae60;
            border-radius: 8px;
            background: #f8fff9;
            box-shadow: 0 2px 10px rgba(39, 174, 96, 0.1);
        }}
        .screenshot {{
            max-width: 100%;
            height: auto;
            border: 2px solid #bdc3c7;
            border-radius: 5px;
            margin: 15px 0;
            cursor: pointer;
            transition: all 0.3s ease;
        }}
        .screenshot:hover {{
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            transform: scale(1.01);
        }}
        .failed-list {{
            background: #ffeaea;
            padding: 20px;
            border-radius: 8px;
            margin: 15px 0;
            border: 2px solid #e74c3c;
        }}
        .url {{
            word-break: break-all;
            color: #2980b9;
            font-weight: bold;
            font-size: 16px;
        }}
        .stats {{
            background: #e8f4fc;
            padding: 15px;
            border-radius: 8px;
            margin: 15px 0;
            border: 2px solid #3498db;
        }}
        .success {{ color: #27ae60; font-weight: bold; }}
        .failed {{ color: #e74c3c; font-weight: bold; }}
        .timestamp {{ color: #7f8c8d; font-style: italic; }}
        .retry-info {{ color: #f39c12; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Webpage Screenshot Report</h1>
        
        <div class="stats">
            <p class="timestamp">Generated: {time.strftime("%Y-%m-%d %H:%M:%S")}</p>
            <p class="success">Successful screenshots: {len(success_data)}</p>
            <p class="failed">Failed URLs: {len(failed_urls)}</p>
            <p class="retry-info">Retry settings: {config.RETRY_ATTEMPTS} attempts, {config.RETRY_DELAY}s delay</p>
            <p>Total URLs: {len(success_data) + len(failed_urls)}</p>
            <p>Workers used: {config.MAX_WORKERS}</p>
            <p>Headless mode: {config.HEADLESS}</p>
        </div>
'''
        
        if success_data:
            html_content += '''
        <h2>Successful Screenshots</h2>
        <p>Click images to toggle original size</p>
        <div id="success-list">
'''
            
            for i, (url, screenshot_base64, title) in enumerate(success_data):
                html_content += f'''
            <div class="success-item">
                <h3>Screenshot #{i + 1}</h3>
                <p class="url">URL: <a href="{url}" target="_blank" rel="noopener">{url}</a></p>
                <p>Page title: {title or 'No title'}</p>
                <img class="screenshot" 
                     src="data:image/png;base64,{screenshot_base64}" 
                     alt="Screenshot: {url}" 
                     title="Click to toggle size"
                     onclick="this.style.maxWidth=this.style.maxWidth?'none':'100%'">
            </div>
'''
            
            html_content += '</div>'
        else:
            html_content += '<p>No successful screenshots</p>'
        
        if failed_urls:
            html_content += f'''
        <div class="failed-list">
            <h2>Failed URLs ({len(failed_urls)})</h2>
            <p>These URLs could not be accessed:</p>
            <ul>
'''
            
            for url in failed_urls:
                html_content += f'<li><a href="{url}" target="_blank" rel="noopener">{url}</a></li>'
            
            html_content += '''
            </ul>
        </div>
'''
        else:
            html_content += '<div class="success"><p>All URLs successfully captured!</p></div>'
        
        html_content += '''
    </div>
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const images = document.querySelectorAll('.screenshot');
            images.forEach(img => {
                img.addEventListener('click', function() {
                    this.style.maxWidth = this.style.maxWidth ? '' : '100%';
                    this.style.height = this.style.height ? '' : 'auto';
                });
            });
        });
    </script>
</body>
</html>'''
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"Report generated: {output_file}")
        return True
        
    except Exception as e:
        print(f"Report generation failed: {e}")
        return False

def main():
    print("Playwright multi-threaded screenshot task starting")
    
    print("Current configuration:")
    print(f"   - Worker count: {config.MAX_WORKERS}")
    print(f"   - Headless mode: {config.HEADLESS}")
    print(f"   - Proxy: {config.PROXY_SERVER or 'None'}")
    print(f"   - Retry attempts: {config.RETRY_ATTEMPTS}")
    print(f"   - Retry delay: {config.RETRY_DELAY}s")
    print(f"   - Timeout: {config.TIMEOUT}ms")
    
    urls = read_urls_from_file()
    if not urls:
        print("No URLs to process, exiting")
        return
    
    start_time = time.time()
    success_data, failed_urls = capture_screenshots_with_playwright(urls)
    end_time = time.time()
    
    if generate_html_report(success_data, failed_urls):
        print(f"Report saved: {os.path.abspath(config.OUTPUT_FILE)}")
    else:
        print("Report generation failed")
    
    total_time = end_time - start_time
    print(f"\nTask completed!")
    print(f"Total time: {total_time:.2f} seconds")
    print(f"Workers used: {config.MAX_WORKERS}")
    print(f"Success: {len(success_data)}")
    print(f"Failed: {len(failed_urls)}")
    
    if urls:
        avg_time = total_time / len(urls)
        print(f"Average time per URL: {avg_time:.2f} seconds")

if __name__ == "__main__":
    main()
