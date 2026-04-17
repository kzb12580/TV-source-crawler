# TV-Source-Crawler 🎬

[![更新视频源](https://github.com/kzb12580/TV-source-crawler/actions/workflows/update-sources.yml/badge.svg)](https://github.com/kzb12580/TV-source-crawler/actions/workflows/update-sources.yml)
[![可用源](https://img.shields.io/badge/可用源-动态-brightgreen)](https://github.com/kzb12580/TV-source-crawler/blob/main/sources.json)

自动爬取、检测和维护视频源列表。

## ✨ 功能

- 🕷️ **自动爬取**: 从多个来源自动爬取视频源
- 🧪 **可用性检测**: 自动检测源是否可用
- 🗑️ **失效清理**: 自动移除失效源
- 📊 **统计报告**: 生成可用源统计报告
- ⏰ **定时更新**: 每周自动更新一次

## 📊 目标

- 总源数: **110+**
- 可用源数: **90+**
- 可用率: **80%+**

## 📦 使用

### 获取最新源列表

```bash
# JSON 格式
curl -sL https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json

# 或直接在项目中引用
# config.json 或 api.json
```

### 在 LunaTV/MoonTV 中使用

将 `sources.json` 中的 `api_site` 内容复制到你的项目配置中。

## 🔄 更新机制

- **自动更新**: 每周一北京时间上午 10 点
- **手动触发**: 在 Actions 页面手动运行

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| `sources.json` | 最新的视频源列表 |
| `STATS.md` | 统计报告 |
| `crawler.py` | 爬虫脚本 |

## 🔗 数据来源

- GitHub 开源项目
- Gitee 开源项目
- 已知稳定源

## 📋 源格式

```json
{
  "api_1": {
    "name": "源名称",
    "api": "https://example.com/api.php/provide/vod",
    "detail": "https://example.com",
    "status": "available"
  }
}
```

## 🛠️ 本地运行

```bash
# 克隆仓库
git clone https://github.com/kzb12580/TV-source-crawler.git
cd TV-source-crawler

# 安装依赖
pip install requests aiohttp

# 运行爬虫
python crawler.py
```

## 📜 License

MIT
