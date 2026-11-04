"""Descargador de música estilo Apple Music (FastAPI + yt-dlp + ffmpeg)."""
import glob
import hashlib
import hmac
import io
import json
import os
import queue
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

import requests
import yt_dlp
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TPE2, TRCK, ID3NoHeaderError
from PIL import Image
from pydantic import BaseModel

APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "admin")
MUSIC_ROOT = Path(os.environ.get("MUSIC_DIR", "/music")).resolve()
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
SECRET = (os.environ.get("SECRET_KEY") or hashlib.sha256(
    f"{APP_USER}:{APP_PASSWORD}:descarga-musica".encode()).hexdigest()).encode()
COOKIE = "session"
SESSION_DAYS = 30

STATIC = Path(__file__).parent / "static"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
COOKIES_FILE = CONFIG_DIR / "cookies.txt"
FORMATS = ("mp3", "flac")

for d in (MUSIC_ROOT, CONFIG_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Descarga Música", docs_url=None, redoc_url=None, openapi_url=None)


# ───────────────────────── Auth ─────────────────────────
def _sign(exp: int) -> str:
    return hmac.new(SECRET, f"{APP_USER}.{exp}".encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    return f"{exp}.{_sign(exp)}"


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    try:
        return int(exp) > time.time() and hmac.compare_digest(sig, _sign(int(exp)))
    except ValueError:
        return False


def require_auth(request: Request):
    if not valid_token(request.cookies.get(COOKIE)):
        raise HTTPException(401, "No autenticado")


class Login(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: Login, response: Response):
    ok_user = hmac.compare_digest(body.username.encode(), APP_USER.encode())
    ok_pass = hmac.compare_digest(body.password.encode(), APP_PASSWORD.encode())
    if not (ok_user and ok_pass):
        time.sleep(1)  # frena fuerza bruta
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    response.set_cookie(COOKIE, make_token(), max_age=SESSION_DAYS * 86400,
                        httponly=True, samesite="lax")
    return {"ok": True, "user": APP_USER}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me", dependencies=[Depends(require_auth)])
def me():
    return {"user": APP_USER}


# ───────────────────────── Ajustes ─────────────────────────
_settings_lock = threading.Lock()


def safe_path(rel: str) -> Path:
    """Resuelve una ruta relativa dentro de MUSIC_ROOT (evita path traversal)."""
    p = (MUSIC_ROOT / (rel or "").lstrip("/\\")).resolve()
    if p != MUSIC_ROOT and MUSIC_ROOT not in p.parents:
        raise HTTPException(400, "Ruta fuera de la carpeta de música")
    return p


def load_settings() -> dict:
    s = {"format": "mp3", "output_dir": ""}
    try:
        s.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
    except (OSError, ValueError):
        pass
    return s


class Settings(BaseModel):
    format: str
    output_dir: str = ""


@app.get("/api/settings", dependencies=[Depends(require_auth)])
def get_settings():
    return {**load_settings(), "root": str(MUSIC_ROOT)}


@app.put("/api/settings", dependencies=[Depends(require_auth)])
def put_settings(body: Settings):
    if body.format not in FORMATS:
        raise HTTPException(400, "Formato no válido")
    p = safe_path(body.output_dir)
    p.mkdir(parents=True, exist_ok=True)
    rel = "" if p == MUSIC_ROOT else p.relative_to(MUSIC_ROOT).as_posix()
    with _settings_lock:
        SETTINGS_FILE.write_text(json.dumps({"format": body.format, "output_dir": rel}), "utf-8")
    return {**load_settings(), "root": str(MUSIC_ROOT)}


@app.get("/api/folders", dependencies=[Depends(require_auth)])
def list_folders(path: str = ""):
    p = safe_path(path)
    if not p.is_dir():
        raise HTTPException(404, "No existe")
    dirs = sorted((c.name for c in p.iterdir() if c.is_dir() and not c.name.startswith(".")),
                  key=str.lower)
    rel = "" if p == MUSIC_ROOT else p.relative_to(MUSIC_ROOT).as_posix()
    return {"path": rel, "folders": dirs}


class NewFolder(BaseModel):
    path: str = ""
    name: str


@app.post("/api/folders", dependencies=[Depends(require_auth)])
def new_folder(body: NewFolder):
    name = sanitize(body.name)
    if not name:
        raise HTTPException(400, "Nombre no válido")
    (safe_path(body.path) / name).mkdir(parents=True, exist_ok=True)
    return list_folders(body.path)


# ───────────────────────── Utilidades ─────────────────────────
def sanitize(s: str, limit: int = 150) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s or "").strip(" .")
    return s[:limit].strip(" .")


def best_thumb(thumbs, fallback: str = "") -> str:
    if thumbs:
        t = max(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0))
        if t.get("url"):
            return t["url"]
        return thumbs[-1].get("url", fallback)
    return fallback


def clean_artist(name: str | None) -> str:
    return re.sub(r"\s*-\s*Topic$", "", name or "").strip()


_TAG = (r"(?:official|oficial|music|lyrics?|letra|video|audio|visuali[sz]er|"
        r"hd|hq|4k|full|con|with|en|vevo|\s|[-–|&,+/])+")
_BRACKETED = re.compile(r"\s*[\(\[\{]\s*" + _TAG + r"\s*[\)\]\}]", re.I)
_TRAILING = re.compile(r"\s*[|\-–]\s*" + _TAG + r"$", re.I)


def clean_title(title: str) -> str:
    """Quita etiquetas como (Audio), [Official Video], | Lyric Video del título."""
    t = _BRACKETED.sub("", title or "")
    t = _TRAILING.sub("", t)
    t = re.sub(r"\s+(?:hd|hq|4k)$", "", t, flags=re.I).strip()
    return t or (title or "").strip()


def base_opts() -> dict:
    o = {"quiet": True, "no_warnings": True, "noprogress": True}
    if COOKIES_FILE.exists():
        o["cookiefile"] = str(COOKIES_FILE)
    return o


# ───────────────────────── Playlist ─────────────────────────
class PlaylistReq(BaseModel):
    url: str


@app.post("/api/playlist", dependencies=[Depends(require_auth)])
def playlist_info(body: PlaylistReq):
    url = body.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "Pega un enlace válido (http/https)")
    try:
        with yt_dlp.YoutubeDL({**base_opts(), "extract_flat": "in_playlist",
                               "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"No se pudo leer el enlace: {str(e)[:300]}")

    entries = info.get("entries")
    is_playlist = entries is not None
    if not is_playlist:
        entries = [info]

    tracks = []
    for e in entries:
        if not e or not e.get("id"):
            continue
        title = e.get("title") or ""
        if title in ("[Private video]", "[Deleted video]"):
            continue
        vid = e["id"]
        tracks.append({
            "id": vid,
            "index": len(tracks) + 1,
            "title": clean_title(title) or vid,
            "artist": clean_artist(e.get("artist") or e.get("creator")
                                   or e.get("uploader") or e.get("channel")),
            "duration": e.get("duration"),
            "thumb": best_thumb(e.get("thumbnails"), f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"),
            "url": e.get("webpage_url") or (e.get("url") if (e.get("url") or "").startswith("http")
                                            else f"https://www.youtube.com/watch?v={vid}"),
        })
    if not tracks:
        raise HTTPException(400, "No se encontraron canciones en ese enlace")

    cover = best_thumb(info.get("thumbnails") if is_playlist else None, tracks[0]["thumb"])
    artist = clean_artist(info.get("uploader") or info.get("channel")) or tracks[0]["artist"]
    return {
        "title": clean_title(info.get("title") or "") or tracks[0]["title"],
        "artist": artist,
        "cover": cover,
        "tracks": tracks,
    }


# ───────────────────────── Descargas ─────────────────────────
jobs: dict[str, dict] = {}
job_queue: "queue.Queue[str]" = queue.Queue()


class TrackIn(BaseModel):
    id: str
    index: int | None = None
    title: str
    artist: str = ""
    url: str
    thumb: str = ""


class JobIn(BaseModel):
    title: str
    artist: str = ""
    cover: str = ""
    tracks: list[TrackIn]


@app.post("/api/jobs", dependencies=[Depends(require_auth)])
def create_job(body: JobIn):
    if not body.tracks:
        raise HTTPException(400, "Selecciona al menos una canción")
    s = load_settings()
    job = {
        "id": uuid.uuid4().hex[:12],
        "title": body.title.strip() or "Playlist",
        "artist": body.artist.strip(),
        "cover": body.cover,
        "format": s["format"],
        "dir": s["output_dir"],
        "status": "queued",
        "cancel": False,
        "created": time.time(),
        "error": None,
        "tracks": [{**t.model_dump(), "status": "queued", "progress": 0, "error": None}
                   for t in body.tracks],
    }
    jobs[job["id"]] = job
    job_queue.put(job["id"])
    return public_job(job)


def public_job(j: dict) -> dict:
    n = len(j["tracks"]) or 1
    progress = sum(t["progress"] for t in j["tracks"]) / n
    out = {k: v for k, v in j.items() if k != "tracks"}
    out["progress"] = round(progress, 1)
    out["done_count"] = sum(1 for t in j["tracks"] if t["status"] == "done")
    out["tracks"] = [{k: t[k] for k in ("id", "title", "artist", "thumb", "status", "progress", "error")}
                     for t in j["tracks"]]
    return out


@app.get("/api/jobs", dependencies=[Depends(require_auth)])
def list_jobs():
    return sorted((public_job(j) for j in list(jobs.values())),
                  key=lambda j: j["created"], reverse=True)


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def delete_job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    if j["status"] in ("queued", "running"):
        j["cancel"] = True  # el worker lo marca como cancelado
    else:
        jobs.pop(job_id, None)
    return {"ok": True}


@app.post("/api/jobs/clear", dependencies=[Depends(require_auth)])
def clear_finished():
    for k in [k for k, j in jobs.items() if j["status"] not in ("queued", "running")]:
        jobs.pop(k, None)
    return {"ok": True}


def fetch_cover(url: str) -> bytes | None:
    """Descarga la carátula y la deja cuadrada (JPEG, máx. 1200px)."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        w, h = img.size
        side = min(w, h)
        img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        if side > 1200:
            img = img.resize((1200, 1200), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def tag_file(path: Path, fmt: str, *, title, artist, album, album_artist, number, total, cover):
    if fmt == "mp3":
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TALB(encoding=3, text=album))
        tags.add(TPE2(encoding=3, text=album_artist))
        tags.add(TRCK(encoding=3, text=f"{number}/{total}"))
        if cover:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
        tags.save(str(path), v2_version=3)
    else:
        f = FLAC(str(path))
        f["title"], f["artist"], f["album"] = title, artist, album
        f["albumartist"], f["tracknumber"], f["tracktotal"] = album_artist, str(number), str(total)
        if cover:
            f.clear_pictures()
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", cover
            f.add_picture(pic)
        f.save()


def download_track(job: dict, t: dict, number: int, album_dir: Path, cover: bytes | None):
    fmt = job["format"]
    base = f"{number:02d} - {sanitize(t['title']) or t['id']}"
    final = album_dir / f"{base}.{fmt}"
    if final.exists():
        t["progress"], t["status"] = 100, "done"
        return

    def hook(d):
        if job["cancel"]:
            raise yt_dlp.utils.DownloadCancelled()
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                t["progress"] = round(min(d.get("downloaded_bytes", 0) / total, 1) * 88, 1)
        elif d["status"] == "finished":
            t["progress"] = 90  # falta la conversión

    codec = {"preferredcodec": fmt, "key": "FFmpegExtractAudio"}
    if fmt == "mp3":
        codec["preferredquality"] = "320"
    opts = {
        **base_opts(),
        "format": "bestaudio/best",
        "outtmpl": str(album_dir / f"{base}.%(ext)s"),
        "noplaylist": True,
        "postprocessors": [codec],
        "progress_hooks": [hook],
        "retries": 5,
        "fragment_retries": 5,
    }
    t["status"] = "downloading"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(t["url"], download=True)
        artist = t["artist"] or clean_artist(info.get("artist") or info.get("creator")
                                             or info.get("uploader")) or job["artist"]
        t["progress"] = 95
        tag_file(final, fmt, title=t["title"], artist=artist, album=job["title"],
                 album_artist=job["artist"] or artist, number=number,
                 total=len(job["tracks"]), cover=cover)
        t["progress"], t["status"] = 100, "done"
    except yt_dlp.utils.DownloadCancelled:
        t["status"] = "cancelled"
    except Exception as e:  # noqa: BLE001
        t["status"], t["error"] = "error", str(e)[:300]
    finally:
        if t["status"] != "done":
            for f in glob.glob(glob.escape(str(album_dir / base)) + ".*"):
                try:
                    os.remove(f)
                except OSError:
                    pass


def run_job(job: dict):
    if job["cancel"]:
        job["status"] = "cancelled"
        return
    job["status"] = "running"
    try:
        album_dir = safe_path(job["dir"]) / (sanitize(job["title"]) or "Playlist")
        album_dir.mkdir(parents=True, exist_ok=True)
        cover = fetch_cover(job["cover"])
        if cover:
            (album_dir / "cover.jpg").write_bytes(cover)
        for i, t in enumerate(job["tracks"], start=1):
            if job["cancel"]:
                break
            download_track(job, t, i, album_dir, cover)
        if job["cancel"]:
            job["status"] = "cancelled"
            for t in job["tracks"]:
                if t["status"] in ("queued", "downloading"):
                    t["status"] = "cancelled"
        else:
            failed = sum(1 for t in job["tracks"] if t["status"] == "error")
            job["status"] = "done" if not failed else "error"
            if failed:
                job["error"] = f"{failed} canción(es) con error"
    except Exception as e:  # noqa: BLE001
        job["status"], job["error"] = "error", str(e)[:300]


def worker():
    while True:
        jid = job_queue.get()
        j = jobs.get(jid)
        if j:
            run_job(j)


threading.Thread(target=worker, daemon=True).start()


# ───────────────────────── Estáticos ─────────────────────────
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
