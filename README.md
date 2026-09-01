# Zolon Healthcare TGIF Photo Compliance Checker

A Flask application for checking whether sales representatives' pharmacy visit photos were taken on the active **Thank God It's Friday (TGIF)** event date.

## Features

### Admin dashboard

- Set the current TGIF date from **Set Current TGIF Date**.
- View live rep submissions with rep name, pharmacy submission counts, and compliance totals.
- Open a submission report in a modal without leaving the dashboard.
- Inspect flagged images and download a CSV report.
- Copy the rep portal link or open the portal directly.

### Sales rep portal

- Displays the active TGIF date as read-only above the form.
- Collects sales rep name and pharmacy name.
- Supports separate **Take Photo** and **Choose from Gallery** actions on mobile.
- Appends photos to a queue instead of replacing earlier selections.
- Shows thumbnails, filenames, total count, and total size.
- Prevents duplicate files and allows individual files to be removed.
- Keeps the submit button disabled until the date is available, both names are filled in, and at least one photo is queued.

### Compliance processing

- Reads `DateTimeOriginal`, `DateTimeDigitized`, and standard EXIF dates.
- Falls back to the device file timestamp when EXIF metadata is unavailable.
- Marks photos as compliant, non-compliant, or unable to verify.
- Supports JPG, JPEG, PNG, WebP, HEIC, TIFF, BMP, and GIF files.
- Compresses images and creates thumbnails before optional Supabase upload.
- Automatically purges submissions and stored files older than seven days.

## Technology stack

- **Backend:** Python 3.12+, Flask
- **Image processing:** Pillow and pillow-heif
- **Database:** SQLite locally, PostgreSQL when `DATABASE_URL` is configured
- **Storage:** Supabase Storage when configured with local-disk fallback
- **Frontend:** HTML, CSS, and vanilla JavaScript
- **Production server:** Gunicorn is included in `requirements.txt`

## Setup

This project can be installed with [uv](https://docs.astral.sh/uv/) or standard `pip`.

1. Create and activate a virtual environment:

   ```bash
   uv venv
   ```

   On Windows:

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

2. Install the project dependencies:

   ```bash
   uv pip install -r requirements.txt
   ```

3. Start the development server:

   ```bash
   python app.py
   ```

4. Open the admin dashboard at <http://127.0.0.1:5000>.

The first startup creates `local_database.db` when PostgreSQL is not configured. Database initialization creates the `app_settings` table, stores the default active date, and adds the `pharmacy_name` column to existing submission databases when needed.

## Configuration

Environment variables are optional for local use:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string. Without it, SQLite is used. |
| `SUPABASE_URL` | Supabase project URL for image storage and public image links. |
| `SUPABASE_KEY` | Supabase storage API key. |
| `SUPABASE_BUCKET` | Storage bucket name; defaults to `tgif-photos`. |
| `SUPABASE_PROJECT_ID` | Optional project ID used to build public image URLs. |
| `CLOUDFLARE_CDN_DOMAIN` | Optional CDN domain for image and thumbnail routes. |

When Supabase variables are absent, processing still works locally and cloud upload is skipped.

## Application routes and APIs

| Route | Purpose |
| --- | --- |
| `GET /` | Redirects to the admin dashboard |
| `GET /admin` | Protected admin dashboard |
| `GET /admin/login` | Admin login page |
| `GET /submit` | Sales rep portal |
| `GET /api/active-tgif-date` | Returns the active date as `{ "active_date": "YYYY-MM-DD" }` |
| `POST /api/set-active-tgif-date` | Admin-only endpoint to set the active date |
| `POST /api/submit-rep-photos` | Accepts `rep_name`, `pharmacy_name`, and one or more `images` files |
| `GET /api/admin/submissions` | Protected admin summary feed |
| `GET /results/<session_id>` | Returns compliance results for a submission |
| `GET /download-csv/<session_id>` | Downloads flagged results as CSV |

The legacy `POST /upload` batch-upload route remains available for backward compatibility, although it is no longer exposed in the admin dashboard.

## Project structure

```text
app.py                    Flask application, routes, database, and processing
templates/index.html      Protected admin dashboard
templates/admin_login.html Login page for the admin area
templates/submit.html     Sales rep upload portal
uploads/                  Temporary local image folders
local_database.db         Local SQLite database, created at runtime
requirements.txt          Runtime dependencies
```

## Admin Access

The admin dashboard is available at `/admin`. Access is protected by a password.
Set the `ADMIN_PASSWORD` environment variable to secure the instance.
The default password (if not set) is a random string, it must be set in production.

## Security note

The admin dashboard and date-setting endpoint currently rely on access to the application URL. Authentication and authorization should be added before exposing the deployment publicly.

## Contributors

**Ikechukwu Obi**,
**Emmanuel Nwachukwu**
