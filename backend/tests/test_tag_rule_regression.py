"""标签规则回归集：用真实模型响应锁定「落库合规规则」的确定性行为。

背景：合规规则（``app/utils/tag_compliance.py``）会丢弃袜/鞋/裙裸词与缺长度
丝袜泛称。历史上每轮调整提示词或规则后，都出现过「改 A 坏 B」——本测试把
线上真实踩过的坑固化成用例，改动规则后立刻能发现回归。

用例来源：``backend/scripts/export_tag_rule_case.py`` 从 ``ai_analysis_log``
导出真实 ``raw_response`` 骨架，人工补 ``expect`` 真值后放入
``tests/fixtures/tag_rule_cases/*.json``：

- ``expect.produce``：该响应中必须产出的标签（漏一个即失败）
- ``expect.drop``：该响应中必须不产出的标签（出现即失败）

只锁「确定性落库规则」，不评价提示词质量（后者靠提示词版本质量看板与人工抽检）。
"""

import json
from pathlib import Path

import pytest

from app.services.ai_parser import parse_analysis_response
from app.services.ai_tag_saver import iter_extracted_tags

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tag_rule_cases"


def _load_cases() -> list[pytest.param]:
    """加载全部回归用例（按文件名排序，保证执行顺序稳定）。"""
    cases: list[pytest.param] = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        case_id = data.get("case_id") or path.stem
        cases.append(pytest.param(data, id=case_id))
    return cases


CASES = _load_cases()


def _produced_names(raw_response: object) -> set[str]:
    """解析一份或多份原始响应，返回当前规则下的标签产出集合。"""
    raws = raw_response if isinstance(raw_response, list) else [raw_response]
    produced: set[str] = set()
    for raw in raws:
        tags_data = parse_analysis_response(raw or "")
        if not tags_data:
            continue
        produced |= {name for name, _category, _conf in iter_extracted_tags(tags_data)}
    return produced


@pytest.mark.skipif(not CASES, reason="尚无回归用例 fixture（见 scripts/export_tag_rule_case.py）")
@pytest.mark.parametrize("case", CASES)
def test_tag_rule_case(case: dict) -> None:
    """断言该真实响应在当前规则下：该产出的都在、该丢弃的都不在。"""
    expect = case.get("expect") or {}
    produce = expect.get("produce") or []
    drop = expect.get("drop") or []
    assert produce or drop, f"用例 {case.get('case_id')} 的 expect 为空，请补真值"

    produced = _produced_names(case.get("raw_response"))

    missing = [name for name in produce if name not in produced]
    assert not missing, (
        f"[{case.get('case_id')}] 应产出但缺失: {missing}\n"
        f"实际产出: {sorted(produced)}"
    )
    leaked = [name for name in drop if name in produced]
    assert not leaked, (
        f"[{case.get('case_id')}] 应丢弃但出现: {leaked}\n"
        f"实际产出: {sorted(produced)}"
    )


@pytest.mark.skipif(not CASES, reason="尚无回归用例 fixture")
def test_fixture_schema() -> None:
    """用例结构校验：字段齐全、expect 非空、case_id 唯一。"""
    seen: set[str] = set()
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        case_id = data.get("case_id")
        assert case_id, f"{path.name} 缺少 case_id"
        assert case_id not in seen, f"case_id 重复: {case_id}"
        seen.add(case_id)
        assert data.get("raw_response"), f"{case_id} 缺少 raw_response"
        expect = data.get("expect") or {}
        assert expect.get("produce") or expect.get("drop"), f"{case_id} 的 expect 为空"
