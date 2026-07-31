"""CLI command implementations for GPCG."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from gpcg.config import PROJECT_ROOT, get_settings
from gpcg.logging import configure_logging

console = Console()


def db_init() -> None:
    """Create database tables."""
    from gpcg.infrastructure.database import init_db

    configure_logging()
    init_db()
    console.print("[green]✓ Database initialized[/green]")


def inbox_scan() -> None:
    """One-shot scan of the gameplay inbox."""
    from gpcg.application.ingestion_service import IngestionService

    configure_logging()
    svc = IngestionService()
    discovered = svc.scan_once()
    console.print(f"[green]✓ Scan complete: {discovered} new file(s) processed[/green]")


def worker() -> None:
    """Run the background worker (inbox watcher + job processor)."""
    from gpcg.application.worker import run_worker

    configure_logging()
    run_worker()


def remote_worker(
    vps_url: str = typer.Option("", "--vps-url", help="VPS API URL (e.g., https://brunointegrations.com/gpcg)"),
    worker_id: str = typer.Option("", "--worker-id", help="Unique worker ID (e.g., home-pc)"),
    api_key: str = typer.Option("", "--api-key", help="Worker API key (shared secret)"),
    storage_dir: str = typer.Option("", "--storage-dir", help="Local storage directory for gameplay files"),
    capabilities: str = typer.Option("", "--capabilities", help="Comma-separated capabilities (e.g., mapping,generation)"),
) -> None:
    """Run the remote worker (Compute Plane — connects to VPS Control Plane).

    The worker registers with the VPS, sends heartbeats, polls for jobs,
    downloads gameplays, runs processing locally (GPU), and reports results.

    All config can be passed via CLI flags or environment variables:
      GPCG_VPS_URL, GPCG_WORKER_ID, GPCG_WORKER_API_KEY,
      GPCG_WORKER_STORAGE, GPCG_WORKER_CAPABILITIES

    Examples:
        gpcg remote-worker --vps-url https://brunointegrations.com/gpcg \\
            --worker-id home-pc --api-key <secret>
    """
    from gpcg.worker.remote_worker import run_remote_worker

    configure_logging()
    run_remote_worker(
        vps_url=vps_url,
        worker_id=worker_id,
        api_key=api_key,
        storage_dir=storage_dir,
        capabilities=capabilities,
    )


def serve() -> None:
    """Run the FastAPI server (serves API + built frontend)."""
    configure_logging()
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "gpcg.api.app:create_app",
        factory=True,
        host=settings.gpcg_host,
        port=settings.gpcg_port,
        reload=False,
    )


def dev() -> None:
    """Run API + frontend in dev mode concurrently."""
    configure_logging()
    settings = get_settings()
    api_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "gpcg.api.app:create_app",
        "--factory",
        "--host",
        settings.gpcg_host,
        "--port",
        str(settings.gpcg_port),
        "--reload",
    ]
    frontend_dir = PROJECT_ROOT / "frontend"
    fe_cmd = ["npm", "run", "dev"]
    console.print("[cyan]Starting API + frontend in dev mode...[/cyan]")
    procs = []
    try:
        if (frontend_dir / "package.json").exists():
            procs.append(subprocess.Popen(fe_cmd, cwd=str(frontend_dir)))
        procs.append(subprocess.Popen(api_cmd))
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def generate(
    game: str = typer.Option(..., "--game", "-g", help="Game canonical name or id"),
    count: int = typer.Option(1, "--count", "-n", help="Number of videos to generate"),
) -> None:
    """Trigger generation job(s) for a game."""
    from gpcg.application.generation_service import GenerationService

    configure_logging()
    svc = GenerationService()
    for _ in range(count):
        job = svc.create_job(game)
        console.print(f"[green]✓ Created job {job.id} for game '{game}'[/green]")


def creative_test(
    topic: str = typer.Option(..., "--topic", "-t", help="Topic / game name"),
    fact: str = typer.Option(..., "--fact", "-f", help="Fact / curiosity to tell"),
    style: str = typer.Option("humor", "--style", "-s", help="Style preset name"),
    context: str = typer.Option("", "--context", "-c", help="Extra context (optional)"),
) -> None:
    """Smoke-test the CreativeEngine in isolation (no video generation).

    Example:
        gpcg creative-test -t "Bully" -f "Você pode dar banhos de privada" -s humor
    """
    from gpcg.application.creative_engine import CreativeEngine, CREATIVE_PRESETS, get_style

    configure_logging()
    if style not in CREATIVE_PRESETS:
        console.print(
            f"[yellow]Unknown style '{style}'. Available: "
            f"{', '.join(CREATIVE_PRESETS.keys())}. Using 'humor'.[/yellow]"
        )
        style = "humor"

    console.print(f"[cyan]CreativeEngine smoke test[/cyan]")
    console.print(f"  model : {get_settings().gpcg_creative_engine_model}")
    console.print(f"  style : {style} ({get_style(style).label})")
    console.print(f"  topic : {topic}")
    console.print(f"  fact  : {fact[:80]}{'...' if len(fact) > 80 else ''}")
    console.print()

    engine = CreativeEngine()
    material = engine.generate_creative_material(
        topic=topic, fact=fact, context=context, style=get_style(style)
    )

    if not material.success:
        console.print(f"[red]✗ CreativeEngine failed: {material.error}[/red]")
        return

    console.print(f"[green]✓ Success — latency {material.latency_ms}ms[/green]\n")

    def _print_list(label: str, items: list[str]) -> None:
        console.print(f"[bold]{label}[/bold]")
        if not items:
            console.print("  (nenhum)")
        for i, it in enumerate(items, 1):
            console.print(f"  {i}. {it}")
        console.print()

    _print_list("HOOKS", material.hooks)
    _print_list("ANGLES", material.angles)
    _print_list("PUNCHLINES", material.punchlines)
    _print_list("OBSERVATIONS", material.observations)


def analyze_gameplay(
    source: str = typer.Option(..., "--source", "-s", help="GameplaySource ID or video file path"),
    no_asr: bool = typer.Option(False, "--no-asr", help="Skip audio transcription"),
    no_score: bool = typer.Option(False, "--no-score", help="Skip interesting score (faster)"),
    save_json: bool = typer.Option(True, "--save-json/--no-save-json", help="Save analysis JSON"),
    persist: bool = typer.Option(True, "--persist/--no-persist", help="Persist to database"),
    camera_type: str = typer.Option(
        "", "--camera-type", "-c",
        help="Camera perspective: third_person, first_person, top_down, isometric, fixed. "
             "Enables cascaded pipeline (YOLO + crop + VLM). Empty = use game's camera_type from DB.",
    ),
    save_crops: bool = typer.Option(False, "--save-crops", help="Save player crops to data/gameplay_analysis/crops/ for debugging"),
) -> None:
    """Analyze a gameplay recording and build the semantic event index.

    This is the MVP verification command — runs the full analysis pipeline
    (coarse → adaptive refine → ASR → merge → interesting score) and
    prints the resulting event timeline.

    When --camera-type is set (or the game's camera_type is configured in DB),
    the cascaded pipeline is used: YOLO detects the player → crop + upscale →
    VLM classifies the player's action on the crop + VLM describes the
    environment on the full frame. This dramatically improves accuracy for
    third-person games where the player is small in the frame.

    Examples:
        gpcg analyze-gameplay -s 1                    # analyze source #1 from DB
        gpcg analyze-gameplay -s /path/to/game.mp4    # analyze a file directly
        gpcg analyze-gameplay -s 1 --no-asr --no-score  # fast visual-only pass
        gpcg analyze-gameplay -s 1 -c third_person    # force cascaded pipeline
        gpcg analyze-gameplay -s 1 -c third_person --save-crops  # debug crops
    """
    from gpcg.application.gameplay_analyzer import GameplayAnalyzer
    from gpcg.application.gameplay_index_service import GameplayIndexService
    from gpcg.infrastructure.database import session_scope
    from gpcg.infrastructure.media import probe

    configure_logging()

    source_id = 0
    source_path: Path
    resolved_camera_type = camera_type or ""  # from --camera-type flag

    # Resolve source: integer = DB source ID, else = file path
    try:
        sid = int(source)
        with session_scope() as session:
            from gpcg.domain.models import GameplaySource, Game
            src = session.get(GameplaySource, sid)
            if src is None:
                console.print(f"[red]✗ GameplaySource #{sid} not found[/red]")
                return
            source_id = sid
            source_path = Path(src.file_path)
            # If no explicit camera_type flag, try to get it from the game
            if not resolved_camera_type and src.game_id:
                game = session.get(Game, src.game_id)
                if game and game.camera_type and game.camera_type != "unknown":
                    resolved_camera_type = game.camera_type
    except ValueError:
        source_path = Path(source)
        if not source_path.exists():
            console.print(f"[red]✗ File not found: {source_path}[/red]")
            return

    # Default to "unknown" if not resolved (legacy full-frame analysis)
    if not resolved_camera_type:
        resolved_camera_type = "unknown"

    info = probe(source_path)
    console.print(f"[cyan]Gameplay Analysis[/cyan]")
    console.print(f"  source      : {source_path.name}")
    console.print(f"  duration    : {info.duration:.1f}s ({info.duration/60:.1f}min)")
    console.print(f"  video       : {info.width}x{info.height} @ {info.fps:.0f}fps")
    console.print(f"  audio       : {'yes' if info.has_audio else 'no'}")
    console.print(f"  ASR         : {'disabled' if no_asr else 'enabled'}")
    console.print(f"  scoring     : {'disabled' if no_score else 'enabled'}")
    console.print(f"  camera_type : {resolved_camera_type}")
    cascade = resolved_camera_type != "unknown"
    console.print(f"  pipeline    : {'cascaded (YOLO + crop + VLM)' if cascade else 'legacy (full-frame VLM)'}")
    crops_dir: Optional[Path] = None
    if save_crops and cascade:
        crops_dir = Path("data/gameplay_analysis/crops")
        console.print(f"  crops saved : {crops_dir}/")
    console.print()

    analyzer = GameplayAnalyzer(camera_type=resolved_camera_type)

    def _progress(stage: str, pct: float) -> None:
        bar = "█" * int(pct * 20) + "░" * (20 - int(pct * 20))
        console.print(f"\r  [{bar}] {stage} {pct*100:.0f}%", end="")

    timeline = analyzer.analyze(
        source_path,
        source_id=source_id,
        enable_asr=not no_asr,
        enable_interesting_score=not no_score,
        progress_callback=_progress,
        save_crops_to=crops_dir,
    )
    console.print()  # newline after progress bar

    console.print(f"\n[green]✓ Analysis complete: {timeline.event_count} events[/green]\n")

    # Print event timeline
    if timeline.events:
        console.print("[bold]Event Timeline:[/bold]")
        for i, ev in enumerate(timeline.events):
            conf_color = "green" if ev.visual_confidence >= 0.7 else ("yellow" if ev.visual_confidence >= 0.4 else "red")
            int_color = "green" if ev.interesting_score >= 0.6 else ("yellow" if ev.interesting_score >= 0.3 else "dim")
            console.print(
                f"  [{ev.start_time:7.1f}-{ev.end_time:7.1f}] "
                f"[{conf_color}]{ev.event_type:20s}[/{conf_color}] "
                f"conf={ev.visual_confidence:.2f} "
                f"[{int_color}]int={ev.interesting_score:.2f}[/{int_color}]"
            )
            if ev.description:
                desc = ev.description[:100] + ("..." if len(ev.description) > 100 else "")
                console.print(f"    → {desc}")
            if ev.transcript:
                tr = ev.transcript[:80] + ("..." if len(ev.transcript) > 80 else "")
                console.print(f"    💬 {tr}")

    # Save JSON
    if save_json:
        index_svc = GameplayIndexService()
        json_path = index_svc.save_analysis_json(timeline)
        console.print(f"\n[dim]Analysis JSON: {json_path}[/dim]")

    # Persist to DB
    if persist and source_id > 0:
        with session_scope() as session:
            index_svc = GameplayIndexService()
            count = index_svc.persist_timeline(session, timeline, source_id=source_id)
            console.print(f"[green]✓ Persisted {count} events to database[/green]")
    elif persist and source_id == 0:
        console.print("[yellow]⚠ Not persisting: no source_id (file mode)[/yellow]")


def set_camera_type(
    game: str = typer.Option(..., "--game", "-g", help="Game name (canonical_name) or ID"),
    camera_type: str = typer.Option(
        ..., "--camera-type", "-c",
        help="Camera perspective: third_person, first_person, top_down, isometric, fixed, unknown",
    ),
) -> None:
    """Set the camera perspective for a game.

    This tells the gameplay analyzer how to extract frames for analysis:
    - third_person: player visible on screen (center-low). YOLO + crop + upscale.
    - first_person: player's arms/weapon in lower corners. Crop lower third.
    - top_down / isometric: player small, centered. Crop + upscale.
    - fixed: static camera. Rely on YOLO person detection.
    - unknown: legacy full-frame VLM analysis (default).

    Examples:
        gpcg set-camera-type -g Bully -c third_person
        gpcg set-camera-type -g 1 -c first_person
    """
    from gpcg.domain.models import CameraType
    from gpcg.infrastructure.database import session_scope
    from sqlalchemy import select

    configure_logging()

    # Validate camera_type
    valid_types = {ct.value for ct in CameraType}
    if camera_type not in valid_types:
        console.print(f"[red]✗ Invalid camera_type: {camera_type}[/red]")
        console.print(f"  Valid options: {', '.join(sorted(valid_types))}")
        return

    with session_scope() as session:
        from gpcg.domain.models import Game
        # Resolve game: integer = ID, else = name
        try:
            gid = int(game)
            g = session.get(Game, gid)
        except ValueError:
            g = session.execute(
                select(Game).where(Game.canonical_name.ilike(f"%{game}%"))
            ).scalars().first()

        if g is None:
            console.print(f"[red]✗ Game not found: {game}[/red]")
            return

        old_type = g.camera_type
        g.camera_type = camera_type
        console.print(f"[green]✓ {g.canonical_name}: camera_type '{old_type}' → '{camera_type}'[/green]")
        console.print()
        if camera_type == "third_person":
            console.print("[dim]Pipeline: YOLO detects player → crop + upscale → VLM classifies action[/dim]")
        elif camera_type == "first_person":
            console.print("[dim]Pipeline: crop lower third → VLM inspects for weapons/hands[/dim]")
        elif camera_type == "unknown":
            console.print("[dim]Pipeline: legacy full-frame VLM analysis[/dim]")
        else:
            console.print(f"[dim]Pipeline: YOLO + crop + upscale (same as third_person)[/dim]")
