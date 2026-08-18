# 软著代码资产分配系统

**核心原则**：通过文件台账和隔离工作区实现硬约束，而非依赖 Prompt 软约束。

## 适用范围与价值边界

本项目只面向**已经存在的真实软件项目**：项目应当具有真实代码、明确的软件功能和
可追溯的开发来源。本工具用于分析既有 Git 仓库，协助完成代码归属核验、软著边界划分、
鉴别材料整理和提交前审计。

本项目**不支持、也不鼓励为了申请软件著作权而临时生成代码**，包括通过 AI 或模板批量
编造、复制、扩写、拼接代码，以及为了满足代码行数或材料页数而添加无实际软件功能的内容。
代码不足、文档不足或证据无法核实时，系统应当明确失败并要求补充真实项目材料，而不是制造
看似合规的申请内容。

本工具生成的是基于既有真实代码和经人工核实说明书的申请辅助材料，不生成可用于冒充真实
研发成果的软件代码，也不能替代申请人对软件真实性、权属和申报内容承担的责任。

## 功能特性

- ✅ **仓库扫描**：读取 Git 跟踪文件，计算哈希、行数、语言
- ✅ **文件分配**：基于 YAML 配置将文件唯一分配给各软著
- ✅ **唯一约束**：数据库层面禁止同一文件分配给多个软著
- ✅ **重合检测**：三级检测（路径重复 + 哈希重复 + 代码片段相似）
- ✅ **隔离工作区**：为每个软著生成独立的代码视图
- ✅ **材料闭环生成**：以 Markdown 为源稿，直接生成 PDF，再按最终版式复核
- ✅ **严格材料审计**：核验60页、逐页行数、字号、A4、行号、重复内容和主要功能字数
- ✅ **已提交材料冻结**：用目录摘要阻止已提交材料被重新生成或覆盖
- ✅ **最终审计**：提交前交叉检查代码分配、工作区和最终 PDF
- ✅ **权属核验**：基于锁定 Commit 的真实 git blame 统计团队贡献比例，低于阈值的文件默认排除
- ✅ **已用代码排除**：已提交过的软著代码可登记标记，分配时默认排除并保留证据
- ✅ **歧义检测**：同一文件命中多个软著规则时明确失败，绝不按 YAML 顺序隐式选择
- ✅ **判定证据**：每个文件输出资格与分配决策的完整证据链（机器可读 JSON）
- ✅ **运行清单**：run-manifest 记录产生当前分配所依赖的 Commit、配置与过滤条件
- ✅ **审计事件日志**：append-only 记录 scan/allocate/generate/audit 等关键操作，`events` 命令可回放
- ✅ **数据库迁移**：schema_version + 有序 migration，旧库可安全升级
- ✅ **仓库级时间过滤**：since 截止日期可按仓库单独设置，互不泄漏（全局值兜底）

## 鉴别材料硬性规则

通用硬性规则统一保存在
[`copyright/config/material-requirements.yaml`](copyright/config/material-requirements.yaml)。
已提交材料的路径和摘要属于机器相关状态，单独保存在被 Git 忽略的
`copyright/config/material-protected-assets.local.yaml`：

- 程序鉴别材料与文档鉴别材料都必须是 A4、恰好60页；内容超过60页时取连续前30页和连续后30页。
- 程序鉴别材料每页不少于50个非空有效代码行，只能使用已分配的真实仓库源代码；文档鉴别材料每页不少于30个非空正文行。
- 正文禁止行号，页脚页码允许保留；空行不计数，不得使用空白页、循环模板、重复段落或通用占位内容补页。
- 程序字号按8.15磅生成、渲染后不得低于8磅；文档字号按10.15磅生成、渲染后不得低于10磅。该字号用于在不增加页数的前提下提高可读性。
- 《软件功能和特点》中的《软件的主要功能》必须为500至1300字。
- 《软件说明书》的“补充操作提示”及其后续内容必须分段、分级编号，实质内容不得重复。
- 文档内容必须与具体系统、代码模块和真实操作流程一致，能够说明输入、处理、输出、异常和验收依据。内容不足时生成失败，不自动编造或填充。
- 配置为冻结状态的材料会在生成前、写入前和最终审计时检查目录摘要，禁止重新生成或覆盖。

需要冻结已提交材料时，先复制示例并填写真实路径与摘要：

```bash
cp copyright/config/material-protected-assets.yaml.example \
  copyright/config/material-protected-assets.local.yaml
```

本地冻结配置不进入 Git；仓库克隆和 CI 使用通用规则时不依赖其他工作区。

先验证规则文件和冻结基线：

```bash
python copyright_cli.py requirements
```

## 快速开始

### 1. 安装依赖

```bash
cd /path/to/copyright-asset-allocator
pip install -r requirements.txt
```

PDF 由 WeasyPrint 直接渲染。如果当前系统缺少 WeasyPrint 所需的本机字体或图形库，
需要先按操作系统安装对应运行依赖，再重新执行上述命令。

### 2. 初始化系统

```bash
python copyright_cli.py init
```

这会创建：
- `copyright/inventory/assets.db` - SQLite 数据库
- `copyright/config/copyrights.yaml` - 示例配置文件
- 必要的目录结构

### 3. 扫描仓库

```bash
python copyright_cli.py scan /path/to/your/repo
```

扫描会锁定一个 Git Commit，之后所有分配、工作区和审计都基于该 Commit。
扫描结果保存在：
- `copyright/inventory/repository-files.csv`
- `copyright/inventory/scan-result.json`

### 4. 配置软著

编辑 `copyright/config/copyrights.yaml`：

```yaml
copyrights:
  - id: collector
    name: 实时数据采集与传输系统
    version: V1.0
    # description 会直接作为《软件的主要功能》，必须填写真实的500至1300字说明。
    description: ""
    include_paths:
      - services/collector/**
      - libs/protocol-receiver/**
    exclude_paths:
      - services/parser/**
      - shared/**
      - vendor/**

  - id: parser
    name: 数据解析与标准化处理系统
    version: V1.0
    description: ""
    include_paths:
      - services/parser/**
      - libs/protocol-parser/**
    exclude_paths:
      - services/collector/**
      - shared/**
      - vendor/**
```

**规则重叠检查**：不同软著的匹配集合必须互斥。如果同一文件同时匹配两个及以上软著，
`allocate` 直接失败退出（exit code 1），并生成 `copyright/allocations/ambiguous-files.json`
列出全部冲突文件；系统绝不会按 YAML 条目顺序隐式选择。请通过调整 `include_paths`
或使用 `exclude_paths` 显式排除来解决冲突。

### 5. （可选）权属核验

基于锁定 Commit 的真实 `git blame` 记录统计每个文件的团队贡献比例：

```bash
# 登记团队成员（也可用 ownership load-config team.yaml 批量加载）
python copyright_cli.py ownership add-member --name 张三 --email zhangsan@example.com
python copyright_cli.py ownership list-members

# 运行权属分析（可加 --since YYYY-MM-DD 只统计该日期后的代码）
python copyright_cli.py ownership analyze
```

分析结果写入数据库 `file_team_stats`。团队贡献比例低于 0.5 的文件在分配时默认排除，
其判定状态为 `skipped_non_team`，证据保留在判定文件中。

### 6. （可选）标记已使用代码

如果部分代码已经用于其他软著的已提交材料，先登记，避免本次重复使用：

```bash
# 方式一：从过往提交文档中抽取匹配代码行
python copyright_cli.py mark-used mark copyright/manuals/old-system.md \
  --copyright-name 旧系统V1.0

# 方式二：从证据 CSV 导入精确的已用路径清单
python copyright_cli.py mark-used import used-manifest.csv --copyright-name 旧系统V1.0

# 查看当前已用标记
python copyright_cli.py mark-used report
```

被标记文件在分配时默认排除，判定状态为 `skipped_used`。

### 7. （可选）时间过滤

只允许某个日期之后提交的代码参与分配：

```bash
python copyright_cli.py set-since set 2025-01-01
python copyright_cli.py set-since show
python copyright_cli.py set-since clear
```

操作多个仓库时，可以把截止日期限定在单个仓库，避免 A 项目的时间过滤
影响 B 项目：

```bash
python copyright_cli.py set-since set 2025-01-01 --repo-path /path/to/repo-a
python copyright_cli.py set-since show --repo-path /path/to/repo-a
python copyright_cli.py set-since clear --repo-path /path/to/repo-a
```

规则：仓库级设置优先于全局设置；清除仓库级设置后回落到全局值；
`ownership analyze --since` 也按当前扫描的仓库作用域保存。
团队成员名单（`team_members`）是全局的：它描述的是人员名册而非项目状态，
资格判定仍然逐 Commit 由 git blame 证据决定（见“核心设计”）。

### 8. 分配文件

```bash
python copyright_cli.py allocate
```

分配结果保存在：
- `copyright/allocations/<copyright-id>-files.txt` / `.json`
- `copyright/allocations/allocation-summary.json`
- `copyright/allocations/allocation-decisions.json` —— 每个文件的资格与分配判定证据
- `copyright/inventory/run-manifest.json` —— 本次分配依赖的 Commit、配置哈希与过滤条件

分配语义：
- **0 个规则匹配** → 记为 UNALLOCATED，不分配
- **恰好 1 个规则匹配** → 正常分配
- **多个规则匹配** → AMBIGUOUS：命令失败退出，输出 `ambiguous-files.json`
- **已用 / 权属不达标文件** → 默认跳过（`skipped_used` / `skipped_non_team`），证据仍被记录

### 9. （替代方案）自动拆分

不手写 `copyrights.yaml` 时，可以按目录/模块把合格代码自动拆分为多个软著：

```bash
python copyright_cli.py auto-split --min-lines 3000 --max-lines 8000 --count 4
```

约束语义是严格的，不会静默降级：
- `--count N`：如果合格代码不足以组成 N 个都满足最低行数的软著，结果为
  `status: infeasible`，命令失败并说明原因，不会偷偷减少数量后报告成功。
- `--max-lines M`：每个拆分结果应满足 `min_lines <= 行数 <= max_lines`；
  单一不可拆模块超过上限时报告 `constraint_violation: oversized_atomic_module`。
- 仅当 `status: ok` 时才写出 `copyright/config/copyrights-auto.yaml`；
  完整报告始终写入 `copyright/inventory/auto-split-report.json`。

### 10. 验证分配

```bash
python copyright_cli.py validate
```

检查：
- 是否有文件分配给多个软著
- 每个软著是否有足够的代码（默认 3000 行）
- 是否有未分配的源文件

### 11. 构建工作区

为每个软著构建隔离工作区：

```bash
python copyright_cli.py workspace collector
python copyright_cli.py workspace parser
```

工作区内容从**锁定的 Commit** 读取（`git show`），而不是当前工作区树，避免
“扫描时 HEAD=A、生成时 HEAD=B”的漂移；每个文件写入前重新校验 SHA-256，拒绝
`../`、绝对路径和符号链接逃逸，并与 run-manifest 中的 Commit 做一致性检查。

工作区结构：
```
copyright/workspaces/collector/
├── SOURCE_MANIFEST.json
├── PROJECT_CONTEXT.md
└── source/
    ├── services/collector/
    └── libs/protocol-receiver/
```

### 12. 检测重合

```bash
python copyright_cli.py detect
```

三级检测：
1. **路径重复**：同一文件路径分配给多个软著
2. **哈希重复**：不同路径但内容完全相同
3. **代码相似**：函数级别的代码相似度

相似代码的发现结果带有人工复核所需的定位信息（`duplicate-report.json`）：
`type`、`file_a`/`file_b`、`function_a`/`function_b`、相似度以及
1-based 的 `lines_a`/`lines_b` 行号区间，而不只是“相似对数量”。

### 13. 准备说明书并生成材料

每个软著必须准备一份系统专用 Markdown 说明书。推荐保存为
`copyright/manuals/<copyright-id>.md`，也可以通过 `--manual` 显式指定。说明书写作约束见
[`copyright/manuals/README.md`](copyright/manuals/README.md)。

```bash
python copyright_cli.py generate collector --manual copyright/manuals/collector.md
python copyright_cli.py generate parser --manual copyright/manuals/parser.md
```

每套材料严格执行 `Markdown → PDF → 复核`，生成：

- `源代码文档.md` 与 `源代码文档.pdf`
- `软件说明书.md` 与 `软件说明书.pdf`
- `<软件名称>-源代码清单.json`

生成器会把超过60页的有效内容裁成前30页与后30页；少于60页、逐页行数不足、出现正文行号、字号过小或渲染后页数变化时直接失败。

也可以独立审计一套已经存在的材料：

```bash
python copyright_cli.py material-audit /path/to/单套软著材料目录 \
  --output copyright/outputs/material-audit.json
```

### 14. 最终审计

```bash
python copyright_cli.py audit
```

检查：
- 代码路径重复 = 0
- 完整文件重复 = 0
- 软件名称一致性
- 版本号一致性
- Commit 一致性（扫描、run-manifest、工作区与审计必须一致，不一致直接失败）
- 工作区完整性
- 两份最终 PDF 均为 A4、恰好60页
- 程序每页不少于50行，说明书每页不少于30行
- 正文无行号，字号达到规则下限
- 《软件的主要功能》字数与“补充操作提示”结构、去重检查通过
- 已提交冻结目录摘要未变化

## 目录结构

```
copyright-asset-allocator/
├── copyright_cli.py              # CLI 入口（兼容入口，业务逻辑在 scripts/）
├── pyproject.toml                # 项目元数据与 pytest 配置
├── requirements.txt              # Python 依赖（安装用权威清单）
├── README.md                     # 本文档
├── docs/
│   └── architecture-and-invariants.md # 架构、数据流与不变量清单（I1–I10）
├── .github/workflows/tests.yml   # CI：core 与 materials 测试，Python 3.11/3.12
│
├── copyright/
│   ├── config/
│   │   ├── copyrights.yaml       # 软著配置
│   │   ├── copyrights-auto.yaml  # auto-split 生成的配置（仅 status=ok 时写出）
│   │   ├── material-requirements.yaml # 通用材料硬性规则
│   │   ├── material-protected-assets.yaml.example # 冻结配置示例
│   │   └── material-protected-assets.local.yaml # 本机冻结清单，不进入Git
│   │
│   ├── inventory/
│   │   ├── assets.db             # SQLite 数据库（schema_version + migrations）
│   │   ├── repository-files.csv  # 文件清单
│   │   ├── scan-result.json      # 扫描结果
│   │   ├── run-manifest.json     # 本次分配依赖的 Commit/配置/过滤条件
│   │   ├── auto-split-report.json
│   │   ├── validation-report.json
│   │   └── duplicate-report.json
│   │
│   ├── allocations/
│   │   ├── <copyright-id>-files.txt / .json
│   │   ├── allocation-summary.json
│   │   ├── allocation-decisions.json  # 每文件判定证据（资格+分配）
│   │   └── ambiguous-files.json       # 多规则匹配冲突清单（存在即分配失败）
│   │
│   ├── workspaces/
│   │   └── <copyright-id>/
│   │       ├── SOURCE_MANIFEST.json
│   │       ├── PROJECT_CONTEXT.md
│   │       └── source/
│   ├── manuals/
│   │   ├── README.md             # 系统专用说明书写作约束
│   │   └── <copyright-id>.md     # 人工核实的系统说明书
│   │
│   ├── outputs/
│   │   ├── <copyright-id>/
│   │   │   ├── 源代码文档.md / .pdf
│   │   │   ├── 软件说明书.md / .pdf
│   │   │   └── XX系统-源代码清单.json
│   │   └── final-audit.json
│   │
│   └── scripts/
│       ├── database.py           # 数据库操作（UNIQUE(commit_id,file_id) 硬约束、migrations、审计事件）
│       ├── scan_repository.py    # 仓库扫描与 Commit 锁定
│       ├── analyze_ownership.py  # git blame 团队权属分析
│       ├── mark_used_code.py     # 已用代码登记与排除
│       ├── allocate_files.py     # 文件分配（含歧义检测与判定证据）
│       ├── auto_split.py         # 自动拆分（min/max/count 严格约束）
│       ├── validate_allocations.py
│       ├── build_workspace.py    # 工作区构建（从锁定 Commit 取内容）
│       ├── detect_duplicates.py  # 重合检测
│       ├── run_manifest.py       # 运行清单与 Commit 一致性校验
│       ├── generate_markdown_pdf.py # Markdown→PDF主生成流程
│       ├── generate_docx.py      # DOCX兼容辅助与通用分页函数
│       ├── material_requirements.py # 规则加载与冻结保护
│       ├── validate_materials.py # PDF/DOCX/Markdown材料审计
│       └── final_audit.py        # 最终审计（引用 run-manifest）
```

## 核心设计

### 数据库唯一约束

```sql
CREATE TABLE allocations (
    commit_id INTEGER,
    file_id INTEGER,
    copyright_id INTEGER,
    UNIQUE(commit_id, file_id)  -- 同一 commit 下，同一文件只能分配一次
)
```

### 三级重合检测

1. **路径重复**：`assert len(all_paths) == len(set(all_paths))`
2. **哈希重复**：`SHA-256(normalized_content)` 比较
3. **代码相似**：函数级 token 指纹比较

### 隔离工作区

Agent 只能访问 `copyright/workspaces/<copyright-id>/source/` 目录，无法越界读取其他软著的代码。
工作区写入路径全部经过边界校验（拒绝 `../`、绝对路径与符号链接逃逸）。

### 判定证据（Decision Evidence）

每个文件都会产生机器可读的判定记录（`copyright/allocations/allocation-decisions.json`），
用于回答“这个文件为什么合格、为什么属于这个软著”：

```json
{
  "path": "services/parser/main.py",
  "commit": "abcdef",
  "sha256": "...",
  "eligibility": {
    "used_before": false,
    "team_ratio": 0.91,
    "team_threshold": 0.5,
    "eligible": true
  },
  "allocation": {
    "matched_rules": ["parser"],
    "excluded_rules": [],
    "decision": "parser",
    "status": "allocated"
  }
}
```

冲突文件的 `decision` 为 `null`、`status` 为 `ambiguous`；被排除文件保留
`skipped_used` / `skipped_non_team` 状态及对应证据。

### 运行清单与 Commit 一致性

每次 `allocate` 写出 `copyright/inventory/run-manifest.json`，记录工具版本、仓库、
锁定 Commit、配置与 requirements 的 SHA-256、时间过滤和拆分参数。工作区构建与最终审计
都会校验 Commit 一致性（扫描 / manifest / 工作区 / 审计），任何不一致直接失败。

完整的数据流、业务不变量（I1–I10）与代码责任位置见
[`docs/architecture-and-invariants.md`](docs/architecture-and-invariants.md)。

### 数据库 Schema 版本化

`assets.db` 内置 `schema_migrations` 表：每次启动时按版本号顺序补齐缺失的
migration（单事务执行，失败整体回滚）。已发布的 migration 永不改写，新变更
只能追加新版本号。当前版本：

| 版本 | 内容 |
| --- | --- |
| 1 | 基线 schema（repositories/commits/files/allocations/…，含 `UNIQUE(commit_id, file_id)` 硬约束） |
| 2 | `audit_events` 审计事件表（INSERT-only） |
| 3 | `repository_settings` 仓库级设置（`UNIQUE(repository_id, key)`） |

### 审计事件日志

每个关键操作（scan、allocate、workspace、detect、generate、audit、mark-used、
ownership analyze、auto-split、材料审计等）都会向 `audit_events` 追加一条记录：
时间、仓库、Commit、操作类型、配置 SHA-256、状态与细节。分配被歧义拒绝时记录
`rejected_ambiguous`。事件只增不改不删（模块不提供 update/delete 入口）：

```bash
python copyright_cli.py events            # 查看全部事件（按时间正序）
python copyright_cli.py events --limit 20 --operation ALLOCATION_APPLIED
```

### 扫描性能

同一 Commit 的文件清单以单连接、单事务批量写入（`executemany` upsert），
重复扫描保持幂等；任何坏行都会整体回滚，不会留下半批数据。

## 完整工作流

```bash
# 1. 初始化
python copyright_cli.py init

# 2. 扫描仓库（锁定 Commit）
python copyright_cli.py scan /path/to/repo

# 3. （可选）权属核验
python copyright_cli.py ownership load-config team.yaml
python copyright_cli.py ownership analyze

# 4. （可选）标记已用于其他软著的代码
python copyright_cli.py mark-used import used-manifest.csv --copyright-name 旧系统V1.0

# 5. （可选）设置时间过滤
python copyright_cli.py set-since set 2025-01-01

# 6. 编辑软著配置（或用 auto-split 生成 copyrights-auto.yaml）
vim copyright/config/copyrights.yaml

# 7. 分配文件（产生判定证据与 run-manifest）
python copyright_cli.py allocate

# 8. 验证分配
python copyright_cli.py validate

# 9. 构建工作区（每个软著，内容取自锁定 Commit）
python copyright_cli.py workspace collector
python copyright_cli.py workspace parser

# 10. 检测重合
python copyright_cli.py detect

# 11. 生成材料（每个软著）
python copyright_cli.py requirements
python copyright_cli.py generate collector --manual copyright/manuals/collector.md
python copyright_cli.py generate parser --manual copyright/manuals/parser.md

# 12. 最终审计
python copyright_cli.py audit
```

## 注意事项

1. **Commit 锁定**：每次扫描绑定一个 Git Commit，后续所有操作基于该 Commit
2. **公共代码处理**：`shared/**` 等公共代码建议不进入任何软著，或单独申请
3. **代码量要求**：程序材料至少需要3000个可打印非空代码行，空白行不计数
4. **文档量要求**：说明书至少需要1800个可打印非空正文行，且不能以重复内容补足
5. **文件真实性**：所有代码必须来自真实项目，说明书内容必须与系统逻辑一致，禁止编造
6. **以 PDF 为准**：Markdown 是可追溯源稿，最终是否60页及逐页行数必须检查直接渲染出的 PDF
7. **冻结材料**：已经提交的材料只允许只读审计；需要更新冻结基线时必须先确认申请状态，不能自行改摘要
8. **无隐式顺序**：文件匹配多个软著时立即失败，YAML 条目顺序永远不会成为裁决依据
9. **显式失败**：不可满足的拆分数量或行数约束只会报告 infeasible / constraint_violation，不会静默降级
10. **Commit 一致性**：扫描、run-manifest、工作区与最终审计的 Commit 必须一致，任何不一致都会导致失败

## License

MIT
