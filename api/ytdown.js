import ytdl from "@distube/ytdl-core";

function json(res, data, status = 200) {
  res.status(status).setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("access-control-allow-origin", "*");
  res.end(JSON.stringify(data, null, 2));
}

function normalizeQuality(q = "") {
  q = String(q || "").toLowerCase().trim();

  const aliases = {
    "1080": "1080p",
    "1080p": "1080p",
    "720": "720p",
    "720p": "720p",
    "480": "480p",
    "480p": "480p",
    "360": "360p",
    "360p": "360p",
    "240": "240p",
    "240p": "240p",
    "144": "144p",
    "144p": "144p",
    "mp3": "mp3",
    "audio": "audio",
    "m4a": "m4a"
  };

  return aliases[q] || q;
}

function cleanFormat(f) {
  return {
    label: [
      f.container?.toUpperCase(),
      f.qualityLabel || f.audioBitrate ? `${f.qualityLabel || f.audioBitrate + "kbps"}` : null
    ].filter(Boolean).join(" - "),
    type: f.hasVideo && f.hasAudio ? "video_audio" : f.hasVideo ? "video" : "audio",
    ext: f.container || null,
    quality: f.qualityLabel || (f.audioBitrate ? `${f.audioBitrate}kbps` : null),
    itag: f.itag,
    mime: f.mimeType || null,
    has_audio: !!f.hasAudio,
    has_video: !!f.hasVideo,
    filesize: f.contentLength ? Number(f.contentLength) : null,
    bitrate: f.bitrate || null,
    audio_bitrate: f.audioBitrate || null,
    download_url: f.url
  };
}

function pickFormat(formats, quality) {
  const q = normalizeQuality(quality);

  if (!q) return null;

  if (q === "mp3" || q === "audio" || q === "m4a") {
    const audio = formats
      .filter(f => f.hasAudio && !f.hasVideo)
      .sort((a, b) => (b.audioBitrate || 0) - (a.audioBitrate || 0));

    if (q === "m4a") {
      return audio.find(f => String(f.container).toLowerCase() === "mp4") || audio[0] || null;
    }

    return audio[0] || null;
  }

  const progressive = formats
    .filter(f => f.hasVideo && f.hasAudio && f.qualityLabel === q)
    .sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0));

  if (progressive[0]) return progressive[0];

  const videoOnly = formats
    .filter(f => f.hasVideo && f.qualityLabel === q)
    .sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0));

  return videoOnly[0] || null;
}

export default async function handler(req, res) {
  try {
    const url = req.query.url;
    const quality = req.query.quality || "";

    if (!url) {
      return json(res, {
        status: "error",
        message: "Parameter url wajib diisi"
      }, 400);
    }

    if (!ytdl.validateURL(url)) {
      return json(res, {
        status: "error",
        message: "URL YouTube tidak valid"
      }, 400);
    }

    const info = await ytdl.getInfo(url);
    const details = info.videoDetails;
    const videoId = details.videoId;

    const formatsRaw = info.formats || [];
    const formats = formatsRaw.map(cleanFormat);

    const videoFormats = formats.filter(f => f.has_video);
    const audioFormats = formats.filter(f => f.has_audio && !f.has_video);

    if (!quality) {
      return json(res, {
        status: "success",
        source: "vercel-ytdl-core",
        video_id: videoId,
        title: details.title,
        author: details.author?.name || null,
        duration: details.lengthSeconds ? Number(details.lengthSeconds) : null,
        thumbnail: `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`,
        total_formats: formats.length,
        video_formats: videoFormats,
        audio_formats: audioFormats,
        formats
      });
    }

    const selectedRaw = pickFormat(formatsRaw, quality);

    if (!selectedRaw) {
      return json(res, {
        status: "error",
        source: "vercel-ytdl-core",
        message: `Format tidak ditemukan: ${quality}`,
        video_id: videoId,
        title: details.title,
        requested_quality: quality,
        available_qualities: [...new Set(formats.map(f => f.quality).filter(Boolean))]
      });
    }

    const selected = cleanFormat(selectedRaw);

    return json(res, {
      status: "success",
      source: "vercel-ytdl-core",
      video_id: videoId,
      title: details.title,
      author: details.author?.name || null,
      duration: details.lengthSeconds ? Number(details.lengthSeconds) : null,
      thumbnail: `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`,
      requested_quality: quality,
      selected_quality: selected.quality,
      file_size: selected.filesize,
      download_url: selected.download_url
    });
  } catch (e) {
    return json(res, {
      status: "error",
      source: "vercel-ytdl-core",
      message: String(e?.message || e)
    }, 500);
  }
}
