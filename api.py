from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, HTMLResponse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re
import requests
import time


app = FastAPI(title="Downloader API", docs_url=None, redoc_url=None, openapi_url=None)


def get_youtube_id(url: str):
    url = str(url or "").strip()

    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("/")[0]

    m = re.search(r"[?&]v=([^&]+)", url)
    if m:
        return m.group(1)

    m = re.search(r"/shorts/([^?/%]+)", url)
    if m:
        return m.group(1)

    return None


def get_thumbnail(video_id: str | None):
    if not video_id:
        return None

    return {
        "default": f"https://i.ytimg.com/vi/{video_id}/default.jpg",
        "medium": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        "high": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "standard": f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
        "maxres": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
    }


def normalize_quality(q: str):
    q = str(q or "").lower().strip().replace(" ", "")

    aliases = {
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
    }

    return aliases.get(q, q)


def match_quality(item, q: str):
    if not q:
        return False

    label = str(item.get("label") or "").lower()
    quality = str(item.get("quality") or "").lower()
    url = str(item.get("download_url") or "").lower()

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
        return "mp3" in label or item.get("ext") == "mp3"

    if q == "m4a":
        return "m4a" in label or item.get("ext") == "m4a"

    if q == "audio":
        return item.get("type") == "audio"

    if q in ["128k", "48k"]:
        return q in label or q in quality or q in url

    return q in label or q in quality or q in url


def parse_option(label: str, url: str, filesize: str | None):
    label = " ".join(str(label or "").split())

    ext = None
    item_type = "unknown"
    quality = None

    if label.upper().startswith("MP4"):
        ext = "mp4"
        item_type = "video"
    elif label.upper().startswith("M4A"):
        ext = "m4a"
        item_type = "audio"
    elif label.upper().startswith("MP3"):
        ext = "mp3"
        item_type = "audio"

    res = re.search(r"(\d+x\d+)", label)
    bitrate = re.search(r"(\d+K)", label, re.I)

    if res:
        quality = res.group(1)
    elif bitrate:
        quality = bitrate.group(1)

    return {
        "label": label,
        "type": item_type,
        "ext": ext,
        "quality": quality,
        "filesize": filesize,
        "download_url": url,
    }



def poll_worker(worker_url: str, max_attempts: int = 30, delay: int = 3):
    last_data = None

    for attempt in range(1, max_attempts + 1):
        r = requests.get(
            worker_url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json,text/plain,*/*",
            },
        )

        try:
            data = r.json()
        except Exception:
            data = {
                "status": "error",
                "message": "Worker response bukan JSON",
                "http_status": r.status_code,
                "text": r.text[:500],
            }

        last_data = data

        file_url = data.get("fileUrl")
        status = str(data.get("status") or "").lower()

        if file_url and file_url != "Waiting...":
            return {
                "ready": True,
                "attempt": attempt,
                "worker_status": data.get("status"),
                "worker_response": data,
                "download_url": file_url,
            }

        if status in ["error", "failed", "fail"]:
            return {
                "ready": False,
                "attempt": attempt,
                "worker_status": data.get("status"),
                "worker_response": data,
                "download_url": None,
            }

        time.sleep(delay)

    return {
        "ready": False,
        "attempt": max_attempts,
        "worker_status": last_data.get("status") if isinstance(last_data, dict) else None,
        "worker_response": last_data,
        "download_url": None,
    }



def poll_worker_url(worker_url: str, cookies: dict | None = None, max_attempts: int = 60, delay: int = 2):
    last = None

    session = requests.Session()

    if cookies:
        session.cookies.update(cookies)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://app.ytdown.to/id29",
        "Origin": "https://app.ytdown.to",
        "Connection": "keep-alive",
    }

    for attempt in range(1, max_attempts + 1):
        r = session.get(worker_url, timeout=30, headers=headers)

        try:
            data = r.json()
        except Exception:
            data = {
                "status": "error",
                "message": "Worker response bukan JSON",
                "http_status": r.status_code,
                "text": r.text[:500],
            }

        last = data

        file_url = data.get("fileUrl")
        status = str(data.get("status") or "").lower()

        if file_url and file_url != "Waiting...":
            return {
                "ready": True,
                "attempt": attempt,
                "status": data.get("status"),
                "file_name": data.get("fileName"),
                "percent": data.get("percent"),
                "progress": data.get("progress"),
                "estimated_file_size": data.get("estimatedFileSize"),
                "file_size": data.get("fileSize"),
                "download_url": file_url,
                "raw": data,
            }

        if status in ["error", "failed", "fail"]:
            return {
                "ready": False,
                "attempt": attempt,
                "status": data.get("status"),
                "download_url": None,
                "raw": data,
            }

        time.sleep(delay)

    return {
        "ready": False,
        "attempt": max_attempts,
        "status": last.get("status") if isinstance(last, dict) else None,
        "download_url": None,
        "raw": last,
    }



def clean_quality_response(data):
    if not isinstance(data, dict):
        return data

    # Kalau quality kosong / semua format, biarkan apa adanya
    if "requested_quality" not in data:
        return data

    thumb = data.get("thumbnail")
    if isinstance(thumb, dict):
        thumbnail = thumb.get("high") or thumb.get("default") or thumb.get("medium")
    else:
        thumbnail = thumb

    worker = data.get("worker_response") or {}
    selected = data.get("selected_format") or {}

    if data.get("download_url"):
        return {
            "status": "success",
            "source": data.get("source"),
            "video_id": data.get("video_id"),
            "title": data.get("title"),
            "duration": data.get("duration"),
            "thumbnail": thumbnail,
            "requested_quality": data.get("requested_quality"),
            "selected_quality": data.get("selected_quality"),
            "file_name": data.get("file_name") or worker.get("fileName"),
            "file_size": data.get("file_size") or worker.get("fileSize") or selected.get("filesize"),
            "download_url": data.get("download_url"),
        }

    return {
        "status": "processing",
        "source": data.get("source"),
        "video_id": data.get("video_id"),
        "title": data.get("title"),
        "duration": data.get("duration"),
        "thumbnail": thumbnail,
        "requested_quality": data.get("requested_quality"),
        "selected_quality": data.get("selected_quality"),
        "file_name": data.get("file_name") or worker.get("fileName"),
        "file_size": data.get("file_size") or worker.get("fileSize") or selected.get("filesize"),
        "message": "File masih diproses oleh YTDown, coba request ulang beberapa detik lagi.",
    }


def clean_api_response(data):
    if not isinstance(data, dict):
        return data

    # Kalau semua format, biarkan detail formats tetap keluar.
    if not data.get("requested_quality"):
        thumb = data.get("thumbnail")
        if isinstance(thumb, dict):
            data["thumbnail"] = thumb.get("high") or thumb.get("default") or thumb.get("medium")
        return data

    thumb = data.get("thumbnail")
    if isinstance(thumb, dict):
        thumbnail = thumb.get("high") or thumb.get("default") or thumb.get("medium")
    else:
        thumbnail = thumb

    worker = data.get("worker_response") or {}
    selected = data.get("selected_format") or {}
    download_url = data.get("download_url") or worker.get("fileUrl")

    if download_url == "Waiting...":
        download_url = None

    base = {
        "status": "success" if download_url else "processing",
        "source": data.get("source"),
        "video_id": data.get("video_id"),
        "title": data.get("title"),
        "duration": data.get("duration"),
        "thumbnail": thumbnail,
        "requested_quality": data.get("requested_quality"),
        "selected_quality": data.get("selected_quality"),
        "file_name": data.get("file_name") or worker.get("fileName"),
        "file_size": data.get("file_size") or worker.get("fileSize") or selected.get("filesize"),
    }

    if download_url:
        base["download_url"] = download_url
    else:
        base["message"] = "File masih diproses oleh YTDown, coba request ulang beberapa detik lagi."

    return base


def pick_quality_from_result(result, quality):
    q = normalize_quality(quality)

    if not q:
        return result

    formats = result.get("formats", [])

    if q in ["mp3", "m4a", "audio", "128k", "48k"]:
        pool = [x for x in formats if x.get("type") == "audio"]
    else:
        pool = [x for x in formats if x.get("type") == "video"]

    selected = next((item for item in pool if match_quality(item, q)), None)

    if not selected and q == "audio" and pool:
        selected = pool[0]

    if not selected:
        return {
            "status": "error",
            "source": "ytdown-selenium",
            "message": f"Format tidak ditemukan: {quality}",
            "video_id": result.get("video_id"),
            "title": result.get("title"),
            "duration": result.get("duration"),
            "thumbnail": result.get("thumbnail"),
            "requested_quality": quality,
            "available_qualities": [x.get("label") for x in pool],
        }

    worker_url = selected.get("download_url")

    worker = poll_worker_url(worker_url) if "poll_worker_url" in globals() else {
        "download_url": worker_url,
        "raw": {},
    }

    raw = worker.get("raw") or {}
    final_url = worker.get("download_url")

    return {
        "status": "success" if final_url else "processing",
        "source": "ytdown-selenium",
        "video_id": result.get("video_id"),
        "title": result.get("title"),
        "duration": result.get("duration"),
        "thumbnail": result.get("thumbnail"),
        "requested_quality": quality,
        "selected_quality": selected.get("quality"),
        "selected_format": selected,
        "file_name": raw.get("fileName"),
        "file_size": raw.get("fileSize") or selected.get("filesize"),
        "download_url": final_url,
        "worker_response": raw,
    }

def scrape_ytdown(video_url: str):
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )

    driver = webdriver.Chrome(options=chrome_options)

    try:
        driver.get("https://app.ytdown.to/id29")

        input_field = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "input[placeholder*='Tempel tautan'], input[type='text'], textarea",
                )
            )
        )

        input_field.clear()
        input_field.send_keys(video_url)

        submit_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Unduh')]"))
        )
        submit_btn.click()

        WebDriverWait(driver, 90).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "select.download-option option")
            )
        )

        title = None
        duration = None

        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            duration_match = re.search(r"\b\d{1,2}:\d{2}:\d{2}\b|\b\d{1,2}:\d{2}\b", body_text)
            if duration_match:
                duration = duration_match.group(0)
        except Exception:
            pass

        try:
            title_candidates = driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3, strong, b, .title")
            for el in title_candidates:
                text = " ".join(el.text.split())
                if text and not re.search(r"informasi|media|unduh|ytdown|download", text, re.I):
                    title = text
                    break
        except Exception:
            pass

        options = driver.find_elements(By.CSS_SELECTOR, "select.download-option option")

        formats = []
        for opt in options:
            label = opt.text
            url = opt.get_attribute("value")
            filesize = opt.get_attribute("data-filesize")

            if url and url.startswith("http"):
                formats.append(parse_option(label, url, filesize))

        video_formats = [x for x in formats if x["type"] == "video"]
        audio_formats = [x for x in formats if x["type"] == "audio"]

        video_id = get_youtube_id(video_url)

        cookies = {c.get("name"): c.get("value") for c in driver.get_cookies()}

        return {
            "status": "success" if formats else "error",
            "source": "ytdown-selenium",
            "input_url": video_url,
            "video_id": video_id,
            "title": title,
            "duration": duration,
            "thumbnail": get_thumbnail(video_id),
            "total_formats": len(formats),
            "total_video_formats": len(video_formats),
            "total_audio_formats": len(audio_formats),
            "available_qualities": [x.get("label") for x in formats],
            "default_download": video_formats[0] if video_formats else None,
            "best_video": video_formats[0] if video_formats else None,
            "best_audio": audio_formats[0] if audio_formats else None,
            "video_formats": video_formats,
            "audio_formats": audio_formats,
            "formats": formats,
            "_cookies": cookies,
        }

    finally:
        driver.quit()


@app.get("/")
async def home():
    return {
        "status": "online",
        "endpoints": {
            "all_formats": "/ytdown?url=https://youtu.be/VIDEO_ID",
            "by_quality": "/ytdown?url=https://youtu.be/VIDEO_ID&quality=720p",
        },
    }






@app.get("/ytdown")
async def ytdown(
    url: str = Query(..., description="YouTube URL"),
    quality: str = Query("", description="Optional: 1080p, 720p, 480p, 360p, 240p, 144p, mp3, m4a, 128k, 48k"),
):
    try:
        result = scrape_ytdown(url)

        if quality:
            result = pick_quality_from_result(result, quality)

        return JSONResponse(clean_api_response(result))

    except Exception as e:
        return JSONResponse({
            "status": "error",
            "source": "ytdown-selenium",
            "error_type": type(e).__name__,
            "message": str(e) or repr(e),
        })




@app.get("/web", response_class=HTMLResponse)
async def web():
    return """
<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Downloader API Tester</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Arial, sans-serif;
      background: #0f172a;
      color: #e5e7eb;
      padding: 20px;
    }
    .box {
      max-width: 920px;
      margin: auto;
      background: #111827;
      border: 1px solid #334155;
      border-radius: 18px;
      padding: 20px;
    }
    h1 { margin-top: 0; font-size: 26px; }
    p { color: #94a3b8; line-height: 1.45; }
    label {
      display: block;
      margin-top: 14px;
      margin-bottom: 6px;
      color: #cbd5e1;
      font-size: 14px;
    }
    input, select {
      width: 100%;
      padding: 13px;
      border-radius: 12px;
      border: 1px solid #475569;
      background: #020617;
      color: white;
      font-size: 15px;
    }
    .choose {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-top: 18px;
    }
    .choice-btn {
      padding: 22px 14px;
      border: 1px solid #334155;
      border-radius: 16px;
      background: #020617;
      color: #e5e7eb;
      cursor: pointer;
      font-size: 18px;
      font-weight: bold;
    }
    .choice-btn:hover {
      border-color: #22c55e;
      background: #052e16;
    }
    .back-btn {
      width: auto;
      padding: 10px 14px;
      margin: 0 0 14px 0;
      background: #334155;
      color: #e5e7eb;
      font-size: 14px;
    }
    button {
      margin-top: 16px;
      width: 100%;
      padding: 14px;
      border: 0;
      border-radius: 12px;
      background: #22c55e;
      color: #052e16;
      font-weight: bold;
      cursor: pointer;
      font-size: 16px;
    }
    button:disabled {
      opacity: .65;
      cursor: wait;
    }
    pre {
      margin-top: 18px;
      padding: 16px;
      min-height: 400px;
      white-space: pre-wrap;
      word-break: break-word;
      overflow: auto;
      background: #020617;
      border: 1px solid #334155;
      border-radius: 14px;
      color: #d1fae5;
      font-size: 13px;
      line-height: 1.45;
    }
    .hint {
      margin-top: 10px;
      padding: 12px;
      background: #020617;
      border: 1px solid #334155;
      border-radius: 12px;
      color: #94a3b8;
      font-size: 13px;
    }
    .hidden { display: none; }
    @media (max-width: 650px) {
      .choose { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="box">
    <div id="selectScreen">
      <h1>Downloader API Tester</h1>
      <p>Pilih dulu mau ambil result dari platform mana.</p>

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
    url.value = "https://youtu.be/-E0Brk3Fy5s";
    hint.textContent = "Endpoint: /ytdown?url=...&quality=720p";
  } else {
    title.textContent = "TikTok Downloader";
    desc.textContent = "Masukkan link TikTok, result berisi video normal dan HD backup.";
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

  if (!platform) {
    out.textContent = JSON.stringify({
      status: "error",
      message: "Pilih platform dulu."
    }, null, 2);
    return;
  }

  if (!url) {
    out.textContent = JSON.stringify({
      status: "error",
      message: "Isi URL dulu."
    }, null, 2);
    return;
  }

  btn.disabled = true;
  btn.textContent = "Memproses...";
  out.textContent = "Loading...";

  try {
    let api = "";

    if (platform === "youtube") {
      api = "/ytdown?url=" + encodeURIComponent(url);
      if (quality) api += "&quality=" + encodeURIComponent(quality);
    } else {
      api = "/snaptik?url=" + encodeURIComponent(url);
    }

    const res = await fetch(api);
    const data = await res.json();

    out.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    out.textContent = JSON.stringify({
      status: "error",
      message: String(e)
    }, null, 2);
  } finally {
    btn.disabled = false;
    btn.textContent = "Ambil Result";
  }
}
</script>
</body>
</html>
"""




@app.get("/snaptik")
async def snaptik(url: str = Query(..., description="TikTok URL")):
    try:
        return JSONResponse(scrape_snaptik(url))
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "source": "snaptik-selenium",
            "error_type": type(e).__name__,
            "message": str(e) or repr(e),
        })


def scrape_snaptik(tiktok_url: str):
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )

    driver = webdriver.Chrome(options=chrome_options)

    try:
        driver.get("https://snaptik.app/en2")

        input_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[name='url'], input[type='text'], textarea")
            )
        )

        input_field.clear()
        input_field.send_keys(tiktok_url)

        submit_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(., 'Download') or contains(., 'Unduh')]")
            )
        )
        submit_btn.click()

        WebDriverWait(driver, 90).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".download-box, a.download-file[href], a[href*='rapidcdn']")
            )
        )

        box = (
            driver.find_element(By.CSS_SELECTOR, ".download-box")
            if driver.find_elements(By.CSS_SELECTOR, ".download-box")
            else driver.find_element(By.TAG_NAME, "body")
        )

        def text_or_none(selector):
            try:
                text = box.find_element(By.CSS_SELECTOR, selector).text.strip()
                return " ".join(text.split()) or None
            except Exception:
                return None

        def attr_or_none(selector, attr):
            try:
                val = box.find_element(By.CSS_SELECTOR, selector).get_attribute(attr)
                return val or None
            except Exception:
                return None

        title = text_or_none(".info .video-title") or text_or_none(".video-title")
        author = text_or_none(".info span")

        thumbnail = (
            attr_or_none(".video-header img", "src")
            or attr_or_none("img", "src")
        )

        video_url = (
            attr_or_none("a.download-file[href]", "href")
            or attr_or_none("a[href*='rapidcdn']", "href")
        )

        video_hd_url = (
            attr_or_none("button.btn-download-hd", "data-backup")
            or attr_or_none("[data-backup]", "data-backup")
        )

        return {
            "status": "success" if video_url or video_hd_url else "error",
            "source": "snaptik-selenium",
            "title": title,
            "author": author,
            "thumbnail": thumbnail,
            "video_url": video_url,
            "video_hd_url": video_hd_url,
        }

    finally:
        driver.quit()
