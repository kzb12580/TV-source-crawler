#!/usr/bin/env python3
"""
视频源爬虫 - 自动检测和维护视频源列表
基于 TVyuan 项目
"""

import json
import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

REQUEST_TIMEOUT = 20
MAX_WORKERS = 5
TEST_KEYWORD = "热门"

BASE_SOURCE_URL = "https://raw.githubusercontent.com/kouzhaobo/TVyuan/main/sources.json"


def load_base_sources():
    resp = requests.get(BASE_SOURCE_URL, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return {"api_site": {}}


def test_api(api_url, name):
    if not api_url.startswith(('http://', 'https://')):
        api_url = 'https://' + api_url
    
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

    print("\n📥 加载基础源...")
    base = load_base_sources()
    sources = [{"name": c.get('name', k), "api": c.get('api', ''), "detail": c.get('detail', '')} 
               for k, c in base.get('api_site', {}).items() if c.get('api')]
    print(f"   加载 {len(sources)} 个源")

    print("\n🧪 测试可用性...")
    available = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(test_api, s['api'], s['name']): s for s in sources}
        for f in as_completed(futures):
            name, api, ok = f.result()
            if ok:
                s = futures[f]
                available.append({"name": name, "api": api, "detail": s.get('detail', ''), "status": "available"})
                print(f"   ✅ {name}")
            time.sleep(0.3)

    print(f"\n📊 可用: {len(available)}/{len(sources)}")

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
        f.write(f"# 视频源统计\n\n- 更新: {result['update_date']}\n- 总源: {result['total_sources']}\n- 可用: {result['total_available']}\n")

    print(f"\n✅ 完成: {result['total_sources']} 个可用源")


if __name__ == "__main__":
    main()
