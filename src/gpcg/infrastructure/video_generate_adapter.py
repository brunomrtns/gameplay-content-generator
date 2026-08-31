"""Video Generate adapter — subprocess integration with the mature video-generate engine.

This follows the EXACT pattern established by videoclip-generator and
trivestia-course-generator: generate a Python script that injects sys.path,
loads video-generate's .env, imports process_video_request, and calls it.

We DO NOT modify video-generate. We consume its public contract:
  process_video_request(request_data) -> bool

Three operations are delegated via subprocess:
  1. TTS synthesis  → src/generators/tts.py::synthesize
  2. BGM selection  → src/library/bgm_selector.py::BGMSelector.select
  3. Video render   → generate.py::process_video_request
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from gpcg.config import get_settings
from gpcg.logging import get_logger

log = get_logger(__name__)


class VideoGenerateError(Exception):
    pass


@dataclass
class TTSResult:
    wav_path: Path
    duration_sec: float
    subtitle_mapping: dict


@dataclass
class RenderResult:
    video_path: Path
    batch_id: str
    success: bool
    stderr: str = ""


class VideoGenerateAdapter:
    """Subprocess-based integration with video-generate."""

    def __init__(self) -> None:
        s = get_settings()
        self.vg_dir = s.video_generate_root
        self.vg_python = s.video_generate_venv_python
        self.ai_media_core = s.ai_media_core_src
        self.tts_voice = s.gpcg_tts_voice
        self.tts_language = s.gpcg_tts_language
        self.render_timeout = s.gpcg_render_timeout

        if not self.vg_python.exists():
            raise VideoGenerateError(f"video-generate python not found: {self.vg_python}")
        if not self.vg_dir.exists():
            raise VideoGenerateError(f"video-generate dir not found: {self.vg_dir}")

    def _env(self) -> dict[str, str]:
        """Build subprocess env with PYTHONPATH including ai-media-core + video-generate."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        parts = [str(self.ai_media_core), str(self.vg_dir)]
        if existing:
            parts.append(existing)
        env["PYTHONPATH"] = ":".join(parts)
        return env

    def _run_script(self, script: str, *, timeout: Optional[int] = None) -> tuple[str, str, int]:
        """Write a script to a temp file, run it with video-generate's venv python."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, prefix="gpcg_vg_", dir=str(self.vg_dir)
        ) as f:
            f.write(script)
            script_path = f.name
        try:
            proc = subprocess.run(
                [str(self.vg_python), script_path],
                capture_output=True,
                text=True,
                timeout=timeout or self.render_timeout,
                cwd=str(self.vg_dir),
                env=self._env(),
            )
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            raise VideoGenerateError(f"subprocess timed out after {timeout or self.render_timeout}s") from e
        finally:
            Path(script_path).unlink(missing_ok=True)

    # ── TTS ─────────────────────────────────────────────────────────────────

    def synthesize_tts(
        self, text: str, output_wav: Path, *, voice_path: Optional[str] = None,
        language: str = "pt",
    ) -> TTSResult:
        """Generate TTS narration using video-generate's mature TTS pipeline.

        Reuses all sanitization/normalization/segmentation from ai_media_core
        and the acronym handler. Returns the WAV path + duration + subtitle_mapping.

        For long texts, we chunk using ai_media_core's prepare_commercial_chunks
        and synthesize each chunk separately (avoiding the known wav_utils merge
        bug when XTTS produces chunks with mismatched sample rates). We then merge
        the per-chunk WAVs with FFmpeg, which handles resampling natively.

        Args:
            voice_path: Optional absolute path to a voice reference WAV/MP3.
                If provided, overrides the config default (GPCG_TTS_VOICE).
                Used for per-job voice selection (uploaded voices).
            language: TTS language code (e.g. "pt", "en", "es"). Defaults to "pt".
                Passed through to video-generate's synthesize() function.
        """
        output_wav = Path(output_wav)
        output_wav.parent.mkdir(parents=True, exist_ok=True)

        # Resolve voice path: per-job override > config default
        if voice_path and Path(voice_path).exists():
            voice_arg = str(voice_path)
        else:
            # Resolve config default relative to video-generate dir
            default_voice = self.vg_dir / self.tts_voice
            if not default_voice.exists():
                log.warning(f"voice file not found: {default_voice}, using default")
                voice_arg = str(self.tts_voice)
            else:
                voice_arg = str(default_voice)

        # Chunk the text using ai_media_core's segmentation (same as video-generate)
        # then synthesize each chunk individually + merge with FFmpeg.
        # For CJK languages (zh, ja, ko), ai_media_core's clean_text_for_tts
        # strips CJK characters via its overly broad emoji regex, and its
        # sentence splitting doesn't handle CJK punctuation (。？！). We use
        # a CJK-aware chunking path instead.
        script = textwrap.dedent(f"""\
            import json, sys, os, tempfile, glob, re
            from pathlib import Path
            sys.path.insert(0, {str(self.vg_dir)!r})
            sys.path.insert(0, {str(self.ai_media_core)!r})
            from dotenv import load_dotenv
            load_dotenv({str(self.vg_dir / '.env')!r})
            from gpcg_bridge import write_result

            text = {json.dumps(text)}
            voice = {voice_arg!r}
            language = {language!r}
            out_path = {str(output_wav)!r}

            # CJK-aware chunking: for Chinese/Japanese/Korean, the standard
            # clean_text_for_tts strips CJK chars (emoji regex too broad) and
            # sentence splitting doesn't handle CJK punctuation. Use a simple
            # CJK-aware splitter instead.
            def is_cjk_lang(lang):
                return lang.split("-")[0].lower() in ("zh", "ja", "ko")

            def cjk_chunk(text, max_chars=200):
                \"\"\"Split CJK text on CJK punctuation, then by max_chars.\"\"\"
                # Split on CJK sentence-ending punctuation: 。！？
                sentences = re.split(r'(?<=[。！？.!?])\\s*', text.strip())
                sentences = [s.strip() for s in sentences if s.strip()]
                if not sentences:
                    return [text] if text.strip() else []
                chunks = []
                current = ""
                for sent in sentences:
                    candidate = (current + " " + sent).strip() if current else sent
                    if len(candidate) > max_chars:
                        if current:
                            chunks.append(current)
                        # If single sentence > max_chars, split by char count
                        if len(sent) > max_chars:
                            for i in range(0, len(sent), max_chars):
                                chunks.append(sent[i:i+max_chars])
                            current = ""
                        else:
                            current = sent
                    else:
                        current = candidate
                if current:
                    chunks.append(current)
                return chunks

            if is_cjk_lang(language):
                chunks = cjk_chunk(text, max_chars=200)
                if not chunks:
                    chunks = [text]
            else:
                from ai_media_core.speech.tts.text_processing import prepare_commercial_chunks
                max_chars = int(os.environ.get("TTS_CHUNK_MAX_CHARS", "170"))
                max_words = int(os.environ.get("TTS_CHUNK_MAX_WORDS", "24"))
                min_words = int(os.environ.get("TTS_CHUNK_MIN_WORDS", "6"))
                chunks = prepare_commercial_chunks(text, max_chars=max_chars, max_words=max_words, min_words=min_words)
                if not chunks:
                    chunks = [text]

            from src.generators.tts import synthesize

            chunk_dir = tempfile.mkdtemp(prefix="gpcg_tts_chunks_")
            chunk_wavs = []
            success = True
            error_msg = ""
            for i, chunk in enumerate(chunks):
                chunk_wav = os.path.join(chunk_dir, f"chunk_{{i:03d}}.wav")
                ok = synthesize(text=chunk, out_path=chunk_wav, speaker_wav=voice, language=language)
                if ok and os.path.exists(chunk_wav) and os.path.getsize(chunk_wav) > 0:
                    chunk_wavs.append(chunk_wav)
                else:
                    success = False
                    error_msg = f"chunk {{i}} synthesis failed"
                    break

            if not success or not chunk_wavs:
                write_result({{"success": False, "error": error_msg or "no chunks produced"}})
                sys.exit(1)

            write_result({{"success": True, "chunk_wavs": chunk_wavs, "chunk_dir": chunk_dir, "tts_text": text, "chunks": chunks}})
        """)

        result_json = self._run_with_bridge(script)
        if not result_json.get("success"):
            raise VideoGenerateError(f"TTS failed: {result_json.get('error', 'unknown')}")

        chunk_wavs = result_json.get("chunk_wavs", [])
        if not chunk_wavs:
            raise VideoGenerateError("TTS produced no chunk WAVs")

        # Measure duration of each chunk before merging (for subtitle timing)
        from gpcg.infrastructure.media import probe, MediaError
        chunk_durations = []
        for cw in chunk_wavs:
            try:
                ci = probe(cw)
                chunk_durations.append(ci.duration)
            except (MediaError, Exception):
                chunk_durations.append(0.0)

        # Merge chunks with FFmpeg (handles different sample rates via resampling)
        self._merge_wavs_with_ffmpeg(chunk_wavs, output_wav)

        # Get total duration
        try:
            info = probe(output_wav)
            duration = info.duration
        except MediaError:
            duration = 0.0

        # Cleanup chunk dir
        import shutil
        chunk_dir = result_json.get("chunk_dir")
        if chunk_dir:
            shutil.rmtree(chunk_dir, ignore_errors=True)

        # Build subtitle_mapping with real per-chunk timings.
        # Each TTS chunk = one subtitle segment with accurate start/end times.
        # This is more reliable than Whisper (which may not be installed)
        # and more accurate than proportional distribution.
        chunks_text = result_json.get("chunks") or [text]
        subtitle_mapping = self._build_chunk_subtitle_mapping(
            chunks_text, chunk_durations, language
        )

        log.info(f"TTS: {len(chunk_wavs)} chunks merged → {duration:.1f}s")
        return TTSResult(
            wav_path=output_wav,
            duration_sec=duration,
            subtitle_mapping=subtitle_mapping,
        )

    @staticmethod
    def _build_chunk_subtitle_mapping(
        chunks_text: list[str], chunk_durations: list[float], language: str
    ) -> dict:
        """Build subtitle_mapping from TTS chunk text + per-chunk durations.

        Each TTS chunk becomes one subtitle segment with accurate start/end
        times measured from the actual audio. This replaces both the old
        proportional _build_subtitle_segments and the Whisper fallback
        (which may not be installed).
        """
        segments = []
        current_time = 0.0
        for i, chunk_text in enumerate(chunks_text):
            dur = chunk_durations[i] if i < len(chunk_durations) else 0.0
            if dur <= 0:
                continue
            text_clean = chunk_text.strip() if chunk_text else ""
            if not text_clean:
                current_time += dur
                continue
            segments.append({
                "subtitle_fragment": text_clean,
                "tts_fragment": text_clean,
                "start_time": round(current_time, 3),
                "end_time": round(current_time + dur, 3),
            })
            current_time += dur

        return {"tts_text": " ".join(chunks_text), "expansions": [], "segments": segments}

    def _merge_wavs_with_ffmpeg(self, wav_paths: list[str], output: Path) -> None:
        """Merge multiple WAVs into one using FFmpeg concat with resampling."""
        import subprocess
        import tempfile

        output.parent.mkdir(parents=True, exist_ok=True)
        if len(wav_paths) == 1:
            # Single chunk — just copy
            import shutil
            shutil.copy2(wav_paths[0], output)
            return

        # Use FFmpeg concat demuxer with a list file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="gpcg_concat_"
        ) as f:
            for w in wav_paths:
                f.write(f"file '{w}'\n")
            list_path = f.name

        try:
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-ar", "22050", "-ac", "1",  # normalize to mono 22050 (XTTS default)
                "-c:a", "pcm_s16le",
                str(output),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                # Fallback: sequential re-encode + concat
                log.warning(f"concat demuxer failed, trying sequential: {result.stderr[-200:]}")
                self._merge_wavs_sequential(wav_paths, output)
        finally:
            Path(list_path).unlink(missing_ok=True)

    def _merge_wavs_sequential(self, wav_paths: list[str], output: Path) -> None:
        """Fallback: merge WAVs by concatenating with filter_complex."""
        import subprocess

        inputs = []
        for w in wav_paths:
            inputs.extend(["-i", w])
        # Build filter_complex
        n = len(wav_paths)
        filter_parts = [f"[{i}:a]aresample=22050,aformat=sample_fmts=s16:channel_layouts=mono[a{i}]" for i in range(n)]
        concat_inputs = "".join(f"[a{i}]" for i in range(n))
        filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={n}:v=0:a=1[out]"

        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "pcm_s16le",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise VideoGenerateError(f"FFmpeg WAV merge failed: {result.stderr[-300:]}")

    # ── BGM ─────────────────────────────────────────────────────────────────

    def select_music(self, mood: str, min_duration: float = 45.0) -> Optional[Path]:
        """Select a background music track from video-generate's curated library."""
        script = textwrap.dedent(f"""\
            import json, sys
            sys.path.insert(0, {str(self.vg_dir)!r})
            from src.library.bgm_selector import BGMSelector
            from gpcg_bridge import write_result
            sel = BGMSelector()
            path = sel.select({json.dumps(mood)}, min_duration={min_duration!r})
            write_result({{"success": True, "music_path": path}})
        """)
        result_json = self._run_with_bridge(script)
        if not result_json.get("success"):
            return None
        p = result_json.get("music_path")
        return Path(p) if p else None

    # ── Render ──────────────────────────────────────────────────────────────

    def render_video(self, request_data: dict[str, Any]) -> RenderResult:
        """Call process_video_request with the given request_data.

        request_data must follow the video-generate contract:
          audio_principal, musica_fundo, delay_musica, img_dir,
          original_narration_text, subtitle_mapping, scene_timeline,
          request_id, batch_id, video_profile

        If request_data contains "_gpcg_custom_profile", the adapter registers
        a custom VideoProfile in video-generate's registry before calling
        process_video_request. This enables custom aspect ratios (1:1, 4:5)
        and subtitle customization without modifying video-generate.
        """
        batch_id = request_data.get("batch_id", "gpcg")
        # Extract custom profile (not part of video-generate's contract)
        custom_profile = request_data.pop("_gpcg_custom_profile", None)

        # Pass request_data via a temp JSON file (avoids JSON null → Python NameError
        # when embedding JSON directly in Python source)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="gpcg_req_"
        ) as f:
            json.dump(request_data, f, ensure_ascii=False)
            req_file = f.name

        # Build profile registration code if a custom profile is provided
        profile_registration = ""
        if custom_profile:
            import json as _json
            import textwrap as _tw
            pd_json = _json.dumps(custom_profile, ensure_ascii=False)
            _reg_code = _tw.dedent(f"""\
                # Register custom video profile (GPCG)
                import json as _json2
                from src.profiles.video_profile import VideoProfile, SubtitleProfile, SafeAreaProfile, ProviderHints
                from src.profiles.profile_registry import VideoProfileRegistry
                _pd = _json2.loads({pd_json!r})
                _sub = _pd["subtitle"]
                _safe = _pd["safe_area"]
                _prov = _pd["provider_hints"]
                _profile = VideoProfile(
                    name=_pd["name"],
                    display_name=_pd["display_name"],
                    category=_pd["category"],
                    width=_pd["width"],
                    height=_pd["height"],
                    aspect_ratio=_pd["aspect_ratio"],
                    orientation=_pd["orientation"],
                    base_scale_factor=_pd["base_scale_factor"],
                    fps=_pd["fps"],
                    crf=_pd["crf"],
                    preset=_pd["preset"],
                    subtitle=SubtitleProfile(**_sub),
                    safe_area=SafeAreaProfile(**_safe),
                    provider_hints=ProviderHints(**_prov),
                )
                VideoProfileRegistry.register(_profile)
                print(f"GPCG custom profile registered: {{_profile.name}} ({{_profile.width}}x{{_profile.height}})")
            """)
            # Indent to match the template's 12-space level so textwrap.dedent works
            profile_registration = "\n".join(
                ("            " + line if line.strip() else "            ")
                for line in _reg_code.split("\n")
            )

        script = textwrap.dedent(f"""\
            import json, sys, os
            sys.path.insert(0, {str(self.vg_dir)!r})
            sys.path.insert(0, {str(self.ai_media_core)!r})
            from dotenv import load_dotenv
            load_dotenv({str(self.vg_dir / '.env')!r})
            {profile_registration}
            from generate import process_video_request
            from gpcg_bridge import write_result

            with open({req_file!r}) as f:
                req = json.load(f)
            ok = process_video_request(req)
            if not ok:
                write_result({{"success": False, "error": "process_video_request returned False"}})
                sys.exit(1)

            # Locate the output file
            from pathlib import Path
            outputs = Path({str(self.vg_dir / 'outputs')!r})
            batch_id = {json.dumps(batch_id)}
            candidates = list(outputs.glob(f"*{{batch_id}}.mp4"))
            if not candidates:
                # Fallback: most recent file
                candidates = sorted(outputs.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
            video_path = str(candidates[0]) if candidates else None
            write_result({{"success": True, "video_path": video_path, "outputs_dir": str(outputs)}})
        """)
        try:
            result_json = self._run_with_bridge(script, timeout=self.render_timeout)
        finally:
            Path(req_file).unlink(missing_ok=True)
        if not result_json.get("success"):
            raise VideoGenerateError(f"render failed: {result_json.get('error', 'unknown')}")
        vp = result_json.get("video_path")
        return RenderResult(
            video_path=Path(vp) if vp else Path("/dev/null"),
            batch_id=batch_id,
            success=bool(vp),
        )

    # ── Bridge helper ───────────────────────────────────────────────────────

    def _run_with_bridge(self, user_script: str, *, timeout: Optional[int] = None) -> dict:
        """Run a script with a gpcg_bridge module available for result passing."""
        with tempfile.TemporaryDirectory(prefix="gpcg_vg_run_") as tmp:
            result_file = Path(tmp) / "result.json"
            bridge_code = textwrap.dedent(f"""\
                import json, sys
                _RESULT_FILE = {str(result_file)!r}
                def write_result(data):
                    with open(_RESULT_FILE, "w") as f:
                        json.dump(data, f)
            """)
            bridge_path = Path(tmp) / "gpcg_bridge.py"
            bridge_path.write_text(bridge_code)

            # Prepend bridge dir to PYTHONPATH
            env = self._env()
            env["PYTHONPATH"] = f"{tmp}:{env['PYTHONPATH']}"

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, prefix="gpcg_vg_", dir=str(self.vg_dir)
            ) as f:
                f.write(user_script)
                script_path = f.name
            try:
                proc = subprocess.run(
                    [str(self.vg_python), script_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout or self.render_timeout,
                    cwd=str(self.vg_dir),
                    env=env,
                )
                if proc.returncode != 0 and not result_file.exists():
                    log.error(f"VG subprocess failed (rc={proc.returncode}):\nSTDOUT:{proc.stdout[-1000:]}\nSTDERR:{proc.stderr[-1000:]}")
                    raise VideoGenerateError(f"subprocess exited {proc.returncode}: {proc.stderr[-500:]}")
                if result_file.exists():
                    result = json.loads(result_file.read_text())
                    if not result.get("success"):
                        log.error(f"VG subprocess failed (result): {result.get('error', 'unknown')}\nSTDERR:{proc.stderr[-2000:]}")
                    return result
                # No result file but rc==0 — treat as failure
                log.error(f"VG subprocess produced no result file. stderr: {proc.stderr[-500:]}")
                return {"success": False, "error": proc.stderr[-500:] or "no result file"}
            except subprocess.TimeoutExpired as e:
                raise VideoGenerateError(f"subprocess timed out after {timeout or self.render_timeout}s") from e
            finally:
                Path(script_path).unlink(missing_ok=True)
