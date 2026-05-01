// Cloudflare Worker: return pre-generated Base58 video source subscription.
//
// Why pre-generated?
// Base58 encoding a large sources.json inside Worker can exceed Cloudflare CPU limits
// (Error 1102). The crawler now generates sources.base58.txt in GitHub Actions.
// This Worker only fetches and caches that text, so it is fast and stable.
//
// Usage for app subscription:
//   https://your-worker.example.com/
//
// Debug / raw files:
//   /?raw=1      -> sources.compact.json
//   /?file=json  -> sources.json
//   /?file=base58 -> sources.base58.txt
//
// Optional env vars:
//   BASE58_URL    default: https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.base58.txt
//   COMPACT_URL   default: https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.compact.json
//   JSON_URL      default: https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json
//   ACCESS_TOKEN  optional token protection
//   CACHE_TTL     optional seconds, default 3600

const DEFAULT_BASE58_URL = 'https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.base58.txt'
const DEFAULT_COMPACT_URL = 'https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.compact.json'
const DEFAULT_JSON_URL = 'https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json'
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
    const cacheTtl = Number(env.CACHE_TTL || DEFAULT_CACHE_TTL)

    let upstreamUrl = env.BASE58_URL || DEFAULT_BASE58_URL
    let contentType = 'text/plain; charset=utf-8'
    let encoded = 'base58'

    if (raw || file === 'compact') {
      upstreamUrl = env.COMPACT_URL || DEFAULT_COMPACT_URL
      contentType = 'application/json; charset=utf-8'
      encoded = 'none'
    } else if (file === 'json') {
      upstreamUrl = env.JSON_URL || DEFAULT_JSON_URL
      contentType = 'application/json; charset=utf-8'
      encoded = 'none'
    } else if (file === 'base58' || !file) {
      upstreamUrl = env.BASE58_URL || DEFAULT_BASE58_URL
    } else {
      return corsResponse('Invalid file parameter. Use base58, compact, json, or raw=1.', 400)
    }

    const valid = validateRawGithubUrl(upstreamUrl)
    if (!valid.ok) return corsResponse(valid.message, valid.status)

    const cacheKey = new Request(`${url.origin}${url.pathname}?file=${file || (raw ? 'compact' : 'base58')}&raw=${raw ? '1' : '0'}`)
    const cached = await caches.default.match(cacheKey)
    if (cached) return withCors(cached)

    const upstream = await fetch(upstreamUrl, {
      headers: {
        'Accept': contentType,
        'User-Agent': 'TV-source-crawler-worker/2.0',
      },
      cf: { cacheTtl, cacheEverything: true },
    })

    if (!upstream.ok) {
      return corsResponse(`Failed to fetch source file: HTTP ${upstream.status}`, upstream.status)
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

function validateRawGithubUrl(input) {
  let url
  try {
    url = new URL(input)
  } catch {
    return { ok: false, status: 400, message: 'Invalid upstream URL' }
  }

  if (url.protocol !== 'https:') {
    return { ok: false, status: 400, message: 'Only https upstream URLs are allowed' }
  }

  if (url.hostname !== 'raw.githubusercontent.com') {
    return { ok: false, status: 403, message: `Upstream host not allowed: ${url.hostname}` }
  }

  if (!url.pathname.includes('/kzb12580/TV-source-crawler/')) {
    return { ok: false, status: 403, message: 'Only kzb12580/TV-source-crawler raw files are allowed' }
  }

  return { ok: true }
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
