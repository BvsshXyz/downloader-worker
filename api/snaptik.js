function json(res, data, status = 200) {
  res.status(status).setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("access-control-allow-origin", "*");
  res.end(JSON.stringify(data, null, 2));
}

function clean(s = "") {
  return String(s || "").replace(/\s+/g, " ").trim();
}

function decodeHtml(s = "") {
  return String(s || "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#039;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function pick(regex, html) {
  const m = html.match(regex);
  return m ? decodeHtml(m[1]) : null;
}

async function postSnapTik(tiktokUrl) {
  const endpoints = [
    "https://snaptik.app/abc2.php",
    "https://snaptik.app/en2/abc2.php",
    "https://snaptik.app/en2"
  ];

  const body = new URLSearchParams();
  body.set("url", tiktokUrl);
  body.set("lang", "en2");

  let lastText = "";

  for (const endpoint of endpoints) {
    try {
      const r = await fetch(endpoint, {
        method: "POST",
        headers: {
          "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
          "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135 Safari/537.36",
          "accept": "*/*",
          "origin": "https://snaptik.app",
          "referer": "https://snaptik.app/en2"
        },
        body
      });

      const text = await r.text();
      lastText = text;

      if (text.includes("download-file") || text.includes("rapidcdn") || text.includes("data-tokenhd")) {
        return text;
      }
    } catch {}
  }

  return lastText;
}

async function resolveHd(hdApi) {
  if (!hdApi) return null;

  try {
    const r = await fetch(hdApi, {
      headers: {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135 Safari/537.36",
        "accept": "application/json,text/plain,*/*",
        "referer": "https://snaptik.app/en2"
      }
    });

    const data = await r.json();

    if (data && data.error === false && data.url) {
      return data.url;
    }
  } catch {}

  return null;
}

export default async function handler(req, res) {
  try {
    const url = req.query.url;

    if (!url) {
      return json(res, {
        status: "error",
        message: "Parameter url wajib diisi"
      }, 400);
    }

    const html = await postSnapTik(url);

    const title =
      clean(pick(/<div[^>]*class=["'][^"']*video-title[^"']*["'][^>]*>([\s\S]*?)<\/div>/i, html)?.replace(/<[^>]+>/g, "")) ||
      null;

    const infoBlock = pick(/<div[^>]*class=["'][^"']*info[^"']*["'][^>]*>([\s\S]*?)<\/div>/i, html) || "";
    const author =
      clean(pick(/<span[^>]*>([\s\S]*?)<\/span>/i, infoBlock)?.replace(/<[^>]+>/g, "")) ||
      null;

    const thumbnail =
      pick(/<img[^>]+src=["']([^"']+)["']/i, html);

    const videoUrl =
      pick(/<a[^>]+class=["'][^"']*download-file[^"']*["'][^>]+href=["']([^"']+)["']/i, html) ||
      pick(/<a[^>]+href=["']([^"']*rapidcdn[^"']+)["']/i, html);

    const hdApi =
      pick(/data-tokenhd=["']([^"']+)["']/i, html);

    const hdBackup =
      pick(/data-backup=["']([^"']+)["']/i, html);

    let videoHdUrl = await resolveHd(hdApi);
    if (!videoHdUrl) videoHdUrl = hdBackup || null;

    if (!videoUrl && !videoHdUrl) {
      return json(res, {
        status: "error",
        source: "vercel-snaptik-fetch",
        message: "Result SnapTik tidak ditemukan. Kemungkinan SnapTik butuh JavaScript/browser atau endpoint berubah.",
        debug_text: clean(html.slice(0, 1000))
      });
    }

    return json(res, {
      status: "success",
      source: "vercel-snaptik-fetch",
      title,
      author,
      thumbnail,
      video_url: videoUrl || null,
      video_hd_url: videoHdUrl || null
    });
  } catch (e) {
    return json(res, {
      status: "error",
      source: "vercel-snaptik-fetch",
      message: String(e?.message || e)
    }, 500);
  }
}
