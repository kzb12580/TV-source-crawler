// Cloudflare Worker: TV-source-crawler sources.json -> compact JSON -> Base58 text
//
// Usage:
//   /                         Encode DEFAULT_SOURCE_URL as Base58
//   /?url=https://...          Encode an allowed JSON URL as Base58
//   /?raw=1                    Return compact JSON without Base58, useful for debug
//   /?token=YOUR_TOKEN         Optional protection if ACCESS_TOKEN is configured
//
// Recommended env vars in Cloudflare Worker:
//   SOURCE_URL     https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json
//   ACCESS_TOKEN   optional, set this if you do not want public open access
//   CACHE_TTL      optional seconds, default 3600

const DEFAULT_SOURCE_URL = 'https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json'
const DEFAULT_CACHE_TTL = 3600
const BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

// 防止变成开放代理。默认只允许 GitHub raw 和你自己的仓库域名。
const ALLOWED_HOSTS = new Set([
  'raw.githubusercontent.com',
  'github.com',
])

export default {
  async fetch(request, env, ctx) {
    return handleRequest(request, env, ctx)
  }
}

// 兼容旧版 Workers addEventListener 部署方式。使用 module worker 时这段不会影响。
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

    const requestUrl = new URL(request.url)
    const sourceUrl = requestUrl.searchParams.get('url') || env.SOURCE_URL || DEFAULT_SOURCE_URL
    const raw = ['1', 'true', 'yes'].includes((requestUrl.searchParams.get('raw') || '').toLowerCase())
    const pretty = ['1', 'true', 'yes'].includes((requestUrl.searchParams.get('pretty') || '').toLowerCase())
    const cacheTtl = Number(env.CACHE_TTL || DEFAULT_CACHE_TTL)

    if (env.ACCESS_TOKEN) {
      const token = requestUrl.searchParams.get('token') || request.headers.get('x-access-token')
      if (token !== env.ACCESS_TOKEN) return corsResponse('Unauthorized', 401)
    }

    const target = parseAndValidateSourceUrl(sourceUrl)
    if (!target.ok) return corsResponse(target.message, target.status)

    const cacheKey = new Request(`${requestUrl.origin}${requestUrl.pathname}?url=${encodeURIComponent(target.url)}&raw=${raw ? '1' : '0'}&pretty=${pretty ? '1' : '0'}`)
    const cache = caches.default
    const cached = await cache.match(cacheKey)
    if (cached) return withCors(cached)

    const upstream = await fetch(target.url, {
      headers: {
        'Accept': 'application/json,text/plain,*/*',
        'User-Agent': 'TV-source-crawler-worker/1.0',
      },
      cf: { cacheTtl, cacheEverything: true },
    })

    if (!upstream.ok) {
      return corsResponse(`Failed to fetch source: HTTP ${upstream.status}`, upstream.status)
    }

    const text = await upstream.text()
    const sourceData = parseJson(text)
    if (!sourceData.ok) return corsResponse(`Invalid JSON source: ${sourceData.message}`, 422)

    const compact = compactVideoSources(sourceData.value)
    const jsonString = JSON.stringify(compact, null, pretty ? 2 : 0)

    const body = raw ? jsonString : base58EncodeUtf8(jsonString)
    const response = corsResponse(body, 200, {
      'Content-Type': raw ? 'application/json; charset=utf-8' : 'text/plain; charset=utf-8',
      'Cache-Control': `public, max-age=${cacheTtl}`,
      'X-Source-URL': target.url,
      'X-Source-Count': String(compact.total_sources || Object.keys(compact.api_site || {}).length),
      'X-Encoded': raw ? 'none' : 'base58',
    })

    if (request.method === 'GET') {
      const promise = cache.put(cacheKey, response.clone())
      if (ctx.waitUntil) ctx.waitUntil(promise)
      else await promise.catch(() => {})
    }

    return response
  } catch (error) {
    return corsResponse(`Error: ${error && error.message ? error.message : String(error)}`, 500)
  }
}

function parseAndValidateSourceUrl(input) {
  let url
  try {
    url = new URL(input)
  } catch {
    return { ok: false, status: 400, message: 'Invalid url parameter' }
  }

  if (url.protocol !== 'https:') {
    return { ok: false, status: 400, message: 'Only https URLs are allowed' }
  }

  if (!ALLOWED_HOSTS.has(url.hostname)) {
    return { ok: false, status: 403, message: `Host not allowed: ${url.hostname}` }
  }

  // 默认用途是转换视频源 JSON，不允许随便拉 GitHub 以外的大文件。
  if (!/\.(json|txt)$/i.test(url.pathname) && !url.pathname.includes('/TV-source-crawler/')) {
    return { ok: false, status: 400, message: 'Only json/txt source files are allowed' }
  }

  return { ok: true, url: url.toString() }
}

function parseJson(text) {
  try {
    return { ok: true, value: JSON.parse(text.replace(/^\uFEFF/, '')) }
  } catch (error) {
    return { ok: false, message: error.message }
  }
}

function compactVideoSources(data) {
  const apiSite = data && data.api_site && typeof data.api_site === 'object' ? data.api_site : {}
  const compactApiSite = {}

  for (const [key, item] of Object.entries(apiSite)) {
    if (!item || typeof item !== 'object') continue
    const api = String(item.api || item.url || '').trim()
    if (!api) continue

    // 保留 MoonTV/LunaTV 常用字段；去掉 reason/status 等运行统计噪音，减小编码体积。
    compactApiSite[key] = {
      name: String(item.name || key),
      api,
      detail: String(item.detail || ''),
    }

    // 成人标记对你有用，保留；false 不写，减少体积。
    if (item.is_adult === true) compactApiSite[key].is_adult = true
  }

  return {
    cache_time: Number(data.cache_time || 9200),
    api_site: compactApiSite,
    update_date: data.update_date || new Date().toISOString(),
    total_sources: Object.keys(compactApiSite).length,
  }
}

// 大文本安全 Base58 编码：不用 BigInt('0x...')，避免超大 JSON 时内存/CPU 突刺。
// 算法：逐字节进位转换，常见 bitcoin/base-x 实现思路。
function base58EncodeUtf8(str) {
  const bytes = new TextEncoder().encode(str)
  if (bytes.length === 0) return ''

  let zeroes = 0
  while (zeroes < bytes.length && bytes[zeroes] === 0) zeroes++

  const digits = [0]
  for (let i = zeroes; i < bytes.length; i++) {
    let carry = bytes[i]
    for (let j = 0; j < digits.length; j++) {
      carry += digits[j] << 8
      digits[j] = carry % 58
      carry = (carry / 58) | 0
    }
    while (carry > 0) {
      digits.push(carry % 58)
      carry = (carry / 58) | 0
    }
  }

  let result = BASE58_ALPHABET[0].repeat(zeroes)
  for (let i = digits.length - 1; i >= 0; i--) {
    result += BASE58_ALPHABET[digits[i]]
  }
  return result
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
