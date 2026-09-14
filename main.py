"""
Dossier_Management — Document Pipeline CLI

Usage:
  python main.py ingest [--project-id PROJ-001]
  python main.py classify [--project-id PROJ-001]
  python main.py package PROJ-001
  python main.py run [--project-id PROJ-001]
  python main.py reset [--project-id PROJ-001]
  python main.py serve [--port 8000]
"""

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.classifier import Classifier
from src.config import project_data_dir
from src.logger import get_logger
from src.pipeline import DossierPipeline, run_full_pipeline

logger = get_logger("main")


def cmd_ingest(args):
    pipeline = DossierPipeline(args.project_id)
    pipeline.init()
    n = pipeline.ingest()
    if n == 0:
        print("No PDF files found. Place files in data/CLINS/, data/FE/, data/CE/")
        sys.exit(1)
    print(f"\nIngest complete: {n} pages indexed.")
    print(f"Pages indexed: {pipeline.total_pages}")


def cmd_classify(args):
    """Auto-classify PDFs in data/inbox/ into CLINS/FE/CE subfolders."""
    classifier = Classifier()
    out = classifier.classify_inbox()
    results = out["results"]
    unprocessed = out.get("unprocessed", [])

    if not results and not unprocessed:
        print("Inbox is empty. Drop PDFs into data/inbox/ first.")
        sys.exit(1)

    print("\nClassification results:")
    print(f"{'FILENAME':<40} {'TYPE':<6} {'CONF':<8} STATUS")
    print("-" * 70)
    for r in results:
        status = "archived" if r["archived"] else "REVIEW"
        print(
            f"{r['filename'][:39]:<40} {r['report_type']:<6} "
            f"{r['confidence']:<8} {status}"
        )
    print(
        "\nHigh-confidence files were auto-moved into data/CLINS|FE|CE/."
    )
    print(
        "Low-confidence / UNKNOWN files remain in data/inbox/ for manual review."
    )
    if unprocessed:
        print(
            "\nThe following file(s) could NOT be classified and were left in "
            "data/inbox/:"
        )
        for u in unprocessed:
            print(f"  - {u['filename']}: {u['reason']}")


def cmd_package(args):
    pipeline = DossierPipeline(args.project_id)
    pipeline.init()
    output_dir = (
        project_data_dir(args.project_id).parent
        / "Dossier_condensed"
        / args.project_id
    )
    result = pipeline.condense(output_dir=output_dir, top_n=args.top_n)
    print(f"\nCondense complete: {result['output_dir']}")
    print(
        f"  sources: {result['sources_processed']}, "
        f"pages dropped: {result['pages_dropped']}"
    )


def cmd_run(args):
    result = run_full_pipeline(args.project_id, top_n=args.top_n)
    print(f"\nFull pipeline complete: {result['output_dir']}")
    print(
        f"  sources: {result['sources_processed']}, "
        f"pages dropped: {result['pages_dropped']}"
    )


def cmd_reset(args):
    pipeline = DossierPipeline(args.project_id)
    pipeline.reset()
    print(f"Project '{args.project_id}' has been reset.")


# Ports tried in order when --port is not given. Same shortlist the bootstrap
# .bat used to scan with netstat; binding the socket directly is a truer test
# than parsing netstat output, and it needs no external command.
PORT_CANDIDATES = (8000, 8001, 8080, 8888, 9000)


def _free_port() -> int:
    """Return the first free port among PORT_CANDIDATES, or give up loudly."""
    for candidate in PORT_CANDIDATES:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    raise SystemExit(
        f"No free port among {PORT_CANDIDATES}. Free one and start again."
    )


def cmd_serve(args):
    import uvicorn

    # --- WatchFiles scope ------------------------------------------------
    # OneDrive real-time sync touches thousands of files under venv/. If
    # WatchFiles watches the whole tree (the `reload=True` default), every
    # sync event reloads the server in an endless loop and the source never
    # settles. Fix: restrict the watcher to SOURCE-CODE ONLY via
    # `reload_dirs` -- uvicorn only watches these directories, so venv/ (and
    # every other dir) is never observed. `reload_excludes` adds
    # belt-and-braces ignores for compiled artifacts inside those dirs.
    #
    # NOTE on format (from uvicorn source): `reload_dirs` must list
    # DIRECTORIES (files are filtered out by is_dir() and ignored).
    # `reload_excludes` is matched with pathlib.Path.match() -- filename
    # only, no cross-directory `**` -- so use bare directory names
    # ("__pycache__"), NOT globs like "venv/**" (those match nothing).
    # A frozen bundle has no .py sources on disk to watch, and reload=True would
    # re-launch sys.executable -- which is the packaged exe itself -- as the
    # reloader child. Hot reload therefore stays available in a source checkout
    # only.
    reload_enabled = not getattr(sys, "frozen", False) and not args.no_reload

    port = args.port or _free_port()
    host = args.host
    shown = "localhost" if host in ("127.0.0.1", "localhost") else host
    url = f"http://{shown}:{port}"

    print(f"Starting server at {url}")
    print(f"API docs: {url}/docs")
    if reload_enabled:
        print("Hot-reload watching source only (src/, static/); venv/ excluded.")

    if not args.no_browser:
        # uvicorn.run blocks the main thread, so the UI has to be opened from a
        # timer instead of after the call returns. The delay covers interpreter
        # start-up and app mounting. This replaces the "start http://..." line
        # the .bat used to run, so the packaged exe is self-contained.
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    if not reload_enabled:
        # Import the app object directly -- with no reloader to spawn there is
        # no reason to go through uvicorn's import-by-string path.
        from src.api import app as fastapi_app

        uvicorn.run(fastapi_app, host=host, port=port)
        return

    ROOT = Path(__file__).resolve().parent
    uvicorn.run(
        "src.api:app",
        host=host,
        port=port,
        reload=True,
        reload_dirs=[
            str(ROOT / "src"),
            str(ROOT / "static"),
        ],
        reload_excludes=[
            "__pycache__",
            "*.pyc",
        ],
    )


def main():
    parser = argparse.ArgumentParser(
        description="Dossier_Management — Document Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py classify
  python main.py ingest --project-id PROJ-001
  python main.py package PROJ-001
  python main.py run
  python main.py reset
  python main.py serve --port 8000
        """,
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # classify
    p_classify = sub.add_parser(
        "classify",
        help="Auto-sort inbox PDFs into data/CLINS|FE|CE by first-page text",
    )
    p_classify.add_argument("--project-id", default="default")
    p_classify.set_defaults(func=cmd_classify)

    # ingest
    p_ingest = sub.add_parser("ingest", help="Parse and index all PDFs in data/")
    p_ingest.add_argument("--project-id", default="default")
    p_ingest.set_defaults(func=cmd_ingest)

    # package
    p_pkg = sub.add_parser(
        "package", help="Denoise source PDFs -> cleaned per-document PDFs"
    )
    p_pkg.add_argument("project_id", nargs="?", default="default")
    p_pkg.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Max pages kept PER report type (CLINS/FE/CE). "
             "Default: no cap (keep all survivors).",
    )
    p_pkg.set_defaults(func=cmd_package)

    # run
    p_run = sub.add_parser("run", help="One-shot: ingest + condense")
    p_run.add_argument("--project-id", default="default")
    p_run.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Max pages kept PER report type (CLINS/FE/CE). "
             "Default: no cap (keep all survivors).",
    )
    p_run.set_defaults(func=cmd_run)

    # reset
    p_reset = sub.add_parser("reset", help="Clear index and screenshots")
    p_reset.add_argument("--project-id", default="default")
    p_reset.set_defaults(func=cmd_reset)

    # serve
    p_serve = sub.add_parser("serve", help="Start the API + frontend server")
    p_serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on. Default: first free port among "
             + ", ".join(str(p) for p in PORT_CANDIDATES) + ".",
    )
    p_serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Loopback by default, which keeps Windows from "
             "raising a firewall prompt; use 0.0.0.0 to expose the UI on the "
             "local network.",
    )
    p_serve.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable hot reload. Always off in a packaged build.",
    )
    p_serve.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the UI in the default browser.",
    )
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if args.command is None:
        # A double-clicked exe passes no arguments at all, so serving the UI is
        # the only sensible default. Nothing would be readable if the app just
        # printed help into a window that closes immediately.
        args = parser.parse_args(["serve"])

    args.func(args)


if __name__ == "__main__":
    main()
