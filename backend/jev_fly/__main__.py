import argparse
import os

from aiohttp import web

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Jev × FlyBrain application shell")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
