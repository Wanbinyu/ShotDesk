"""启动分镜成片台。"""

import argparse
import sys
import webbrowser
from pathlib import Path

from shotdesk.server import make_server, prepare_db

ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> None:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="分镜成片台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8775)
    parser.add_argument("--db", default="")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    db_path = Path(args.db) if args.db else ROOT / "data" / "shotdesk.db"
    prepare_db(db_path)
    server = None
    error = None
    port = args.port
    for candidate in range(args.port, args.port + 10):
        try:
            server = make_server(db_path, args.host, candidate)
            port = candidate
            break
        except OSError as exc:
            error = exc
    if server is None:
        raise SystemExit(f"端口没绑上：{error}")
    url = f"http://{args.host}:{port}"
    print(f"分镜成片台已打开 {url}")
    print("关闭这个窗口即停止。")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
