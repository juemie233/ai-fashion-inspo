"""采集主入口的三个纯函数：配置解析、执行计划、漏斗汇总。

为什么单独测这两块：`run_scraper_sync` 是一个 700+ 行的巨型函数（覆盖率 0%），
里面的**配置校验**与**统计口径**却是最容易出错、也最不该靠「跑一次采集看看」来验证的：

- 配置校验直接决定「任务是被拒绝还是开始跑」，判错了会白跑一次带风控风险的采集；
- 漏斗汇总是人工判断「这次采集成不成功」的唯一凭据，数字错位比报错更危险
  （历史事故 #46 就是数字与写入对不上，没人发现）。

三个函数都不碰网络/浏览器/数据库，可以放心单测。
"""

from __future__ import annotations

import random
import re

import pytest

from scripts.run_scraper import (
    SearchPlan,
    TaskConfig,
    build_search_plan,
    format_funnel_summary,
    parse_task_config,
)


class TestParseTaskConfig:
    """配置解析：关键词清洗、模式判定、缺字段的报错文案。"""

    def test_keywords_are_stripped_and_empty_dropped(self) -> None:
        parsed = parse_task_config({"keywords": [" 春季穿搭 ", "", "   ", "通勤"]}, "xiaohongshu")

        assert parsed.keywords == ["春季穿搭", "通勤"]
        assert parsed.platform == "xiaohongshu"
        assert parsed.is_blogger is False
        assert parsed.blogger_id is None

    def test_max_count_defaults_to_50_and_can_be_overridden(self) -> None:
        assert parse_task_config({"keywords": ["a"]}, "douyin").max_count == 50
        assert parse_task_config({"keywords": ["a"], "max_count": 8}, "douyin").max_count == 8

    @pytest.mark.parametrize("mode_key", ("collect_mode", "mode"))
    def test_blogger_mode_accepts_both_config_keys(self, mode_key: str) -> None:
        """`collect_mode` 是现行键；`mode` 是旧键，历史任务里还有。"""
        parsed = parse_task_config({mode_key: "user", "blogger_id": 7}, "xiaohongshu")

        assert parsed.is_blogger is True
        assert parsed.blogger_id == 7

    def test_blogger_mode_allows_empty_keywords(self) -> None:
        """按博主采集不靠关键词，没有关键词也应通过。"""
        parsed = parse_task_config({"collect_mode": "user", "blogger_id": "12"}, "xiaohongshu")

        assert parsed.keywords == []
        assert parsed.blogger_id == 12

    def test_blogger_mode_without_id_raises(self) -> None:
        with pytest.raises(ValueError, match="按博主采集缺少 blogger_id"):
            parse_task_config({"collect_mode": "user"}, "xiaohongshu")

    def test_search_mode_without_keywords_raises(self) -> None:
        with pytest.raises(ValueError, match="无关键词"):
            parse_task_config({"keywords": ["   "]}, "douyin")

    def test_empty_config_raises_no_keywords(self) -> None:
        with pytest.raises(ValueError, match="无关键词"):
            parse_task_config({}, "douyin")

    def test_default_mode_is_search(self) -> None:
        assert parse_task_config({"keywords": ["x"]}, "douyin").is_blogger is False

    def test_returns_frozen_dataclass(self) -> None:
        parsed = parse_task_config({"keywords": ["x"]}, "douyin")

        assert isinstance(parsed, TaskConfig)
        with pytest.raises(Exception):  # noqa: B017 —— frozen dataclass 赋值必抛
            parsed.max_count = 1  # type: ignore[misc]


class TestFormatFunnelSummary:
    """漏斗汇总：每个数字都要落到报告里，且顺序稳定（人按行读）。"""

    def test_contains_every_counter(self) -> None:
        text = format_funnel_summary(
            items_found=120,
            skipped_existing=30,
            skipped_content_dup=4,
            skipped_non200=2,
            skipped_network=1,
            skipped_video=7,
            items_added=76,
        )

        # 标签后的对齐空格宽度随汉字数变化，用「标签 + 若干空格 + 数字」匹配更稳
        for label, value in (
            ("搜索提取总数", 120),
            ("跨次已存在", 30),
            ("内容MD5重复", 4),
            ("HTTP 非 200", 2),
            ("网络失败", 1),
            ("视频跳过", 7),
            ("★ 最终入库", 76),
        ):
            pattern = re.escape(label) + r":\s+" + str(value) + r"(?:\s|$)"
            assert re.search(pattern, text), f"缺少 {label}={value}"

    def test_renders_box_with_leading_blank_line(self) -> None:
        text = format_funnel_summary(
            items_found=0,
            skipped_existing=0,
            skipped_content_dup=0,
            skipped_non200=0,
            skipped_network=0,
            skipped_video=0,
            items_added=0,
        )

        assert text.startswith("\n  ╔")
        assert text.endswith("  ╚══════════════════════════════════════════")
        assert "采集漏斗汇总" in text


class TestBuildSearchPlan:
    """执行计划：三支语义（按博主 / 恢复断点 / 首次展开）必须稳定。

    计划错了的代价很直接：多跑或漏跑一批「关键词 × 排序」——前者浪费一次带风控风险的
    采集，后者让任务显示完成却没采到东西。
    """

    def test_blogger_mode_has_empty_plan(self) -> None:
        state = build_search_plan(
            keywords=["无所谓"],
            platform="xiaohongshu",
            config={},
            is_blogger=True,
            resume=None,
        )

        assert state == SearchPlan(plan=[], done=0, items_found=0, items_added=0)
        assert state.resumed is False

    def test_douyin_uses_single_general_sort(self) -> None:
        """抖音网页版不支持排序切换：每个关键词只跑综合排序一次。"""
        random.seed(0)
        state = build_search_plan(
            keywords=["通勤", "约会"],
            platform="douyin",
            config={"sort_mode": "latest"},
            is_blogger=False,
            resume=None,
        )

        assert len(state.plan) == 2
        assert {item["s"] for item in state.plan} == {"general"}
        assert {item["k"] for item in state.plan} == {"通勤", "约会"}

    def test_xiaohongshu_default_runs_only_general_sort(self) -> None:
        """未配置 sort_mode 时**只跑综合排序**（不是三种全跑）。"""
        state = build_search_plan(
            keywords=["通勤", "约会"],
            platform="xiaohongshu",
            config={},
            is_blogger=False,
            resume=None,
        )

        assert len(state.plan) == 2
        assert {item["s"] for item in state.plan} == {"general"}

    def test_xiaohongshu_unknown_sort_mode_falls_back_to_all_sorts(self) -> None:
        """配置了认不出来的排序取值 → 回退 SORT_TYPES（关键词数 × 3）。"""
        state = build_search_plan(
            keywords=["通勤", "约会"],
            platform="xiaohongshu",
            config={"sort_mode": "火爆"},
            is_blogger=False,
            resume=None,
        )

        assert len(state.plan) == 6
        assert {item["s"] for item in state.plan} == {
            "general",
            "popularity_descending",
            "time_descending",
        }

    @pytest.mark.parametrize(
        ("sort_mode", "expected"),
        (("latest", "time_descending"), ("popular", "popularity_descending")),
    )
    def test_xiaohongshu_specific_sort(self, sort_mode: str, expected: str) -> None:
        state = build_search_plan(
            keywords=["通勤"],
            platform="xiaohongshu",
            config={"sort_mode": sort_mode},
            is_blogger=False,
            resume=None,
        )

        assert state.plan == [{"k": "通勤", "s": expected}]

    def test_keywords_are_shuffled_once_but_all_kept(self) -> None:
        """打乱只影响顺序，不改变「每个关键词都在计划里」。"""
        state = build_search_plan(
            keywords=["a", "b", "c"],
            platform="douyin",
            config={},
            is_blogger=False,
            resume=None,
        )

        assert [item["k"] for item in state.plan].count("a") == 1
        assert {item["k"] for item in state.plan} == {"a", "b", "c"}

    def test_resume_restores_plan_and_counters(self) -> None:
        """断点恢复：计划原样、done 与累计计数都从 token 里取（跨重启顺序不变）。"""
        saved = {
            "plan": [{"k": "通勤", "s": "general"}, {"k": "约会", "s": "general"}],
            "done": 1,
            "items_found": 42,
            "items_added": 30,
        }

        state = build_search_plan(
            keywords=["会被忽略"],
            platform="douyin",
            config={},
            is_blogger=False,
            resume=saved,
        )

        assert state.plan == saved["plan"]
        assert (state.done, state.items_found, state.items_added) == (1, 42, 30)
        assert state.resumed is True

    def test_resume_with_empty_plan_falls_back_to_fresh(self) -> None:
        """token 里计划为空（历史脏数据）→ 当作首次运行，不原地返回空计划。"""
        state = build_search_plan(
            keywords=["通勤"],
            platform="douyin",
            config={},
            is_blogger=False,
            resume={"plan": [], "done": 3},
        )

        assert state.plan == [{"k": "通勤", "s": "general"}]
        assert state.done == 0
        assert state.resumed is False

    def test_resume_with_invalid_plan_type_falls_back_to_fresh(self) -> None:
        state = build_search_plan(
            keywords=["通勤"],
            platform="douyin",
            config={},
            is_blogger=False,
            resume={"plan": "oops"},
        )

        assert state.plan == [{"k": "通勤", "s": "general"}]
        assert state.resumed is False
