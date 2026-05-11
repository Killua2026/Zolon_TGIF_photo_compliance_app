# Zolon Healthcare - TGIF Photo Compliance Checker

A Python Flask web application built for Zolon Healthcare Ltd to automate the compliance checking of sales representative photos taken during "Thank God It's Friday" (TGIF) events.

## Features

* **Drag and Drop Interface:** Modern, responsive frontend to easily upload single files, multiple files, or entire folders.
* **Metadata Verification:** Extracts EXIF data (including `DateTimeOriginal`) to verify if photos were taken on the selected TGIF event date.
* **Smart Fallback:** Uses file modification timestamps if EXIF data is stripped (e.g., by WhatsApp).
* **HEIC Support:** Native support for Apple's HEIC image format (via `pillow-heif`).
* **Background Processing:** Uploads and metadata extraction run asynchronously with real-time UI polling and progress bars.
* **Auto-Cleanup:** A background daemon automatically deletes uploaded temporary files after one hour to save server space.
* **Reporting:** Generates a visual gallery of non-compliant photos and allows exporting the results to a CSV file.

## Technology Stack

* **Backend:** Python 3, Flask
* **Image Processing:** Pillow, pillow-heif, Werkzeug
* **Frontend:** HTML5, CSS3 (Vanilla), JavaScript
* **Dependency Management:** uv

## Setup & Installation

This project uses [uv](https://docs.astral.sh/uv/) for fast Python package management.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/tgif-compliance-checker.git
    cd tgif-compliance-checker
    ```

2.  **Create and activate a virtual environment using uv:**
    ```bash
    uv venv
    # On Windows:
    .venv\Scripts\activate
    # On macOS/Linux:
    source .venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    uv pip install Flask Pillow Werkzeug pillow-heif
    ```

4.  **Run the application:**
    ```bash
    python app.py
    ```

5.  **Access the web interface:**
    Open your browser and navigate to `http://127.0.0.1:5000`.

## Project Structure

* `app.py`: The main Flask backend application.
* `templates/index.html`: The frontend UI.
* `uploads/`: Temporary directory where images are processed (auto-cleaned).

## Author

**Ikechukwu Obi**
