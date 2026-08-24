"""批量高级编辑：正则查找替换 / 前后缀增删 / 格式归一化 / 正则批量合并。

设计要点：
- 规则引擎统一用 Python ``re``，dry-run 预览与执行走同一实现，
  避免 JS/Python 正则差异导致「预览与执行不一致」；
- 规则按请求顺序作为「管道」作用于每个标签（regex_merge 命中即终止该标签管道）；
- 范围（scope）：tag_ids / category / source / search 四种，取并集；
- 冲突策略：rename 类规则产出的新名已存在时，自动转为「合并到该目标标签」
  （与 merge 语义一致）；批内多个标签改名撞名时，先改者占用、后到者合并；
- dry_run=True 只返回逐条预览（不落库）；执行时同批次规则共享 batch_id
  并写操作历史（operation=batch_edit，可回滚）。
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag
from app.services.tag_history_service import new_batch_id, record_history, snapshot_tags

# 连续重复字符（去重字用）
_DUP_RE = re.compile(r"(.)\1+")

# 全角 → 半角映射（全角标点/字母/数字区间 + 全角空格）
_FULLWIDTH_MAP = {i: chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)}
_FULLWIDTH_MAP[0x3000] = " "
_FULLWIDTH_TRANS = str.maketrans(_FULLWIDTH_MAP)


def _fullwidth_to_halfwidth(name: str) -> str:
    """全角转半角。"""
    return name.translate(_FULLWIDTH_TRANS)


def _normalize_name(name: str, ops: list[str]) -> str:
    """格式归一化：按 ops 依次处理（fullwidth_to_halfwidth / trim / dedup_chars）。"""
    s = name
    for op in ops:
        if op == "fullwidth_to_halfwidth":
            s = _fullwidth_to_halfwidth(s)
        elif op == "trim":
            s = s.strip()
        elif op == "dedup_chars":
            s = _DUP_RE.sub(r"\1", s)
    return s


def _apply_affix(name: str, mode: str, text: str) -> str:
    """前后缀增删（add_prefix / remove_prefix / add_suffix / remove_suffix）。"""
    if mode == "add_prefix":
        return f"{text}{name}" if text else name
    if mode == "remove_prefix":
        return name[len(text):] if text and name.startswith(text) else name
    if mode == "add_suffix":
        return f"{name}{text}" if text else name
    if mode == "remove_suffix":
        return name[: -len(text)] if text and name.endswith(text) else name
    return name


def _expand_target_template(template: str, m: re.Match) -> str:
    """展开合并目标模板（兼容 $1 / ${1} / \\1 / \\g<name> 捕获组写法）。

    JS 侧习惯用 $1，Python 的 re.sub 用 \\1 / \\g<1>：这里把 $ 写法
    统一转成 Python 反斜杠写法后再 expand，保证前后端语义一致。
    """
    if not template:
        return ""
    py_template = re.sub(r"\$\{(\d+)\}", r"\\\1", template)  # ${1} → \1
    py_template = re.sub(r"\$(\d+)", r"\\\1", py_template)  # $1 → \1
    return m.expand(py_template).strip()


async def _resolve_scope_tags(db: AsyncSession, scope: dict) -> list[Tag]:
    """解析规则作用范围（tag_ids / category / source / search 取并集）。"""
    query = select(Tag)
    if scope.get("tag_ids"):
        query = query.where(Tag.id.in_(scope["tag_ids"]))
    if scope.get("category"):
        query = query.where(Tag.category == scope["category"])
    if scope.get("source"):
        query = query.where(Tag.source == scope["source"])
    if scope.get("search"):
        query = query.where(Tag.name.contains(scope["search"]))
    result = await db.execute(query)
    return list(result.scalars().all())


def _evaluate_rules(
    name: str,
    tag_id: int,
    rules: list[dict],
    rule_scopes: list[set[int]],
) -> tuple[str | None, str | None]:
    """对单个标签名依次应用规则管道（每条规则只作用于其 scope 内的标签）。

    返回 (最终名或合并目标名, 动作类型)。
    动作类型: "rename"（改名）/ "merge"（合并）/ None（无变化）。
    """
    cur_name = name
    merge_target: str | None = None
    for i, rule in enumerate(rules):
        if tag_id not in rule_scopes[i]:
            continue  # 该标签不在本条规则的 scope 内，跳过
        rtype = rule.get("type")
        if rtype == "regex_merge":
            m = re.search(rule.get("pattern", ""), cur_name)
            if m:
                merge_target = _expand_target_template(
                    rule.get("target_template", "$1"), m
                )
                break  # 命中合并即终止该标签的管道
            continue
        if rtype == "regex_replace":
            cur_name = re.sub(
                rule.get("pattern", ""), rule.get("replacement", ""), cur_name
            )
        elif rtype == "affix":
            cur_name = _apply_affix(
                cur_name, rule.get("mode", ""), rule.get("text", "")
            )
        elif rtype == "normalize":
            cur_name = _normalize_name(cur_name, rule.get("ops", []))

    cur_name = cur_name.strip()
    if merge_target is not None:
        # 目标模板展开为空、或目标就是源自身 → 视为该条无效果
        if merge_target and merge_target != name:
            return (merge_target, "merge")
        return (None, None)
    if cur_name and cur_name != name:
        return (cur_name, "rename")
    return (None, None)


def _build_plans(
    tags: list[Tag],
    rules: list[dict],
    rule_scopes: list[set[int]],
    name_to_id: dict[str, int],
) -> list[dict]:
    """计算全部标签的最终动作计划（含批内撞名解析）。

    每个计划项: {"tag_id", "from", "action": "rename"|"merge"|"skip",
                 "to", "target_id", "conflict"}
    """
    plans: list[dict] = []
    taken: dict[str, int] = dict(name_to_id)  # 当前名 → 归属标签 id（随执行推进更新）
    for tag in tags:
        outcome, action = _evaluate_rules(tag.name, tag.id, rules, rule_scopes)
        if action is None:
            plans.append(
                {"tag_id": tag.id, "from": tag.name, "action": "skip",
                 "to": None, "target_id": None, "conflict": False}
            )
            continue
        if action == "merge":
            target_id = taken.get(outcome)
            if target_id is not None and target_id != tag.id:
                plans.append(
                    {"tag_id": tag.id, "from": tag.name, "action": "merge",
                     "to": outcome, "target_id": target_id, "conflict": True}
                )
                # 源标签将被合并删除，释放旧名
                taken.pop(tag.name, None)
            else:
                # 目标不存在 → 降级为改名（创建合并目标）
                plans.append(
                    {"tag_id": tag.id, "from": tag.name, "action": "rename",
                     "to": outcome, "target_id": None, "conflict": False}
                )
                taken.pop(tag.name, None)
                taken[outcome] = tag.id
            continue
        # rename
        owner = taken.get(outcome)
        if owner is not None and owner != tag.id:
            plans.append(
                {"tag_id": tag.id, "from": tag.name, "action": "merge",
                 "to": outcome, "target_id": owner, "conflict": True}
            )
            taken.pop(tag.name, None)
        else:
            plans.append(
                {"tag_id": tag.id, "from": tag.name, "action": "rename",
                 "to": outcome, "target_id": None, "conflict": False}
            )
            taken.pop(tag.name, None)
            taken[outcome] = tag.id
    return plans


def _format_preview(plans: list[dict], tags_by_id: dict[int, Tag]) -> list[dict]:
    """把计划格式化为前端预览项。"""
    preview = []
    for p in plans:
        if p["action"] == "skip":
            continue
        item = {
            "tag_id": p["tag_id"],
            "from": p["from"],
            "to": p["to"],
            "action": p["action"],
            "conflict": p["conflict"],
        }
        item["target"] = (
            {"id": p["target_id"], "name": p["to"]} if p["target_id"] is not None else None
        )
        preview.append(item)
    return preview


async def batch_edit_tags(
    db: AsyncSession,
    rules: list[dict],
    dry_run: bool = True,
) -> dict:
    """批量高级编辑主入口（dry-run 预览或执行）。

    参数:
        rules: 规则列表（regex_replace / affix / normalize / regex_merge）
        dry_run: True 只返回预览；False 执行并写操作历史

    返回:
        dry_run=True: {"dry_run", "preview", "summary"}
        dry_run=False: {"dry_run", "batch_id", "summary", "errors"}
    """
    if not rules:
        raise ValueError("请至少提供一条规则")

    # 预编译校验正则（提前报错，避免执行到一半失败）
    try:
        for rule in rules:
            if rule.get("type") in ("regex_replace", "regex_merge"):
                re.compile(rule.get("pattern", ""))
    except re.error as e:
        raise ValueError(f"正则表达式无效: {e}")

    # 收集全部作用标签（各规则 scope 取并集），并记录每条规则的 scope 标签集合
    # （规则管道按各自 scope 过滤，避免误作用于其它规则的标签）
    scope_tags: dict[int, Tag] = {}
    rule_scopes: list[set[int]] = []
    for rule in rules:
        rule_tags = await _resolve_scope_tags(db, rule.get("scope") or {})
        rule_scopes.append({t.id for t in rule_tags})
        for tag in rule_tags:
            scope_tags[tag.id] = tag

    # 现有标签名 → id（冲突检测用）
    all_tags = (await db.execute(select(Tag))).scalars().all()
    name_to_id = {t.name: t.id for t in all_tags}

    plans = _build_plans(list(scope_tags.values()), rules, rule_scopes, name_to_id)
    preview = _format_preview(plans, scope_tags)
    renamed = sum(1 for p in plans if p["action"] == "rename")
    merged = sum(1 for p in plans if p["action"] == "merge")
    skipped = sum(1 for p in plans if p["action"] == "skip")
    summary = {"renamed": renamed, "merged": merged, "skipped": skipped, "errors": 0}

    if dry_run:
        return {"dry_run": True, "preview": preview, "summary": summary}

    # ── 执行 ──
    batch_id = new_batch_id("batch-edit")
    errors: list[dict] = []
    rename_ops = [p for p in plans if p["action"] == "rename"]
    merge_ops = [p for p in plans if p["action"] == "merge"]

    # 1. 批量改名（一次提交 + 一条 batch_edit 历史）
    if rename_ops:
        before_snap = await snapshot_tags(db, [p["tag_id"] for p in rename_ops])
        for p in rename_ops:
            scope_tags[p["tag_id"]].name = p["to"]
        await db.flush()
        after_snap = await snapshot_tags(db, [p["tag_id"] for p in rename_ops])
        await record_history(
            db,
            operation="batch_edit",
            before=before_snap,
            after=after_snap,
            batch_id=batch_id,
            meta={"rules": rules},
        )
        await db.commit()

    # 2. 合并（每次合并内部提交，共享 batch_id）
    for p in merge_ops:
        try:
            from app.services.tag_crud import merge_tags

            await merge_tags(db, p["tag_id"], p["target_id"], batch_id=batch_id)
        except Exception as e:  # noqa: BLE001 单条失败不阻断其余
            errors.append({"tag_id": p["tag_id"], "message": str(e)})

    return {
        "dry_run": False,
        "batch_id": batch_id,
        "summary": {**summary, "errors": len(errors)},
        "errors": errors,
    }
