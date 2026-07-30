"""GPCG CLI — Typer-based entry point.

Usage:
    gpcg --help
    gpcg worker          # run the background worker (jobs + inbox watcher)
    gpcg inbox-scan      # one-shot inbox scan
    gpcg serve           # run the FastAPI + static frontend server
    gpcg dev             # run api + frontend in dev mode (concurrent)
    gpcg db-init         # create tables
    gpcg generate --game <id>   # trigger a single generation job
    gpcg creative-test -t <topic> -f <fact>  # smoke-test the CreativeEngine
"""

from __future__ import annotations

import typer

from gpcg.cli.commands import (
    analyze_gameplay,
    creative_test,
    db_init,
    dev,
    generate,
    inbox_scan,
    serve,
    set_camera_type,
    worker,
)

app = typer.Typer(
    name="gpcg",
    help="gameplay-content-generator — local-first gameplay to Shorts pipeline.",
    no_args_is_help=True,
)

app.command()(db_init)
app.command()(analyze_gameplay)
app.command()(creative_test)
app.command()(dev)
app.command()(generate)
app.command()(inbox_scan)
app.command()(serve)
app.command()(set_camera_type)
app.command()(worker)

if __name__ == "__main__":
    app()
