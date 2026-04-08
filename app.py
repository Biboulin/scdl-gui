#!/usr/bin/env python3
"""
SoundCloud Downloader — Web App
Flask backend : télécharge via scdl, zip les fichiers, les sert en download.
Protégé par mot de passe (APP_PASSWORD dans .env / variables d'env Docker).
"""

import os
import subprocess
import threading
import uuid
import zipfile
import shutil
import tempfile
import time
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    send_file,
    session,
)
from flask_cors import CORS

# ── App ────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=".")
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
CORS(app, supports_credentials=True)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "soundcloud")

# Durée de vie max d'un job (1 heure) — nettoyage automatique
JOB_TTL = 3600

# Stockage des jobs en mémoire
jobs: dict[str, dict] = {}


# ── Auth ───────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify({"error": "Non autorisé"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    if data.get("password") == APP_PASSWORD:
        session["authenticated"] = True
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"error": "Mot de passe incorrect"}), 403


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    return jsonify({"authenticated": bool(session.get("authenticated"))})


# ── Utilitaires ────────────────────────────────────────────────────────────
def find_scdl() -> str | None:
    """Cherche scdl dans PATH et emplacements courants."""
    candidates = [
        shutil.which("scdl"),
        os.path.expanduser("~/.local/bin/scdl"),
        "/usr/local/bin/scdl",
        "/opt/homebrew/bin/scdl",
    ]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def cleanup_job(job_id: str):
    """Supprime les fichiers temporaires d'un job."""
    job = jobs.get(job_id)
    if not job:
        return
    for key in ("tmpdir", "zip_path"):
        path = job.get(key)
        if path and os.path.exists(path):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception:
                pass


def cleanup_old_jobs():
    """Tâche de fond : nettoie les jobs plus vieux que JOB_TTL."""
    while True:
        time.sleep(300)  # toutes les 5 min
        now = time.time()
        stale = [
            jid for jid, j in list(jobs.items())
            if now - j.get("created_at", now) > JOB_TTL
        ]
        for jid in stale:
            cleanup_job(jid)
            jobs.pop(jid, None)


threading.Thread(target=cleanup_old_jobs, daemon=True).start()


# ── Téléchargement ─────────────────────────────────────────────────────────
def run_download(job_id: str, url: str):
    """Lance scdl dans un thread, puis zippe le résultat."""
    job = jobs[job_id]
    job["status"] = "running"

    scdl_path = find_scdl()
    if not scdl_path:
        job["status"] = "error"
        job["logs"].append(
            "❌ scdl introuvable. Assure-toi qu'il est installé (pip install scdl)."
        )
        return

    # Dossier temporaire pour ce job
    tmpdir = tempfile.mkdtemp(prefix=f"scdl-{job_id}-")
    job["tmpdir"] = tmpdir

    cmd = [
        scdl_path, "-l", url,
        "--path", tmpdir,
        "--onlymp3",
        "--addtofile",
        "--no-playlist-folder",   # pas de sous-dossier
    ]

    job["logs"].append(f"▶ Démarrage du téléchargement…")
    job["logs"].append(f"🔗 URL : {url}")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        job["pid"] = process.pid

        for line in iter(process.stdout.readline, ""):
            line = line.rstrip()
            if line:
                job["logs"].append(line)

        process.wait()

        if process.returncode != 0:
            job["status"] = "error"
            job["logs"].append(f"❌ scdl a terminé avec le code {process.returncode}")
            return

        # Récupère les fichiers téléchargés
        files = [f for f in os.listdir(tmpdir) if os.path.isfile(os.path.join(tmpdir, f))]
        if not files:
            job["status"] = "error"
            job["logs"].append("❌ Aucun fichier téléchargé.")
            return

        job["logs"].append(f"📦 Création de l'archive ({len(files)} fichier(s))…")

        # Crée le ZIP
        zip_path = os.path.join(tempfile.gettempdir(), f"scdl-{job_id}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in sorted(files):
                zf.write(os.path.join(tmpdir, fname), fname)

        job["zip_path"] = zip_path
        job["file_count"] = len(files)
        job["status"] = "done"
        job["logs"].append(f"✅ Archive prête — {len(files)} musique(s) téléchargée(s) !")

        # Nettoie le dossier tmp (le ZIP suffit)
        shutil.rmtree(tmpdir, ignore_errors=True)
        job.pop("tmpdir", None)

    except Exception as e:
        job["status"] = "error"
        job["logs"].append(f"❌ Erreur inattendue : {e}")


# ── Routes API ─────────────────────────────────────────────────────────────
@app.route("/api/check-scdl")
@login_required
def check_scdl():
    path = find_scdl()
    if not path:
        return jsonify({"installed": False})
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        version = (r.stdout or r.stderr).strip()
    except Exception:
        version = "?"
    return jsonify({"installed": True, "path": path, "version": version})


@app.route("/api/download", methods=["POST"])
@login_required
def start_download():
    data = request.json or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "URL manquante"}), 400
    if "soundcloud.com" not in url:
        return jsonify({"error": "L'URL doit être une URL SoundCloud"}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "id": job_id,
        "url": url,
        "status": "pending",
        "logs": [],
        "created_at": time.time(),
    }

    threading.Thread(
        target=run_download, args=(job_id, url), daemon=True
    ).start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
@login_required
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404

    since = int(request.args.get("since", 0))
    return jsonify({
        "status": job["status"],
        "logs": job["logs"][since:],
        "total_logs": len(job["logs"]),
        "file_count": job.get("file_count"),
        "has_zip": bool(job.get("zip_path") and os.path.exists(job.get("zip_path", ""))),
    })


@app.route("/api/download-zip/<job_id>")
@login_required
def download_zip(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404
    if job["status"] != "done":
        return jsonify({"error": "Téléchargement pas encore terminé"}), 400

    zip_path = job.get("zip_path")
    if not zip_path or not os.path.exists(zip_path):
        return jsonify({"error": "Archive introuvable"}), 404

    # Nom du fichier ZIP basé sur l'URL
    url = job.get("url", "soundcloud")
    slug = url.rstrip("/").split("/")[-1][:50] or "soundcloud"
    download_name = f"{slug}.zip"

    return send_file(
        zip_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )


@app.route("/api/cancel/<job_id>", methods=["POST"])
@login_required
def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404

    pid = job.get("pid")
    if pid:
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

    job["status"] = "cancelled"
    job["logs"].append("⛔ Téléchargement annulé.")
    cleanup_job(job_id)
    return jsonify({"ok": True})


# ── Frontend ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5005))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"🎵 SoundCloud Downloader — http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
