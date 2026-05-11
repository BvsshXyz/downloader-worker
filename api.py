"""
Cloudflare Python Worker version of the downloader API.

Important:
- This file is designed for Cloudflare Python Workers, not VPS/Uvicorn.
- Selenium/Chrome was removed because Cloudflare Workers cannot run a browser.
- Endpoints are implemented with async HTTP fetch via httpx and HTML parsing.
- If a provider changes its HTML/API flow, the relevant endpoint can return an error JSON.

Routes:
- / or /web
- /snaptik?url=<tiktok_url>
- /ytdown?url=<youtube_url>&quality=<optional>
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs

import asgi
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from workers import WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)


app = FastAPI(title="Downloader API", docs_url=None, redoc_url=None, openapi_url=None)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)

COMMON_HEADERS = {
    "user-agent": UA,
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,text/plain,*/*;q=0.8",
    "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}


# -------------------------
# General helpers
# -------------------------

def clean(text: Any) -> str | None:
    value = re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()
    return value or None


def strip_tags(fragment: str | None) -> str | None:
    if not fragment:
        return None
    fragment = re.sub(r"<script[\s\S]*?</script>", "", fragment, flags=re.I)
    fragment = re.sub(r"<style[\s\S]*?</style>", "", fragment, flags=re.I)
    return clean(re.sub(r"<[^>]+>", " ", fragment))


def attr(fragment: str | None, name: str) -> str | None:
    if not fragment:
        return None
    m = re.search(rf'{re.escape(name)}\s*=\s*["\']([^"\']+)["\']', fragment, re.I)
    return html.unescape(m.group(1)) if m else None


def abs_url(base: str, value: str | None) -> str | None:
    if not value:
        return None
    return urljoin(base, html.unescape(value))


def json_response(data: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code)


def get_youtube_id(url: str) -> str | None:
    url = str(url or "").strip()

    if "youtu.be/" in url:
        return url.split("youtu.be/", 1)[1].split("?", 1)[0].split("/", 1)[0]

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if qs.get("v"):
        return qs["v"][0]

    m = re.search(r"/shorts/([^?/%]+)", url)
    if m:
        return m.group(1)

    return None


def youtube_thumbnail(video_id: str | None) -> str | None:
    if not video_id:
        return None
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def normalize_quality(q: str) -> str:
    q = str(q or "").lower().strip().replace(" ", "")
    return {
        "1080": "1080p",
        "1080p": "1080p",
        "fhd": "1080p",
        "720": "720p",
        "720p": "720p",
        "hd": "720p",
        "480": "480p",
        "480p": "480p",
        "360": "360p",
        "360p": "360p",
        "240": "240p",
        "240p": "240p",
        "144": "144p",
        "144p": "144p",
        "mp3": "mp3",
        "m4a": "m4a",
        "audio": "audio",
        "128": "128k",
        "128k": "128k",
        "48": "48k",
        "48k": "48k",
    }.get(q, q)


def parse_ytdown_option(label: str, url: str, filesize: str | None = None) -> dict[str, Any]:
    label = clean(label) or "Download"
    label_l = label.lower()
    ext = None
    item_type = "unknown"
    quality = None

    if label_l.startswith("mp4"):
        ext = "mp4"
        item_type = "video"
    elif label_l.startswith("m4a"):
        ext = "m4a"
        item_type = "audio"
    elif label_l.startswith("mp3"):
        ext = "mp3"
        item_type = "audio"

    res = re.search(r"(\d+x\d+)", label)
    bitrate = re.search(r"(\d+\s*K)", label, re.I)
    qp = re.search(r"(\d{3,4}p)", url, re.I)

    if res:
        quality = res.group(1)
    elif bitrate:
        quality = bitrate.group(1).replace(" ", "")
    elif qp:
        quality = qp.group(1).lower()

    return {
        "label": label,
        "type": item_type,
        "ext": ext,
        "quality": quality,
        "filesize": clean(filesize),
        "download_url": url,
    }


def match_quality(item: dict[str, Any], q: str) -> bool:
    label = str(item.get("label") or "").lower()
    quality = str(item.get("quality") or "").lower()
    url = str(item.get("download_url") or "").lower()
    ext = str(item.get("ext") or "").lower()
    typ = str(item.get("type") or "").lower()

    if q == "1080p":
        return "1080" in label or "1920x1080" in quality or "/1080p" in url
    if q == "720p":
        return "720" in label or "1280x720" in quality or "/720p" in url
    if q == "480p":
        return "480" in label or "854x480" in quality or "/480p" in url
    if q == "360p":
        return "360" in label or "640x360" in quality or "/360p" in url
    if q == "240p":
        return "240" in label or "426x240" in quality or "/240p" in url
    if q == "144p":
        return "144" in label or "256x144" in quality or "/144p" in url
    if q == "mp3":
        return ext == "mp3" or "mp3" in label
    if q == "m4a":
        return ext == "m4a" or "m4a" in label
    if q == "audio":
        return typ == "audio"
    if q in ["128k", "48k"]:
        return q in label or q in quality or q in url
    return q in label or q in quality or q in url


# -------------------------
# SnapTik, pure HTTP version
# -------------------------

async def resolve_snaptik_hd(client: httpx.AsyncClient, hd_api: str | None) -> str | None:
    if not hd_api:
        return None

    try:
        res = await client.get(
            hd_api,
            headers={
                **COMMON_HEADERS,
                "accept": "application/json, text/plain, */*",
                "referer": "https://snaptik.app/en2",
                "x-requested-with": "XMLHttpRequest",
            },
            timeout=30,
        )
        data = res.json()
        if isinstance(data, dict) and data.get("error") is False and data.get("url"):
            return data.get("url")
    except Exception:
        return None

    return None


def parse_snaptik_html(page: str, base: str = "https://snaptik.app/en2") -> dict[str, Any]:
    info = None
    m_info = re.search(r'<div[^>]+class=["\'][^"\']*\binfo\b[^"\']*["\'][^>]*>([\s\S]*?)</div>\s*</div>', page, re.I)
    if m_info:
        info = m_info.group(1)

    title = None
    author = None
    if info:
        m_title = re.search(r'<div[^>]+class=["\'][^"\']*\bvideo-title\b[^"\']*["\'][^>]*>([\s\S]*?)</div>', info, re.I)
        title = strip_tags(m_title.group(1)) if m_title else None
        m_author = re.search(r'<span[^>]*>([\s\S]*?)</span>', info, re.I)
        author = strip_tags(m_author.group(1)) if m_author else None

    if not title:
        m_title = re.search(r'<div[^>]+class=["\'][^"\']*\bvideo-title\b[^"\']*["\'][^>]*>([\s\S]*?)</div>', page, re.I)
        title = strip_tags(m_title.group(1)) if m_title else None

    m_img = re.search(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', page, re.I)
    thumbnail = abs_url(base, m_img.group(1)) if m_img else None

    # Normal download URL.
    video_url = None
    m_video = re.search(r'<a[^>]+class=["\'][^"\']*\bdownload-file\b[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', page, re.I)
    if not m_video:
        m_video = re.search(r'<a[^>]+href=["\']([^"\']*rapidcdn[^"\']+)["\']', page, re.I)
    if m_video:
        video_url = abs_url(base, m_video.group(1))

    # HD button. data-tokenhd can resolve true HD. data-backup is fallback and can equal normal.
    m_hd_btn = re.search(r'<button[^>]+(?:btn-download-hd|data-tokenhd|data-backup)[\s\S]*?</button>', page, re.I)
    hd_api = attr(m_hd_btn.group(0), "data-tokenhd") if m_hd_btn else None
    hd_backup = attr(m_hd_btn.group(0), "data-backup") if m_hd_btn else None

    return {
        "title": title,
        "author": author,
        "thumbnail": thumbnail,
        "video_url": video_url,
        "video_hd_api": abs_url(base, hd_api),
        "video_hd_backup": abs_url(base, hd_backup),
    }


def extract_form_action_and_fields(page: str, base: str) -> tuple[str, dict[str, str]]:
    # Be deliberately generic because SnapTik rotates markup.
    form = re.search(r"<form[\s\S]*?</form>", page, re.I)
    action = base
    fields: dict[str, str] = {}

    if form:
        form_html = form.group(0)
        action = abs_url(base, attr(form_html, "action")) or base
        for inp in re.finditer(r"<input[^>]*>", form_html, re.I):
            tag = inp.group(0)
            name = attr(tag, "name")
            value = attr(tag, "value") or ""
            if name:
                fields[name] = value

    return action, fields


async def scrape_snaptik_pure(tiktok_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        home = await client.get("https://snaptik.app/en2", headers=COMMON_HEADERS)
        home_text = home.text
        action, fields = extract_form_action_and_fields(home_text, str(home.url))

        # Try likely field names. SnapTik has used url/link in different versions.
        attempts = []
        for field_name in ["url", "link"]:
            data = dict(fields)
            data[field_name] = tiktok_url
            attempts.append((action, data))
        attempts.append(("https://snaptik.app/abc2.php", {"url": tiktok_url}))
        attempts.append(("https://snaptik.app/en2", {"url": tiktok_url}))

        last_text = ""
        parsed: dict[str, Any] | None = None

        for endpoint, data in attempts:
            try:
                res = await client.post(
                    endpoint,
                    data=data,
                    headers={
                        **COMMON_HEADERS,
                        "content-type": "application/x-www-form-urlencoded",
                        "referer": "https://snaptik.app/en2",
                    },
                )
                text = res.text
                last_text = text[:700]
                candidate = parse_snaptik_html(text, str(res.url))
                if candidate.get("video_url") or candidate.get("video_hd_backup") or candidate.get("video_hd_api"):
                    parsed = candidate
                    break
            except Exception as e:
                last_text = str(e)

        if not parsed:
            return {
                "status": "error",
                "source": "snaptik-worker",
                "message": "SnapTik result tidak ditemukan tanpa browser. Situs mungkin butuh JavaScript/anti-bot.",
                "debug_text": last_text,
            }

        video_hd_url = await resolve_snaptik_hd(client, parsed.get("video_hd_api"))
        if not video_hd_url:
            # Fallback: still useful; may equal normal URL on some videos.
            video_hd_url = parsed.get("video_hd_backup")

        return {
            "status": "success" if (parsed.get("video_url") or video_hd_url) else "error",
            "source": "snaptik-worker",
            "title": parsed.get("title"),
            "author": parsed.get("author"),
            "thumbnail": parsed.get("thumbnail"),
            "video_url": parsed.get("video_url"),
            "video_hd_url": video_hd_url,
        }


# -------------------------
# YTDown, pure HTTP version
# -------------------------

async def poll_ytdown_worker(client: httpx.AsyncClient, worker_url: str, max_attempts: int = 20) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            res = await client.get(
                worker_url,
                headers={
                    **COMMON_HEADERS,
                    "accept": "application/json,text/plain,*/*",
                    "referer": "https://app.ytdown.to/id29",
                    "origin": "https://app.ytdown.to",
                },
                timeout=30,
            )
            data = res.json()
        except Exception as e:
            data = {"status": "error", "message": str(e)}

        last = data if isinstance(data, dict) else {"raw": data}
        file_url = last.get("fileUrl")
        if file_url and file_url != "Waiting...":
            return {
                "ready": True,
                "attempt": attempt,
                "raw": last,
                "download_url": file_url,
            }
        if str(last.get("status") or "").lower() in ["error", "failed", "fail"]:
            break

    return {
        "ready": False,
        "attempt": max_attempts,
        "raw": last,
        "download_url": None,
    }


def parse_ytdown_html(page: str, base: str = "https://app.ytdown.to/id29") -> dict[str, Any]:
    title = None
    duration = None

    body_text = strip_tags(page) or ""
    m_duration = re.search(r"\b\d{1,2}:\d{2}:\d{2}\b|\b\d{1,2}:\d{2}\b", body_text)
    if m_duration:
        duration = m_duration.group(0)

    for pat in [
        r'<h[123][^>]*>([\s\S]*?)</h[123]>',
        r'<[^>]+class=["\'][^"\']*\btitle\b[^"\']*["\'][^>]*>([\s\S]*?)</[^>]+>',
    ]:
        m = re.search(pat, page, re.I)
        if m:
            cand = strip_tags(m.group(1))
            if cand and not re.search(r"ytdown|download|unduh|media|informasi", cand, re.I):
                title = cand
                break

    formats = []
    # Parse <option value="worker-url" data-filesize="...">MP4 - (...)</option>
    for opt in re.finditer(r"<option[^>]+value=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</option>", page, re.I):
        tag_start = opt.group(0)
        value = html.unescape(opt.group(1))
        label = strip_tags(opt.group(2)) or "Download"
        if not value.startswith("http"):
            continue
        filesize = attr(tag_start, "data-filesize")
        formats.append(parse_ytdown_option(label, abs_url(base, value) or value, filesize))

    video_formats = [x for x in formats if x.get("type") == "video"]
    audio_formats = [x for x in formats if x.get("type") == "audio"]

    return {
        "title": title,
        "duration": duration,
        "formats": formats,
        "video_formats": video_formats,
        "audio_formats": audio_formats,
    }


async def scrape_ytdown_pure(video_url: str, quality: str = "") -> dict[str, Any]:
    video_id = get_youtube_id(video_url)

    async with httpx.AsyncClient(follow_redirects=True, timeout=90) as client:
        home = await client.get("https://app.ytdown.to/id29", headers=COMMON_HEADERS)
        home_text = home.text
        action, fields = extract_form_action_and_fields(home_text, str(home.url))

        attempts = []
        for field_name in ["url", "link", "q"]:
            data = dict(fields)
            data[field_name] = video_url
            attempts.append((action, data))
        attempts.append(("https://app.ytdown.to/id29", {"url": video_url}))

        parsed = None
        last_text = ""

        for endpoint, data in attempts:
            try:
                res = await client.post(
                    endpoint,
                    data=data,
                    headers={
                        **COMMON_HEADERS,
                        "content-type": "application/x-www-form-urlencoded",
                        "referer": "https://app.ytdown.to/id29",
                    },
                )
                last_text = res.text[:700]
                candidate = parse_ytdown_html(res.text, str(res.url))
                if candidate.get("formats"):
                    parsed = candidate
                    break
            except Exception as e:
                last_text = str(e)

        if not parsed:
            return {
                "status": "error",
                "source": "ytdown-worker",
                "video_id": video_id,
                "thumbnail": youtube_thumbnail(video_id),
                "message": "YTDown result tidak ditemukan tanpa browser. Worker tidak bisa menjalankan Selenium/Chrome.",
                "debug_text": last_text,
            }

        formats = parsed.get("formats", [])
        result = {
            "status": "success" if formats else "error",
            "source": "ytdown-worker",
            "video_id": video_id,
            "title": parsed.get("title"),
            "duration": parsed.get("duration"),
            "thumbnail": youtube_thumbnail(video_id),
            "total_formats": len(formats),
            "available_qualities": [x.get("label") for x in formats],
            "video_formats": parsed.get("video_formats", []),
            "audio_formats": parsed.get("audio_formats", []),
            "formats": formats,
        }

        q = normalize_quality(quality)
        if not q:
            return result

        pool = result["audio_formats"] if q in ["mp3", "m4a", "audio", "128k", "48k"] else result["video_formats"]
        selected = next((item for item in pool if match_quality(item, q)), None)
        if not selected and q == "audio" and pool:
            selected = pool[0]

        if not selected:
            return {
                "status": "error",
                "source": "ytdown-worker",
                "video_id": video_id,
                "title": result.get("title"),
                "duration": result.get("duration"),
                "thumbnail": youtube_thumbnail(video_id),
                "requested_quality": quality,
                "message": f"Format tidak ditemukan: {quality}",
                "available_qualities": [x.get("label") for x in pool],
            }

        worker = await poll_ytdown_worker(client, selected.get("download_url"))
        raw = worker.get("raw") or {}
        download_url = worker.get("download_url")

        base = {
            "status": "success" if download_url else "processing",
            "source": "ytdown-worker",
            "video_id": video_id,
            "title": result.get("title"),
            "duration": result.get("duration"),
            "thumbnail": youtube_thumbnail(video_id),
            "requested_quality": quality,
            "selected_quality": selected.get("quality"),
            "file_name": raw.get("fileName"),
            "file_size": raw.get("fileSize") or selected.get("filesize"),
        }
        if download_url:
            base["download_url"] = download_url
        else:
            base["message"] = "File masih diproses oleh YTDown, coba request ulang beberapa detik lagi."
        return base


# -------------------------
# Web UI
# -------------------------

HTML_PAGE = """<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Downloader API</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; font-family: Arial, sans-serif; background: #0f172a; color: #e5e7eb; padding: 20px; }
    .box { max-width: 920px; margin: auto; background: #111827; border: 1px solid #334155; border-radius: 18px; padding: 20px; }
    h1 { margin-top: 0; font-size: 26px; }
    p { color: #94a3b8; line-height: 1.45; }
    label { display: block; margin-top: 14px; margin-bottom: 6px; color: #cbd5e1; font-size: 14px; }
    input, select { width: 100%; padding: 13px; border-radius: 12px; border: 1px solid #475569; background: #020617; color: white; font-size: 15px; }
    .choose { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 18px; }
    .choice-btn { padding: 22px 14px; border: 1px solid #334155; border-radius: 16px; background: #020617; color: #e5e7eb; cursor: pointer; font-size: 18px; font-weight: bold; }
    .choice-btn:hover { border-color: #22c55e; background: #052e16; }
    .back-btn { width: auto; padding: 10px 14px; margin: 0 0 14px 0; background: #334155; color: #e5e7eb; font-size: 14px; }
    button { margin-top: 16px; width: 100%; padding: 14px; border: 0; border-radius: 12px; background: #22c55e; color: #052e16; font-weight: bold; cursor: pointer; font-size: 16px; }
    button:disabled { opacity: .65; cursor: wait; }
    pre { margin-top: 18px; padding: 16px; min-height: 400px; white-space: pre-wrap; word-break: break-word; overflow: auto; background: #020617; border: 1px solid #334155; border-radius: 14px; color: #d1fae5; font-size: 13px; line-height: 1.45; }
    .hint { margin-top: 10px; padding: 12px; background: #020617; border: 1px solid #334155; border-radius: 12px; color: #94a3b8; font-size: 13px; }
    .warn { border-color: #92400e; background: #1c1917; color: #fed7aa; }
    .hidden { display: none; }
    @media (max-width: 650px) { .choose { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class="box">
    <div id="selectScreen">
      <h1>Downloader API</h1>
      <p>Pilih dulu mau ambil result dari platform mana.</p>
      <div class="hint warn">Cloudflare Worker tidak bisa menjalankan Selenium/Chrome. API ini memakai HTTP fetch murni, jadi kalau situs target butuh JavaScript/anti-bot, endpoint bisa gagal dan mengembalikan error JSON.</div>
      <div class="choose">
        <button class="choice-btn" onclick="choosePlatform('tiktok')">TikTok</button>
        <button class="choice-btn" onclick="choosePlatform('youtube')">YouTube</button>
      </div>
      <pre>Silakan pilih TikTok atau YouTube...</pre>
    </div>

    <div id="formScreen" class="hidden">
      <button class="back-btn" onclick="goBack()">← Ganti Platform</button>
      <h1 id="title">Downloader</h1>
      <p id="desc"></p>
      <div id="qualityBox">
        <label>Format / Quality YouTube</label>
        <select id="quality">
          <option value="">Semua format</option>
          <option value="1080p">Video 1080p</option>
          <option value="720p">Video 720p</option>
          <option value="480p">Video 480p</option>
          <option value="360p">Video 360p</option>
          <option value="240p">Video 240p</option>
          <option value="144p">Video 144p</option>
          <option value="mp3">Audio MP3</option>
          <option value="m4a">Audio M4A</option>
          <option value="128k">Audio 128K</option>
          <option value="48k">Audio 48K</option>
        </select>
      </div>
      <label>URL</label>
      <input id="url" placeholder="Masukkan URL">
      <div class="hint" id="hint"></div>
      <button id="btn" onclick="run()">Ambil Result</button>
      <pre id="out">Result JSON akan muncul di sini...</pre>
    </div>
  </div>

<script>
let platform = "";
function choosePlatform(p) {
  platform = p;
  document.getElementById("selectScreen").classList.add("hidden");
  document.getElementById("formScreen").classList.remove("hidden");
  const title = document.getElementById("title");
  const desc = document.getElementById("desc");
  const qualityBox = document.getElementById("qualityBox");
  const url = document.getElementById("url");
  const hint = document.getElementById("hint");
  const out = document.getElementById("out");
  out.textContent = "Result JSON akan muncul di sini...";
  if (platform === "youtube") {
    title.textContent = "YouTube Downloader";
    desc.textContent = "Masukkan link YouTube, lalu pilih format video/audio.";
    qualityBox.style.display = "block";
    url.placeholder = "https://youtu.be/VIDEO_ID";
    url.value = "";
    hint.textContent = "Endpoint: /ytdown?url=...&quality=720p";
  } else {
    title.textContent = "TikTok Downloader";
    desc.textContent = "Masukkan link TikTok.";
    qualityBox.style.display = "none";
    url.placeholder = "https://www.tiktok.com/@username/video/...";
    url.value = "";
    hint.textContent = "Endpoint: /snaptik?url=...";
  }
}
function goBack() {
  platform = "";
  document.getElementById("formScreen").classList.add("hidden");
  document.getElementById("selectScreen").classList.remove("hidden");
}
async function run() {
  const url = document.getElementById("url").value.trim();
  const quality = document.getElementById("quality").value.trim();
  const out = document.getElementById("out");
  const btn = document.getElementById("btn");
  if (!platform) { out.textContent = JSON.stringify({status:"error", message:"Pilih platform dulu."}, null, 2); return; }
  if (!url) { out.textContent = JSON.stringify({status:"error", message:"Isi URL dulu."}, null, 2); return; }
  btn.disabled = true; btn.textContent = "Memproses..."; out.textContent = "Loading...";
  try {
    let api = platform === "youtube" ? "/ytdown?url=" + encodeURIComponent(url) : "/snaptik?url=" + encodeURIComponent(url);
    if (platform === "youtube" && quality) api += "&quality=" + encodeURIComponent(quality);
    const res = await fetch(api);
    const data = await res.json();
    out.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    out.textContent = JSON.stringify({status:"error", message:String(e)}, null, 2);
  } finally {
    btn.disabled = false; btn.textContent = "Ambil Result";
  }
}
</script>
</body>
</html>"""


# -------------------------
# Routes
# -------------------------

@app.get("/")
async def home():
    return {
        "status": "online",
        "runtime": "cloudflare-python-worker",
        "note": "Pure HTTP version. Selenium/Chrome is not available in Cloudflare Workers.",
        "endpoints": {
            "web": "/web",
            "youtube": "/ytdown?url=https://youtu.be/VIDEO_ID&quality=720p",
            "tiktok": "/snaptik?url=https://www.tiktok.com/@user/video/ID",
        },
    }


@app.get("/web", response_class=HTMLResponse)
async def web():
    return HTMLResponse(HTML_PAGE)


@app.get("/snaptik")
async def snaptik(url: str = Query(..., description="TikTok URL")):
    try:
        return json_response(await scrape_snaptik_pure(url))
    except Exception as e:
        return json_response({
            "status": "error",
            "source": "snaptik-worker",
            "error_type": type(e).__name__,
            "message": str(e) or repr(e),
        })


@app.get("/ytdown")
async def ytdown(
    url: str = Query(..., description="YouTube URL"),
    quality: str = Query("", description="Optional: 1080p, 720p, 480p, 360p, 240p, 144p, mp3, m4a, 128k, 48k"),
):
    try:
        return json_response(await scrape_ytdown_pure(url, quality))
    except Exception as e:
        return json_response({
            "status": "error",
            "source": "ytdown-worker",
            "error_type": type(e).__name__,
            "message": str(e) or repr(e),
        })
