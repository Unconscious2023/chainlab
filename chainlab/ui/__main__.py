import argparse
import sys

from chainlab.ui.server import serve


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="chainlab.ui", description="启动信号链路仿真界面")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    serve(args.port, not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
