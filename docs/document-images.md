# 说明书图片：采集、排版与审计

## 已落地的范围

主流程继续使用 `Markdown → PDF → 审计`，程序鉴别材料不受影响。说明书现在支持本地 PNG/JPEG 图片、独立来源清单、随材料携带的证据副本和最终 PDF 图片检查。旧 DOCX 入口遇到图片会明确提示改用主流程，避免把图片语法当成正文输出。

这里的60页、每页30行是本仓库现有材料策略。工具校验通过表示符合该策略，不表示审查机构已经认可图片类型或通过申请；具体退回材料应按补正意见设置所需图片数量。

## 对原设计的改进

1. **保持正文计数**：图片与图注都不计入30行。含图页行距为1.5，普通页为2.28；字体仍为10.15pt。最多一张图，正文后排图，等比限制在510×240pt内。打印短边小于20mm或有效分辨率低于100dpi直接拒绝。截图内部文字仍需人工核实可读性。
2. **跟随内容选择图片**：图依附于它前面最近的正文打印行；开头的图依附首行。保留既有前900行＋后900行选择策略。图片跟随锚点进入最终页，裁掉的中段图片记为 `pdf_page: null`，不会偷偷移到别处。该规则沿用仓库的行选择语义，并未改变已有分页策略。
3. **完整携带证据**：输出 Markdown 改写为 `image-assets/` 相对路径；图片和证据均复制并记录SHA-256。输出目录搬迁后仍可独立审计，不依赖原工作区绝对路径。
4. **检查最终产物**：检查图片数量/页码、图注、边界、图文重叠、打印尺寸，核对Markdown/PDF/图片/证据摘要及重新计算的分页计划。图注从正文行数中排除。来源类型是作者声明，哈希只能证明完整性，不能证明截图真实性。
5. **明确失败**：不支持的图片语法、缺图、损坏图片、证据缺失、摘要变化、同页多图、图片太窄或清晰度不足都失败。不会降级为纯文字，也不会通过减少正文行数挤入图片。
6. **保护已冻结材料**：先在临时目录完成渲染检查，写入前再次检查冻结边界。不会自动刷新冻结摘要。发布是逐文件替换而非整个目录的原子事务；意外中断造成的混合版本可通过图片清单中的摘要发现，应重新生成未冻结材料。

## 准备说明书

推荐结构：

```text
copyright/manuals/<id>/
  SOFTWARE_MANUAL.md
  images.yaml
  assets/
    fig-startup.png
  evidence/
    startup.log
    capture.json
```

Markdown中每张图片独占一行，路径相对于说明书目录。仅支持直接引用；不支持内联图片、引用式图片、HTML图片、网络URL、SVG、绝对路径和越界符号链接。

```markdown
### 4.1 启动与检查

1. 按本节步骤启动系统，核对监听地址及启动状态。

![图4-1 终端启动结果截图](assets/fig-startup.png)
```

侧录文件优先使用同名的 `SOFTWARE_MANUAL.images.yaml`，找不到时使用目录内的 `images.yaml`。一个目录有多份说明书时使用同名侧录文件。图片文件、id和Markdown引用须一一对应；不同图注不能复用同一个文件路径。

```yaml
schema_version: 1
images:
  - id: fig-startup
    file: assets/fig-startup.png
    caption: 图4-1 终端启动结果截图
    source_type: real_terminal_capture
    sha256: 实际图片文件的SHA256
    evidence:
      - file: evidence/startup.log
        sha256: 实际日志文件的SHA256
      - file: evidence/capture.json
        sha256: 实际采集记录的SHA256
```

以上摘要占位仅用于格式说明，正式生成必须使用真实摘要。截图脚本会自动计算。

| source_type | 用途 | 是否计入 minimum_real_captures |
|---|---|---|
| `real_web_capture` | 真实系统Web页面截图 | 是，仍须作者核实 |
| `real_terminal_capture` | 真实终端窗口截图 | 是，仍须作者核实 |
| `generated_from_source` | 从源码/配置生成的HTML或其他示意图 | 否 |
| `rendered_log` | 把真实日志重新排版成图片 | 否 |

后两类图注必须包含“示意图”，不能包含“运行截图”“界面截图”“实拍”。图注最多45字符；详细说明写到正文。不要将重排日志或手工模拟UI登记为真实截图。

## 采集或生成图片

安装可选截图依赖（只插入已有图片不需要Playwright）：

```bash
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
```

真实网页：等待并截取指定元素；无selector则截取固定视口。脚本使用新浏览器会话，不自动读取日常浏览器的登录状态。需要登录的系统可手工截图后导入，并附上实际运行记录。

```bash
.venv/bin/python copyright/scripts/capture_screens.py web \
  --url http://127.0.0.1:8080/dashboard --selector '#app' \
  --id fig-dashboard --caption '图4-1 系统运行界面截图' \
  --output /tmp/manual-dashboard-capture
```

HTML示意图：HTML应自包含，样式、SVG等内联。脚本从归档副本渲染，禁止HTTP(S)资源请求。`--evidence`可重复传入源码、清单等真实依据。

```bash
.venv/bin/python copyright/scripts/capture_screens.py html \
  --html /path/to/diagram.html --selector '#diagram' \
  --evidence /path/to/SOURCE_MANIFEST.json \
  --id fig-flow --caption '图2-1 源码数据流示意图' \
  --output /tmp/manual-flow-capture
```

导入已有终端截图（不执行或伪造终端命令）：

```bash
.venv/bin/python copyright/scripts/capture_screens.py import \
  --image /path/to/terminal.png --evidence /path/to/run.log \
  --source-type real_terminal_capture \
  --id fig-startup --caption '图4-1 终端启动结果截图' \
  --output /tmp/manual-terminal-capture
```

每次输出目录必须尚不存在，防止覆盖既有证据。目录包含 `assets/`、`evidence/`、`images.yaml` 和 `image-snippet.md`。将这些文件复制到说明书目录，合并 `images.yaml` 的 `images` 列表，将片段放到对应正文后；多次采集应使用独立id。证据使用 `evidence/<id>/` 独立子目录，不同id的采集包可以安全合并。图片包不是完整说明书。

网页元数据保存时间、去掉查询参数/片段的URL、标题、视口、selector和浏览器版本；不保存Cookie/浏览器会话。截图和导入日志本身仍可能包含敏感内容，应在归档前由作者检查。脱敏后须使用新的图片及证据摘要。

## 生成与审计

```bash
.venv/bin/python copyright_cli.py generate <id> \
  --manual copyright/manuals/<id>/SOFTWARE_MANUAL.md
.venv/bin/python copyright_cli.py material-audit copyright/outputs/<id>
```

新增产物：

```text
软件说明书.md / 软件说明书.pdf
软件说明书.images.yaml        # 有图时生成
软件说明书-图片清单.json       # 含PDF/Markdown摘要、图片来源、锚点、页码、打印尺寸
image-assets/                 # 内容摘要命名的图片及证据副本
```

目录审计仍同时要求程序PDF和文档PDF。可在Python测试或单独检查说明书时调用 `audit_material_directory(..., require_rendered_pair=False)`，不影响正式CLI的双材料检查。

若本次补正要求至少三张图片，其中两张必须为实际运行截图，在本次使用的规则文件中修改：

```yaml
document_material:
  images:
    minimum_count: 3
    minimum_real_captures: 2
    max_per_page: 1
    caption_counts_as_body_line: false
    local_evidence_required: true
```

保留规则文件其他字段。生成与审计需使用同一份规则（CLI的 `--requirements` 参数），不要只改审计。默认两项数量都为0，兼容既有纯文本材料；图片被裁切掉不计数。

## 运行本仓库 HTML demo

```bash
# 完整生成：离线HTML → Chromium截图 → 单页PDF → 版式检查
.venv/bin/python docs/examples/render_image_example.py

# 只生成离线HTML、源码清单和快照，不需要Playwright/WeasyPrint
.venv/bin/python docs/examples/render_image_example.py --html-only

# 指定新的输出目录；已有目录始终拒绝覆盖
.venv/bin/python docs/examples/render_image_example.py --output /tmp/materials-demo
```

默认输出为 `copyright/outputs/html-demos/<时间戳>/`，可以反复运行。命令完成后打印实际HTML路径；完整模式还会打印PDF路径。支持 `--requirements` 指定冻结规则。浏览器依赖缺失或渲染失败时不发布半成品；安装可选依赖后可以重试同一个尚未创建的目标目录。

生成内容：

- `source-inventory.html`：自包含离线页面，无需服务器或CDN。上方为适合截图的中文模块概览，下方展示全部顶层函数，支持关键词搜索、模块筛选、空结果恢复及源码清单下载；手机上采用单列布局。
- `source-inventory.json`：由AST提取的函数名、同步/异步类型、源码行范围、docstring、顶层静态导入和每个文件的SHA-256。Git版本仅为参考，实际统计来自工作区快照。
- `sources/*.py`：生成时读取并归档的原始源码，HTML可直接下载。源码仅做静态解析，不执行。
- `bundle/`：完整模式生成的截图和证据包。截图只截取 `#diagram` 概览，避免将完整函数表缩成难以辨认的小图；证据包含同一份源码快照和JSON清单。
- `图片排版预览.pdf`、`preview-verification.json`：完整模式的单页预览及30行正文、图注、图文边界、字号检查结果。

生成器与页面模板分离：修改 `docs/examples/source_inventory.html` 可调整视觉样式及交互；`docs/examples/render_image_example.py` 负责采集快照、生成内容和输出检查。概览中的职责文字是针对本项目人工整理的说明，函数及依赖数据来自源码；AST只统计顶层函数和导入，不表示运行时调用顺序，也不包含类方法、嵌套函数和动态导入。

`图片排版预览.pdf`是单页排版演示，**不是申报说明书**。60页、前后裁切、完整生成、搬迁后审计、篡改失败等由 `tests/test_material_images.py` 中明确标注的测试夹具验证；`tests/test_html_demo.py` 验证源码快照、HTML转义、只生成HTML、失败清理和冻结保护。实际业务补图仍需对应系统的说明书、真实截图和补正意见。
