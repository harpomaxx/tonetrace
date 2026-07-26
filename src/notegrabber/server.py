"""Small local upload server for re-running notegrabber analysis from a browser."""

from __future__ import annotations

import cgi
import html
import mimetypes
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .analyzer import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_DURATION_SECONDS,
    BASIC_PITCH_ONSET_THRESHOLD,
    CQT_THRESHOLD,
    BackendName,
)
from .visualizer import create_visualization

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def serve_upload_app(
    *,
    host: str,
    port: int,
    out_dir: Path,
    default_backend: BackendName = "basic-pitch",
    render_midi: bool = True,
) -> None:
    """Run a local HTTP server that accepts audio uploads and generates viewers."""

    out_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir = out_dir / "uploads"
    analyses_dir = out_dir / "analyses"
    uploads_dir.mkdir(exist_ok=True)
    analyses_dir.mkdir(exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        server_version = "notegrabber-upload/0.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path in ("/", "/index.html"):
                self._send_html(upload_page(default_backend))
                return
            if self.path.startswith("/viewer/"):
                self._serve_viewer_file(analyses_dir)
                return
            self.send_error(404, "not found")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/analyze":
                self.send_error(404, "not found")
                return
            try:
                redirect_path = self._handle_upload(uploads_dir, analyses_dir, default_backend, render_midi)
            except Exception as exc:  # pragma: no cover - exercised manually/integration
                self.send_error(400, f"analysis failed: {exc}")
                return
            self.send_response(303)
            self.send_header("Location", redirect_path)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}")

        def _handle_upload(
            self,
            uploads_root: Path,
            analyses_root: Path,
            fallback_backend: BackendName,
            should_render_midi: bool,
        ) -> str:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data"):
                raise ValueError("expected multipart/form-data upload")

            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                },
            )
            file_item = form["audio"] if "audio" in form else None
            if file_item is None or not getattr(file_item, "filename", ""):
                raise ValueError("missing audio file field named 'audio'")

            filename = safe_filename(Path(file_item.filename).name or "upload.wav")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            upload_path = uploads_root / f"{stamp}-{filename}"
            upload_path.write_bytes(file_item.file.read())

            backend = parse_backend(form_value(form, "backend", fallback_backend), fallback_backend)
            threshold = parse_float(form_value(form, "threshold", str(CQT_THRESHOLD)), CQT_THRESHOLD)
            onset_threshold = parse_float(form_value(form, "onset_threshold", str(BASIC_PITCH_ONSET_THRESHOLD)), BASIC_PITCH_ONSET_THRESHOLD)
            frame_threshold = parse_float(form_value(form, "frame_threshold", str(BASIC_PITCH_FRAME_THRESHOLD)), BASIC_PITCH_FRAME_THRESHOLD)
            min_duration = parse_float(form_value(form, "min_duration", str(BASIC_PITCH_MIN_DURATION_SECONDS)), BASIC_PITCH_MIN_DURATION_SECONDS)

            analysis_id = safe_filename(f"{upload_path.stem}-{backend}")
            analysis_dir = analyses_root / analysis_id
            suffix = 1
            while analysis_dir.exists():
                suffix += 1
                analysis_dir = analyses_root / f"{analysis_id}-{suffix}"
            create_visualization(
                upload_path,
                analysis_dir,
                backend=backend,
                render_midi=should_render_midi,
                threshold=threshold,
                onset_threshold=onset_threshold,
                frame_threshold=frame_threshold,
                min_duration_seconds=min_duration,
            )
            return f"/viewer/{analysis_dir.name}/index.html"

        def _serve_viewer_file(self, analyses_root: Path) -> None:
            relative = unquote(self.path.removeprefix("/viewer/")).split("?", 1)[0]
            candidate = (analyses_root / relative).resolve()
            analyses_resolved = analyses_root.resolve()
            if analyses_resolved not in candidate.parents and candidate != analyses_resolved:
                self.send_error(403, "forbidden")
                return
            if not candidate.is_file():
                self.send_error(404, "not found")
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            data = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_html(self, body: str, status: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"notegrabber upload server listening at http://{host}:{httpd.server_port}/")
    print(f"writing uploads/viewers under {out_dir}")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def safe_filename(name: str) -> str:
    """Return a conservative filesystem-safe filename."""

    cleaned = _SAFE_NAME_RE.sub("-", name).strip(".-")
    return cleaned or "upload.wav"


def form_value(form: cgi.FieldStorage, key: str, default: object) -> str:
    """Read a scalar multipart value."""

    if key not in form:
        return str(default)
    item = form[key]
    if isinstance(item, list):
        item = item[0]
    value = item.value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def parse_backend(value: str, default: BackendName) -> BackendName:
    if value in ("simple", "cqt", "basic-pitch"):
        return value  # type: ignore[return-value]
    return default


def parse_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def upload_page(default_backend: BackendName) -> str:
    """Return the local upload page."""

    backend_options = "".join(
        f'<option value="{html.escape(backend)}" {"selected" if backend == default_backend else ""}>{html.escape(backend)}</option>'
        for backend in ("basic-pitch", "cqt", "simple")
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>notegrabber local upload</title>
<style>
  body {{ margin: 0; font-family: system-ui, sans-serif; background: #111; color: #eee; }}
  main {{ max-width: 46rem; margin: 0 auto; padding: 2rem; }}
  form {{ display: grid; gap: 1rem; background: #1b1b1b; border: 1px solid #333; border-radius: 0.75rem; padding: 1rem; }}
  label {{ display: grid; gap: 0.35rem; }}
  input, select, button {{ font: inherit; }}
  input, select {{ padding: 0.45rem; border-radius: 0.35rem; border: 1px solid #444; background: #080808; color: #eee; }}
  button {{ background: #2d6cdf; color: #fff; border: 0; border-radius: 0.4rem; padding: 0.75rem 1rem; cursor: pointer; }}
  button:hover {{ background: #3d7cff; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.75rem; }}
  .meta {{ color: #bbb; }}
</style>
</head>
<body>
<main>
  <h1>notegrabber local upload</h1>
  <p class="meta">Upload an audio file to run the local Python analyzer and generate a fresh MIDI, heatmap, rendered MIDI audio preview when TiMidity++ is available, and interactive viewer. Large files can take a while.</p>
  <form method="post" action="/analyze" enctype="multipart/form-data">
    <label>Audio file <input required name="audio" type="file" accept="audio/*,.wav,.mp3,.flac,.ogg,.aiff,.aif"></label>
    <label>Backend <select name="backend">{backend_options}</select></label>
    <div class="grid">
      <label>CQT threshold <input name="threshold" type="number" step="0.01" min="0" max="1" value="{CQT_THRESHOLD}"></label>
      <label>Onset threshold <input name="onset_threshold" type="number" step="0.01" min="0" max="1" value="{BASIC_PITCH_ONSET_THRESHOLD}"></label>
      <label>Frame threshold <input name="frame_threshold" type="number" step="0.01" min="0" max="1" value="{BASIC_PITCH_FRAME_THRESHOLD}"></label>
      <label>Min duration seconds <input name="min_duration" type="number" step="0.01" min="0" value="{BASIC_PITCH_MIN_DURATION_SECONDS}"></label>
    </div>
    <button type="submit">Analyze and open viewer</button>
  </form>
</main>
</body>
</html>
"""
