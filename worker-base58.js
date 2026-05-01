// Cloudflare Worker: return pre-generated Base58 video source subscription.
//
// Why pre-generated?
// Base58 encoding a large sources.json inside Worker can exceed Cloudflare CPU limits
// (Error 1102). The crawler now generates sources.base58.txt in GitHub Actions.
// This Worker only fetches and caches that text, so it is fast and stable.
//
// Usage for app subscription:
//   https://your-worker.example.com/?url=https://raw.githubusercontent.com/kzb12580/TV-source-crawler/refs/heads/main/sources.json
//   https://your-worker.example.com/?url=https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json
//
// The url=... form is kept for compatibility with apps that require a JSON config
// subscription address and expect this Worker to return Base58 encoded text.
// For the known TV-source-crawler JSON, the Worker maps sources.json ->
// pre-generated sources.base58.txt to avoid Cloudflare Error 1102.
//
// Debug / raw files:
//   /?url=...&raw=1 -> sources.compact.json
//   /?file=json     -> sources.json
//   /?file=base58   -> sources.base58.txt
//
// Optional env vars:
//   BASE58_URL    default: https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.base58.txt
//   COMPACT_URL   default: https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.compact.json
//   JSON_URL      default: https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json
//   ACCESS_TOKEN  optional token protection
//   CACHE_TTL     optional seconds, default 3600

const RAW_BASE = 'https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main'
const CDN_BASE = 'https://cdn.jsdelivr.net/gh/kzb12580/TV-source-crawler@main'
const DEFAULT_BASE58_URL = `${RAW_BASE}/sources.base58.txt`
const DEFAULT_COMPACT_URL = `${RAW_BASE}/sources.compact.json`
const DEFAULT_JSON_URL = `${RAW_BASE}/sources.json`
const DEFAULT_CACHE_TTL = 3600

export default {
  async fetch(request, env, ctx) {
    return handleRequest(request, env, ctx)
  }
}

if (typeof addEventListener === 'function') {
  addEventListener('fetch', event => {
    event.respondWith(handleRequest(event.request, {}, event))
  })
}

async function handleRequest(request, env = {}, ctx = {}) {
  try {
    if (request.method === 'OPTIONS') return corsResponse('', 204)
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return corsResponse('Method Not Allowed', 405)
    }

    const url = new URL(request.url)
    const token = url.searchParams.get('token') || request.headers.get('x-access-token')
    if (env.ACCESS_TOKEN && token !== env.ACCESS_TOKEN) {
      return corsResponse('Unauthorized', 401)
    }

    const file = (url.searchParams.get('file') || '').toLowerCase()
    const raw = ['1', 'true', 'yes'].includes((url.searchParams.get('raw') || '').toLowerCase())
    const sourceParam = url.searchParams.get('url') || ''
    const cacheTtl = Number(env.CACHE_TTL || DEFAULT_CACHE_TTL)

    const resolved = resolveUpstream({ file, raw, sourceParam, env })
    if (!resolved.ok) return corsResponse(resolved.message, resolved.status)

    let { upstreamUrl, fallbackUrl, contentType, encoded, mode } = resolved

    const cacheKey = new Request(`${url.origin}${url.pathname}?mode=${mode}&src=${encodeURIComponent(sourceParam || file || (raw ? 'raw' : 'default'))}`)
    const cached = await caches.default.match(cacheKey)
    if (cached) return withCors(cached)

    let upstream = await fetchSource(upstreamUrl, contentType, cacheTtl)
    if (!upstream.ok && fallbackUrl) {
      upstream = await fetchSource(fallbackUrl, contentType, cacheTtl)
      if (upstream.ok) upstreamUrl = fallbackUrl
    }

    if (!upstream.ok) {
      // 部分播放器把非 200 直接显示“拉取失败: 503”。这里返回 502 更明确。
      return corsResponse(`Failed to fetch source file: HTTP ${upstream.status}`, 502)
    }

    const body = await upstream.text()
    const response = corsResponse(body.trim() + '\n', 200, {
      'Content-Type': contentType,
      'Cache-Control': `public, max-age=${cacheTtl}`,
      'X-Source-URL': upstreamUrl,
      'X-Encoded': encoded,
    })

    const put = caches.default.put(cacheKey, response.clone())
    if (ctx.waitUntil) ctx.waitUntil(put)
    else await put.catch(() => {})

    return response
  } catch (error) {
    return corsResponse(`Error: ${error && error.message ? error.message : String(error)}`, 500)
  }
}

function resolveUpstream({ file, raw, sourceParam, env }) {
  let upstreamUrl = env.BASE58_URL || DEFAULT_BASE58_URL
  let fallbackUrl = `${CDN_BASE}/sources.base58.txt`
  let contentType = 'text/plain; charset=utf-8'
  let encoded = 'base58'
  let mode = 'base58'

  if (sourceParam) {
    const source = validateSourceParam(sourceParam)
    if (!source.ok) return source

    // 关键兼容：软件仍然传 ?url=...sources.json，Worker 返回对应的 Base58。
    // 不在 Worker 内实时编码，避免 1102。
    if (source.kind === 'sources-json') {
      if (raw) {
        upstreamUrl = env.COMPACT_URL || DEFAULT_COMPACT_URL
        fallbackUrl = `${CDN_BASE}/sources.compact.json`
        contentType = 'application/json; charset=utf-8'
        encoded = 'none'
        mode = 'compact-from-url'
      } else {
        upstreamUrl = env.BASE58_URL || DEFAULT_BASE58_URL
        fallbackUrl = `${CDN_BASE}/sources.base58.txt`
        mode = 'base58-from-url'
      }
      return { ok: true, upstreamUrl, fallbackUrl, contentType, encoded, mode }
    }

    if (source.kind === 'base58') {
      upstreamUrl = source.url
      fallbackUrl = source.url.replace('https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main', CDN_BASE)
      return { ok: true, upstreamUrl, fallbackUrl, contentType, encoded, mode: 'base58-url' }
    }

    return { ok: false, status: 400, message: 'Only TV-source-crawler sources.json/base58 URLs are supported' }
  }

  if (raw || file === 'compact') {
    upstreamUrl = env.COMPACT_URL || DEFAULT_COMPACT_URL
    fallbackUrl = `${CDN_BASE}/sources.compact.json`
    contentType = 'application/json; charset=utf-8'
    encoded = 'none'
    mode = 'compact'
  } else if (file === 'json') {
    upstreamUrl = env.JSON_URL || DEFAULT_JSON_URL
    fallbackUrl = `${CDN_BASE}/sources.json`
    contentType = 'application/json; charset=utf-8'
    encoded = 'none'
    mode = 'json'
  } else if (file === 'base58' || !file) {
    upstreamUrl = env.BASE58_URL || DEFAULT_BASE58_URL
    fallbackUrl = `${CDN_BASE}/sources.base58.txt`
    mode = 'base58'
  } else {
    return { ok: false, status: 400, message: 'Invalid file parameter. Use base58, compact, json, or raw=1.' }
  }

  return { ok: true, upstreamUrl, fallbackUrl, contentType, encoded, mode }
}

function validateSourceParam(input) {
  let url
  try {
    url = new URL(input)
  } catch {
    return { ok: false, status: 400, message: 'Invalid url parameter' }
  }

  if (url.protocol !== 'https:') {
    return { ok: false, status: 400, message: 'Only https source URLs are allowed' }
  }

  const isRaw = url.hostname === 'raw.githubusercontent.com'
  const isJsDelivr = url.hostname === 'cdn.jsdelivr.net'
  if (!isRaw && !isJsDelivr) {
    return { ok: false, status: 403, message: `Source host not allowed: ${url.hostname}` }
  }

  const normalizedPath = url.pathname.replace('/refs/heads/main/', '/main/')
  const isRepo = normalizedPath.includes('/kzb12580/TV-source-crawler/')
  if (!isRepo) {
    return { ok: false, status: 403, message: 'Only kzb12580/TV-source-crawler source URLs are allowed' }
  }

  if (normalizedPath.endsWith('/sources.json')) {
    return { ok: true, kind: 'sources-json', url: url.toString() }
  }
  if (normalizedPath.endsWith('/sources.base58.txt')) {
    return { ok: true, kind: 'base58', url: url.toString().replace('/refs/heads/main/', '/main/') }
  }
  return { ok: true, kind: 'other', url: url.toString() }
}

function fetchSource(upstreamUrl, contentType, cacheTtl) {
  return fetch(upstreamUrl, {
    headers: {
      'Accept': contentType,
      'User-Agent': 'TV-source-crawler-worker/2.1',
    },
    cf: { cacheTtl, cacheEverything: true },
  })
}

function corsResponse(body, status = 200, headers = {}) {
  return new Response(body, {
    status,
    headers: {
      ...headers,
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, X-Access-Token',
    },
  })
}

function withCors(response) {
  const headers = new Headers(response.headers)
  headers.set('Access-Control-Allow-Origin', '*')
  headers.set('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
  headers.set('Access-Control-Allow-Headers', 'Content-Type, X-Access-Token')
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers })
}
