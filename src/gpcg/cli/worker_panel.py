"""GPCG Worker Panel — TUI for managing the local worker and VPS pipeline.

A rich terminal interface built with Textual that shows:
- Worker status (online, GPU, current job)
- Job queue (mapping + generation)
- Recent videos
- Live logs
- Controls (start/stop worker, pause/resume automation, collect ideas)

Usage:
    gpcg panel
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    Log,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)


# ── Config ───────────────────────────────────────────────────────────────────

def _get_config():
    """Read worker config from env vars, falling back to systemd service file."""
    vps_url = os.environ.get("GPCG_VPS_URL", "")
    worker_id = os.environ.get("GPCG_WORKER_ID", "home-pc")
    api_key = os.environ.get("GPCG_WORKER_API_KEY", "")

    # If not in env, try reading from systemd service file
    if not vps_url or not api_key:
        try:
            import subprocess as sp
            r = sp.run(
                ["systemctl", "--user", "cat", "gpcg-worker"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("Environment=GPCG_VPS_URL="):
                    vps_url = line.split("=", 2)[2]
                elif line.startswith("Environment=GPCG_WORKER_ID="):
                    worker_id = line.split("=", 2)[2]
                elif line.startswith("Environment=GPCG_WORKER_API_KEY="):
                    api_key = line.split("=", 2)[2]
        except Exception:
            pass

    return {
        "vps_url": vps_url,
        "worker_id": worker_id,
        "api_key": api_key,
    }


def _api_get(cfg: dict, path: str) -> dict | list | None:
    """GET from VPS API with worker auth."""
    try:
        url = f"{cfg['vps_url']}/api{path}"
        r = requests.get(url, headers={"X-Worker-Key": cfg["api_key"]}, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def _api_post(cfg: dict, path: str, json_data: dict = None) -> dict | None:
    """POST to VPS API with worker auth."""
    try:
        url = f"{cfg['vps_url']}/api{path}"
        r = requests.post(url, headers={"X-Worker-Key": cfg["api_key"]}, json=json_data or {}, timeout=10)
        if r.status_code in (200, 201):
            return r.json()
        return {"error": f"{r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def _systemctl(action: str) -> str:
    """Run systemctl --user command on gpcg-worker."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", action, "gpcg-worker"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or r.stderr.strip() or "OK"
    except Exception as e:
        return f"Error: {e}"


def _systemctl_status() -> str:
    """Get systemd service status."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "gpcg-worker"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()  # "active", "inactive", "failed"
    except Exception:
        return "unknown"


def _get_gpu_info() -> str:
    """Get GPU name and usage via nvidia-smi."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(", ")
            if len(parts) >= 4:
                return f"{parts[0]} | GPU {parts[1]}% | VRAM {parts[2]}/{parts[3]}MB"
        return "No GPU detected"
    except Exception:
        return "nvidia-smi not available"


def _get_recent_logs(lines: int = 50) -> str:
    """Get recent journalctl logs for gpcg-worker."""
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", "gpcg-worker", "--no-pager",
             "-n", str(lines), "--output=cat"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout
    except Exception:
        return "Failed to read logs"


# ── Status Widgets ────────────────────────────────────────────────────────────

class StatusCard(Static):
    """A card showing a label and value."""

    def __init__(self, label: str, value: str = "—", color: str = "white"):
        super().__init__()
        self.label = label
        self._value = value
        self._color = color

    def update_value(self, value: str, color: str = "white"):
        self._value = value
        self._color = color
        self.refresh()

    def render(self) -> Text:
        return Text.assemble(
            (f"{self.label}\n", "dim"),
            (self._value, self._color),
        )


# ── Main App ──────────────────────────────────────────────────────────────────

class WorkerPanelApp(App):
    """GPCG Worker Panel — terminal dashboard for the local worker."""

    CSS = """
    Screen {
        background: $surface;
    }
    #status-bar {
        height: 3;
        dock: top;
        layout: horizontal;
    }
    .status-card {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: 100%;
        text-align: center;
    }
    #main-content {
        height: 1fr;
    }
    RichLog {
        border: round $accent;
    }
    DataTable {
        height: 1fr;
    }
    .control-btn {
        width: auto;
        margin: 0 1;
    }
    #controls-bar {
        height: 3;
        dock: bottom;
        layout: horizontal;
        align: center middle;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("s", "start_worker", "Start Worker"),
        Binding("x", "stop_worker", "Stop Worker"),
        Binding("p", "pause_automation", "Pause Auto"),
        Binding("a", "resume_automation", "Resume Auto"),
        Binding("c", "collect_ideas", "Collect Ideas"),
        Binding("l", "focus_logs", "Logs"),
        Binding("j", "focus_jobs", "Jobs"),
        Binding("v", "focus_videos", "Videos"),
    ]

    title = "GPCG Worker Panel"
    refresh_counter = reactive(0)

    def __init__(self):
        super().__init__()
        self.cfg = _get_config()
        self._log_thread: Optional[threading.Thread] = None
        self._log_running = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # Status bar — 4 cards
        with Horizontal(id="status-bar"):
            yield StatusCard("Worker", "—")
            yield StatusCard("GPU", "—")
            yield StatusCard("Current Job", "—")
            yield StatusCard("Automation", "—")

        # Main content — tabs
        with TabbedContent(id="main-content"):
            with TabPane("Jobs", id="jobs-tab"):
                yield DataTable(id="jobs-table")
            with TabPane("Videos", id="videos-tab"):
                yield DataTable(id="videos-table")
            with TabPane("Logs", id="logs-tab"):
                yield RichLog(id="logs-view", wrap=True, markup=True)
            with TabPane("Ideas", id="ideas-tab"):
                yield DataTable(id="ideas-table")

        # Controls bar
        with Horizontal(id="controls-bar"):
            yield Button("Start (s)", id="btn-start", classes="control-btn")
            yield Button("Stop (x)", id="btn-stop", classes="control-btn")
            yield Button("Pause Auto (p)", id="btn-pause", classes="control-btn")
            yield Button("Resume Auto (a)", id="btn-resume", classes="control-btn")
            yield Button("Collect Ideas (c)", id="btn-collect", classes="control-btn")
            yield Button("Refresh (r)", id="btn-refresh", classes="control-btn")

        yield Footer()

    def on_mount(self) -> None:
        # Setup tables
        jobs_table = self.query_one("#jobs-table", DataTable)
        jobs_table.add_columns("ID", "Type", "Status", "Game", "Stage", "Worker", "Created")
        jobs_table.cursor_type = "row"

        videos_table = self.query_one("#videos-table", DataTable)
        videos_table.add_columns("ID", "Game", "Duration", "Status", "Job", "Created")
        videos_table.cursor_type = "row"

        ideas_table = self.query_one("#ideas-table", DataTable)
        ideas_table.add_columns("ID", "Status", "Type", "Score", "Title", "Source")
        ideas_table.cursor_type = "row"

        # Initial load
        self.do_refresh()
        self.start_log_stream()

        # Auto-refresh every 5s
        self.set_interval(5, self.do_refresh)

    def start_log_stream(self):
        """Start streaming journalctl logs."""
        self._log_running = True
        self._log_thread = threading.Thread(target=self._stream_logs, daemon=True)
        self._log_thread.start()

    def _stream_logs(self):
        """Stream journalctl -f in background."""
        try:
            proc = subprocess.Popen(
                ["journalctl", "--user", "-u", "gpcg-worker", "--no-pager",
                 "-f", "-n", "0", "--output=cat"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            for line in proc.stdout:
                if not self._log_running:
                    break
                # Strip timestamp prefix and push to UI
                clean = line.strip()
                if clean:
                    try:
                        self.call_from_thread(self._append_log, clean)
                    except Exception:
                        pass
        except Exception:
            pass

    def _append_log(self, line: str):
        """Append a log line to the logs view."""
        log_view = self.query_one("#logs-view", RichLog)
        # Color-code by level
        if "ERROR" in line:
            log_view.write(line, style="red")
        elif "WARNING" in line:
            log_view.write(line, style="yellow")
        elif "completed" in line or "SUCCESS" in line:
            log_view.write(line, style="green")
        elif "stage=" in line:
            log_view.write(line, style="cyan")
        else:
            log_view.write(line)

    def do_refresh(self):
        """Refresh all data from VPS."""
        if not self.cfg["vps_url"]:
            return

        # Worker status
        svc_status = _systemctl_status()
        cards = list(self.query(StatusCard))
        if len(cards) >= 4:
            color = "green" if svc_status == "active" else "red"
            cards[0].update_value(svc_status.upper(), color)

            # GPU
            cards[1].update_value(_get_gpu_info(), "cyan")

            # Current job — from workers list
            workers_data = _api_get(self.cfg, "/workers")
            current_job = None
            activity = "Idle"
            if workers_data and isinstance(workers_data, dict):
                for w in workers_data.get("workers", []):
                    if w.get("worker_id") == self.cfg["worker_id"]:
                        activity = w.get("current_activity", "Idle")
                        current_job = w.get("current_job_id")
                        break
            cards[2].update_value(
                f"{activity}" + (f" (#{current_job})" if current_job else ""),
                "yellow" if current_job else "dim",
            )

            # Automation status
            auto_data = _api_get(self.cfg, "/panel/automation")
            if auto_data and isinstance(auto_data, dict):
                auto_status = auto_data.get("status", "—")
                color = "green" if auto_status == "running" else "red"
                cards[3].update_value(auto_status.upper(), color)

        # Jobs table
        jobs = _api_get(self.cfg, "/panel/jobs?limit=30")
        jobs_table = self.query_one("#jobs-table", DataTable)
        jobs_table.clear()
        if jobs and isinstance(jobs, list):
            for j in jobs:
                jobs_table.add_row(
                    str(j.get("id", "")),
                    j.get("job_type", ""),
                    j.get("status", ""),
                    str(j.get("game_id", "")),
                    j.get("stage", "") or "",
                    j.get("worker_id", "") or "",
                    (j.get("created_at", "") or "")[:19],
                )

        # Videos table
        videos = _api_get(self.cfg, "/panel/videos?limit=30")
        videos_table = self.query_one("#videos-table", DataTable)
        videos_table.clear()
        if videos and isinstance(videos, list):
            for v in videos:
                dur = v.get("duration", 0)
                videos_table.add_row(
                    str(v.get("id", "")),
                    str(v.get("game_id", "")),
                    f"{dur:.0f}s" if dur else "—",
                    v.get("status", ""),
                    str(v.get("job_id", "") or "—"),
                    (v.get("created_at", "") or "")[:19],
                )

        # Ideas table
        ideas = _api_get(self.cfg, "/panel/ideas?limit=30")
        ideas_table = self.query_one("#ideas-table", DataTable)
        ideas_table.clear()
        if ideas and isinstance(ideas, list):
            for ki in ideas:
                score = ki.get("editorial_score", 0)
                ideas_table.add_row(
                    str(ki.get("id", "")),
                    ki.get("status", ""),
                    ki.get("item_type", ""),
                    f"{score:.0f}",
                    (ki.get("title", "") or "")[:60],
                    ki.get("source_name", "") or "",
                )

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_refresh(self):
        self.do_refresh()
        self.notify("Refreshed", timeout=1)

    def action_start_worker(self):
        result = _systemctl("start")
        self.notify(f"Worker: {result}", timeout=2)

    def action_stop_worker(self):
        result = _systemctl("stop")
        self.notify(f"Worker: {result}", timeout=2)

    def action_pause_automation(self):
        r = _api_post(self.cfg, "/panel/automation/pause")
        self.notify(f"Automation: {r}" if r else "Failed", timeout=2)
        self.do_refresh()

    def action_resume_automation(self):
        r = _api_post(self.cfg, "/panel/automation/resume")
        self.notify(f"Automation: {r}" if r else "Failed", timeout=2)
        self.do_refresh()

    def action_collect_ideas(self):
        r = _api_post(self.cfg, "/panel/collect-ideas")
        self.notify(f"Collection: {r}" if r else "Failed", timeout=2)

    def action_focus_logs(self):
        self.query_one(TabbedContent).active = "logs-tab"

    def action_focus_jobs(self):
        self.query_one(TabbedContent).active = "jobs-tab"

    def action_focus_videos(self):
        self.query_one(TabbedContent).active = "videos-tab"

    # ── Button handlers ───────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed):
        btn_id = event.button.id
        if btn_id == "btn-start":
            self.action_start_worker()
        elif btn_id == "btn-stop":
            self.action_stop_worker()
        elif btn_id == "btn-pause":
            self.action_pause_automation()
        elif btn_id == "btn-resume":
            self.action_resume_automation()
        elif btn_id == "btn-collect":
            self.action_collect_ideas()
        elif btn_id == "btn-refresh":
            self.action_refresh()

    def on_unmount(self):
        self._log_running = False


def run_worker_panel():
    """Entry point for the worker panel TUI."""
    app = WorkerPanelApp()
    app.run()
