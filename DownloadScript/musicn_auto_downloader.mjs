#!/usr/bin/env node
import got from "got";
import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, stat } from "node:fs/promises";
import { basename, join } from "node:path";
import { pipeline } from "node:stream/promises";

const STOP_WORDS = new Set([
  "a",
  "an",
  "and",
  "are",
  "as",
  "at",
  "be",
  "by",
  "for",
  "from",
  "i",
  "if",
  "in",
  "is",
  "it",
  "its",
  "me",
  "my",
  "of",
  "on",
  "or",
  "the",
  "to",
  "with",
  "you",
  "your",
]);

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith("--")) continue;
    const key = item.slice(2);
    const value = argv[index + 1] && !argv[index + 1].startsWith("--") ? argv[++index] : "1";
    args[key] = value;
  }
  return args;
}

function normalize(value = "") {
  return value
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function tokens(value = "") {
  return new Set(normalize(value).split(" ").filter((token) => token.length > 1 && !STOP_WORDS.has(token)));
}

function intersectCount(left, right) {
  let count = 0;
  for (const item of left) {
    if (right.has(item)) count += 1;
  }
  return count;
}

function matchesTrack(candidate, artist, title) {
  const searchTokens = tokens(`${candidate.title} ${candidate.artist} ${candidate.filename}`);
  const titleTokens = tokens(title);
  const artistTokens = tokens(artist);
  if (!titleTokens.size) return false;

  const titleHits = intersectCount(titleTokens, searchTokens);
  const neededTitleHits = titleTokens.size === 1 ? 1 : titleTokens.size <= 4 ? 2 : 3;
  if (titleHits < neededTitleHits) return false;

  const artistHits = intersectCount(artistTokens, searchTokens);
  if (artistTokens.size && artistHits === 0) {
    return titleHits >= Math.max(3, neededTitleHits);
  }
  return true;
}

function scoreCandidate(candidate, artist, title) {
  const searchTokens = tokens(`${candidate.title} ${candidate.artist} ${candidate.filename}`);
  const titleHits = intersectCount(tokens(title), searchTokens);
  const artistHits = intersectCount(tokens(artist), searchTokens);
  const urlBonus = candidate.url ? 5 : 0;
  return titleHits * 10 + artistHits * 4 + urlBonus;
}

function cleanFilename(value) {
  return value
    .replace(/[\\/:*?"<>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

async function exists(path) {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

async function uniquePath(directory, filename) {
  const safeName = cleanFilename(filename) || "unknown.mp3";
  const dotIndex = safeName.lastIndexOf(".");
  const stem = dotIndex > 0 ? safeName.slice(0, dotIndex) : safeName;
  const ext = dotIndex > 0 ? safeName.slice(dotIndex) : ".mp3";
  let candidate = join(directory, `${stem}${ext}`);
  for (let index = 1; await exists(candidate); index += 1) {
    candidate = join(directory, `${stem} (${index})${ext}`);
  }
  return candidate;
}

function artistsText(items = []) {
  return items.map((item) => item.name || item.artistName || item.singerName).filter(Boolean).join(", ");
}

function candidateFilename(artist, title, ext = "mp3") {
  const safeArtist = artist || "Unknown Artist";
  const safeTitle = title || "Unknown Song";
  return `${safeArtist} - ${safeTitle}.${ext}`;
}

async function searchMigu(query, size) {
  const url = `https://pd.musicapp.migu.cn/MIGUM3.0/v1.0/content/search_all.do?text=${encodeURIComponent(query)}&pageNo=1&pageSize=${size}&searchSwitch={song:1}`;
  const data = await got(url, { timeout: { request: 30000 } }).json();
  const rows = data?.songResultData?.result || [];
  const candidates = [];

  for (const row of rows) {
    try {
      const resourceUrl = `https://c.musicapp.migu.cn/MIGUM2.0/v1.0/content/resourceinfo.do?copyrightId=${row.copyrightId}&resourceType=0`;
      const resourceData = await got(resourceUrl, { timeout: { request: 30000 } }).json();
      const audioUrl = resourceData?.resource?.[0]?.audioUrl;
      if (!audioUrl) continue;

      const parsedUrl = new URL(audioUrl);
      const downloadUrl = decodeURIComponent(`https://freetyst.nf.migu.cn${parsedUrl.pathname}`).replace(
        "彩铃/6_mp3-128kbps",
        "标清高清/MP3_320_16_Stero",
      );
      const extMatch = audioUrl.match(/\.(mp3|flac|m4a|wav)(?:$|\?)/i);
      const ext = extMatch ? extMatch[1].toLowerCase() : "mp3";
      const title = row.name || row.songName || "Unknown Song";
      const artist = artistsText(row.singers || row.artists);

      candidates.push({
        service: "migu",
        title,
        artist,
        filename: candidateFilename(artist, title, ext),
        url: downloadUrl,
      });
    } catch {
      continue;
    }
  }

  return candidates;
}

async function searchWangyi(query, size) {
  const headers = {
    "User-Agent": "Mozilla/5.0",
    Referer: "https://music.163.com/",
  };
  const searchUrl = `https://music.163.com/api/search/get/web?s=${encodeURIComponent(query)}&type=1&limit=${size}&offset=0`;
  const data = await got(searchUrl, { headers, timeout: { request: 30000 } }).json();
  const rows = data?.result?.songs || [];
  const candidates = [];

  for (const row of rows) {
    try {
      const playUrl = `https://music.163.com/api/song/enhance/player/url/v1?id=${row.id}&ids=[${row.id}]&level=standard&encodeType=mp3`;
      const playData = await got(playUrl, { headers, timeout: { request: 30000 } }).json();
      const item = playData?.data?.[0];
      if (!item?.url) continue;

      const title = row.name || "Unknown Song";
      const artist = artistsText(row.artists);
      candidates.push({
        service: "wangyi",
        title,
        artist,
        filename: candidateFilename(artist, title, "mp3"),
        url: item.url,
      });
    } catch {
      continue;
    }
  }

  return candidates;
}

async function searchKuwo(query, size) {
  const searchUrl = `https://search.kuwo.cn/r.s?client=kt&all=${encodeURIComponent(query)}&pn=0&rn=${size}&vipver=1&ft=music&encoding=utf8&rformat=json&mobi=1`;
  const data = await got(searchUrl, { timeout: { request: 30000 } }).json();
  const rows = data?.abslist || data?.musiclist || [];
  const candidates = [];

  for (const row of rows) {
    try {
      const id = row.DC_TARGETID || row.id || row.MUSICRID?.replace(/^MUSIC_/, "");
      if (!id) continue;

      const playUrl = `https://www.kuwo.cn/api/v1/www/music/playUrl?mid=${id}&type=1`;
      const playData = await got(playUrl, {
        headers: {
          "User-Agent": "Mozilla/5.0",
          Referer: "https://www.kuwo.cn/",
        },
        timeout: { request: 30000 },
      }).json();
      const downloadUrl = playData?.data?.url;
      if (!downloadUrl) continue;

      const title = row.NAME || row.name || "Unknown Song";
      const artist = (row.ARTIST || row.artist || "Unknown Artist").replaceAll("&", ",");
      const extMatch = downloadUrl.match(/\.(mp3|flac|m4a|wav)(?:$|\?)/i);
      const ext = extMatch ? extMatch[1].toLowerCase() : "mp3";

      candidates.push({
        service: "kuwo",
        title,
        artist,
        filename: candidateFilename(artist, title, ext),
        url: downloadUrl,
      });
    } catch {
      continue;
    }
  }

  return candidates;
}

async function searchKugou(query, size) {
  const searchUrl = `http://msearchcdn.kugou.com/api/v3/search/song?pagesize=${size}&keyword=${encodeURIComponent(query)}&page=1`;
  const data = await got(searchUrl, { timeout: { request: 30000 } }).json();
  const rows = data?.data?.info || [];
  const candidates = [];

  for (const row of rows) {
    try {
      if (!row.hash) continue;
      const key = createHash("md5").update(`${row.hash}kgcloudv2`).digest("hex");
      const playUrl = `http://trackercdn.kugou.com/i/v2/?key=${key}&hash=${row.hash}&br=hq&appid=1005&pid=2&cmd=25&behavior=play`;
      const playData = await got(playUrl, { timeout: { request: 30000 } }).json();
      const downloadUrl = playData?.url?.[0];
      if (!downloadUrl) continue;

      const filename = row.filename || "";
      const parts = filename.includes(" - ") ? filename.split(" - ") : [];
      const artist = parts[0] || row.singername || "Unknown Artist";
      const title = parts.slice(1).join(" - ") || row.songname || "Unknown Song";
      const extMatch = downloadUrl.match(/\.(mp3|flac|m4a|wav)(?:$|\?)/i);
      const ext = extMatch ? extMatch[1].toLowerCase() : "mp3";

      candidates.push({
        service: "kugou",
        title,
        artist,
        filename: candidateFilename(artist, title, ext),
        url: downloadUrl,
      });
    } catch {
      continue;
    }
  }

  return candidates;
}

async function searchCandidates(service, query, size) {
  if (service === "migu") return searchMigu(query, size);
  if (service === "wangyi") return searchWangyi(query, size);
  if (service === "kuwo") return searchKuwo(query, size);
  if (service === "kugou") return searchKugou(query, size);
  throw new Error(`Unsupported MusicnService: ${service}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const artist = args.artist || "";
  const title = args.title || "";
  const query = args.query || `${artist} ${title}`.trim();
  const service = args.service || "migu";
  const size = Number(args.size || "10");
  const incomingDir = args["incoming-dir"];

  if (!query || !incomingDir) {
    throw new Error("Missing required --query or --incoming-dir.");
  }

  await mkdir(incomingDir, { recursive: true });
  const candidates = await searchCandidates(service, query, size);
  const viable = candidates
    .filter((candidate) => candidate.url && matchesTrack(candidate, artist, title))
    .sort((left, right) => scoreCandidate(right, artist, title) - scoreCandidate(left, artist, title));

  if (!viable.length) {
    throw new Error(`No matching musicn result for: ${query}`);
  }

  const selected = viable[0];
  const target = await uniquePath(incomingDir, selected.filename);
  await pipeline(got.stream(selected.url, { timeout: { request: 60000 } }), createWriteStream(target));

  process.stdout.write(
    `${JSON.stringify({
      ok: true,
      path: target,
      filename: basename(target),
      service: selected.service,
      title: selected.title,
      artist: selected.artist,
    })}\n`,
  );
}

main().catch((error) => {
  process.stdout.write(`${JSON.stringify({ ok: false, error: error.message || String(error) })}\n`);
  process.exit(1);
});
