#!/usr/bin/env python3
"""
视频源爬虫 - 从 GitHub 搜索和整合多个视频源库
"""

import json
import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

REQUEST_TIMEOUT = 15
MAX_WORKERS = 8
TEST_KEYWORD = "热门"

# 已知源库配置
SOURCE_REPOS = [
    ("vodtv/api", "LunaTV-config.json"),
    ("kouzhaobo/TVyuan", "sources.json"),
    ("dongyubin/IPTV", "sources.json"),
    ("morpheus0mp/chjhy", "api.json"),
]


def fetch_from_repos():
    """从已知源库获取"""
    all_apis = {}  # api_url -> {name, detail}
    
    for repo, filename in SOURCE_REPOS:
        url = f"https://raw.githubusercontent.com/{repo}/main/{filename}"
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                data = r.json()
                count = 0
                for key in ['api_site', 'sites', 'sources']:
                    items = data.get(key, {})
                    if isinstance(items, dict):
                        for k, v in items.items():
                            if isinstance(v, dict):
                                api = v.get('api', '')
                                name = v.get('name', k)
                                if api and api.startswith('http'):
                                    if api not in all_apis:
                                        all_apis[api] = {
                                            'name': name,
                                            'api': api,
                                            'detail': v.get('detail', '')
                                        }
                                        count += 1
                print(f"   ✅ {repo}: +{count} 个新源")
        except Exception as e:
            print(f"   ❌ {repo}: {e}")
        time.sleep(0.5)
    
    return list(all_apis.values())


def test_api(api_url, name):
    """测试单个 API"""
    test_url = f"{api_url}?ac=videolist&wd={TEST_KEYWORD}" if '?' not in api_url else f"{api_url}&ac=videolist&wd={TEST_KEYWORD}"
    
    try:
        resp = requests.get(test_url, timeout=REQUEST_TIMEOUT, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code == 200 and len(resp.text) > 100:
            try:
                data = resp.json()
                if any(k in data for k in ['list', 'data', 'result', 'videos', 'class', 'total']):
                    return name, api_url, True
            except:
                if len(resp.text) > 500:
                    return name, api_url, True
    except:
        pass
    return name, api_url, False


def main():
    print("=" * 50)
    print(f"🎬 视频源爬虫 - {datetime.now().isoformat()}")
    print("=" * 50)

    # 1. 获取源
    print("\n📥 步骤1: 从源库获取...")
    all_sources = fetch_from_repos()
    print(f"\n📊 去重后: {len(all_sources)} 个源")

    # 2. 测试可用性
    print("\n🧪 步骤2: 测试可用性...")
    available = []
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(test_api, s['api'], s['name']): s for s in all_sources}
        
        for f in as_completed(futures):
            name, api, ok = f.result()
            if ok:
                s = futures[f]
                available.append({
                    "name": name,
                    "api": api,
                    "detail": s.get('detail', ''),
                    "status": "available"
                })
                print(f"   ✅ {name}")
            time.sleep(0.2)

    print(f"\n📊 可用: {len(available)}/{len(all_sources)}")

    # 3. 保存结果
    api_site = {f"api_{i+1}": s for i, s in enumerate(available)}
    
    result = {
        "cache_time": 7200,
        "api_site": api_site,
        "update_date": datetime.now().isoformat(),
        "total_sources": len(api_site),
        "total_available": len(available)
    }

    with open('sources.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    with open('STATS.md', 'w') as f:
        rate = len(available) / max(len(all_sources), 1) * 100
        f.write(f"# 视频源统计\n\n")
        f.write(f"- 更新时间: {result['update_date']}\n")
        f.write(f"- 总源数: {result['total_sources']}\n")
        f.write(f"- 可用源数: {result['total_available']}\n")
        f.write(f"- 可用率: {rate:.1f}%\n")

    print("\n" + "=" * 50)
    print(f"✅ 完成!")
    print(f"📊 总源: {result['total_sources']}")
    print(f"✅ 可用: {result['total_available']}")
    
    if result['total_sources'] >= 90:
        print("🎉 达到目标!")
    print("=" * 50)


if __name__ == "__main__":
    main()
