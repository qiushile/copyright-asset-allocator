#!/usr/bin/env python3
"""Audit generated copyright materials against the shared strict rules."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pdfplumber
from docx import Document
from pypdf import PdfReader

from material_requirements import load_material_requirements


A4_WIDTH_PT = 595.276
A4_HEIGHT_PT = 841.89


def _error(code: str, message: str, path: Path, **details: Any) -> dict[str, Any]:
    return {
        "severity": "error",
        "code": code,
        "message": message,
        "path": str(path),
        **details,
    }


def _warning(code: str, message: str, path: Path, **details: Any) -> dict[str, Any]:
    return {
        "severity": "warning",
        "code": code,
        "message": message,
        "path": str(path),
        **details,
    }


def classify_material(path: Path) -> str | None:
    name = path.stem
    if "源代码" in name or "程序鉴别" in name or "代码材料" in name:
        return "program"
    if "软件说明书" in name or "文档鉴别" in name:
        return "document"
    if "软件功能和特点" in name:
        return "feature_summary"
    return None


def _body_top_values(page: pdfplumber.page.Page) -> set[float]:
    return {
        round(float(word["top"]), 1)
        for word in page.extract_words()
        if 40 < float(word["top"]) < float(page.height) - 45
    }


def _has_line_number_column(page: pdfplumber.page.Page, expected_rows: int) -> bool:
    numeric_left_words = [
        (round(float(word["top"]), 1), int(str(word["text"])))
        for word in page.extract_words()
        if float(word["x0"]) < float(page.width) * 0.12
        and 40 < float(word["top"]) < float(page.height) - 45
        and re.fullmatch(r"\d{1,6}", str(word["text"]))
    ]
    numeric_left_words.sort()
    values = [value for _, value in numeric_left_words]
    threshold = max(10, int(expected_rows * 0.8))
    if len(values) < threshold:
        return False
    consecutive = sum(current == previous + 1 for previous, current in zip(values, values[1:]))
    return consecutive >= int((len(values) - 1) * 0.8)


def _body_font_sizes(page: pdfplumber.page.Page) -> list[float]:
    return [
        float(char["size"])
        for char in page.chars
        if 40 < float(char["top"]) < float(page.height) - 45
        and str(char.get("text", "")).strip()
        and float(char.get("size", 0)) > 0
    ]


def audit_pdf(
    path: Path,
    material_type: str,
    requirements: dict[str, Any],
) -> dict[str, Any]:
    selection = requirements["page_selection"]
    policy = (
        requirements["program_material"]
        if material_type == "program"
        else requirements["document_material"]
    )
    expected_pages = int(selection["total_pages"])
    expected_rows = int(policy["lines_per_page"])
    issues: list[dict[str, Any]] = []

    reader = PdfReader(str(path))
    page_count = len(reader.pages)
    if page_count != expected_pages:
        issues.append(
            _error(
                "page_count",
                f"必须为{expected_pages}页，实际{page_count}页",
                path,
                expected=expected_pages,
                actual=page_count,
            )
        )

    for page_number, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if abs(width - A4_WIDTH_PT) > 1.0 or abs(height - A4_HEIGHT_PT) > 1.0:
            issues.append(
                _error(
                    "page_size",
                    f"第{page_number}页不是A4",
                    path,
                    page=page_number,
                    width=width,
                    height=height,
                )
            )

    row_counts: list[int] = []
    minimum_font_sizes: list[float] = []
    with pdfplumber.open(path) as document:
        for page_number, page in enumerate(document.pages, start=1):
            row_count = len(_body_top_values(page))
            row_counts.append(row_count)
            if row_count < expected_rows:
                issues.append(
                    _error(
                        "lines_per_page",
                        f"第{page_number}页正文不足{expected_rows}行，实际{row_count}行",
                        path,
                        page=page_number,
                        expected=expected_rows,
                        actual=row_count,
                    )
                )
            if policy.get("line_numbers") == "forbidden" and _has_line_number_column(
                page, expected_rows
            ):
                issues.append(
                    _error(
                        "line_numbers",
                        f"第{page_number}页检测到连续正文行号",
                        path,
                        page=page_number,
                    )
                )
            font_sizes = _body_font_sizes(page)
            if font_sizes:
                actual_minimum = min(font_sizes)
                minimum_font_sizes.append(actual_minimum)
                required_minimum = float(policy["font"]["minimum_rendered_point_size"])
                if actual_minimum + 0.05 < required_minimum:
                    issues.append(
                        _error(
                            "font_size",
                            f"第{page_number}页正文最小字号低于{required_minimum:g}磅，"
                            f"实际{actual_minimum:.2f}磅",
                            path,
                            page=page_number,
                            expected_minimum=required_minimum,
                            actual=round(actual_minimum, 2),
                        )
                    )

    return {
        "path": str(path),
        "format": "pdf",
        "material_type": material_type,
        "page_count": page_count,
        "minimum_body_rows": min(row_counts) if row_counts else 0,
        "maximum_body_rows": max(row_counts) if row_counts else 0,
        "minimum_body_font_size": (
            round(min(minimum_font_sizes), 2) if minimum_font_sizes else None
        ),
        "passed": not any(issue["severity"] == "error" for issue in issues),
        "issues": issues,
    }


def split_docx_pages(document: Document) -> list[list[str]]:
    pages: list[list[str]] = [[]]
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            pages[-1].append(text)
        page_breaks = paragraph._p.xml.count('w:type="page"')
        for _ in range(page_breaks):
            pages.append([])
    while pages and not pages[-1]:
        pages.pop()
    return pages


def _docx_has_line_numbers(page_lines: list[str], expected_rows: int) -> bool:
    values = []
    for line in page_lines:
        match = re.match(r"^\s*(\d{3,6}|\d{1,3})[.:]?\s+\S", line)
        if match:
            values.append(int(match.group(1)))
    threshold = max(10, int(expected_rows * 0.8))
    if len(values) < threshold:
        return False
    consecutive = sum(current == previous + 1 for previous, current in zip(values, values[1:]))
    return consecutive >= int((len(values) - 1) * 0.8)


def audit_docx(
    path: Path,
    material_type: str,
    requirements: dict[str, Any],
) -> dict[str, Any]:
    selection = requirements["page_selection"]
    policy = (
        requirements["program_material"]
        if material_type == "program"
        else requirements["document_material"]
    )
    expected_pages = int(selection["total_pages"])
    expected_rows = int(policy["lines_per_page"])
    issues: list[dict[str, Any]] = []

    document = Document(str(path))
    pages = split_docx_pages(document)
    if len(pages) != expected_pages:
        issues.append(
            _error(
                "explicit_page_count",
                f"DOCX显式分页必须为{expected_pages}页，实际{len(pages)}页",
                path,
                expected=expected_pages,
                actual=len(pages),
            )
        )

    for section_number, section in enumerate(document.sections, start=1):
        width = float(section.page_width.pt)
        height = float(section.page_height.pt)
        if abs(width - A4_WIDTH_PT) > 1.0 or abs(height - A4_HEIGHT_PT) > 1.0:
            issues.append(
                _error(
                    "page_size",
                    f"第{section_number}节页面不是A4",
                    path,
                    section=section_number,
                    width=width,
                    height=height,
                )
            )

    counts = [len(page) for page in pages]
    for page_number, page_lines in enumerate(pages, start=1):
        if len(page_lines) < expected_rows:
            issues.append(
                _error(
                    "lines_per_page",
                    f"第{page_number}页非空段落不足{expected_rows}行，实际{len(page_lines)}行",
                    path,
                    page=page_number,
                    expected=expected_rows,
                    actual=len(page_lines),
                )
            )
        if policy.get("line_numbers") == "forbidden" and _docx_has_line_numbers(
            page_lines, expected_rows
        ):
            issues.append(
                _error(
                    "line_numbers",
                    f"第{page_number}页检测到连续正文行号",
                    path,
                    page=page_number,
                )
            )

    issues.append(
        _warning(
            "render_required",
            "DOCX的最终视觉行数仍须转换为PDF后复核，本检查只验证显式分页和非空段落",
            path,
        )
    )
    return {
        "path": str(path),
        "format": "docx",
        "material_type": material_type,
        "page_count": len(pages),
        "minimum_body_rows": min(counts) if counts else 0,
        "maximum_body_rows": max(counts) if counts else 0,
        "passed": not any(issue["severity"] == "error" for issue in issues),
        "issues": issues,
    }


def _plain_markdown(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[`*_>#|\[\]()-]", "", text)
    return re.sub(r"\s+", "", text)


def _extract_markdown_section(text: str, heading_name: str) -> str | None:
    lines = text.splitlines()
    start = None
    level = None
    heading_style = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and heading_name in match.group(2):
            start = index + 1
            level = len(match.group(1))
            heading_style = "hash"
            break
        bold_match = re.match(r"^\*\*(.+?)\*\*\s*$", line)
        if bold_match and heading_name in bold_match.group(1):
            start = index + 1
            heading_style = "bold"
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start, len(lines)):
        if heading_style == "hash":
            match = re.match(r"^(#{1,6})\s+", lines[index])
            if match and len(match.group(1)) <= int(level):
                end = index
                break
        elif re.match(r"^\*\*(.+?)\*\*\s*$", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def _extract_markdown_tail(text: str, heading_name: str) -> str | None:
    """Return everything after a Markdown or bold-label heading."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        hash_heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        bold_heading = re.match(r"^\*\*(.+?)\*\*\s*$", line)
        title = hash_heading.group(2) if hash_heading else (
            bold_heading.group(1) if bold_heading else ""
        )
        if heading_name in title:
            return "\n".join(lines[index + 1:])
    return None


def _substantive_duplicates(text: str) -> list[str]:
    blocks = []
    for block in re.split(r"\n\s*\n", text):
        normalized = _plain_markdown(block)
        if len(normalized) >= 30:
            # Number-only substitutions are a common looping-template pattern.
            # Canonicalizing them catches repeated content disguised by item indexes.
            blocks.append(re.sub(r"\d+(?:\.\d+)*", "<N>", normalized))
    return [block for block, count in Counter(blocks).items() if count > 1]


def audit_markdown(path: Path, requirements: dict[str, Any]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    material_type = classify_material(path)
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    if material_type in {"document", "feature_summary"}:
        forbidden = requirements["generation"].get("forbidden_placeholder_markers", [])
        for marker in forbidden:
            if marker.lower() in text.lower():
                issues.append(
                    _error(
                        "placeholder",
                        f"检测到禁止的占位标记：{marker}",
                        path,
                        marker=marker,
                    )
                )

    if material_type == "feature_summary":
        rule = requirements["document_material"]["main_function_section"]
        section = _extract_markdown_section(text, rule["heading"])
        if section is None:
            issues.append(
                _error(
                    "missing_main_function_section",
                    f"缺少《{rule['heading']}》章节",
                    path,
                )
            )
        else:
            characters = len(_plain_markdown(section))
            metrics["main_function_characters"] = characters
            if not rule["minimum_characters"] <= characters <= rule["maximum_characters"]:
                issues.append(
                    _error(
                        "main_function_characters",
                        f"《{rule['heading']}》应为{rule['minimum_characters']}至"
                        f"{rule['maximum_characters']}字，实际{characters}字",
                        path,
                        actual=characters,
                    )
                )

    if material_type == "document" or "软件说明书" in path.stem:
        rule = requirements["document_material"]["supplemental_operation_tips"]
        section = _extract_markdown_tail(text, rule["heading"])
        if section is None:
            issues.append(
                _error(
                    "missing_supplemental_tips",
                    f"缺少“{rule['heading']}”章节",
                    path,
                )
            )
        else:
            duplicates = _substantive_duplicates(section)
            metrics["supplemental_duplicate_blocks"] = len(duplicates)
            if duplicates:
                issues.append(
                    _error(
                        "repeated_supplemental_content",
                        f"“{rule['heading']}”之后存在{len(duplicates)}个重复内容块",
                        path,
                        duplicate_count=len(duplicates),
                        duplicate_examples=[item[:160] for item in duplicates[:5]],
                    )
                )
            has_structure = bool(
                re.search(r"^#{2,6}\s+", section, flags=re.MULTILINE)
                or re.search(r"^\s*(?:\d+[.、]|[（(]\d+[）)])", section, flags=re.MULTILINE)
            )
            if not has_structure:
                issues.append(
                    _error(
                        "supplemental_format",
                        "“补充操作提示”之后必须分段并使用章节或步骤编号",
                        path,
                    )
                )

    return {
        "path": str(path),
        "format": "markdown",
        "material_type": material_type,
        "passed": not any(issue["severity"] == "error" for issue in issues),
        "metrics": metrics,
        "issues": issues,
    }


def audit_material_directory(
    directory: Path | str,
    requirements: dict[str, Any] | None = None,
    require_rendered_pair: bool = True,
) -> dict[str, Any]:
    root = Path(directory)
    rules = requirements or load_material_requirements()
    if not root.is_dir():
        raise FileNotFoundError(f"材料目录不存在：{root}")

    results: list[dict[str, Any]] = []
    rendered_pdf_types: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        material_type = classify_material(path)
        if material_type is None:
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf" and material_type in {"program", "document"}:
                results.append(audit_pdf(path, material_type, rules))
                rendered_pdf_types.add(material_type)
            elif suffix == ".docx" and material_type in {"program", "document"}:
                results.append(audit_docx(path, material_type, rules))
            elif suffix in {".md", ".markdown"}:
                results.append(audit_markdown(path, rules))
        except Exception as exc:
            results.append(
                {
                    "path": str(path),
                    "format": suffix.lstrip("."),
                    "material_type": material_type,
                    "passed": False,
                    "issues": [
                        _error("unreadable_material", f"材料无法读取：{exc}", path)
                    ],
                }
            )

    directory_issues: list[dict[str, Any]] = []
    if require_rendered_pair:
        for required_type, label in (("program", "程序鉴别材料"), ("document", "文档鉴别材料")):
            if required_type not in rendered_pdf_types:
                directory_issues.append(
                    _error(
                        "missing_rendered_material",
                        f"目录中缺少可核验最终页数的{label}PDF",
                        root,
                        material_type=required_type,
                    )
                )

    errors = directory_issues + [
        issue
        for result in results
        for issue in result["issues"]
        if issue["severity"] == "error"
    ]
    warnings = [
        issue
        for result in results
        for issue in result["issues"]
        if issue["severity"] == "warning"
    ]
    return {
        "directory": str(root.resolve()),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "materials": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="待检查的单个软著材料目录")
    parser.add_argument(
        "--requirements",
        type=Path,
        default=None,
        help="材料要求YAML，默认使用 copyright/config/material-requirements.yaml",
    )
    parser.add_argument("--output", type=Path, help="可选JSON报告路径")
    args = parser.parse_args()

    requirements = load_material_requirements(args.requirements)
    report = audit_material_directory(args.directory, requirements)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
