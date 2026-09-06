"""
desktop.py — 客户端启动入口
- 启动 Flask (后台)
- 自动开浏览器到 http://127.0.0.1:5050
- 单实例: 关闭浏览器后, 用 Ctrl+C 停服务
"""
import os
import sys
import time
import socket
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))


def find_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_server(url: str, timeout: float = 120):
    import urllib.request
    import urllib.error
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    return False


def main():
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    from app import app, _do_full_update, _scheduler

    def run_server():
        _do_full_update()
        t = threading.Thread(target=_scheduler, daemon=True)
        t.start()
        app.run(host="127.0.0.1", port=port, debug=False,
                use_reloader=False, threaded=True)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    print(f"[desktop] waiting for {url} ...")
    if not wait_for_server(url + "/api/state", timeout=120):
        print("[desktop] 服务启动超时, 请检查网络或 Blave API 限流")
        input("按回车退出...")
        return
    print(f"[desktop] 服务就绪: {url}")
    print(f"[desktop] 打开浏览器 ... (关闭浏览器后 Ctrl+C 退出)")

    # 自动开浏览器 (默认浏览器)
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[desktop] 自动开浏览器失败: {e}")
        print(f"[desktop] 请手动访问: {url}")

    # 阻塞, 等待用户 Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[desktop] 退出")


if __name__ == "__main__":
    main()
