#!/usr/bin/env python3
"""Load material rules and enforce immutable submitted assets."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


DEFAULT_REQUIREMENTS_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "material-requirements.yaml"
)
DEFAULT_PROTECTED_ASSETS_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "material-protected-assets.local.yaml"
)


class RequirementError(ValueError):
    """Raised when the material requirement configuration is invalid."""


class FrozenAssetError(PermissionError):
    """Raised when generation would modify an immutable submitted asset."""


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise RequirementError(f"配置必须是YAML对象：{path}")
    return value


def load_material_requirements(
    path: Path | str | None = None,
    protected_assets_path: Path | str | None = None,
) -> dict[str, Any]:
    explicit_requirements_path = path is not None
    config_path = Path(path) if path else DEFAULT_REQUIREMENTS_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"材料要求配置不存在：{config_path}")
    requirements = _load_yaml_mapping(config_path)
    requirements["_config_path"] = str(config_path.resolve())

    overlay_path = Path(protected_assets_path) if protected_assets_path else None
    if overlay_path is None and not explicit_requirements_path and DEFAULT_PROTECTED_ASSETS_PATH.exists():
        overlay_path = DEFAULT_PROTECTED_ASSETS_PATH
    if overlay_path is not None:
        if not overlay_path.exists():
            raise FileNotFoundError(f"冻结材料配置不存在：{overlay_path}")
        overlay = _load_yaml_mapping(overlay_path)
        requirements["protected_assets"] = overlay.get("protected_assets", [])
        requirements["_protected_assets_path"] = str(overlay_path.resolve())
    else:
        requirements.setdefault("protected_assets", [])

    validate_requirement_config(requirements)
    return requirements


def validate_requirement_config(requirements: dict[str, Any]) -> None:
    errors: list[str] = []
    selection = requirements.get("page_selection", {})
    program = requirements.get("program_material", {})
    document = requirements.get("document_material", {})
    main_function = document.get("main_function_section", {})
    supplemental = document.get("supplemental_operation_tips", {})
    generation = requirements.get("generation", {})

    if selection.get("page_size") != "A4":
        errors.append("page_selection.page_size 必须为 A4")
    if selection.get("total_pages") != 60:
        errors.append("page_selection.total_pages 必须为 60")
    if selection.get("front_pages") != 30 or selection.get("back_pages") != 30:
        errors.append("鉴别材料必须由前30页和后30页组成")
    if selection.get("front_pages", 0) + selection.get("back_pages", 0) != selection.get(
        "total_pages"
    ):
        errors.append("前后页数之和必须等于总页数")
    if program.get("lines_per_page") != 50:
        errors.append("程序鉴别材料每页必须为50个有效代码行")
    if document.get("lines_per_page") != 30:
        errors.append("文档鉴别材料每页必须为30个有效正文行")
    if program.get("line_numbers") != "forbidden":
        errors.append("程序鉴别材料必须禁止正文行号")
    if document.get("line_numbers") != "forbidden":
        errors.append("文档鉴别材料必须禁止正文行号")
    if program.get("blank_lines_count") is not False:
        errors.append("程序空白行不得计入有效行")
    if document.get("blank_lines_count") is not False:
        errors.append("文档空白行不得计入有效行")
    if program.get("pure_repository_source_only") is not True:
        errors.append("程序鉴别材料必须只使用真实仓库源代码")
    if program.get("explanatory_filler") != "forbidden":
        errors.append("程序鉴别材料必须禁止说明性填充")
    if program.get("max_print_columns") != 100:
        errors.append("程序代码每个打印行最多100个等宽列，避免PDF横向裁切")
    if supplemental.get("heading") != "补充操作提示":
        errors.append("必须配置“补充操作提示”章节规则")
    if supplemental.get("formatted_sections") != "required":
        errors.append("“补充操作提示”之后必须分段并编号")
    if supplemental.get("repeated_substantive_content") != "forbidden":
        errors.append("“补充操作提示”之后必须禁止重复实质内容")
    if main_function.get("minimum_characters") != 500:
        errors.append("《软件的主要功能》最少字数必须为500")
    if main_function.get("maximum_characters") != 1300:
        errors.append("《软件的主要功能》最多字数必须为1300")
    if generation.get("insufficient_material") != "fail_instead_of_blank_or_repeated_padding":
        errors.append("材料不足时必须失败，禁止空白或重复填充")
    if generation.get("output_formats") != ["markdown", "pdf"]:
        errors.append("材料主流程必须严格为 Markdown -> PDF")
    for label, policy, expected in (
        ("程序", program, 8.15),
        ("文档", document, 10.15),
    ):
        font = policy.get("font", {})
        if font.get("strategy") != "maximize_without_increasing_page_count":
            errors.append(f"{label}鉴别材料字号策略必须为不增页前提下最大化")
        if float(font.get("generation_point_size", 0)) != expected:
            errors.append(f"{label}鉴别材料生成字号必须为{expected:g}磅")
    for item in requirements.get("protected_assets", []):
        if item.get("immutable") is not True:
            errors.append(f"冻结项 {item.get('copyright_id', '?')} 必须设置 immutable: true")
        if not item.get("expected_digest"):
            errors.append(f"冻结项 {item.get('copyright_id', '?')} 缺少 expected_digest")

    if errors:
        raise RequirementError("；".join(errors))


def compute_openssl_directory_digest(directory: Path | str) -> str:
    """Compute a stable recursive digest compatible with the existing flat layout."""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"冻结目录不存在：{root}")
    digest_lines: list[str] = []
    for file_path in sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
    ):
        file_digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        relative_name = file_path.relative_to(root.parent).as_posix()
        digest_lines.append(f"SHA2-256({relative_name})= {file_digest}\n")
    return hashlib.sha256("".join(digest_lines).encode("utf-8")).hexdigest()


def _asset_path(asset: dict[str, Any], requirements: dict[str, Any]) -> Path:
    path = Path(asset["path"]).expanduser()
    if path.is_absolute():
        return path.resolve()
    source_path = requirements.get("_protected_assets_path") or requirements["_config_path"]
    return (Path(source_path).parent / path).resolve()


def audit_protected_assets(requirements: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    passed = True
    for asset in requirements.get("protected_assets", []):
        path = _asset_path(asset, requirements)
        item = {
            "copyright_id": str(asset.get("copyright_id", "")),
            "software_name": asset.get("software_name", ""),
            "path": str(path),
            "expected_digest": asset.get("expected_digest"),
            "actual_digest": None,
            "passed": False,
        }
        try:
            if asset.get("digest_algorithm") != "openssl-directory-v1":
                raise RequirementError(
                    f"不支持的冻结摘要算法：{asset.get('digest_algorithm')}"
                )
            item["actual_digest"] = compute_openssl_directory_digest(path)
            item["passed"] = item["actual_digest"] == item["expected_digest"]
        except (FileNotFoundError, RequirementError) as exc:
            item["error"] = str(exc)
        passed = passed and item["passed"]
        results.append(item)
    return {"passed": passed, "assets": results}


def assert_generation_allowed(
    copyright_id: str,
    software_name: str,
    requirements: dict[str, Any],
) -> None:
    normalized_id = str(copyright_id).strip()
    normalized_name = software_name.strip()
    for asset in requirements.get("protected_assets", []):
        protected_id = str(asset.get("copyright_id", "")).strip()
        protected_name = str(asset.get("software_name", "")).strip()
        id_matches = normalized_id == protected_id or normalized_id.startswith(f"{protected_id}-")
        name_matches = normalized_name == protected_name
        if asset.get("immutable") and (id_matches or name_matches):
            raise FrozenAssetError(
                f"{protected_id}《{protected_name}》已提交并冻结，禁止重新生成或覆盖材料"
            )


def assert_output_path_allowed(
    output_path: Path | str,
    requirements: dict[str, Any],
) -> None:
    """Reject writes whose destination is inside an immutable asset directory."""
    target = Path(output_path).expanduser().resolve()
    for asset in requirements.get("protected_assets", []):
        if not asset.get("immutable"):
            continue
        protected = _asset_path(asset, requirements)
        if target == protected or protected in target.parents:
            protected_id = str(asset.get("copyright_id", "")).strip()
            protected_name = str(asset.get("software_name", "")).strip()
            raise FrozenAssetError(
                f"输出路径位于已冻结的{protected_id}《{protected_name}》目录内，禁止写入：{target}"
            )


def requirement_summary(requirements: dict[str, Any]) -> dict[str, Any]:
    selection = requirements["page_selection"]
    program = requirements["program_material"]
    document = requirements["document_material"]
    main_function = document["main_function_section"]
    return {
        "page_size": selection["page_size"],
        "total_pages": selection["total_pages"],
        "front_pages": selection["front_pages"],
        "back_pages": selection["back_pages"],
        "program_lines_per_page": program["lines_per_page"],
        "document_lines_per_page": document["lines_per_page"],
        "line_numbers": "forbidden",
        "program_font_point_size": program["font"]["generation_point_size"],
        "document_font_point_size": document["font"]["generation_point_size"],
        "main_function_characters": [
            main_function["minimum_characters"],
            main_function["maximum_characters"],
        ],
        "protected_asset_count": len(requirements.get("protected_assets", [])),
    }
