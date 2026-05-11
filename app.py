"""
Zolon Healthcare Ltd – TGIF Photo Compliance Checker
Flask backend: upload, EXIF extraction, compliance checking, temp cleanup.
"""

import os
import csv
import uuid
import time
import shutil
import threading
from datetime import datetime
from io import StringIO, BytesIO

from flask import (
    Flask, request, jsonify, render_template,
    send_file, send_from_directory
)
from PIL import Image
from PIL.ExifTags import TAGS
from werkzeug.utils import secure_filename

# ── Optional HEIC support ────────────────────────────────────────────────────
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.urandom(32)

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
UPLOAD_BASE  = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_BASE, exist_ok=True)

ALLOWED_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic",
    ".tiff", ".tif", ".bmp", ".gif"
}
SESSION_TTL = 3600          # seconds – auto-delete uploads after 1 hour
CLEANUP_INTERVAL = 300      # seconds – cleanup thread wakes every 5 min

# ── In-memory session store ──────────────────────────────────────────────────
# { session_id: { progress, total, results, done, tgif_date, created_at } }
SESSIONS: dict = {}
SESSIONS_LOCK = threading.Lock()


# ── Helpers ──────────────────────────────────────────────────────────────────

def allowed_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXT


def unique_filename(folder: str, filename: str) -> str:
    """
    If `filename` already exists in `folder`, append (1), (2), …
    before the extension, e.g.  photo.jpg → photo(1).jpg.
    """
    name, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{name}({counter}){ext}"
        counter += 1
    return candidate


def _parse_exif_datestr(raw) -> str | None:
    """
    Convert an EXIF date string 'YYYY:MM:DD HH:MM:SS' →  'YYYY-MM-DD'.
    Returns None on any parse failure.
    """
    try:
        date_part = str(raw).split(" ")[0]          # discard time component
        parts = date_part.split(":")
        if len(parts) == 3:
            y, m, d = parts
            # Basic sanity check
            if y.isdigit() and m.isdigit() and d.isdigit():
                if 1900 < int(y) < 2100 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
                    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        pass
    return None


def get_image_date(filepath: str) -> tuple[str | None, str]:
    """
    Extract the capture date from an image file.

    Priority:
      1. EXIF DateTimeOriginal  (tag 36867)
      2. EXIF DateTimeDigitized (tag 36868)
      3. EXIF DateTime          (tag 306)
      4. File modification time (os.path.getmtime)

    Returns  (date_str, source)  where date_str is 'YYYY-MM-DD' or None,
    and source is one of: 'exif', 'file_date', 'no_metadata'.
    """
    EXIF_DATE_TAGS = {36867: "DateTimeOriginal",
                      36868: "DateTimeDigitized",
                      306:   "DateTime"}

    # ── 1 + 2 + 3: Try Pillow EXIF ──────────────────────────────────────────
    try:
        img = Image.open(filepath)

        # Modern API (works for JPEG, TIFF, WebP, HEIC via pillow-heif)
        try:
            exif = img.getexif()
            if exif:
                for tag_id in [36867, 36868, 306]:
                    val = exif.get(tag_id)
                    if val:
                        parsed = _parse_exif_datestr(val)
                        if parsed:
                            return parsed, "exif"
        except Exception:
            pass

        # Legacy JPEG API
        try:
            if hasattr(img, "_getexif"):
                raw_exif = img._getexif()  # type: ignore[attr-defined]
                if raw_exif:
                    for tag_id, value in raw_exif.items():
                        if tag_id in EXIF_DATE_TAGS:
                            parsed = _parse_exif_datestr(value)
                            if parsed:
                                return parsed, "exif"
        except Exception:
            pass

    except Exception:
        pass

    # ── 4: Fallback – file modification time ─────────────────────────────────
    try:
        mtime = os.path.getmtime(filepath)
        date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        return date_str, "file_date"
    except Exception:
        pass

    return None, "no_metadata"


# ── Background: image processing ────────────────────────────────────────────

def process_images(session_id: str, tgif_date: str):
    """
    Runs in a daemon thread.
    Iterates over every saved image, compares its date to tgif_date,
    and writes results back to SESSIONS[session_id].
    """
    folder = os.path.join(UPLOAD_BASE, session_id)
    try:
        files = sorted(
            f for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f))
        )
    except Exception:
        files = []

    total = len(files)
    with SESSIONS_LOCK:
        SESSIONS[session_id]["total"] = total

    results = []
    for i, filename in enumerate(files):
        filepath = os.path.join(folder, filename)
        date_str, source = get_image_date(filepath)

        if source == "no_metadata" or date_str is None:
            status = "no_metadata"
            compliant = False
        elif date_str == tgif_date:
            status = "compliant"
            compliant = True
        else:
            status = "non_compliant"
            compliant = False

        results.append({
            "filename": filename,
            "date":     date_str,
            "source":   source,
            "status":   status,
            "compliant": compliant,
        })

        with SESSIONS_LOCK:
            SESSIONS[session_id]["progress"] = i + 1

    with SESSIONS_LOCK:
        SESSIONS[session_id]["results"] = results
        SESSIONS[session_id]["done"]    = True


# ── Background: cleanup expired sessions ─────────────────────────────────────

def _cleanup_loop():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = time.time()
        with SESSIONS_LOCK:
            expired = [
                sid for sid, d in SESSIONS.items()
                if now - d.get("created_at", now) > SESSION_TTL
            ]
        for sid in expired:
            folder = os.path.join(UPLOAD_BASE, sid)
            shutil.rmtree(folder, ignore_errors=True)
            with SESSIONS_LOCK:
                SESSIONS.pop(sid, None)


_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
_cleanup_thread.start()


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """
    Accept files + tgif_date, save to a session folder, kick off
    background processing, return { session_id, total_saved }.
    """
    tgif_date = (request.form.get("tgif_date") or "").strip()
    if not tgif_date:
        return jsonify({"error": "No TGIF date provided."}), 400

    # Validate date format
    try:
        datetime.strptime(tgif_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "No files received."}), 400

    session_id = str(uuid.uuid4())
    folder = os.path.join(UPLOAD_BASE, session_id)
    os.makedirs(folder, exist_ok=True)

    with SESSIONS_LOCK:
        SESSIONS[session_id] = {
            "progress":   0,
            "total":      0,
            "results":    [],
            "done":       False,
            "tgif_date":  tgif_date,
            "created_at": time.time(),
        }

    # Save files (synchronous; XHR progress handles the transfer phase)
    saved = 0
    for f in files:
        if not f or not f.filename:
            continue
        # Use only the basename (handles webkitdirectory relative paths)
        basename = os.path.basename(secure_filename(f.filename))
        if not basename or not allowed_file(basename):
            continue
        safe_name = unique_filename(folder, basename)
        f.save(os.path.join(folder, safe_name))
        saved += 1

    if saved == 0:
        shutil.rmtree(folder, ignore_errors=True)
        with SESSIONS_LOCK:
            SESSIONS.pop(session_id, None)
        return jsonify({"error": "No valid image files found."}), 400

    # Start background processing
    t = threading.Thread(target=process_images,
                         args=(session_id, tgif_date),
                         daemon=True)
    t.start()

    return jsonify({"session_id": session_id, "total_saved": saved})


@app.route("/progress/<session_id>")
def progress(session_id: str):
    """Poll endpoint: returns processing progress for a session."""
    with SESSIONS_LOCK:
        data = SESSIONS.get(session_id)
    if not data:
        return jsonify({"error": "Session not found."}), 404
    return jsonify({
        "progress": data["progress"],
        "total":    data["total"],
        "done":     data["done"],
    })


@app.route("/results/<session_id>")
def results(session_id: str):
    """Return full results once processing is complete."""
    with SESSIONS_LOCK:
        data = SESSIONS.get(session_id)
    if not data:
        return jsonify({"error": "Session not found."}), 404
    if not data["done"]:
        return jsonify({"error": "Processing not complete yet."}), 202
    return jsonify({
        "results":   data["results"],
        "tgif_date": data["tgif_date"],
    })


@app.route("/thumb/<session_id>/<path:filename>")
def thumb(session_id: str, filename: str):
    """
    Serve a JPEG thumbnail (≤ 320 px on the longest side) for gallery display.
    Converts HEIC / RGBA to RGB/JPEG for broad browser compatibility.
    """
    folder = os.path.join(UPLOAD_BASE, session_id)
    filepath = os.path.join(folder, filename)
    if not os.path.isfile(filepath):
        return "", 404
    try:
        img = Image.open(filepath)
        img.thumbnail((320, 320), Image.Resampling.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg")
    except Exception:
        return "", 500


@app.route("/image/<session_id>/<path:filename>")
def serve_image(session_id: str, filename: str):
    """Serve the original uploaded image (for full-size preview)."""
    folder = os.path.join(UPLOAD_BASE, session_id)
    return send_from_directory(folder, filename)


@app.route("/download-csv/<session_id>")
def download_csv(session_id: str):
    """Generate and download a CSV of all flagged (non-compliant) images."""
    with SESSIONS_LOCK:
        data = SESSIONS.get(session_id)
    if not data or not data["done"]:
        return jsonify({"error": "Results not ready."}), 404

    flagged = [r for r in data["results"] if not r["compliant"]]
    tgif_date = data["tgif_date"]

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Filename", "Extracted Date", "Date Source", "Status"])
    for row in flagged:
        status_label = {
            "non_compliant": "Non-compliant (wrong date)",
            "no_metadata":   "No metadata – unable to verify",
        }.get(row["status"], row["status"])
        writer.writerow([
            row["filename"],
            row["date"] or "N/A",
            row["source"],
            status_label,
        ])

    buf = BytesIO(si.getvalue().encode("utf-8-sig"))   # utf-8-sig for Excel compat
    buf.seek(0)
    return send_file(
        buf,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"flagged_images_{tgif_date}.csv",
    )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)