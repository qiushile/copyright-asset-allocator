# 软著代码资产分配系统

**核心原则**：通过文件台账和隔离工作区实现硬约束，而非依赖 Prompt 软约束。

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
cd /opt/WorkStation/copyright-asset-allocator
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

### 5. 分配文件

```bash
python copyright_cli.py allocate
```

分配结果保存在：
- `copyright/allocations/<copyright-id>-files.txt`
- `copyright/allocations/<copyright-id>-files.json`
- `copyright/allocations/allocation-summary.json`

### 6. 验证分配

```bash
python copyright_cli.py validate
```

检查：
- 是否有文件分配给多个软著
- 每个软著是否有足够的代码（默认 3000 行）
- 是否有未分配的源文件

### 7. 构建工作区

为每个软著构建隔离工作区：

```bash
python copyright_cli.py workspace collector
python copyright_cli.py workspace parser
```

工作区结构：
```
copyright/workspaces/collector/
├── SOURCE_MANIFEST.json
├── PROJECT_CONTEXT.md
└── source/
    ├── services/collector/
    └── libs/protocol-receiver/
```

### 8. 检测重合

```bash
python copyright_cli.py detect
```

三级检测：
1. **路径重复**：同一文件路径分配给多个软著
2. **哈希重复**：不同路径但内容完全相同
3. **代码相似**：函数级别的代码相似度

### 9. 准备说明书并生成材料

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

### 10. 最终审计

```bash
python copyright_cli.py audit
```

检查：
- 代码路径重复 = 0
- 完整文件重复 = 0
- 软件名称一致性
- 版本号一致性
- Commit 一致性
- 工作区完整性
- 两份最终 PDF 均为 A4、恰好60页
- 程序每页不少于50行，说明书每页不少于30行
- 正文无行号，字号达到规则下限
- 《软件的主要功能》字数与“补充操作提示”结构、去重检查通过
- 已提交冻结目录摘要未变化

## 目录结构

```
copyright-asset-allocator/
├── copyright_cli.py              # CLI 入口
├── requirements.txt              # Python 依赖
├── README.md                     # 本文档
│
├── copyright/
│   ├── config/
│   │   ├── copyrights.yaml       # 软著配置
│   │   ├── material-requirements.yaml # 通用材料硬性规则
│   │   ├── material-protected-assets.yaml.example # 冻结配置示例
│   │   └── material-protected-assets.local.yaml # 本机冻结清单，不进入Git
│   │
│   ├── inventory/
│   │   ├── assets.db             # SQLite 数据库
│   │   ├── repository-files.csv  # 文件清单
│   │   ├── scan-result.json      # 扫描结果
│   │   ├── validation-report.json
│   │   └── duplicate-report.json
│   │
│   ├── allocations/
│   │   ├── collector-files.txt
│   │   ├── collector-files.json
│   │   ├── parser-files.txt
│   │   ├── parser-files.json
│   │   └── allocation-summary.json
│   │
│   ├── workspaces/
│   │   ├── collector/
│   │   │   ├── SOURCE_MANIFEST.json
│   │   │   ├── PROJECT_CONTEXT.md
│   │   │   └── source/
│   │   └── parser/
│   │       ├── SOURCE_MANIFEST.json
│   │       ├── PROJECT_CONTEXT.md
│   │       └── source/
│   ├── manuals/
│   │   ├── README.md             # 系统专用说明书写作约束
│   │   └── <copyright-id>.md     # 人工核实的系统说明书
│   │
│   ├── outputs/
│   │   ├── collector/
│   │   │   ├── 源代码文档.md
│   │   │   ├── 源代码文档.pdf
│   │   │   ├── 软件说明书.md
│   │   │   ├── 软件说明书.pdf
│   │   │   └── XX系统-源代码清单.json
│   │   └── final-audit.json
│   │
│   └── scripts/
│       ├── database.py           # 数据库操作
│       ├── scan_repository.py    # 仓库扫描
│       ├── allocate_files.py     # 文件分配
│       ├── validate_allocations.py
│       ├── build_workspace.py    # 工作区构建
│       ├── detect_duplicates.py  # 重合检测
│       ├── generate_markdown_pdf.py # Markdown→PDF主生成流程
│       ├── generate_docx.py      # DOCX兼容辅助与通用分页函数
│       ├── material_requirements.py # 规则加载与冻结保护
│       ├── validate_materials.py # PDF/DOCX/Markdown材料审计
│       └── final_audit.py        # 最终审计
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

## 完整工作流

```bash
# 1. 初始化
python copyright_cli.py init

# 2. 扫描仓库
python copyright_cli.py scan /path/to/repo

# 3. 编辑配置
vim copyright/config/copyrights.yaml

# 4. 分配文件
python copyright_cli.py allocate

# 5. 验证分配
python copyright_cli.py validate

# 6. 构建工作区（每个软著）
python copyright_cli.py workspace collector
python copyright_cli.py workspace parser

# 7. 检测重合
python copyright_cli.py detect

# 8. 生成材料（每个软著）
python copyright_cli.py requirements
python copyright_cli.py generate collector --manual copyright/manuals/collector.md
python copyright_cli.py generate parser --manual copyright/manuals/parser.md

# 9. 最终审计
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

## License

MIT
