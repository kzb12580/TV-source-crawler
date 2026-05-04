#!/usr/bin/env python3
"""
视频源爬虫/整理器

原则：
- 成人源与普通源同等保留，不做内容类型过滤；
- 以 manual_sources.json 作为稳定种子，再合并远程开源配置；
- 按 API 地址去重，保留更完整的名称、detail、is_adult 标记；
- 测活只剔除明显不可访问/明显不是采集接口的源。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

REQUEST_TIMEOUT = 8
MAX_WORKERS = 30
CACHE_TIME = 9200
GITHUB_SEARCH_PER_PAGE = 30
GITHUB_SEARCH_MAX_ITEMS = 180
# conservative: 只输出实测可用；balanced: 输出可用+疑似可用；loose: 只剔除明显垃圾/示例源
STRICTNESS = os.environ.get("STRICTNESS", "balanced").lower()
TEST_KEYWORDS = ("热门", "电影")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

ROOT = Path(__file__).resolve().parent
MANUAL_SOURCES = ROOT / "manual_sources.json"

# 已知远程源库。manual_sources.json 是主来源；远程源和 GitHub 公开搜索只做补充。
SOURCE_REPOS = [
    ("vodtv/api", "LunaTV-config.json"),
    ("kouzhaobo/TVyuan", "sources.json"),
    ("YYDS678/uzVideo", "video_sources_sese.json"),
    ("YYDS678/uzVideo", "video_sources_default.json"),
    ("adminlove520/Source-Collector", "config_isadult.json"),
    ("adminlove520/Source-Collector", "configplus_isadult.json"),
    ("iphoneten/CNTV", "config_av.json"),
    ("PAICNI/Videos_PLUS", "MoonTV视频源Plus版.json"),
    ("kimballic/IPTV2", "小黃AV.txt"),
    ("sooyaaabo/VOD-CMS-Widgets", "XPTV/CMS/AV.json"),
]

GITHUB_CODE_QUERIES = [
    '"api.php/provide/vod" "api_site"',
    '"api.php/provide/vod" "is_adult"',
    '"api.php/provide/vod" "AV-"',
    '"api.php/provide/vod" "采集"',
    '"/api.php/provide/vod" "name" "api"',
    '"inc/apijson" "api_site"',
]

API_URL_RE = re.compile(
    r"https?://[^\s'\"<>，,，）)]+?(?:api\.php/provide(?:/vod)?|provide/vod|inc/api[^\s'\"<>，,，）)]*\.php|inc/apijson[^\s'\"<>，,，）)]*\.php|api/json\.php|api/json)",
    re.IGNORECASE,
)

ADULT_HINTS = (
    "AV-", "🔞", "成人", "福利", "麻豆", "番号", "伦理", "情色", "黄色", "黄黄",
    "老色", "色猫", "色南", "色嗨", "白嫖", "淫", "香奶", "奶香", "杏吧", "小鸡",
    "91", "souav", "gayapi", "sex", "av", "xrbsp", "kxgav", "msnii", "gdlsp",
)


def normalize_api(api: str) -> str:
    api = (api or "").strip()
    if not api:
        return ""
    # 统一常见 http/https 重复；保留 path，因为不同 path 可能是不同接口。
    api = api.replace("http://", "https://", 1)
    api = re.sub(r"/+$", "", api)
    return api


def source_key(api: str, fallback: str, used: set[str]) -> str:
    parsed = urlparse(api)
    host = parsed.netloc.lower().replace("www.", "")
    base = re.sub(r"[^a-z0-9]+", "_", host.split(":")[0]).strip("_") or fallback or "api"
    key = base[:36]
    if key and key[0].isdigit():
        key = "s_" + key
    original = key or "api"
    i = 2
    while key in used:
        key = f"{original}_{i}"
        i += 1
    used.add(key)
    return key


def is_adult_source(item: dict[str, Any]) -> bool:
    if item.get("is_adult") is True:
        return True
    text = f"{item.get('name', '')} {item.get('api', '')} {item.get('detail', '')}".lower()
    return any(h.lower() in text for h in ADULT_HINTS)


def iter_items(data: Any):
    if isinstance(data, list):
        yield from (x for x in data if isinstance(x, dict))
        return

    if not isinstance(data, dict):
        return

    # 常见格式：api_site/sites/sources 是 dict 或 list。
    for key in ("api_site", "sites", "sources"):
        items = data.get(key)
        if isinstance(items, dict):
            for k, v in items.items():
                if isinstance(v, dict):
                    item = dict(v)
                    item.setdefault("id", k)
                    yield item
        elif isinstance(items, list):
            yield from (x for x in items if isinstance(x, dict))

    # 有些文件本身就是 {id: {api/name/detail}}
    if "api" not in data:
        for k, v in data.items():
            if isinstance(v, dict) and ("api" in v or "url" in v):
                item = dict(v)
                item.setdefault("id", k)
                yield item


def normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    api = normalize_api(str(item.get("api") or item.get("url") or ""))
    if not api.startswith("http"):
        return None
    name = str(item.get("name") or item.get("title") or item.get("id") or urlparse(api).netloc or "Unknown").strip()
    detail = str(item.get("detail") or item.get("home") or "").strip()
    normalized = {
        "name": name,
        "api": api,
        "detail": detail,
    }
    normalized["is_adult"] = is_adult_source({**item, **normalized})
    return normalized


def merge_source(pool: OrderedDict[str, dict[str, Any]], item: dict[str, Any]) -> bool:
    normalized = normalize_item(item)
    if not normalized:
        return False
    api = normalized["api"]
    old = pool.get(api)
    if not old:
        pool[api] = normalized
        return True

    # 保留更明确的成人标记、更长/更干净的名称和 detail。
    old["is_adult"] = bool(old.get("is_adult")) or bool(normalized.get("is_adult"))
    if (not old.get("detail")) and normalized.get("detail"):
        old["detail"] = normalized["detail"]
    if normalized.get("name") and (
        not old.get("name")
        or old["name"].startswith("🎬")
        or (normalized["name"].startswith(("TV-", "AV-")) and not old["name"].startswith(("TV-", "AV-")))
    ):
        old["name"] = normalized["name"]
    return False


def load_json_file(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"   ❌ 读取失败 {path.name}: {exc}")
        return None


def fetch_url_text(url: str) -> str | None:
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        if resp.status_code == 200 and resp.text.strip():
            return resp.text
        print(f"   ⚠️ {url}: HTTP {resp.status_code}")
    except Exception as exc:
        print(f"   ❌ {url}: {exc}")
    return None


def fetch_json_url(url: str) -> Any | None:
    text = fetch_url_text(url)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_sources_from_text(text: str, default_name: str = "发现源") -> list[dict[str, Any]]:
    """从 txt/js/md/html 等公开配置文本里抓采集 API URL。"""
    found: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for m in API_URL_RE.finditer(text or ""):
        api = normalize_api(m.group(0).rstrip("/\\\"'，,。;；"))
        if not api:
            continue
        # 尝试从 URL 所在行推断名称，推断不到就用域名。
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = min(len(text), m.end() + 120)
        line = text[line_start:line_end]
        name_match = re.search(r"[\"'“”]?name[\"'“”]?\s*[:=]\s*[\"'“”]([^\"'“”]{1,40})", line, re.I)
        name = name_match.group(1).strip() if name_match else default_name
        if name == default_name:
            host = urlparse(api).netloc.replace("www.", "")
            name = f"发现源-{host}"
        found[api] = {"name": name, "api": api, "detail": f"https://{urlparse(api).netloc}"}
    return list(found.values())


def github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True, timeout=5).strip()
    except Exception:
        return None


def github_api(path: str, params: dict[str, Any] | None = None) -> Any | None:
    token = github_token()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/{path.lstrip('/')}"
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        print(f"   ⚠️ GitHub {path}: HTTP {resp.status_code}")
    except Exception as exc:
        print(f"   ❌ GitHub {path}: {exc}")
    return None


def github_search_files() -> list[tuple[str, str, str]]:
    """搜索公开 GitHub 代码，返回 (repo, path, html_url)。只读公开内容。"""
    files: OrderedDict[tuple[str, str], str] = OrderedDict()
    for query in GITHUB_CODE_QUERIES:
        data = github_api("search/code", {"q": query, "per_page": GITHUB_SEARCH_PER_PAGE})
        for item in (data or {}).get("items", []):
            repo = item.get("repository", {}).get("full_name")
            path = item.get("path")
            url = item.get("html_url", "")
            if repo and path:
                files[(repo, path)] = url
                if len(files) >= GITHUB_SEARCH_MAX_ITEMS:
                    return [(r, p, u) for (r, p), u in files.items()]
        time.sleep(1.0)
    return [(r, p, u) for (r, p), u in files.items()]


def fetch_github_file(repo: str, path: str) -> str | None:
    data = github_api(f"repos/{repo}/contents/{path}")
    if not isinstance(data, dict):
        return None
    download_url = data.get("download_url")
    if download_url:
        return fetch_url_text(download_url)
    return None


def collect_sources() -> list[dict[str, Any]]:
    pool: OrderedDict[str, dict[str, Any]] = OrderedDict()

    print("\n📥 步骤1: 加载本地稳定种子...")
    local = load_json_file(MANUAL_SOURCES)
    local_count = 0
    if local:
        for item in iter_items(local):
            local_count += int(merge_source(pool, item))
    print(f"   ✅ manual_sources.json: +{local_count} 个源")

    print("\n📥 步骤2: 合并远程源库...")
    for repo, filename in SOURCE_REPOS:
        url = f"https://raw.githubusercontent.com/{repo}/main/{filename}"
        before = len(pool)
        text = fetch_url_text(url)
        if text:
            try:
                data = json.loads(text)
                for item in iter_items(data):
                    merge_source(pool, item)
            except Exception:
                for item in extract_sources_from_text(text, default_name=f"发现源-{repo}"):
                    merge_source(pool, item)
        print(f"   ✅ {repo}/{filename}: +{len(pool) - before} 个新源")
        time.sleep(0.3)

    print("\n🔎 步骤3: 搜索 GitHub 公开配置...")
    files = github_search_files()
    print(f"   🔍 命中文件: {len(files)} 个")
    github_new = 0
    for repo, path, _ in files:
        before = len(pool)
        text = fetch_github_file(repo, path)
        if text:
            try:
                data = json.loads(text)
                for item in iter_items(data):
                    merge_source(pool, item)
            except Exception:
                for item in extract_sources_from_text(text, default_name=f"发现源-{repo}"):
                    merge_source(pool, item)
        added = len(pool) - before
        github_new += added
        if added:
            print(f"   ✅ {repo}/{path}: +{added}")
        time.sleep(0.2)
    print(f"   📦 GitHub 公开配置新增: {github_new} 个源")

    return list(pool.values())


def request_api(api_url: str, params: dict[str, str]) -> requests.Response:
    sep = "&" if "?" in api_url else "?"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return requests.get(f"{api_url}{sep}{query}", timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})


def is_obvious_junk(item: dict[str, Any]) -> bool:
    text = f"{item.get('name', '')} {item.get('api', '')} {item.get('detail', '')}".lower()
    junk = ("example.com", "your-api.com", "old-proxy.com", "adult.example.com", "测试", "示例")
    return any(x in text for x in junk)


def classify_payload(text: str) -> tuple[str, str]:
    if not text:
        return "failed", "empty"
    lower = text[:2000].lower()
    if any(x in lower for x in ("404 not found", "502 bad gateway", "access denied")):
        return "failed", "error-page"
    if len(text) < 20:
        return "failed", "too-short"

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if any(isinstance(data.get(k), list) and data[k] for k in ("list", "data", "class", "videos")):
                return "available", "structured-json"
            if any(k in data for k in ("list", "data", "class", "total", "page", "code", "msg")):
                return "maybe", "json-api-shape"
        elif isinstance(data, list) and data:
            return "available", "json-list"
    except Exception:
        # XML/MacCMS 老接口经常不是 JSON。
        if any(tag in lower for tag in ("<rss", "<list", "<video", "vod_name", "vod_id", "type_name")):
            return "available", "xml-api"
        if any(tag in lower for tag in ("provide/vod", "api.php", "maccms", "采集")):
            return "maybe", "api-text"

    if len(text) >= 300 and "html" not in lower[:300]:
        return "maybe", "non-empty"
    return "failed", "unrecognized"


def test_api(item: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    if is_obvious_junk(item):
        return item, "failed", "obvious-junk"

    api = item["api"]
    # conservative: 只测1次，失败即扔；balanced/loose: 3次全面探测
    if STRICTNESS == "conservative":
        attempts = [{"ac": "list"}]
    else:
        attempts = [
            {"ac": "list"},
            {"ac": "videolist", "wd": TEST_KEYWORDS[0]},
            {"ac": "detail", "wd": TEST_KEYWORDS[1]},
        ]
    errors = []
    saw_http_200 = False
    saw_cloudflare = False
    for params in attempts:
        try:
            resp = request_api(api, params)
            saw_http_200 = saw_http_200 or resp.status_code == 200
            if resp.status_code in {403, 429, 500, 520, 521, 522, 523, 524, 525}:
                saw_cloudflare = True
            if resp.status_code == 200:
                status, reason = classify_payload(resp.text)
                if status == "available":
                    return item, "available", reason
                if status == "maybe":
                    errors.append(reason)
                    continue
            errors.append(f"HTTP {resp.status_code}/{len(resp.text)}")
        except Exception as exc:
            errors.append(type(exc).__name__)

    joined = ";".join(errors[-3:])
    if STRICTNESS == "loose" and not is_obvious_junk(item):
        if saw_http_200 or not any(e in joined for e in ("InvalidURL", "MissingSchema")):
            return item, "maybe", joined or "loose-keep"
    if STRICTNESS == "balanced":
        if saw_http_200 or saw_cloudflare or any(e in joined for e in ("ReadTimeout", "ConnectTimeout", "SSLError")):
            return item, "maybe", joined or "balanced-keep"
    return item, "failed", joined


def base58_encode_utf8(text: str) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    data = text.encode("utf-8")
    if not data:
        return ""

    zeroes = 0
    while zeroes < len(data) and data[zeroes] == 0:
        zeroes += 1

    digits = [0]
    for byte in data[zeroes:]:
        carry = byte
        for i in range(len(digits)):
            carry += digits[i] << 8
            digits[i] = carry % 58
            carry //= 58
        while carry:
            digits.append(carry % 58)
            carry //= 58

    return alphabet[0] * zeroes + "".join(alphabet[d] for d in reversed(digits))


def main() -> None:
    print("=" * 50)
    print(f"🎬 视频源爬虫 - {datetime.now().isoformat()}")
    print("=" * 50)

    all_sources = collect_sources()
    total_adult = sum(1 for s in all_sources if s.get("is_adult"))
    print(f"\n📊 去重后: {len(all_sources)} 个源，其中成人源 {total_adult} 个")

    print("\n🧪 步骤4: 测试可用性...")
    available: list[dict[str, Any]] = []
    maybe: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(test_api, s): s for s in all_sources}
        for f in as_completed(futures):
            item, status, reason = f.result()
            item = dict(item)
            item["status"] = status
            item["reason"] = reason
            if status == "available":
                available.append(item)
                print(f"   ✅ {item['name']}")
            elif status == "maybe":
                maybe.append(item)
                print(f"   🟡 {item['name']} ({reason})")
            else:
                failed.append(item)
                print(f"   ❌ {item['name']} ({reason})")

    included = available + maybe if STRICTNESS in {"balanced", "loose"} else available

    # 稳定排序：普通源在前，成人源在后；各组按名称。
    included.sort(key=lambda x: (bool(x.get("is_adult")), x.get("status", ""), x.get("name", "")))

    used_keys: set[str] = set()
    api_site = OrderedDict()
    for item in included:
        key = source_key(item["api"], item.get("name", "api"), used_keys)
        api_site[key] = {
            "name": item.get("name", "Unknown"),
            "api": item["api"],
            "detail": item.get("detail", ""),
            "is_adult": bool(item.get("is_adult")),
            "status": item.get("status", "available"),
        }
        if item.get("status") == "maybe":
            api_site[key]["reason"] = item.get("reason", "maybe")

    now = datetime.now().isoformat()

    # 拆分普通源和成人源
    normal_api_site = OrderedDict()
    adult_api_site = OrderedDict()
    for key, item in api_site.items():
        if item.get("is_adult"):
            adult_api_site[key] = item
        else:
            normal_api_site[key] = item

    def build_result(api_site_dict, total_adult, total_normal):
        return OrderedDict([
            ("cache_time", CACHE_TIME),
            ("strictness", STRICTNESS),
            ("api_site", api_site_dict),
            ("update_date", now),
            ("total_sources", len(api_site_dict)),
            ("total_available", len(available)),
            ("total_maybe", len(maybe)),
            ("total_included", len(api_site_dict)),
            ("total_adult", total_adult),
            ("total_normal", total_normal),
        ])

    def build_compact(api_site_dict):
        compact = OrderedDict()
        for key, item in api_site_dict.items():
            compact[key] = {"name": item["name"], "api": item["api"], "detail": item.get("detail", "")}
            if item.get("is_adult") is True:
                compact[key]["is_adult"] = True
        return compact

    def write_json(path, data):
        (ROOT / path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_compact(path, compact_dict):
        text = json.dumps(OrderedDict([
            ("cache_time", CACHE_TIME),
            ("api_site", compact_dict),
            ("update_date", now),
            ("total_sources", len(compact_dict)),
        ]), ensure_ascii=False, separators=(",", ":"))
        (ROOT / path).write_text(text + "\n", encoding="utf-8")

    def write_base58(path, compact_dict):
        text = json.dumps(compact_dict, ensure_ascii=False, separators=(",", ":"))
        (ROOT / path).write_text(base58_encode_utf8(text) + "\n", encoding="utf-8")

    # 普通源（主文件）
    normal_compact = build_compact(normal_api_site)
    write_json("sources.json", build_result(normal_api_site, 0, len(normal_api_site)))
    write_compact("sources.compact.json", normal_compact)
    write_base58("sources.base58.txt", normal_compact)

    # 成人源（独立文件）
    adult_compact = build_compact(adult_api_site)
    write_json("sources.adult.json", build_result(adult_api_site, len(adult_api_site), 0))
    write_compact("sources.adult.compact.json", adult_compact)
    write_base58("sources.adult.base58.txt", adult_compact)
    (ROOT / "maybe_sources.json").write_text(json.dumps(maybe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "failed_sources.json").write_text(json.dumps(failed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rate = len(included) / max(len(all_sources), 1) * 100
    adult_available = len(adult_api_site)
    normal_available = len(normal_api_site)
    stats = (
        "# 视频源统计\n\n"
        f"- 更新时间: {now}\n"
        f"- 测活模式: {STRICTNESS}\n"
        f"- 候选源数: {len(all_sources)}\n"
        f"- 实测可用: {len(available)}\n"
        f"- 疑似可用/慢源: {len(maybe)}\n"
        f"- 输出源数: {len(included)}\n"
        f"- 普通输出源: {normal_available}\n"
        f"- 成人输出源: {adult_available}\n"
        f"- 明确失效/垃圾: {len(failed)}\n"
        f"- 输出率: {rate:.1f}%\n"
    )
    (ROOT / "STATS.md").write_text(stats, encoding="utf-8")

    print("\n" + "=" * 50)
    print("✅ 完成!")
    print(f"📊 候选: {len(all_sources)}")
    print(f"✅ 实测可用: {len(available)}")
    print(f"🟡 疑似可用: {len(maybe)}")
    print(f"📦 输出: {len(included)}")
    print(f"🔞 成人输出: {adult_available}")
    print("=" * 50)


if __name__ == "__main__":
    main()
