#!/usr/bin/env python3
"""Generate DOCX materials for software copyright application.

Standard requirements (中国版权保护中心):
- 程序鉴别材料: 60页（连续的前30页 + 后30页），纯代码，不含说明性文字
- 文档鉴别材料: 60页（连续的前30页 + 后30页），软件设计/使用说明文档
- 每页不少于50行（结束页除外）
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

try:
    from docx import Document
    from docx.shared import Pt, Inches, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.style import WD_STYLE_TYPE
except ImportError:
    print("Error: python-docx not installed. Run: pip install python-docx")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))


# 标准参数
LINES_PER_PAGE = 50       # 每页50行
FRONT_PAGES = 30          # 前30页
BACK_PAGES = 30           # 后30页
TOTAL_PAGES = 60          # 总共60页


def create_code_document(title: str, version: str) -> Document:
    """Create a new DOCX document for code material (程序鉴别材料)."""
    doc = Document()
    
    # 设置页面边距（标准：上2.5cm 下2.5cm 左3cm 右2.5cm）
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3)
        section.right_margin = Cm(2.5)
    
    # 添加页眉（软件名称 + 版本号）
    section = doc.sections[0]
    header = section.header
    header_para = header.paragraphs[0]
    header_para.text = f"{title} {version}"
    header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    for run in header_para.runs:
        run.font.size = Pt(9)
    
    return doc


def create_doc_document(title: str, version: str) -> Document:
    """Create a new DOCX document for document material (文档鉴别材料)."""
    doc = Document()
    
    # 设置页面边距
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3)
        section.right_margin = Cm(2.5)
    
    # 页眉
    section = doc.sections[0]
    header = section.header
    header_para = header.paragraphs[0]
    header_para.text = f"{title} {version} 设计说明书"
    header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    for run in header_para.runs:
        run.font.size = Pt(9)
    
    return doc


def read_source_files(workspace_dir: Path, manifest: dict) -> list:
    """Read all source files from workspace.
    
    Returns raw code content only - no added annotations or explanatory text.
    """
    source_dir = workspace_dir / 'source'
    files_content = []
    
    for file_info in manifest['allowedFiles']:
        file_path = source_dir / file_info['path']
        
        if not file_path.exists():
            continue
        
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            lines = content.split('\n')
            
            # 保留所有原始代码行（包括注释，因为注释是代码仓库的一部分）
            # 仅过滤纯空行（连续空行压缩为1行）
            effective_lines = []
            prev_empty = False
            for line in lines:
                if line.strip() == '':
                    if not prev_empty:
                        effective_lines.append('')
                    prev_empty = True
                else:
                    effective_lines.append(line)
                    prev_empty = False
            
            # 去除首尾空行
            while effective_lines and effective_lines[0].strip() == '':
                effective_lines.pop(0)
            while effective_lines and effective_lines[-1].strip() == '':
                effective_lines.pop()
            
            files_content.append({
                'path': file_info['path'],
                'language': file_info['language'],
                'lines': lines,
                'effective_lines': effective_lines,
                'total_lines': len(lines),
                'effective_line_count': len(effective_lines),
            })
        except Exception as e:
            print(f"Warning: Could not read {file_path}: {e}")
    
    return files_content


def collect_pure_code_lines(files_content: list) -> list:
    """Collect all code lines from files - PURE CODE ONLY.
    
    No file path annotations, no separator comments, no explanatory text.
    Only the actual source code content from the repository.
    """
    all_lines = []
    
    for file_info in files_content:
        # 直接添加代码行，不添加任何说明性文字
        for line in file_info['effective_lines']:
            all_lines.append(line)
    
    return all_lines


def paginate_lines(all_lines: list) -> list:
    """Split lines into pages of LINES_PER_PAGE lines each."""
    pages = []
    for i in range(0, len(all_lines), LINES_PER_PAGE):
        page_lines = all_lines[i:i + LINES_PER_PAGE]
        pages.append(page_lines)
    return pages


def select_pages(pages: list) -> tuple:
    """Select front 30 + back 30 pages per standard.
    
    Returns: (front_pages, back_pages, is_full)
    - If total <= 60 pages: return all pages as-is (is_full=True)
    - If total > 60 pages: return first 30 + last 30
    """
    total = len(pages)
    
    if total <= TOTAL_PAGES:
        # 不足60页，全部输出
        return pages, [], True
    else:
        # 前30页 + 后30页
        front = pages[:FRONT_PAGES]
        back = pages[-BACK_PAGES:]
        return front, back, False


def write_code_to_docx(doc: Document, pages: list, is_last_section: bool = True):
    """Write code pages to DOCX document.
    
    Pure code output - monospace font, no decorations.
    """
    # 设置默认字体为等宽字体
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Courier New'
    font.size = Pt(9)
    
    for page_idx, page_lines in enumerate(pages):
        for line_idx, line in enumerate(page_lines):
            para = doc.add_paragraph()
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.space_after = Pt(0)
            para.paragraph_format.line_spacing = 1.0
            
            # 添加代码文本
            run = para.add_run(line if line else ' ')
            run.font.name = 'Courier New'
            run.font.size = Pt(9)
        
        # 每页后添加分页符（除了最后一页）
        is_last_page = (page_idx == len(pages) - 1) and is_last_section
        if not is_last_page:
            doc.add_page_break()


def generate_code_material(
    workspace_dir: Path,
    output_dir: Path,
    software_name: str,
    version: str,
):
    """Generate program identification material (程序鉴别材料).
    
    Standard: 60 pages (front 30 + back 30), pure code only.
    No explanatory text, no file path annotations - only repository code.
    """
    # 加载 manifest
    manifest_path = workspace_dir / 'SOURCE_MANIFEST.json'
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    
    # 读取源文件
    files_content = read_source_files(workspace_dir, manifest)
    
    if not files_content:
        print("Error: No source files found in workspace")
        return None
    
    # 收集纯代码行（不含任何说明性文字）
    all_lines = collect_pure_code_lines(files_content)
    total_lines = len(all_lines)
    
    # 分页
    pages = paginate_lines(all_lines)
    total_pages = len(pages)
    
    print(f"Total code lines: {total_lines}")
    print(f"Total pages: {total_pages} ({LINES_PER_PAGE} lines/page)")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 选择页面
    front_pages, back_pages, is_full = select_pages(pages)
    
    if is_full:
        # 不足60页，生成全部代码（单文件）
        print(f"Code < {TOTAL_PAGES} pages, generating all {total_pages} pages")
        
        doc = create_code_document(software_name, version)
        write_code_to_docx(doc, front_pages, is_last_section=True)
        
        output_path = output_dir / f"{software_name}-程序鉴别材料.docx"
        doc.save(str(output_path))
        print(f"Generated: {output_path}")
        print(f"  Pages: {total_pages}")
        
        if total_pages < TOTAL_PAGES:
            print(f"  WARNING: Only {total_pages} pages (standard requires {TOTAL_PAGES})")
            print(f"  Need at least {TOTAL_PAGES * LINES_PER_PAGE} effective code lines")
    else:
        # 前30页 + 后30页（单文件，连续编排）
        print(f"Generating standard {TOTAL_PAGES}-page material (front {FRONT_PAGES} + back {BACK_PAGES})...")
        
        doc = create_code_document(software_name, version)
        
        # 写入前30页
        write_code_to_docx(doc, front_pages, is_last_section=False)
        
        # 添加分隔标记（省略号页，表示中间部分省略）
        doc.add_page_break()
        omit_para = doc.add_paragraph()
        omit_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        omit_run = omit_para.add_run(f"（此处省略第 {FRONT_PAGES + 1} 页至第 {total_pages - BACK_PAGES} 页）")
        omit_run.font.size = Pt(10)
        omit_run.font.name = 'SimSun'
        doc.add_page_break()
        
        # 写入后30页
        write_code_to_docx(doc, back_pages, is_last_section=True)
        
        output_path = output_dir / f"{software_name}-程序鉴别材料.docx"
        doc.save(str(output_path))
        print(f"Generated: {output_path}")
        print(f"  Front pages: {len(front_pages)}")
        print(f"  Back pages: {len(back_pages)}")
        print(f"  Total output: {len(front_pages) + len(back_pages)} code pages + 1 omit marker")
    
    # 生成代码清单（辅助文件，不属于鉴别材料）
    manifest_output = output_dir / f"{software_name}-源代码清单.json"
    with open(manifest_output, 'w', encoding='utf-8') as f:
        json.dump({
            'software_name': software_name,
            'version': version,
            'total_files': len(files_content),
            'total_effective_lines': total_lines,
            'total_pages': total_pages,
            'output_pages': min(total_pages, TOTAL_PAGES),
            'files': [
                {
                    'path': f['path'],
                    'language': f['language'],
                    'total_lines': f['total_lines'],
                    'effective_lines': f['effective_line_count'],
                }
                for f in files_content
            ]
        }, f, indent=2, ensure_ascii=False)
    
    print(f"Generated: {manifest_output}")
    return output_path


def generate_doc_material(
    workspace_dir: Path,
    output_dir: Path,
    software_name: str,
    version: str,
    description: str = "",
):
    """Generate document identification material (文档鉴别材料).
    
    Standard: 60 pages (front 30 + back 30).
    Content: Software design document / user manual structure.
    
    If a design doc exists in workspace, use it.
    Otherwise, generate a structured template based on code analysis.
    """
    # 加载 manifest
    manifest_path = workspace_dir / 'SOURCE_MANIFEST.json'
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    
    # 读取源文件（用于分析结构）
    files_content = read_source_files(workspace_dir, manifest)
    
    if not files_content:
        print("Error: No source files found in workspace")
        return None
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 检查是否有现成的设计文档
    project_context_path = workspace_dir / 'PROJECT_CONTEXT.md'
    doc_lines = []
    
    if project_context_path.exists():
        # 使用已有的项目文档
        content = project_context_path.read_text(encoding='utf-8', errors='ignore')
        doc_lines = [line for line in content.split('\n')]
    
    # 生成文档内容（基于代码结构分析）
    doc_content = _build_document_content(
        software_name, version, description, files_content, doc_lines
    )
    
    # 分页
    pages = paginate_lines(doc_content)
    total_pages = len(pages)
    
    print(f"Document material: {len(doc_content)} lines, {total_pages} pages")
    
    # 选择页面
    front_pages, back_pages, is_full = select_pages(pages)
    
    doc = create_doc_document(software_name, version)
    
    if is_full:
        write_doc_pages_to_docx(doc, front_pages, is_last_section=True)
    else:
        write_doc_pages_to_docx(doc, front_pages, is_last_section=False)
        # 省略标记
        doc.add_page_break()
        omit_para = doc.add_paragraph()
        omit_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        omit_run = omit_para.add_run(f"（此处省略第 {FRONT_PAGES + 1} 页至第 {total_pages - BACK_PAGES} 页）")
        omit_run.font.size = Pt(10)
        omit_run.font.name = 'SimSun'
        doc.add_page_break()
        write_doc_pages_to_docx(doc, back_pages, is_last_section=True)
    
    output_path = output_dir / f"{software_name}-文档鉴别材料.docx"
    doc.save(str(output_path))
    print(f"Generated: {output_path}")
    print(f"  Pages: {min(total_pages, TOTAL_PAGES)}")
    
    if total_pages < TOTAL_PAGES:
        print(f"  WARNING: Only {total_pages} pages (standard requires {TOTAL_PAGES})")
    
    return output_path


def _build_document_content(
    software_name: str,
    version: str,
    description: str,
    files_content: list,
    existing_doc_lines: list,
) -> list:
    """Build document content lines for 文档鉴别材料.
    
    Generates a structured software design document based on code analysis.
    """
    lines = []
    
    # 封面信息
    lines.append(f"{software_name}")
    lines.append(f"版本：{version}")
    lines.append(f"设计说明书")
    lines.append("")
    lines.append("")
    
    # 目录结构
    lines.append("目  录")
    lines.append("")
    lines.append("一、软件概述")
    lines.append("二、运行环境")
    lines.append("三、软件架构设计")
    lines.append("四、模块详细设计")
    lines.append("五、数据结构设计")
    lines.append("六、接口设计")
    lines.append("七、核心算法与流程")
    lines.append("八、安全设计")
    lines.append("九、部署与运维")
    lines.append("十、测试说明")
    lines.append("")
    lines.append("")
    
    # 一、软件概述
    lines.append("一、软件概述")
    lines.append("")
    lines.append(f"1.1 软件名称：{software_name}")
    lines.append(f"1.2 版本号：{version}")
    if description:
        lines.append(f"1.3 功能描述：{description}")
    lines.append(f"1.4 开发目的：本软件旨在提供完整的{software_name}功能实现，")
    lines.append("满足业务需求，提高数据处理效率和系统可靠性。")
    lines.append("")
    lines.append("1.5 主要功能：")
    
    # 从代码结构推断功能模块
    modules = set()
    for f in files_content:
        parts = Path(f['path']).parts
        if len(parts) >= 2:
            modules.add(parts[0] if len(parts) == 2 else f"{parts[0]}/{parts[1]}")
    
    for i, mod in enumerate(sorted(modules), 1):
        lines.append(f"  ({i}) {mod} 模块")
    lines.append("")
    lines.append("")
    
    # 二、运行环境
    lines.append("二、运行环境")
    lines.append("")
    lines.append("2.1 硬件环境：")
    lines.append("  - 处理器：x86_64 架构，2核及以上")
    lines.append("  - 内存：4GB 及以上")
    lines.append("  - 硬盘：50GB 可用空间")
    lines.append("")
    lines.append("2.2 软件环境：")
    lines.append("  - 操作系统：Linux (CentOS 7+ / Ubuntu 18.04+)")
    lines.append("  - 运行时：Python 3.8+ / Node.js 16+ / JDK 11+")
    lines.append("  - 数据库：根据实际部署需求配置")
    lines.append("")
    lines.append("2.3 网络环境：")
    lines.append("  - 支持 TCP/IP 网络协议")
    lines.append("  - 带宽：100Mbps 及以上")
    lines.append("")
    lines.append("")
    
    # 三、软件架构设计
    lines.append("三、软件架构设计")
    lines.append("")
    lines.append("3.1 总体架构")
    lines.append("")
    lines.append("本系统采用分层架构设计，自下而上分为：")
    lines.append("  - 基础设施层：提供计算、存储、网络等基础资源")
    lines.append("  - 数据层：负责数据持久化、缓存和消息队列")
    lines.append("  - 服务层：实现核心业务逻辑")
    lines.append("  - 接口层：对外提供 API 和数据交互接口")
    lines.append("")
    lines.append("3.2 技术选型")
    lines.append("")
    
    # 从文件扩展名推断技术栈
    languages = set(f['language'] for f in files_content if f['language'])
    lines.append(f"  主要开发语言：{', '.join(sorted(languages)) if languages else 'Python'}")
    lines.append(f"  源文件数量：{len(files_content)} 个")
    total_code_lines = sum(f['effective_line_count'] for f in files_content)
    lines.append(f"  代码总行数：{total_code_lines} 行（有效行）")
    lines.append("")
    lines.append("")
    
    # 四、模块详细设计
    lines.append("四、模块详细设计")
    lines.append("")
    
    # 按目录分组
    dir_files = {}
    for f in files_content:
        dir_name = str(Path(f['path']).parent)
        if dir_name not in dir_files:
            dir_files[dir_name] = []
        dir_files[dir_name].append(f)
    
    section_num = 1
    for dir_name in sorted(dir_files.keys()):
        files = dir_files[dir_name]
        lines.append(f"4.{section_num} {dir_name} 模块")
        lines.append("")
        lines.append(f"  包含 {len(files)} 个源文件：")
        for f in files:
            fname = Path(f['path']).name
            lines.append(f"    - {fname} ({f['effective_line_count']} 行)")
        lines.append("")
        
        # 提取文件中的类/函数定义作为接口说明
        for f in files:
            classes = [l.strip() for l in f['effective_lines'] 
                      if l.strip().startswith('class ') or l.strip().startswith('def ')]
            if classes:
                fname = Path(f['path']).name
                lines.append(f"  {fname} 主要接口：")
                for cls in classes[:10]:
                    lines.append(f"    {cls}")
                lines.append("")
        
        section_num += 1
    
    lines.append("")
    
    # 五、数据结构设计
    lines.append("五、数据结构设计")
    lines.append("")
    lines.append("5.1 核心数据模型")
    lines.append("")
    
    # 提取 dataclass / model 定义
    for f in files_content:
        if 'model' in f['path'].lower() or 'schema' in f['path'].lower():
            lines.append(f"  {f['path']}:")
            for line in f['effective_lines']:
                stripped = line.strip()
                if stripped.startswith('class ') or stripped.startswith('@dataclass'):
                    lines.append(f"    {stripped}")
            lines.append("")
    
    lines.append("")
    
    # 六、接口设计
    lines.append("六、接口设计")
    lines.append("")
    lines.append("6.1 内部接口")
    lines.append("")
    lines.append("  各模块间通过函数调用和消息传递进行通信。")
    lines.append("")
    lines.append("6.2 外部接口")
    lines.append("")
    lines.append("  系统对外提供以下接口：")
    lines.append("  - TCP 数据接收接口")
    lines.append("  - 消息队列生产/消费接口")
    lines.append("  - 配置管理接口")
    lines.append("")
    lines.append("")
    
    # 七、核心算法与流程
    lines.append("七、核心算法与流程")
    lines.append("")
    lines.append("7.1 数据处理流程")
    lines.append("")
    lines.append("  (1) 数据接收：通过 TCP 连接接收原始数据流")
    lines.append("  (2) 协议解析：按照预定义协议格式解析数据帧")
    lines.append("  (3) 数据转换：将原始数据转换为标准化格式")
    lines.append("  (4) 数据分发：将处理后的数据发送至下游系统")
    lines.append("")
    lines.append("7.2 错误处理机制")
    lines.append("")
    lines.append("  - 连接超时自动重连")
    lines.append("  - 数据校验失败丢弃并记录日志")
    lines.append("  - 缓冲区溢出时启用背压机制")
    lines.append("")
    lines.append("")
    
    # 八、安全设计
    lines.append("八、安全设计")
    lines.append("")
    lines.append("8.1 通信安全")
    lines.append("  - 支持 TLS/SSL 加密传输")
    lines.append("  - 数据帧完整性校验")
    lines.append("")
    lines.append("8.2 访问控制")
    lines.append("  - 基于 IP 白名单的连接控制")
    lines.append("  - 操作日志审计")
    lines.append("")
    lines.append("")
    
    # 九、部署与运维
    lines.append("九、部署与运维")
    lines.append("")
    lines.append("9.1 部署方式")
    lines.append("  - 支持容器化部署 (Docker/Kubernetes)")
    lines.append("  - 支持传统进程部署")
    lines.append("")
    lines.append("9.2 监控告警")
    lines.append("  - 运行状态监控")
    lines.append("  - 性能指标采集")
    lines.append("  - 异常告警通知")
    lines.append("")
    lines.append("")
    
    # 十、测试说明
    lines.append("十、测试说明")
    lines.append("")
    lines.append("10.1 测试策略")
    lines.append("  - 单元测试：覆盖核心业务逻辑")
    lines.append("  - 集成测试：验证模块间协作")
    lines.append("  - 性能测试：验证系统吞吐量和延迟")
    lines.append("")
    lines.append("10.2 测试环境")
    lines.append("  - 与生产环境同配置")
    lines.append("  - 使用模拟数据源进行压力测试")
    lines.append("")
    
    # 如果已有文档内容，追加
    if existing_doc_lines:
        lines.append("")
        lines.append("附录：项目补充说明")
        lines.append("")
        lines.extend(existing_doc_lines)
    
    # 如果内容不够60页，补充详细说明
    current_pages = (len(lines) + LINES_PER_PAGE - 1) // LINES_PER_PAGE
    if current_pages < TOTAL_PAGES:
        lines.extend(_generate_additional_content(files_content, TOTAL_PAGES * LINES_PER_PAGE - len(lines)))
    
    return lines


def _generate_additional_content(files_content: list, needed_lines: int) -> list:
    """Generate additional document content to fill pages."""
    lines = []
    
    lines.append("")
    lines.append("附录A：源文件清单")
    lines.append("")
    lines.append(f"{'序号':<6}{'文件路径':<50}{'语言':<12}{'有效行数':<10}")
    lines.append("-" * 78)
    
    for i, f in enumerate(files_content, 1):
        lines.append(f"{i:<6}{f['path']:<50}{f['language'] or '-':<12}{f['effective_line_count']:<10}")
    
    lines.append("")
    lines.append("")
    lines.append("附录B：核心代码结构说明")
    lines.append("")
    
    # 为每个文件生成结构说明
    for f in files_content:
        lines.append(f"B.{files_content.index(f) + 1} {f['path']}")
        lines.append("")
        
        # 提取类、函数、常量定义
        definitions = []
        for line in f['effective_lines']:
            stripped = line.strip()
            if any(stripped.startswith(kw) for kw in 
                   ['class ', 'def ', 'const ', 'export ', 'public ', 'private ',
                    'interface ', 'type ', 'struct ', 'enum ']):
                definitions.append(stripped)
        
        if definitions:
            lines.append("  主要定义：")
            for d in definitions:
                lines.append(f"    {d}")
        else:
            lines.append(f"  文件行数：{f['effective_line_count']} 行")
        
        lines.append("")
    
    # 如果还不够，添加通用说明
    if len(lines) < needed_lines:
        lines.append("")
        lines.append("附录C：术语表")
        lines.append("")
        terms = [
            ("TCP", "传输控制协议，提供可靠的面向连接的数据传输服务"),
            ("Kafka", "分布式消息队列系统，用于高吞吐量的数据流处理"),
            ("Buffer", "缓冲区，用于临时存储数据以平衡生产者和消费者速度差异"),
            ("Protocol", "协议，定义数据交换格式和规则的约定"),
            ("Handler", "处理器，负责处理特定类型事件的组件"),
            ("Consumer", "消费者，从消息队列中读取和处理消息的组件"),
            ("Producer", "生产者，向消息队列中发送消息的组件"),
            ("Schema", "模式，定义数据结构的标准格式"),
            ("Transform", "转换，将数据从一种格式转换为另一种格式的处理过程"),
            ("Normalizer", "标准化器，将不同来源的数据统一为标准格式"),
        ]
        for term, desc in terms:
            lines.append(f"  {term}：{desc}")
            lines.append("")
    
    return lines[:needed_lines]


def write_doc_pages_to_docx(doc: Document, pages: list, is_last_section: bool = True):
    """Write document pages to DOCX (文档鉴别材料 uses Song font)."""
    style = doc.styles['Normal']
    font = style.font
    font.name = 'SimSun'
    font.size = Pt(10.5)
    
    for page_idx, page_lines in enumerate(pages):
        for line in page_lines:
            para = doc.add_paragraph()
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.space_after = Pt(0)
            para.paragraph_format.line_spacing = 1.0
            
            run = para.add_run(line if line else ' ')
            run.font.name = 'SimSun'
            run.font.size = Pt(10.5)
        
        is_last_page = (page_idx == len(pages) - 1) and is_last_section
        if not is_last_page:
            doc.add_page_break()


def main():
    parser = argparse.ArgumentParser(description='Generate DOCX code materials')
    parser.add_argument('workspace', type=Path, help='Workspace directory')
    parser.add_argument('--output', type=Path, help='Output directory')
    parser.add_argument('--doc-only', action='store_true', help='Only generate document material')
    parser.add_argument('--code-only', action='store_true', help='Only generate code material')
    
    args = parser.parse_args()
    
    if not args.workspace.exists():
        print(f"Error: Workspace not found: {args.workspace}")
        sys.exit(1)
    
    manifest_path = args.workspace / 'SOURCE_MANIFEST.json'
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}")
        sys.exit(1)
    
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    
    software_name = manifest['softwareName']
    version = manifest['version']
    description = manifest.get('description', '')
    
    output_dir = args.output or (args.workspace.parent.parent / 'outputs' / manifest['copyrightId'])
    
    print(f"Generating materials for: {software_name} {version}")
    print(f"Workspace: {args.workspace}")
    print(f"Output: {output_dir}")
    print()
    
    if not args.doc_only:
        print("=== 程序鉴别材料 ===")
        generate_code_material(args.workspace, output_dir, software_name, version)
        print()
    
    if not args.code_only:
        print("=== 文档鉴别材料 ===")
        generate_doc_material(args.workspace, output_dir, software_name, version, description)


if __name__ == '__main__':
    main()
