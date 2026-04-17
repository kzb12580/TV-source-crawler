#!/usr/bin/env python3
"""
视频源爬虫 - 自动检测和维护视频源列表
功能：
1. 从多个来源爬取视频源
2. 检测源是否可用
3. 移除失效源
4. 保证至少110+源，90+可用
"""

import json
import requests
import time
import re
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

# 配置
MIN_TOTAL_SOURCES = 110  # 最少总源数
MIN_AVAILABLE_SOURCES = 90  # 最少可用源数
REQUEST_TIMEOUT = 15
MAX_WORKERS = 10
TEST_KEYWORD = "热门"  # 测试搜索关键词

# 视频源获取地址
RETREVE_URLS = [
    # GitHub 源
    "https://raw.githubusercontent.com/ngo5/IPTV/main/sources.json",
    "https://raw.githubusercontent.com/dongyubin/IPTV/main/sources.json",
    "https://raw.githubusercontent.com/gaotianliuyun/gao/main/sources.json",
    "https://raw.githubusercontent.com/qist/tvbox/main/sources.json",
    "https://raw.githubusercontent.com/Zhou-Li-Bin/Tvbox-QingNing/main/sources.json",
    "https://raw.githubusercontent.com/tongxunlu/tvbox-tvb-gd/main/tvbox.json",
    "https://raw.githubusercontent.com/katelya77/KatelyaTV/main/api.json",
    "https://raw.githubusercontent.com/Newtxin/TVBoxSource/main/cangku.json",
    "https://raw.githubusercontent.com/vcloudc/tvbox/main/tw/api.json",
    "https://raw.githubusercontent.com/Archmage83/tvapk/main/sources.json",
    "https://raw.githubusercontent.com/kjxhb/Box/main/m.json",
    "https://raw.githubusercontent.com/lyghgx/tv/main/README.md",
    "https://raw.githubusercontent.com/jazzforlove/VShare/main/README.md",
    # Gitee 源
    "https://gitee.com/xuxiamu/xm/raw/master/xiamu.json",
    "https://gitee.com/guot54/ygbh666/raw/master/ygbh666.json",
    "https://gitee.com/ChenAnRong/tvbox-config/raw/master/tvbox.json",
    "https://gitee.com/stbang/live-streaming-source/raw/master/dxaz.json",
    "https://gitee.com/xlsn0w/tvbox-source-address/raw/master/sources.json",
    "https://gitee.com/hepingwang/tvbox/raw/master/sources.json",
    "https://gitee.com/xuxiamu/xm/raw/master/tvbox.json",
]

# 已知稳定源列表（备用）
KNOWN_STABLE_SOURCES = [
    {"name": "非凡影视", "api": "http://ffzy5.tv/api.php/provide/vod", "detail": "http://ffzy5.tv"},
    {"name": "量子资源", "api": "https://cj.lziapi.com/api.php/provide/vod", "detail": "https://cj.lziapi.com"},
    {"name": "非凡资源", "api": "http://ffzy1.tv/api.php/provide/vod", "detail": "http://ffzy1.tv"},
    {"name": "快播资源", "api": "https://www.kuaibozy.com/api.php/provide/vod", "detail": "https://www.kuaibozy.com"},
    {"name": "天空资源", "api": "https://api.tkys3.com/api.php/provide/vod", "detail": "https://api.tkys3.com"},
    {"name": "最大资源", "api": "https://api.zuidapi.com/api.php/provide/vod", "detail": "https://api.zuidapi.com"},
    {"name": "无尽资源", "api": "https://api.wujinapi.me/api.php/provide/vod", "detail": ""},
    {"name": "暴风资源", "api": "https://bfzyapi.com/api.php/provide/vod", "detail": ""},
    {"name": "红牛资源", "api": "https://www.hongniuzy2.com/api.php/provide/vod", "detail": "https://www.hongniuzy2.com"},
    {"name": "OK资源", "api": "https://www.okzyw.com/api.php/provide/vod", "detail": "https://www.okzyw.com"},
    {"name": "天空M3U8", "api": "https://api.tkys3.com/api.php/provide/vod/at/json", "detail": ""},
    {"name": "闪电资源", "api": "https://sdzyapi.com/api.php/provide/vod", "detail": "https://sdzyapi.com"},
    {"name": "39影视", "api": "https://www.39kan.com/api.php/provide/vod", "detail": "https://www.39kan.com"},
    {"name": "天空云", "api": "https://api.tiankongapi.com/api.php/provide/vod", "detail": ""},
    {"name": "天空云M3U8", "api": "https://api.tiankongapi.com/api.php/provide/vod/at/json/m3u8", "detail": ""},
]


def load_existing_sources() -> Dict[str, Any]:
    """加载现有源"""
    try:
        with open('sources.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"cache_time": 7200, "api_site": {}}


def save_sources(sources: Dict[str, Any]):
    """保存源到文件"""
    sources['update_date'] = datetime.now().isoformat()
    sources['total_sources'] = len(sources.get('api_site', {}))
    sources['total_available'] = sum(
        1 for v in sources.get('api_site', {}).values()
        if v.get('status') == 'available'
    )

    with open('sources.json', 'w', encoding='utf-8') as f:
        json.dump(sources, f, ensure_ascii=False, indent=2)

    # 写入统计文件
    with open('STATS.md', 'w', encoding='utf-8') as f:
        f.write(f"# 视频源统计\n\n")
        f.write(f"- 更新时间: {sources['update_date']}\n")
        f.write(f"- 总源数: {sources['total_sources']}\n")
        f.write(f"- 可用源数: {sources['total_available']}\n")
        f.write(f"- 可用率: {sources['total_available']/max(sources['total_sources'],1)*100:.1f}%\n")


def test_single_api(api_url: str, name: str) -> Tuple[str, str, bool]:
    """测试单个API是否可用"""
    # 确保有协议
    if not api_url.startswith(('http://', 'https://')):
        api_url = 'https://' + api_url

    # 构建测试URL
    test_url = api_url
    if '?' in api_url:
        test_url = f"{api_url}&ac=videolist&wd={TEST_KEYWORD}"
    else:
        test_url = f"{api_url}?ac=videolist&wd={TEST_KEYWORD}"

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
        }
        resp = requests.get(test_url, timeout=REQUEST_TIMEOUT, headers=headers)

        if resp.status_code == 200:
            content = resp.text
            if len(content) < 50:
                return name, api_url, False

            # 尝试解析JSON
            try:
                data = resp.json()
                # 检查是否包含视频列表
                if any(key in data for key in ['list', 'data', 'result', 'videos', 'class']):
                    return name, api_url, True
            except:
                # 如果不是JSON，检查XML格式
                if '<list>' in content or '<class>' in content or '<vod>' in content:
                    return name, api_url, True
                # 检查是否有内容
                if len(content) > 500:
                    return name, api_url, True

    except requests.exceptions.Timeout:
        print(f"  ⏱️ 超时: {name}")
    except requests.exceptions.ConnectionError:
        print(f"  🔌 连接失败: {name}")
    except Exception as e:
        print(f"  ❌ 错误 {name}: {str(e)[:50]}")

    return name, api_url, False


def test_sources_batch(sources: List[Dict]) -> List[Dict]:
    """批量测试源"""
    available = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(test_single_api, s['api'], s['name']): s
            for s in sources
        }

        for future in as_completed(futures):
            name, api_url, is_available = future.result()
            source = futures[future]
            if is_available:
                available.append({
                    "name": name,
                    "api": api_url,
                    "detail": source.get('detail', ''),
                    "status": "available"
                })
                print(f"  ✅ 可用: {name}")
            time.sleep(0.5)  # 避免请求过快

    return available


def fetch_from_url(url: str) -> List[Dict]:
    """从URL获取源列表"""
    sources = []
    try:
        print(f"📥 获取: {url}")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, timeout=15, headers=headers)

        if resp.status_code != 200:
            return sources

        content_type = resp.headers.get('Content-Type', '')

        # 处理JSON
        if 'json' in content_type or url.endswith('.json'):
            try:
                data = resp.json()
                # 查找源列表
                for key in ['api_site', 'sites', 'list', 'api_list', 'sources', 'configs', 'interfaces']:
                    if key in data and isinstance(data[key], (dict, list)):
                        items = data[key].values() if isinstance(data[key], dict) else data[key]
                        for item in items:
                            if isinstance(item, dict):
                                api = item.get('api', item.get('url', ''))
                                if api and ('api.php' in api or 'vod' in api.lower()):
                                    sources.append({
                                        "name": item.get('name', item.get('title', 'Unknown')),
                                        "api": api,
                                        "detail": item.get('detail', item.get('url', ''))
                                    })
                        break
            except:
                pass

        # 处理Markdown/文本
        elif 'text' in content_type or url.endswith('.md'):
            for line in resp.text.split('\n'):
                # 匹配 name,api 格式
                if 'api.php' in line or 'provide/vod' in line:
                    parts = [p.strip() for p in line.split(',') if p.strip()]
                    if len(parts) >= 2:
                        name = parts[0].replace('name:', '').strip().strip('"\'')
                        api = parts[1].replace('api:', '').strip().strip('"\'')
                        if api:
                            sources.append({"name": name, "api": api, "detail": ""})

    except Exception as e:
        print(f"  ❌ 获取失败 {url}: {str(e)[:50]}")

    return sources


def main():
    print("=" * 50)
    print("🎬 视频源爬虫启动")
    print(f"📅 时间: {datetime.now().isoformat()}")
    print("=" * 50)

    # 加载现有源
    existing = load_existing_sources()
    all_sources = []

    # 1. 从远程获取源
    print("\n📥 步骤1: 从远程获取源...")
    for url in RETREVE_URLS:
        sources = fetch_from_url(url)
        all_sources.extend(sources)
        time.sleep(1)

    # 2. 添加已知稳定源
    print("\n📦 步骤2: 添加已知稳定源...")
    all_sources.extend(KNOWN_STABLE_SOURCES)

    # 3. 去重
    print("\n🔄 步骤3: 去重...")
    seen_apis = set()
    unique_sources = []
    for s in all_sources:
        api_key = s['api'].lower().replace('https://', '').replace('http://', '').rstrip('/')
        if api_key not in seen_apis:
            seen_apis.add(api_key)
            unique_sources.append(s)

    print(f"  去重后: {len(unique_sources)} 个源")

    # 4. 批量测试
    print("\n🧪 步骤4: 测试源可用性...")
    available_sources = test_sources_batch(unique_sources)

    print(f"\n✅ 可用源: {len(available_sources)}/{len(unique_sources)}")

    # 5. 确保最小数量
    if len(available_sources) < MIN_AVAILABLE_SOURCES:
        print(f"\n⚠️ 可用源不足 {MIN_AVAILABLE_SOURCES}，从已知稳定源补充...")
        # 重新测试已知稳定源
        for s in KNOWN_STABLE_SOURCES:
            if s['api'].lower().replace('https://', '').replace('http://', '').rstrip('/') not in seen_apis:
                name, api, is_ok = test_single_api(s['api'], s['name'])
                if is_ok:
                    available_sources.append({
                        "name": name,
                        "api": api,
                        "detail": s.get('detail', ''),
                        "status": "available"
                    })
                time.sleep(1)

    # 6. 构建最终结果
    print("\n📝 步骤5: 构建结果...")
    api_site = {}
    for i, s in enumerate(available_sources, 1):
        key = f"api_{i}"
        api_site[key] = {
            "name": s['name'],
            "api": s['api'],
            "detail": s.get('detail', ''),
            "status": "available"
        }

    result = {
        "cache_time": 7200,
        "api_site": api_site,
        "update_date": datetime.now().isoformat(),
        "total_sources": len(api_site),
        "total_available": len(available_sources)
    }

    # 7. 保存
    save_sources(result)

    print("\n" + "=" * 50)
    print("✅ 更新完成!")
    print(f"📊 总源数: {result['total_sources']}")
    print(f"✅ 可用源: {result['total_available']}")
    print(f"📈 可用率: {result['total_available']/max(result['total_sources'],1)*100:.1f}%")
    print("=" * 50)

    # 检查是否满足要求
    if result['total_sources'] < MIN_TOTAL_SOURCES:
        print(f"⚠️ 警告: 总源数 {result['total_sources']} 低于要求 {MIN_TOTAL_SOURCES}")
    if result['total_available'] < MIN_AVAILABLE_SOURCES:
        print(f"⚠️ 警告: 可用源数 {result['total_available']} 低于要求 {MIN_AVAILABLE_SOURCES}")

    return result


if __name__ == "__main__":
    main()
