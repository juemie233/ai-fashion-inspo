"""人物简介生成路由的共用逻辑（`routers/_person_bio.py`）单测。

博主与模特两个路由共用这个函数，出错时前端只看得到一句话，所以三类语义必须钉住：

1. **没标签就不生成**：返回 400 并说清「先去关联素材」——否则模型会凭空编风格；
2. **只喂标签画像**：不读取抖音签名（「从抖音博主获取简介」能力已下线），
   用户手填/AI 生成过的简介也不回喂，避免模型自我引用；
3. **生成失败转 502**：`BioGenerationError` 是模型不可达/返回空，属于上游故障，
   不能吞成 500 或空简介。

`platform_label` 的映射也在这里（小红书/抖音/其他），未知平台回退原始值。
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import _person_bio as pb
from app.services.person_bio import BioGenerationError, PersonBioInputs


class _FakeService:
    """PersonServiceBase 的最小替身：只用到 label / get / style_profile。"""

    label = "穿搭博主"

    def __init__(self, person, style_profile):
        self.person = person
        self.style_profile_data = style_profile

    async def get(self, _db, _person_id):
        return self.person

    async def style_profile(self, _db, _person_id):
        return self.style_profile_data


def _person(**overrides):
    base = {
        "name": "小葵",
        "platform": "douyin",
        "ip_location": "上海",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_STYLE = {
    "top_tags": [{"name": "法式", "category": "风格", "count": 5}],
    "by_category": {"风格": 5},
}


@pytest.fixture
def captured(monkeypatch):
    """拦截 generate_person_bio，记录传给模型的上下文并返回固定简介。"""
    seen: dict[str, PersonBioInputs] = {}

    async def _fake(inputs: PersonBioInputs) -> str:
        seen["inputs"] = inputs
        return "生成的简介"

    monkeypatch.setattr(pb, "generate_person_bio", _fake)
    return seen


class TestSuccess:
    async def test_generates_from_tags_only(self, captured):
        service = _FakeService(_person(), _STYLE)

        result = await pb.generate_person_bio_endpoint(db=None, service=service, person_id=7)

        assert result == {"bio": "生成的简介"}
        inputs = captured["inputs"]
        assert inputs.kind_label == "穿搭博主"
        assert inputs.name == "小葵"
        assert inputs.platform_label == "抖音"
        assert inputs.ip_location == "上海"
        assert inputs.top_tags == _STYLE["top_tags"]
        assert inputs.by_category == {"风格": 5}


class TestPlatformLabel:
    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("xiaohongshu", "小红书"),
            ("douyin", "抖音"),
            ("other", "其他"),
            ("weibo", "weibo"),  # 未登记的平台：原样透传，方便发现新平台
            (None, "其他"),  # 空值：按「其他」
        ],
    )
    async def test_platform_mapping(self, captured, platform, expected):
        service = _FakeService(_person(platform=platform), _STYLE)

        await pb.generate_person_bio_endpoint(db=None, service=service, person_id=1)

        assert captured["inputs"].platform_label == expected


class TestFailures:
    async def test_no_tags_raises_400_with_actionable_hint(self, captured):
        service = _FakeService(_person(), {"top_tags": [], "by_category": {}})

        with pytest.raises(HTTPException) as exc_info:
            await pb.generate_person_bio_endpoint(db=None, service=service, person_id=3)

        assert exc_info.value.status_code == 400
        assert "还没有任何标签" in exc_info.value.detail
        assert "穿搭博主" in exc_info.value.detail  # 博主/模特文案不同，取自 service.label
        assert captured == {}  # 没调模型

    async def test_missing_top_tags_key_also_raises(self, captured):
        service = _FakeService(_person(), {"by_category": {}})

        with pytest.raises(HTTPException) as exc_info:
            await pb.generate_person_bio_endpoint(db=None, service=service, person_id=3)

        assert exc_info.value.status_code == 400

    async def test_generation_error_becomes_502(self, monkeypatch):
        async def _boom(_inputs):
            raise BioGenerationError("模型不可达")

        monkeypatch.setattr(pb, "generate_person_bio", _boom)
        service = _FakeService(_person(), _STYLE)

        with pytest.raises(HTTPException) as exc_info:
            await pb.generate_person_bio_endpoint(db=None, service=service, person_id=3)

        assert exc_info.value.status_code == 502
        assert exc_info.value.detail == "模型不可达"
