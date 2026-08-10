"""
Zolon Healthcare Ltd – TGIF Photo Compliance Checker
Production Flask app with PostgreSQL storage, 7-day automated cleanup,
and support for Admin Batch Uploads plus Direct Sales Rep Submissions.
"""

import os
import csv
import uuid
import time
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta
from io import StringIO, BytesIO
from typing import Any, cast

from flask import (
    Flask, request, jsonify, render_template,
    send_file, send_from_directory, redirect
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

# ── Optional PostgreSQL Driver ───────────────────────────────────────────────
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

# ── Optional Boto3 (Backblaze B2 Integration) ────────────────────────────────
try:
    import boto3
    from botocore.config import Config
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

# ── App Setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.urandom(32)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_BASE = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_BASE, exist_ok=True)

ALLOWED_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic",
    ".tiff", ".tif", ".bmp", ".gif"
}

DATABASE_URL = os.environ.get("DATABASE_URL")

# Backblaze B2 environment variables
B2_ENDPOINT_URL = os.environ.get("B2_ENDPOINT_URL")      # e.g., https://s3.us-west-004.backblazeb2.com
B2_KEY_ID = os.environ.get("B2_KEY_ID")                  # keyID
B2_APPLICATION_KEY = os.environ.get("B2_APPLICATION_KEY")  # applicationKey
B2_BUCKET_NAME = os.environ.get("B2_BUCKET_NAME")        # bucket name

# ── In‑memory progress store (for admin batch uploads only) ────────────────
# { session_id: { progress, total, done, tgif_date, rep_name, created_at } }
SESSIONS: dict = {}
SESSIONS_LOCK = threading.Lock()

# ── Database Connection & Initialization ─────────────────────────────────────

def get_db_connection():
    """Returns a PostgreSQL connection if DATABASE_URL is set, else SQLite connection."""
    if DATABASE_URL and POSTGRES_AVAILABLE:
        # Render/Neon postgres URLs sometimes start with postgres://
        conn_str = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(conn_str)
        return conn, "postgres"
    else:
        # Fallback for local testing without Neon
        db_path = os.path.join(BASE_DIR, "local_database.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def init_db():
    """Initializes schema for PostgreSQL or SQLite."""
    conn, db_type = get_db_connection()
    cur = conn.cursor()

    if db_type == "postgres":
        cur.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                session_id VARCHAR(64) PRIMARY KEY,
                rep_name VARCHAR(255) NOT NULL,
                tgif_date VARCHAR(10) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS submission_files (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(64) REFERENCES submissions(session_id) ON DELETE CASCADE,
                filename VARCHAR(255) NOT NULL,
                extracted_date VARCHAR(10),
                source VARCHAR(50),
                status VARCHAR(50),
                is_compliant BOOLEAN NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                session_id TEXT PRIMARY KEY,
                rep_name TEXT NOT NULL,
                tgif_date TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS submission_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                filename TEXT NOT NULL,
                extracted_date TEXT,
                source TEXT,
                status TEXT,
                is_compliant INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES submissions(session_id) ON DELETE CASCADE
            );
        """)

    conn.commit()
    cur.close()
    conn.close()

# Run DB initialization at startup
init_db()


# ── Backblaze B2 Client Helpers ──────────────────────────────────────────────

def is_b2_configured() -> bool:
    """Check if all required B2 environment variables are set."""
    return all([B2_ENDPOINT_URL, B2_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME]) and BOTO3_AVAILABLE

def get_b2_client():
    if is_b2_configured():
        endpoint_url = B2_ENDPOINT_URL
        if not endpoint_url:
            return None
        endpoint = endpoint_url if endpoint_url.startswith("http") else f"https://{endpoint_url}"
        return boto3.client(
            service_name="s3",
            endpoint_url=endpoint,
            aws_access_key_id=B2_KEY_ID,
            aws_secret_access_key=B2_APPLICATION_KEY,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",  # Standard dummy region for S3 compatibility
        )
    return None

def upload_file_to_b2(local_path: str, b2_key: str, content_type: str = "image/jpeg"):
    s3 = get_b2_client()
    if s3 and B2_BUCKET_NAME:
        try:
            s3.upload_file(
                Filename=local_path,
                Bucket=B2_BUCKET_NAME,
                Key=b2_key,
                ExtraArgs={"ContentType": content_type}
            )
            return True
        except Exception as e:
            print(f"[B2 Upload Error]: {e}")
    return False

def delete_b2_session_files(session_id: str):
    s3 = get_b2_client()
    if s3 and B2_BUCKET_NAME:
        try:
            response = s3.list_objects_v2(Bucket=B2_BUCKET_NAME, Prefix=f"{session_id}/")
            if "Contents" in response:
                objects = [{"Key": obj["Key"]} for obj in response["Contents"]]
                s3.delete_objects(Bucket=B2_BUCKET_NAME, Delete={"Objects": objects})
                print(f"[B2 Purge] Deleted {len(objects)} objects for session {session_id}")
        except Exception as e:
            print(f"[B2 Delete Error]: {e}")

def get_b2_presigned_url(b2_key: str) -> str | None:
    s3 = get_b2_client()
    if s3 and B2_BUCKET_NAME:
        try:
            url = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': B2_BUCKET_NAME, 'Key': b2_key},
                ExpiresIn=3600  # Valid for 1 hour
            )
            return url
        except Exception as e:
            print(f"[Presigned URL Error]: {e}")
    return None

def generate_and_upload_thumb(local_path: str, session_id: str, safe_name: str):
    """Generates a thumbnail locally and pushes it to Backblaze B2."""
    if not is_b2_configured():
        return  # Skip if B2 not available
    try:
        img = Image.open(local_path)
        img.thumbnail((320, 320), Image.Resampling.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        thumb_dir = os.path.dirname(local_path)
        thumb_path = os.path.join(thumb_dir, f"thumb_{safe_name}")
        img.save(thumb_path, format="JPEG", quality=80)

        # Upload thumbnail to Backblaze B2
        upload_file_to_b2(thumb_path, f"{session_id}/thumb_{safe_name}")
    except Exception as e:
        print(f"[Thumbnail Generation Error]: {e}")


# ── Automated 7-Day Retention Purge Thread ────────────────────────────────────

def purge_expired_7day_data():
    """Background daemon running every 4 hours to purge records & images > 7 days old."""
    while True:
        try:
            conn, db_type = get_db_connection()
            cur = conn.cursor()

            if db_type == "postgres":
                cur.execute("""
                    SELECT session_id FROM submissions 
                    WHERE created_at < NOW() - INTERVAL '7 days';
                """)
                rows = cur.fetchall()
                expired_sids = [r[0] for r in rows]

                for sid in expired_sids:
                    # Delete from Backblaze B2
                    delete_b2_session_files(sid)
                    # Delete physical image folder
                    folder = os.path.join(UPLOAD_BASE, sid)
                    shutil.rmtree(folder, ignore_errors=True)

                if expired_sids:
                    cur.execute("DELETE FROM submissions WHERE created_at < NOW() - INTERVAL '7 days';")
                    conn.commit()
                    print(f"[Auto-Purge] Cleaned {len(expired_sids)} submissions older than 7 days.")
            else:
                seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("SELECT session_id FROM submissions WHERE created_at < ?;", (seven_days_ago,))
                rows = cur.fetchall()
                expired_sids = [r[0] for r in rows]

                for sid in expired_sids:
                    delete_b2_session_files(sid)
                    folder = os.path.join(UPLOAD_BASE, sid)
                    shutil.rmtree(folder, ignore_errors=True)

                if expired_sids:
                    cur.execute("DELETE FROM submissions WHERE created_at < ?;", (seven_days_ago,))
                    conn.commit()

            cur.close()
            conn.close()
        except Exception as e:
            print(f"[Purge Thread Error]: {e}")

        # Check every 4 hours (14,400 seconds)
        time.sleep(14400)

threading.Thread(target=purge_expired_7day_data, daemon=True).start()


# ── In‑memory session cleanup (for progress tracking only) ───────────────────
def _cleanup_sessions():
    """Remove stale progress entries older than 1 hour."""
    while True:
        time.sleep(300)  # every 5 minutes
        now = time.time()
        with SESSIONS_LOCK:
            expired = [
                sid for sid, data in SESSIONS.items()
                if now - data.get("created_at", now) > 3600
            ]
            for sid in expired:
                SESSIONS.pop(sid, None)

threading.Thread(target=_cleanup_sessions, daemon=True).start()


# ── Metadata Extraction Helpers ──────────────────────────────────────────────

def allowed_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXT

def unique_filename(folder: str, filename: str) -> str:
    name, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{name}({counter}){ext}"
        counter += 1
    return candidate

def _parse_exif_datestr(raw) -> str | None:
    try:
        date_part = str(raw).split(" ")[0]
        parts = date_part.split(":")
        if len(parts) == 3:
            y, m, d = parts
            if y.isdigit() and m.isdigit() and d.isdigit():
                if 1900 < int(y) < 2100 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
                    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        pass
    return None

def get_image_date(filepath: str, fallback_timestamp_ms: str | None = None) -> tuple[str | None, str]:
    EXIF_DATE_TAGS = {36867: "DateTimeOriginal", 36868: "DateTimeDigitized", 306: "DateTime"}

    try:
        img = Image.open(filepath)
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

        try:
            raw_getexif = getattr(img, "_getexif", None)
            if callable(raw_getexif):
                raw_exif = cast(dict[int, Any], raw_getexif())
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

    if fallback_timestamp_ms and str(fallback_timestamp_ms).isdigit():
        try:
            ts_sec = float(fallback_timestamp_ms) / 1000.0
            return datetime.fromtimestamp(ts_sec).strftime("%Y-%m-%d"), "file_date"
        except Exception:
            pass

    try:
        mtime = os.path.getmtime(filepath)
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"), "file_date"
    except Exception:
        pass

    return None, "no_metadata"


# ── Background processing for Admin Batch Uploads ──────────────────────────

def process_admin_batch(session_id: str, tgif_date: str):
    """Process each image in the session folder, update DB and progress."""
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
        if session_id in SESSIONS:
            SESSIONS[session_id]["total"] = total

    conn, db_type = get_db_connection()
    cur = conn.cursor()

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

        # Insert into DB
        if db_type == "postgres":
            cur.execute("""
                INSERT INTO submission_files 
                (session_id, filename, extracted_date, source, status, is_compliant) 
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (session_id, filename, date_str, source, status, compliant))
        else:
            cur.execute("""
                INSERT INTO submission_files 
                (session_id, filename, extracted_date, source, status, is_compliant) 
                VALUES (?, ?, ?, ?, ?, ?);
            """, (session_id, filename, date_str, source, status, 1 if compliant else 0))

        # Upload original and thumbnail to Backblaze B2
        upload_file_to_b2(filepath, f"{session_id}/{filename}")
        generate_and_upload_thumb(filepath, session_id, filename)

        # Update progress
        with SESSIONS_LOCK:
            if session_id in SESSIONS:
                SESSIONS[session_id]["progress"] = i + 1

    conn.commit()
    cur.close()
    conn.close()

    # Mark as done and delete local folder after uploads
    with SESSIONS_LOCK:
        if session_id in SESSIONS:
            SESSIONS[session_id]["done"] = True

    shutil.rmtree(folder, ignore_errors=True)


# ── Flask Web Routes ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Admin Dashboard"""
    return render_template("index.html")

@app.route("/submit")
def rep_submit_page():
    """Sales Rep Mobile Portal"""
    return render_template("submit.html")

@app.route("/upload", methods=["POST"])
def admin_upload():
    """Admin batch upload endpoint."""
    tgif_date = (request.form.get("tgif_date") or "").strip()
    if not tgif_date:
        return jsonify({"error": "No TGIF date provided."}), 400

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

    # Insert submission record (rep_name = "Admin" for batch uploads)
    conn, db_type = get_db_connection()
    cur = conn.cursor()
    if db_type == "postgres":
        cur.execute(
            "INSERT INTO submissions (session_id, rep_name, tgif_date) VALUES (%s, %s, %s);",
            (session_id, "Admin", tgif_date)
        )
    else:
        cur.execute(
            "INSERT INTO submissions (session_id, rep_name, tgif_date) VALUES (?, ?, ?);",
            (session_id, "Admin", tgif_date)
        )
    conn.commit()
    cur.close()
    conn.close()

    saved = 0
    for f in files:
        if not f or not f.filename:
            continue
        basename = os.path.basename(secure_filename(f.filename))
        if not basename or not allowed_file(basename):
            continue
        safe_name = unique_filename(folder, basename)
        f.save(os.path.join(folder, safe_name))
        saved += 1

    if saved == 0:
        shutil.rmtree(folder, ignore_errors=True)
        return jsonify({"error": "No valid image files found."}), 400

    with SESSIONS_LOCK:
        SESSIONS[session_id] = {
            "progress": 0,
            "total": 0,          # will be set in background thread
            "done": False,
            "tgif_date": tgif_date,
            "rep_name": "Admin",
            "created_at": time.time(),
        }

    # Start background processing
    t = threading.Thread(target=process_admin_batch, args=(session_id, tgif_date), daemon=True)
    t.start()

    return jsonify({"session_id": session_id, "total_saved": saved})

@app.route("/api/submit-rep-photos", methods=["POST"])
def submit_rep_photos():
    """API Endpoint for direct phone uploads from Sales Reps."""
    rep_name = (request.form.get("rep_name") or "Anonymous Rep").strip()
    tgif_date = (request.form.get("tgif_date") or "").strip()
    files = request.files.getlist("images")

    if not tgif_date or not files:
        return jsonify({"error": "Please specify the TGIF date and select photos."}), 400

    session_id = str(uuid.uuid4())
    folder = os.path.join(UPLOAD_BASE, session_id)
    os.makedirs(folder, exist_ok=True)

    # Insert submission record
    conn, db_type = get_db_connection()
    cur = conn.cursor()
    if db_type == "postgres":
        cur.execute(
            "INSERT INTO submissions (session_id, rep_name, tgif_date) VALUES (%s, %s, %s);",
            (session_id, rep_name, tgif_date)
        )
    else:
        cur.execute(
            "INSERT INTO submissions (session_id, rep_name, tgif_date) VALUES (?, ?, ?);",
            (session_id, rep_name, tgif_date)
        )

    saved_count = 0
    compliant_count = 0
    flagged_count = 0

    for f in files:
        if not f or not f.filename:
            continue
        original_name = os.path.basename(secure_filename(f.filename))
        if not original_name or not allowed_file(original_name):
            continue

        safe_name = unique_filename(folder, original_name)
        file_path = os.path.join(folder, safe_name)
        f.save(file_path)

        fallback_ms = request.form.get(f"lm_{f.filename}")
        date_str, source = get_image_date(file_path, fallback_ms)

        if date_str == tgif_date and source != "no_metadata":
            status = "compliant"
            is_compliant = True
            compliant_count += 1
        else:
            status = "no_metadata" if source == "no_metadata" else "non_compliant"
            is_compliant = False
            flagged_count += 1

        # Insert file record
        if db_type == "postgres":
            cur.execute("""
                INSERT INTO submission_files 
                (session_id, filename, extracted_date, source, status, is_compliant) 
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (session_id, safe_name, date_str, source, status, is_compliant))
        else:
            cur.execute("""
                INSERT INTO submission_files 
                (session_id, filename, extracted_date, source, status, is_compliant) 
                VALUES (?, ?, ?, ?, ?, ?);
            """, (session_id, safe_name, date_str, source, status, 1 if is_compliant else 0))

        # Upload original and thumbnail to Backblaze B2
        upload_file_to_b2(file_path, f"{session_id}/{safe_name}")
        generate_and_upload_thumb(file_path, session_id, safe_name)

        saved_count += 1

    conn.commit()
    cur.close()
    conn.close()

    # Clean local temporary files after B2 upload completes
    shutil.rmtree(folder, ignore_errors=True)

    return jsonify({
        "session_id": session_id,
        "total": saved_count,
        "rep_name": rep_name,
        "tgif_date": tgif_date
    })

@app.route("/progress/<session_id>")
def progress(session_id: str):
    """Return progress for admin batch uploads."""
    with SESSIONS_LOCK:
        data = SESSIONS.get(session_id)
    if not data:
        # If not found, maybe it's a rep submission – assume done
        return jsonify({"progress": 100, "total": 100, "done": True})
    return jsonify({
        "progress": data["progress"],
        "total": data["total"],
        "done": data["done"],
    })

@app.route("/results/<session_id>")
def results(session_id: str):
    """Return results from database for a given session."""
    conn, db_type = get_db_connection()
    cur = conn.cursor()

    if db_type == "postgres":
        cur.execute("SELECT rep_name, tgif_date FROM submissions WHERE session_id = %s;", (session_id,))
        sub = cur.fetchone()
        if not sub:
            return jsonify({"error": "Session not found."}), 404

        cur.execute("""
            SELECT filename, extracted_date, source, status, is_compliant 
            FROM submission_files WHERE session_id = %s;
        """, (session_id,))
        files = cur.fetchall()

        results_list = [{
            "filename": f[0], "date": f[1], "source": f[2],
            "status": f[3], "compliant": f[4]
        } for f in files]

        tgif_date, rep_name = sub[1], sub[0]
    else:
        cur.execute("SELECT rep_name, tgif_date FROM submissions WHERE session_id = ?;", (session_id,))
        sub = cur.fetchone()
        if not sub:
            return jsonify({"error": "Session not found."}), 404

        cur.execute("""
            SELECT filename, extracted_date, source, status, is_compliant 
            FROM submission_files WHERE session_id = ?;
        """, (session_id,))
        files = cur.fetchall()

        results_list = [{
            "filename": f[0], "date": f[1], "source": f[2],
            "status": f[3], "compliant": bool(f[4])
        } for f in files]

        rep_name, tgif_date = sub[0], sub[1]

    cur.close()
    conn.close()

    return jsonify({
        "results": results_list,
        "tgif_date": tgif_date,
        "rep_name": rep_name
    })

@app.route("/thumb/<session_id>/<path:filename>")
def thumb(session_id: str, filename: str):
    # Try Backblaze B2 presigned URL redirect
    b2_url = get_b2_presigned_url(f"{session_id}/thumb_{filename}")
    if b2_url:
        return redirect(b2_url)

    # Local disk fallback
    folder = os.path.join(UPLOAD_BASE, session_id)
    filepath = os.path.join(folder, f"thumb_{filename}")
    if not os.path.isfile(filepath):
        # Try to generate on the fly if original exists (legacy)
        orig_path = os.path.join(folder, filename)
        if os.path.isfile(orig_path):
            try:
                img = Image.open(orig_path)
                img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=80)
                buf.seek(0)
                return send_file(buf, mimetype="image/jpeg")
            except Exception:
                pass
        return "", 404
    return send_file(filepath)

@app.route("/image/<session_id>/<path:filename>")
def serve_image(session_id: str, filename: str):
    # Try Backblaze B2 presigned URL redirect
    b2_url = get_b2_presigned_url(f"{session_id}/{filename}")
    if b2_url:
        return redirect(b2_url)

    # Local disk fallback
    folder = os.path.join(UPLOAD_BASE, session_id)
    return send_from_directory(folder, filename)

@app.route("/download-csv/<session_id>")
def download_csv(session_id: str):
    """Download flagged (non‑compliant) files as CSV."""
    conn, db_type = get_db_connection()
    cur = conn.cursor()

    if db_type == "postgres":
        cur.execute("SELECT rep_name, tgif_date FROM submissions WHERE session_id = %s;", (session_id,))
        sub = cur.fetchone()
        if not sub:
            return jsonify({"error": "Not found"}), 404
        rep_name, tgif_date = sub[0], sub[1]

        cur.execute("""
            SELECT filename, extracted_date, source, status 
            FROM submission_files WHERE session_id = %s AND is_compliant = FALSE;
        """, (session_id,))
        flagged = cur.fetchall()
    else:
        cur.execute("SELECT rep_name, tgif_date FROM submissions WHERE session_id = ?;", (session_id,))
        sub = cur.fetchone()
        if not sub:
            return jsonify({"error": "Not found"}), 404
        rep_name, tgif_date = sub[0], sub[1]

        cur.execute("""
            SELECT filename, extracted_date, source, status 
            FROM submission_files WHERE session_id = ? AND is_compliant = 0;
        """, (session_id,))
        flagged = cur.fetchall()

    cur.close()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Rep Name", "Filename", "Extracted Date", "Date Source", "Status"])
    for row in flagged:
        status_label = {
            "non_compliant": "Non-compliant (wrong date)",
            "no_metadata": "No metadata – unable to verify",
        }.get(row[3], row[3])
        writer.writerow([rep_name, row[0], row[1] or "N/A", row[2], status_label])

    buf = BytesIO(si.getvalue().encode("utf-8-sig"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"flagged_{tgif_date}_{rep_name}.csv",
    )

@app.route("/api/admin/submissions")
def get_all_submissions():
    """Returns a summary of all submissions (both admin and rep)."""
    conn, db_type = get_db_connection()

    if db_type == "postgres":
        from psycopg2.extras import RealDictCursor as PgRealDictCursor

        cur = cast(Any, conn).cursor(cursor_factory=PgRealDictCursor)
        cur.execute("""
            SELECT s.session_id, s.rep_name, s.tgif_date, s.created_at,
                   COUNT(f.id) as total,
                   COUNT(CASE WHEN f.is_compliant = TRUE THEN 1 END) as compliant,
                   COUNT(CASE WHEN f.is_compliant = FALSE THEN 1 END) as flagged
            FROM submissions s
            LEFT JOIN submission_files f ON s.session_id = f.session_id
            GROUP BY s.session_id, s.rep_name, s.tgif_date, s.created_at
            ORDER BY s.created_at DESC;
        """)
        rows = cur.fetchall()
    else:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.session_id, s.rep_name, s.tgif_date, s.created_at,
                   COUNT(f.id) as total,
                   SUM(CASE WHEN f.is_compliant = 1 THEN 1 ELSE 0 END) as compliant,
                   SUM(CASE WHEN f.is_compliant = 0 THEN 1 ELSE 0 END) as flagged
            FROM submissions s
            LEFT JOIN submission_files f ON s.session_id = f.session_id
            GROUP BY s.session_id
            ORDER BY s.created_at DESC;
        """)
        raw = cur.fetchall()
        rows = []
        for r in raw:
            rows.append({
                "session_id": r[0], "rep_name": r[1], "tgif_date": r[2],
                "created_at": r[3], "total": r[4], "compliant": r[5] or 0, "flagged": r[6] or 0
            })

    cur.close()
    conn.close()

    formatted = []
    for r in rows:
        created_str = str(r["created_at"]).split(".")[0]
        formatted.append({
            "session_id": r["session_id"],
            "rep_name": r["rep_name"],
            "tgif_date": r["tgif_date"],
            "total": r["total"],
            "compliant": r["compliant"],
            "flagged": r["flagged"],
            "created_at": created_str
        })

    return jsonify({"submissions": formatted})

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)