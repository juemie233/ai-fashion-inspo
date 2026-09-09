"""从真实分析日志导出「标签规则回归用例」骨架。

用途：把线上发现的误标案例固化成回归用例（见 tests/test_tag_rule_regression.py）。
脚本读取 ``ai_analysis_log.raw_response``，按当前合规规则列出「会产出」与
「会被丢弃」的标签候选，写出 JSON 骨架；人工补 ``expect``（真值）后即生效。

用法（backend 目录下）::

    python scripts/export_tag_rule_case.py --log-id 10707 --case-id shoe-bare-2026-09
    python scripts/export_tag_rule_case.py --log-id 10707 10859 --case-id silk-2026-09
    python scripts/export_tag_rule_case.py --log-id 10707 --stdout   # 只打印不写文件

骨架中的 expect 默认留空（produce/drop 均为空数组），需人工填写：
- produce：该响应中**必须产出**的标签（漏一个即回归失败）
- drop：该响应中**必须不产出**的标签（出现即回归失败）
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行时找到 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import async_session  # noqa: E402
from app.models.inspiration import AIAnalysisLog  # noqa: E402
from app.services.ai_parser import extract_tag_names, parse_analysis_response  # noqa: E402
from app.services.ai_tag_saver import iter_extracted_tags  # noqa: E402
from app.utils.tag_compliance import classify_noncompliant  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tag_rule_cases"


async def _load_log(log_id: int) -> AIAnalysisLog | None:
    """读取一条分析日志。"""
    async with async_session() as db:
        return await db.get(AIAnalysisLog, log_id)


def _collect_names(tags_data: dict) -> list[str]:
    """收集响应中所有「名称候选」（含会被规则丢弃的），用于人工标注参考。"""
    names: list[str] = []
    items = tags_data.get("items") or []
    if not isinstance(items, list):
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_type = item.get("type", "")
        if isinstance(raw_type, list):
            raw_type = raw_type[0] if raw_type else ""
        if str(raw_type).strip():
            names.append(str(raw_type).strip())
        features = item.get("features", [])
        if isinstance(features, str):
            features = [
                p.strip()
                for p in features.replace("，", ",").replace("、", ",").split(",")
                if p.strip()
            ]
        for feat in features if isinstance(features, list) else []:
            for name in extract_tag_names(feat):
                if name:
                    names.append(name)
    for key in ("style", "fit", "design_detail", "material", "attributes",
                "atmosphere", "expression", "leg_posture"):
        values = tags_data.get(key) or []
        if not isinstance(values, list):
            values = [values] if values else []
        for value in values:
            for name in extract_tag_names(value):
                if name:
                    names.append(name)
    return list(dict.fromkeys(names))


async def build_case(log_ids: list[int], case_id: str) -> dict:
    """按日志构造回归用例骨架。"""
    raw_responses: list[str] = []
    meta: list[dict] = []
    for log_id in log_ids:
        log = await _load_log(log_id)
        if log is None:
            raise SystemExit(f"日志不存在: #{log_id}")
        raw_responses.append(log.raw_response or "")
        meta.append({
            "log_id": log.id,
            "model_name": log.model_name,
            "prompt_version": log.prompt_version,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    # 单条日志：raw_response 直接落字段；多条：拼成数组由测试逐条解析
    if len(raw_responses) == 1:
        raw_field: object = raw_responses[0]
    else:
        raw_field = raw_responses

    # 打印当前规则下的产出/丢弃候选，供人工补 expect
    produced: set[str] = set()
    dropped: set[str] = set()
    for raw in raw_responses:
        tags_data = parse_analysis_response(raw or "")
        if not tags_data:
            continue
        for name, _cat, _conf in iter_extracted_tags(tags_data):
            produced.add(name)
        for name in _collect_names(tags_data):
            if classify_noncompliant(name):
                dropped.add(name)

    print(f"用例 {case_id!r}：")
    print(f"  当前规则产出候选（供 expect.produce 参考）: {sorted(produced)}")
    print(f"  当前规则丢弃候选（供 expect.drop 参考）:    {sorted(dropped)}")
    print("  ⚠️ expect 需人工确认真值后填写（勿直接照抄候选）")

    return {
        "case_id": case_id,
        "origin": meta,
        "note": "",
        "raw_response": raw_field,
        "expect": {"produce": [], "drop": []},
    }


async def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="导出标签规则回归用例骨架")
    parser.add_argument("--log-id", type=int, nargs="+", required=True,
                        help="一个或多个分析日志 ID（多条会合并为一个用例）")
    parser.add_argument("--case-id", required=True, help="用例标识（英文短横线）")
    parser.add_argument("--stdout", action="store_true", help="只打印不写文件")
    args = parser.parse_args()

    case = await build_case(args.log_id, args.case_id)
    payload = json.dumps(case, ensure_ascii=False, indent=2)

    if args.stdout:
        print(payload)
        return

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURE_DIR / f"{args.case_id}.json"
    out.write_text(payload + "\n", encoding="utf-8")
    print(f"已写入 {out}")
    print("请补全 expect.produce / expect.drop 后运行 pytest tests/test_tag_rule_regression.py")


if __name__ == "__main__":
    asyncio.run(main())
