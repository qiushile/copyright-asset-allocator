# 架构与业务不变量基线报告（Phase 0）

生成时间：2026-08-17（automation-3 首轮基线）

## 1. 行为基线

| 项目 | 值 |
|---|---|
| Python | 3.9.20（pyenv；运行时依赖已全局安装，含 weasyprint 66.0） |
| 测试框架 | pytest 8.4.2（项目本地 `.venv`，system-site-packages） |
| 基线测试 | `pytest tests/` → **17 passed**（全部集中在材料/PDF/冻结/历史扫描） |
| CLI 命令 | init, scan, allocate, validate, workspace, detect, generate, audit, requirements, material-audit, mark-used(mark/import/report/clear), ownership(add-member/remove-member/list-members/load-config/analyze), set-since(set/show/clear), auto-split |
| 核心引擎测试 | **无**（database / allocate / auto-split 约束 / validate / detect / ownership 均无单元测试） |

## 2. 数据流

```
Git Repository(locked commit)
  → scan（git ls-tree / git show 读取已提交树，非 worktree）
  → repositories / commits / files（inventory）
  → mark-used / ownership analyze / set-since（资格过滤数据）
  → eligible code（used 排除 + team 资格 + 时间过滤）
  → auto-split 或 copyrights.yaml（软著定义）
  → allocate（写入 allocations）
  → validate → workspace → detect → generate → material-audit → audit
```

## 3. 数据库 Schema 摘要（assets.db）

repositories, commits, files, copyrights, allocations, excluded_files,
duplicate_reports, used_markdown_code(历史), used_files, team_members,
code_ownership, file_team_stats, project_settings。

关键硬约束：

- `allocations.UNIQUE(commit_id, file_id)` —— 同一 Commit 下同一文件只能分配给一个软著。
- `files.UNIQUE(commit_id, file_path)`、`commits.UNIQUE(repository_id, commit_hash)`。
- `used_files.UNIQUE(commit_id, file_id, copyright_name)`。
- 无 `schema_version`（P1：需要迁移机制）。

## 4. 业务不变量 → 责任代码位置

| # | 不变量 | 责任位置 |
|---|---|---|
| I1 | 同 commit + 同真实文件只能分配给一个软著 | `database.py` allocations UNIQUE + `allocate_file()`；`validate_allocations.py` 复查 |
| I2 | 文件内容必须来自锁定的 Git Commit（不读脏 worktree） | `scan_repository.py`：`get_commit_files`/`read_commit_file`（git ls-tree + git show） |
| I3 | 重复 scan 幂等 | `insert_file` ON CONFLICT DO UPDATE；`get_or_create_*` |
| I4 | 已用于其他软著的代码默认排除 | `allocate_files.py`/`auto_split.py` 中 `get_used_file_ids` + `skip_used=True` 默认 |
| I5 | 非团队主导代码默认排除 | `file_team_stats.is_eligible` + `get_team_eligible_file_ids`（未分析时放行并返回 None） |
| I6 | 三级重合检测（路径/哈希/相似） | `detect_duplicates.py` |
| I7 | 隔离工作区只含该软著已分配文件 | `build_workspace.py` + SOURCE_MANIFEST.json |
| I8 | 材料硬规则（60页/行数/字号/重复/冻结）全部来自配置 | `material-requirements.yaml` + `material_requirements.py` + `validate_materials.py` |
| I9 | 已提交冻结材料不可重新生成/覆盖 | `material_requirements.py` 冻结摘要校验（生成前/写入前/审计时） |
| I10 | 最终目录摘要一致 | `final_audit.py` |

## 5. 本轮确认的缺陷（Findings）

- **[P0] allocate_files.py 隐式顺序依赖**：文件按 `copyrights_config` YAML 顺序取第一个匹配
  （`for cr_config in copyrights_config: ... break`）。`services/**` 与 `services/parser/**`
  同时匹配时静默分给前者。conflicts 只记录数据库层冲突，不记录多规则匹配歧义。
- **[P0] auto_split.py `estimate_copyright_count`**：
  - `total_lines < min_lines → return 1`：静默产出低于最低行数的软著，无 infeasible 状态。
  - `max_lines` 分支公式 `min(n_by_min, max(n_by_max, n_by_min))` 恒等于 `n_by_min`，
    max_lines 对数量估计实际失效；`greedy_bin_pack` 也从不检查 max_lines。
  - `--count N` 不可满足时（underflow bin 合并）静默产出少于 N 个软著并按成功返回。
  - 单一超大不可拆模块无 `oversized_atomic_module` 报告。
- **[P0] 核心引擎无测试**：DB 不变量、分配、归属、去重均无回归保护。
- **[P1] 无 decision evidence**：文件只有 allocated/skipped 结果，无法回答"为什么分给它"。
- **[P1] 无 run manifest**：无法证明"哪个 commit + 哪套配置 + 哪组过滤"产生当前材料。
- **[P1] build_workspace 从 worktree 复制**（有哈希校验兜底，但应改为从锁定 commit 读取；
  且无 symlink/路径逃逸防护）。
- **[P1] 无 schema_version**；`project_settings`/`team_members`/since 为全局而非 repository scoped
  （多仓库下会互相污染）。
- **[P1] detect_duplicates 相似检测仅输出计数**，缺少人工复核所需的行级证据。

## 6. 本轮范围决策

- 先修 P0（歧义、auto-split 约束语义）并补齐核心引擎测试，再做最高价值 P1：
  decision evidence、run-manifest、commit 一致性、workspace 从锁定 commit 读取 + 路径逃逸防护。
- 材料规则链路（I8/I9/I10）不改动，仅保护。
- 不引入 priority 机制（显式配置留待后续迭代）；本轮只消灭隐式顺序依赖。
