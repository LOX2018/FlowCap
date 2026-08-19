# coding=utf-8
"""通过指纹浏览器抓取 douyin.com/chat 的全部网络请求，
分析浏览器实际调用哪些 API 拉取会话列表、昵称、头像、消息内容。

用法：python debug_chat_api.py
"""
import json
import time
import os

from playwright.sync_api import sync_playwright

CHROME_EXE = r"C:\temp\dyautodm_test\vb_chromium\ungoogled-chromium_148.0.7778.215-1.1_windows_x64\chrome.exe"
PROFILE_DIR = r"C:\temp\dyautodm_test\auto_dm\accounts\测试小助理\profile"

CHAT_URL = "https://www.douyin.com/chat"

# 抓取的请求记录
captured = []

def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            executable_path=CHROME_EXE,
            headless=False,
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-extensions",
                "--disable-sync",
            ],
            ignore_default_args=["--no-sandbox"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_request(request):
            url = request.url
            # 只关注 API 请求，跳过静态资源
            if any(x in url for x in [
                "imapi.douyin.com", "www.douyin.com/aweme", "/im/",
                "get_message_by_init", "get_user_message", "user/info",
                "conversation", "stranger", "frontier"
            ]):
                entry = {
                    "url": url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data[:500] if request.post_data else None,
                }
                captured.append(entry)
                print(f"[REQ] {request.method} {url[:120]}")

        def on_response(response):
            url = response.url
            if any(x in url for x in [
                "imapi.douyin.com", "www.douyin.com/aweme", "/im/",
                "get_message_by_init", "get_user_message", "user/info",
                "conversation", "stranger", "frontier"
            ]):
                try:
                    body = response.body()
                    body_len = len(body)
                    # 尝试解析 JSON
                    try:
                        text = body.decode("utf-8", errors="replace")
                        if text.startswith("{"):
                            parsed = json.loads(text)
                            preview = json.dumps(parsed, ensure_ascii=False)[:500]
                        else:
                            # protobuf 或二进制
                            preview = f"[binary {body_len} bytes] {text[:200]}"
                    except Exception:
                        preview = f"[binary {body_len} bytes]"
                except Exception as e:
                    preview = f"[body read error: {e}]"

                print(f"[RESP] {response.status} {url[:120]} → {preview[:200]}")

                # 保存到 captured
                for entry in captured:
                    if entry["url"] == url and "response" not in entry:
                        entry["response_status"] = response.status
                        entry["response_body"] = preview
                        entry["response_body_len"] = body_len if 'body_len' in dir() else 0
                        break

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"==> 导航到 {CHAT_URL}")
        page.goto(CHAT_URL, wait_until="networkidle", timeout=30000)

        # 等待页面加载和 API 调用完成
        print("==> 等待 15 秒让页面完成全部 API 调用...")
        time.sleep(15)

        # 再滚动一下触发懒加载
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(5)
        except Exception:
            pass

        print(f"\n==> 共抓取 {len(captured)} 个 API 请求\n")

        # 按类型分类输出
        categories = {
            "get_message_by_init": [],
            "get_user_message": [],
            "im_user_info": [],
            "user_profile": [],
            "conversation": [],
            "stranger": [],
            "frontier_ws": [],
            "other_im": [],
        }

        for entry in captured:
            url = entry["url"]
            if "get_message_by_init" in url:
                categories["get_message_by_init"].append(entry)
            elif "get_user_message" in url:
                categories["get_user_message"].append(entry)
            elif "im/user/info" in url:
                categories["im_user_info"].append(entry)
            elif "user/profile/other" in url:
                categories["user_profile"].append(entry)
            elif "conversation" in url.lower():
                categories["conversation"].append(entry)
            elif "stranger" in url:
                categories["stranger"].append(entry)
            elif "frontier" in url:
                categories["frontier_ws"].append(entry)
            else:
                categories["other_im"].append(entry)

        for cat, items in categories.items():
            if items:
                print(f"\n{'='*60}")
                print(f"【{cat}】共 {len(items)} 个请求")
                print(f"{'='*60}")
                for i, item in enumerate(items):
                    print(f"\n  #{i}: {item['method']} {item['url'][:150]}")
                    if "response_body" in item:
                        print(f"  响应: {item['response_body'][:300]}")
                    # 打印关键请求头
                    hs = item.get("headers", {})
                    for hk in ["authorization", "cookie", "content-type", "referer"]:
                        if hk in hs:
                            val = hs[hk]
                            if len(val) > 100:
                                print(f"  {hk}: {val[:80]}...({len(val)} chars)")
                            else:
                                print(f"  {hk}: {val}")

        # 保存完整结果到文件
        out_path = os.path.join(os.path.dirname(__file__), "debug_chat_api_output.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(captured, f, ensure_ascii=False, indent=2)
        print(f"\n==> 完整结果已保存到: {out_path}")

        print("\n==> 关闭浏览器")
        context.close()

if __name__ == "__main__":
    main()
