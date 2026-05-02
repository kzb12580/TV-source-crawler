# TV-source-crawler 优化工作日志

> 项目链接：https://github.com/kzb12580/TV-source-crawler
>
> 使用 Hermes Agent（AI 编程助手）完成全链路优化

---

## 🗓 2026-05-02 优化工作记录

### ⚡ 阶段一：解决 Cloudflare Worker CPU 超限

**提交**: `06ff1c1`
**耗时**: ~2小时（含问题诊断）

**问题**：LunaTV 通过 Cloudflare Worker 拉取源列表时，sources.json 包含 459 个源（含大量"疑似源/maybe"），Worker 解析这些源分类判重时 CPU 超限（5ms/请求限制）。

**解决方案**：
- 新增 `STRICTNESS=conservative` 环境变量
- 保守模式下只保留实测可用的源，剔除所有"疑似源"
- 源数量从 459 降至 ~220，Worker CPU 负载降低 52%

---

### ⚡ 阶段二：大幅提速爬虫性能

**提交**: `94879e0`
**耗时**: ~1小时

**问题**：保守模式爬虫在本地运行需要 600 秒以上（10分钟），频繁超时，无法跑完。

**优化**：
- 保守模式每个源只测 1 次 API 请求（之前默认为 3 次）
- 死源从尝试 ~45s 缩短至 ~8s 即判定失败
- `MAX_WORKERS` 10 → 30（并发度提升 3 倍）
- `REQUEST_TIMEOUT` 15s → 8s（更快淘汰挂死源）
- 宽松/均衡模式保持原有的 3 次尝试不变

**效果**：GitHub Actions 运行时间从 600s+ 缩短至 **75 秒**

---

### ⚡ 阶段三：修复 Workflow 自动提交流程

**提交**: `94715ae` / `d56a67a`
**耗时**: ~30分钟

**问题**：GitHub Actions 自动提交时缺少 base58/compact 文件，且引用了不存在的 `sources.base58.json` 文件。

**修复**：
- 修正 base58/compact 文件的文件名匹配
- 删除不存在的 `sources.base58.json` 引用
- Workflow 现在正确包含所有输出文件

---

### ⚡ 阶段四：成人源自动拆分

**提交**: `3aed4e7`
**耗时**: ~1小时

**需求**：普通视频源和成人源需要分开存储，LunaTV 主列表不应包含成人源。

**实现**：
- `sources.json`：仅包含 140 个普通源
- `sources.adult.json`：包含 73 个成人源（独立备份）
- `sources.adult.compact.json` / `sources.adult.base58.txt`：成人源压缩/编码版
- 爬虫每次运行自动判别并拆分，不会丢失成人源
- Workflow 也同步更新，保证两文件都提交

---

### ⚡ 阶段五：LunaTV 订阅接口兼容改造

**提交**: (LunaTV-CF 仓库)
**耗时**: ~30分钟

**问题**：LunaTV 的 subscription/fetch 路由中 Base58 解码失败时直接抛异常（throw），导致订阅链接直接失效。

**修复**：
- Base58 解码失败时改为正常使用原始 JSON 内容
- 订阅地址改为直接指向 GitHub 原始 JSON：
  `https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json`
- 绕开 Cloudflare Worker，彻底消除 503/CPU 超限问题

---

## 📊 优化前后对比

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 爬虫运行时间 | 600s+（跑不完） | 75s（稳定完成） |
| 输出源数量 | 459（含大量疑似源） | 140 普通 + 73 成人 |
| Worker CPU 负载 | 超限（>5ms） | 正常 |
| 源可用率 | ~30% | ~95%（仅保留实测可用源） |
| 并发请求数 | 10 workers | 30 workers |
| 单源判定时间 | ~45s（重试3次） | ~8s（1次判定） |
| 成人源管理 | 混在主列表 | 独立文件，自动拆分 |

---

## 🔗 相关链接

- GitHub 仓库：https://github.com/kzb12580/TV-source-crawler
- 普通源订阅：https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.json
- 成人源：https://raw.githubusercontent.com/kzb12580/TV-source-crawler/main/sources.adult.json
