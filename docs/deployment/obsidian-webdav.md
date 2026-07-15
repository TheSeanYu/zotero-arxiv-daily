# Obsidian WebDAV 自动导出与七日轮换

本文记录 Zotero-arXiv-Daily 在云服务器上直接写入 Obsidian WebDAV
存储目录的验证结果、部署约束和后续实施计划。

## 阶段边界

功能开发与服务器部署分为两个阶段：

1. **本机工作流验收**：在开发机完成并验证论文检索、重排、全文提取、结构化
   总结、邮件发送、Obsidian Markdown、静态 HTML 和七日轮换。所有自动化测试
   与至少一次本机端到端运行通过后，功能才视为可部署。
2. **服务器部署**：将已经在本机验证的同一版本移植到服务器，配置独立运行
   用户、最小目录权限、密钥、cron、`flock`、日志和备份。本阶段不同时开发新
   功能，只处理环境差异和运行保障。

服务器 WebDAV 的文件创建、双向修改和删除传播能力已经单独验证通过。这证明
部署路径可行，但不代表完整论文处理工作流已经在服务器验收。

### 本机自动化测试结果

自动化测试验证日期：2026-07-14。

- Hydra 能在私有配置不存在时使用仓库默认值，并支持可选加载
  `config/private/local.yaml`。
- 邮件、结构化总结、Markdown 和静态 HTML 已接入同一次 Executor 运行；端到端
  测试使用 mock API/SMTP 验证三个输出可以同时成功。
- Obsidian exporter 的幂等写入、日期目录、七日清理、`dry_run` 和符号链接边界
  已通过单元测试。
- HTML 每日页面、索引页和归档清理已通过单元测试。
- 完整非慢速测试结果为 `107 passed, 1 deselected`。
- tiktoken 编码表不可联网下载时会使用保守字符分块，不会阻断总结和邮件流程。

### 本机真实工作流结果

真实网络试运行日期：2026-07-14。

- Zotero 鉴权成功，读取到 24 篇语料。
- debug 模式从当天 arXiv 候选中取 10 篇完成摘要重排。
- Jina 本地 embedding 模型在 Apple MPS 上成功加载和推理。
- LLM TLDR 和结构化总结调用成功。
- debug Top-1 实测 reranker 分数为 `3.092`；正式 main 候选集的高分论文集中在
  `4.8–5.2`。因此 debug 分数不能用于生产阈值校准，当前建议生产
  `min_score: 4.8`。
- Top-1 arXiv HTML 全文成功提取，共 69,750 字符。
- 真实 Markdown 已写入日期目录，frontmatter 与全部固定章节完整。
- 真实 HTML 日报和 `index.html` 已生成，日报包含结构化总结。
- SMTP sender 与授权码已经修正，QQ `465/SSL` 登录和最小测试邮件发送成功。

### 本机生产级 Canary 结果

生产配置 canary 验证日期：2026-07-14。运行命令为
`uv run src/zotero_arxiv_daily/main.py`，总耗时约 13 分钟，进程以退出码 0
结束。

- 从 Zotero 读取 24 篇语料，从 arXiv 获取并转换 195 篇候选。
- 本地 embedding 缓存为 `24 hits / 0 misses`；缓存数据库保持 24 条记录，
  文件权限为 `0600`。
- 完成 100 篇候选的 TLDR 与机构生成，最高重排分数为 `5.211`。
- 发布规则 `Top 3 + score >= 4.8` 的并集选出 7 篇，未触发
  `max_export_num: 50` 硬上限。
- 7 篇均完成结构化总结；其中 1 篇 arXiv HTML 下载失败后按设计降级，未中断
  其余处理和最终发布。
- 同一次运行成功写入 7 个 Markdown、生成包含 7 篇论文的当日 HTML 与
  `index.html`，并成功发送生产邮件。Markdown 与 HTML 均包含 arXiv 摘要页和
  PDF 链接。
- 七日清理在本次运行中保持 `dry_run: true`，没有执行真实删除。

本次 canary 证明本机生产配置下的论文抓取、重排、缓存、LLM 总结、Markdown、
HTML 和邮件链路可以在同一次运行中完成。

### 本机真实七日清理 Canary 结果

清理 canary 验证日期：2026-07-15。测试目录为
`/private/tmp/zotero-arxiv-daily-retention-canary`，配置与生产保留策略一致：
`retention.days: 7`、`timezone: Asia/Shanghai`、`directory_format: "%Y-%m-%d"`、
`dry_run: false`。

测试预置了 `2026-07-07` 到 `2026-07-15` 共 9 个日期目录，并额外创建
`archive/` 非日期目录和根目录 `README.md`。执行真实清理后：

- `2026-07-09` 到 `2026-07-15` 保留，正好覆盖最近 7 个自然日。
- `2026-07-08` 和 `2026-07-07` 被真实删除。
- `archive/` 和 `README.md` 均保留，说明清理逻辑只处理符合日期格式的直属目录。

该测试证明七日循环保留策略可以在隔离目录中真实删除过期日期目录。服务器部署
时仍建议先用 `dry_run: true` 跑一次，确认日志中的待删除路径符合预期后再打开
真实删除。

真实试运行同时发现并修复了以下问题：

1. 全文提取原本发生在重排前，会对全部候选下载正文；现已延迟到重排后的
   高分 Top-N，避免处理当天全部 195 篇候选。
2. arXiv Python 客户端返回 HTTP 链接；现统一规范化为 HTTPS。
3. macOS 上 `fork` 后调用网络 API 会卡住；全文硬超时子进程已改用跨平台
   `spawn`。
4. 全文回退顺序调整为 HTML、LaTeX 源码、PDF，并为每一步设置硬超时。
5. SMTP 发送前会校验邮箱格式；端口 465 在 `auto` 模式下直接使用 SSL。

### Zotero Embedding 缓存

Zotero corpus 摘要 embedding 支持持久化 SQLite 缓存，local 和 API reranker
共用同一套缓存机制。每天变化的候选论文不写缓存，避免它们挤占稳定语料空间。

缓存键包含 reranker 后端、模型、API base URL 或 local encode 参数，以及摘要
内容的 SHA-256。修改模型、服务商、编码参数或摘要后会自动产生新条目，不会
错误复用旧向量。重复摘要只计算一次，但在相似度矩阵中仍保持原有列数和权重。

```yaml
reranker:
  cache:
    enabled: true
    path: "/absolute/path/to/embedding-cache.sqlite3"
    max_entries: 2000
    max_age_days: 180
```

- 每次命中会更新 `last_access`。
- 初始化和写入后会删除超过 `max_age_days` 未使用的条目。
- 超过 `max_entries` 时按 LRU 删除最久未使用的条目。
- 缓存数据库权限自动设置为 `0600`。
- API key 不写入 namespace 或数据库。
- 本机真实探针第一次为 `0 hits / 24 misses`，第二次为
  `24 hits / 0 misses`，证明 Zotero embedding 被完整复用。

邮件中的每篇论文现在同时提供 arXiv 摘要页和 PDF 链接。

### 深度发布选择规则

Markdown 和 HTML 的论文集合为以下两部分的并集：

1. arXiv 重排结果中的 Top `min_export_num`，保证每天至少处理 N 篇。
2. 所有 `score >= min_score` 的 arXiv 论文，避免高分论文因固定 Top-N 被遗漏。

选择发生在完整重排结果上，不受邮件的 `executor.max_paper_num` 展示上限影响。
`max_export_num` 默认为 `null`；如配置数字，它是成本保护用的最终硬上限，可能
截断部分达到阈值的论文，因此仅在明确需要限制最坏成本时启用。

```yaml
exporter:
  min_score: 4.8
  min_export_num: 3
  max_export_num: null
```

## 已验证环境

验证日期：2026-07-13。

服务器上的 WebDAV 由 Nginx 提供，Nginx worker 使用 `www:www` 运行。
实际使用的 Obsidian vault 物理路径为：

```text
/www/wwwroot/dav.xiangyupeng.online/SeanYu
```

vault 及现有文件由 `www:www` 持有，目录权限为 `0750`，文件权限通常为
`0640`。普通 `admin` 用户不属于 `www` 组，因而不能直接列出或写入 vault；
这是预期的权限隔离。

以下链路已经实际验证通过：

1. 以服务器 `www` 用户在 vault 内创建测试目录和 Markdown 文件。
2. Remote Save 能将服务器直接创建的文件同步到本地 Obsidian。
3. 本地修改能够通过 Remote Save 同步回服务器。
4. 服务器删除测试文件后，Remote Save 能将删除同步到本地，且测试文件没有在
   后续同步中被重新上传。

因此，当前环境可以由流水线直接写服务器文件系统，不需要使用 WebDAV HTTP
PUT。该结论只适用于当前 Nginx 文件系统型 WebDAV 部署。若迁移到 Nextcloud、
带数据库索引的服务，或者启用 Remote Save 远端加密，需要重新评估。

## 输出边界

自动生成内容只能写入：

```text
/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily
```

用户阅读后可将有价值的论文笔记移动到 vault 中独立的归档目录。流水线不得
扫描、覆盖或删除 `arxiv-daily/` 之外的任何内容。

建议配置：

```yaml
exporter:
  obsidian:
    vault_path: "/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily"
```

## 七日循环窗口

`arxiv-daily/` 始终只保留最近七个自然日生成的笔记。建议使用日期目录：

```text
arxiv-daily/
├── 2026-07-07/
├── 2026-07-08/
├── 2026-07-09/
├── 2026-07-10/
├── 2026-07-11/
├── 2026-07-12/
└── 2026-07-13/
```

这在逻辑上是容量为七天的循环缓冲区，但不建议循环复用固定的 `周一` 到
`周日` 路径。固定路径每周会删除并重新创建同名内容，离线客户端可能将上周
的旧文件重新上传，也不利于审计和恢复。

如需显示星期，可采用 `2026-07-13-周一`，但日期必须保留。

### 保留规则

以 `Asia/Shanghai` 的自然日为准：

1. 只识别符合配置日期格式的 `arxiv-daily/` 直属目录。
2. 保留今天及之前六个自然日，共七天。
3. 不处理不符合日期格式的目录或文件。
4. 当天目录仅创建新论文；目标文件存在时跳过，永不覆盖。
5. 只有当天导出整体成功后才执行过期清理。
6. 删除前必须验证解析后的路径仍位于 `arxiv-daily/` 内。
7. 部署初期使用 `dry_run`，仅记录拟删除路径。

### 写入规则

- 使用 `arxiv_id` 作为幂等键和文件名的一部分。
- 在同一文件系统中完整生成临时文件，再原子移动到目标路径。
- 已存在的目标文件始终跳过，保护用户添加的备注。
- cron 使用 `flock`，防止流水线实例重叠。
- 流水线不删除、移动或覆盖自动目录之外的内容。
- 服务器保持 NTP 时间同步，减少基于 mtime 判断产生的冲突。

## 删除同步验证

创建、双向修改和服务器删除向当前客户端传播均已验证。正式启用自动清理前，
还需要覆盖离线客户端和归档边界：

1. 在 `arxiv-daily-test/2026-07-01/` 创建测试笔记并同步到所有客户端。
2. 从服务器删除测试文件，再执行 Remote Save。（已通过）
3. 确认客户端文件被删除，并且再次同步时不会重新上传。（已通过）
4. 用一个离线客户端重复测试，检查旧文件是否会复活。
5. 将一篇测试笔记移动到归档目录，确认自动目录清理不影响归档副本。
6. 检查 Remote Save 的删除策略、冲突策略和同步日志。

在以上测试通过前，保持 `retention.dry_run: true`。

### 手动删除测试文件

`admin` 无权直接访问 vault，但可以通过 `sudo` 以目录所有者 `www` 的身份
操作。先确认目标路径：

```bash
sudo -u www ls -la \
  "/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily-test"
```

删除单个测试文件：

```bash
sudo -u www rm -- \
  "/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily-test/server-test.md"
```

确认服务器端已经删除：

```bash
sudo -u www test ! -e \
  "/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily-test/server-test.md" \
  && echo "deleted"
```

只在测试目录已经为空时删除目录：

```bash
sudo -u www rmdir -- \
  "/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily-test"
```

如果测试目录内还有模拟日期子目录，先检查边界和内容：

```bash
sudo -u www find \
  "/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily-test" \
  -maxdepth 3 -print
```

确认输出全部属于该测试目录后，才可清理整个测试树：

```bash
sudo -u www rm -r -- \
  "/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily-test"
```

不得对 `SeanYu`、vault 根目录或路径变量为空的目标执行递归删除。

## 权限模型

正式流水线不应以 `www` 用户运行，因为它还会持有 Zotero、LLM 和 SMTP 等
密钥。建议创建独立系统用户 `arxiv`，通过 ACL 只授权自动输出目录。

目标状态：

- `www` 能通过 WebDAV 读写和删除 `arxiv-daily/`。
- `arxiv` 能创建当日目录、写入新文件并清理过期目录。
- `arxiv` 不能读取或修改 vault 中其他目录。
- 不把整个 vault 修改为 `0777`，也不把 `arxiv` 加入权限过宽的通用组。

权限配置完成后分别验证：

```bash
sudo -u arxiv test -w \
  /www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily

sudo -u www test -w \
  /www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily
```

## 建议配置

所有 API key、SMTP 凭据、输出开关和本机/服务器路径统一保存在
`config/private/local.yaml`。该文件已被 `.gitignore` 忽略；仓库只提交不含真实
凭据的 `config/private/local.example.yaml`。首次配置：

```bash
cp config/private/local.example.yaml config/private/local.yaml
chmod 600 config/private/local.yaml
git check-ignore config/private/local.yaml
```

最后一条命令必须输出 `config/private/local.yaml`，否则不要填入真实密钥。
Hydra 的默认 `.hydra/config.yaml` 快照已关闭，避免运行时把合成后的真实凭据复制
到输出目录。应用日志也不得打印完整配置对象或 API key。

```yaml
exporter:
  enabled: true
  min_score: 4.8
  min_export_num: 3
  max_export_num: null

  obsidian:
    enabled: true
    vault_path: "/www/wwwroot/dav.xiangyupeng.online/SeanYu/arxiv-daily"

    retention:
      enabled: true
      days: 7
      timezone: Asia/Shanghai
      directory_format: "%Y-%m-%d"
      dry_run: true

reranker:
  local:
    hf_endpoint_fallbacks:
      - "https://hf-mirror.com"

  cache:
    enabled: true
    path: "/var/lib/zotero-arxiv-daily/embedding-cache.sqlite3"
    max_entries: 2000
    max_age_days: 180
```

首期不上传 PDF，先验证 Markdown 生成、同步、归档和清理闭环。

## 实施计划

### Phase 0：确认部署路径可行

- [x] 确认实际 vault 路径为 `SeanYu`。
- [x] 验证服务器文件系统直写和双向修改。
- [x] 验证服务器删除能够传播到当前客户端且不会立即回传。
- [ ] 验证离线客户端不会让过期文件复活。
- [ ] 创建 `arxiv` 用户并完成最小权限配置。
- [ ] 确认备份和恢复方案。

其中运行用户、ACL 和备份配置留到服务器部署阶段完成。

### Phase 1：本机实现与验收

- [x] 增加结构化总结器，要求 LLM 输出可校验 JSON。
- [x] 增加 Obsidian exporter 和 Markdown 模板。
- [x] 实现日期目录、幂等创建和七日保留策略。
- [x] 增加静态 HTML 日报与索引页生成。
- [x] 为路径边界、日期计算、幂等和清理添加单元测试。
- [x] 完整非慢速测试通过（`107 passed, 1 deselected`）。
- [x] 深度发布采用 Top-N 与分数阈值并集，且与邮件上限解耦。
- [x] 增加 Zotero corpus embedding 缓存、LRU 上限与按年龄清理。
- [x] 验证真实 local embedding 缓存二次运行全部命中。
- [x] 邮件增加 arXiv 摘要页链接。
- [x] 在本机用真实 Zotero、arXiv 和 LLM 生成 Markdown 与 HTML。
- [x] QQ SMTP 登录成功并发送真实测试邮件。
- [x] 在本机验证邮件、Markdown 和 HTML 同次运行均成功生成。
- [x] 在本机以临时目录验证真实七日清理。

### Phase 2：服务器部署与运行保障

- [ ] 在服务器安装并锁定与本机一致的 Python/uv 环境。
- [ ] 创建 `arxiv` 用户并配置最小 ACL。
- [ ] 配置密钥、cron 和工作目录。
- [ ] 使用 `flock` 防止定时任务重叠。
- [ ] 增加结构化日志、失败通知和删除审计日志。
- [ ] 启用 vault 快照或其他每日备份。
- [ ] 先以 `dry_run` 运行，再启用实际七日清理。
- [ ] 将静态 HTML 接入 Nginx/WordPress 子路径。
- [ ] 再评估 PDF 附件。
