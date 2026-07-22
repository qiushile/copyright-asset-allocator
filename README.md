# 软著代码资产分配系统

**核心原则**：通过文件台账和隔离工作区实现硬约束，而非依赖 Prompt 软约束。

## 功能特性

- ✅ **仓库扫描**：读取 Git 跟踪文件，计算哈希、行数、语言
- ✅ **文件分配**：基于 YAML 配置将文件唯一分配给各软著
- ✅ **唯一约束**：数据库层面禁止同一文件分配给多个软著
- ✅ **重合检测**：三级检测（路径重复 + 哈希重复 + 代码片段相似）
- ✅ **隔离工作区**：为每个软著生成独立的代码视图
- ✅ **DOCX 生成**：自动生成前30页/后30页代码材料
- ✅ **最终审计**：提交前交叉检查所有约束

## 快速开始

### 1. 安装依赖

```bash
cd /opt/WorkStation/copyright-asset-allocator
pip install -r requirements.txt
```

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
    description: 接收TCP数据、完成报文解密并发送至Kafka
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
    description: 消费原始Kafka消息并完成协议解析和字段标准化
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

### 9. 生成材料

```bash
python copyright_cli.py generate collector
python copyright_cli.py generate parser
```

生成：
- `copyright/outputs/collector/XX系统-代码(前30页).docx`
- `copyright/outputs/collector/XX系统-代码(后30页).docx`
- `copyright/outputs/collector/XX系统-源代码清单.json`

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

## 目录结构

```
copyright-asset-allocator/
├── copyright_cli.py              # CLI 入口
├── requirements.txt              # Python 依赖
├── README.md                     # 本文档
│
├── copyright/
│   ├── config/
│   │   └── copyrights.yaml       # 软著配置
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
│   │
│   ├── outputs/
│   │   ├── collector/
│   │   │   ├── XX系统-代码(前30页).docx
│   │   │   ├── XX系统-代码(后30页).docx
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
│       ├── generate_docx.py      # DOCX 生成
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
python copyright_cli.py generate collector
python copyright_cli.py generate parser

# 9. 最终审计
python copyright_cli.py audit
```

## 注意事项

1. **Commit 锁定**：每次扫描绑定一个 Git Commit，后续所有操作基于该 Commit
2. **公共代码处理**：`shared/**` 等公共代码建议不进入任何软著，或单独申请
3. **代码量要求**：每个软著建议至少 3000 行有效代码
4. **文件真实性**：所有代码必须来自真实项目，禁止 AI 编造

## License

MIT
