"""fetch_xhs_following 脚本纯函数单元测试（无需浏览器与网络）。

覆盖：用户 ID 提取、IP 属地解析、小红书号解析、去重、CSV 写出。
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fetch_xhs_following as fx  # noqa: E402


class TestExtractUserIdFromHref:
    """从个人主页链接提取用户 ID。"""

    def test_standard_relative_link(self):
        assert fx.extract_user_id_from_href("/user/profile/5e3a2b1c9f4d8e7a") == "5e3a2b1c9f4d8e7a"

    def test_with_query(self):
        assert fx.extract_user_id_from_href("/user/profile/abc123?tab=follow") == "abc123"

    def test_full_url(self):
        assert (
            fx.extract_user_id_from_href("https://www.xiaohongshu.com/user/profile/xyz789")
            == "xyz789"
        )

    def test_hex_uid(self):
        assert fx.extract_user_id_from_href("/user/profile/5a1f2b3c4d5e6f7a") == "5a1f2b3c4d5e6f7a"

    def test_invalid(self):
        assert fx.extract_user_id_from_href("/explore/abc") is None
        assert fx.extract_user_id_from_href("") is None
        assert fx.extract_user_id_from_href(None) is None


class TestParseIpLocation:
    """从文本解析 IP 属地。"""

    def test_with_chinese_colon(self):
        assert fx.parse_ip_location("IP属地：浙江") == "浙江"

    def test_with_halfwidth_colon(self):
        assert fx.parse_ip_location("IP属地: 广东") == "广东"

    def test_multiline_text(self):
        text = "穿搭博主\nIP属地：上海\n关注"
        assert fx.parse_ip_location(text) == "上海"

    def test_no_prefix_returns_empty(self):
        # 无前缀的纯文本不猜测，避免把昵称误判为属地
        assert fx.parse_ip_location("浙江") == ""

    def test_empty_input(self):
        assert fx.parse_ip_location("") == ""
        assert fx.parse_ip_location(None) == ""


class TestParseXhsId:
    """从文本解析小红书号。"""

    def test_chinese_colon(self):
        assert fx.parse_xhs_id("小红书号：123456789") == "123456789"

    def test_halfwidth_colon(self):
        assert fx.parse_xhs_id("小红书号: abc123xyz") == "abc123xyz"

    def test_alphanumeric_mixed(self):
        assert fx.parse_xhs_id("小红书号：xiaohongshu888") == "xiaohongshu888"

    def test_with_other_text(self):
        text = "昵称\n小红书号：abcd1234\nIP属地：浙江"
        assert fx.parse_xhs_id(text) == "abcd1234"

    def test_no_id(self):
        assert fx.parse_xhs_id("这个卡片没有小红书号") == ""
        assert fx.parse_xhs_id("") == ""
        assert fx.parse_xhs_id(None) == ""


class TestParseFollowingJson:
    """关注列表 API 响应解析。"""

    def test_success(self):
        raw = '{"success": true, "list": [{"user_id": "abc123", "nick_name": "博主A"}, {"user_id": "def456", "nick_name": "博主B"}]}'
        rows = fx.parse_following_json(raw)
        assert len(rows) == 2
        assert rows[0] == {"nickname": "博主A", "xhs_id": "", "ip_location": "", "uid": "abc123"}
        assert rows[1]["uid"] == "def456"

    def test_empty_list(self):
        assert fx.parse_following_json('{"success": true, "list": []}') == []

    def test_api_failure(self):
        assert fx.parse_following_json('{"success": false, "msg": "无登录信息"}') == []

    def test_invalid_json(self):
        assert fx.parse_following_json("create invalid") == []
        assert fx.parse_following_json("") == []
        assert fx.parse_following_json(None) == []

    def test_missing_uid_skipped(self):
        raw = '{"success": true, "list": [{"user_id": "", "nick_name": "无ID"}, {"user_id": "ok1", "nick_name": "有ID"}]}'
        rows = fx.parse_following_json(raw)
        assert len(rows) == 1
        assert rows[0]["nickname"] == "有ID"


class TestLoadExistingCsv:
    """断点续跑：读取已有 CSV。"""

    def test_parse_existing(self, tmp_path):
        path = tmp_path / "xhs_following.csv"
        fx.write_csv(
            [
                {"nickname": "博主A", "xhs_id": "abc123", "ip_location": "浙江"},
                {"nickname": "博主B", "xhs_id": "", "ip_location": ""},
            ],
            path,
        )
        existing = fx.load_existing_csv(path)
        assert existing["博主A"]["xhs_id"] == "abc123"
        assert existing["博主A"]["ip_location"] == "浙江"
        assert existing["博主B"]["xhs_id"] == ""

    def test_missing_file(self, tmp_path):
        assert fx.load_existing_csv(tmp_path / "不存在.csv") == {}


class TestDedupeRows:
    """按昵称去重，保留首条。"""

    def test_keep_first(self):
        rows = [
            {"nickname": "博主A", "xhs_id": "1", "ip_location": "浙江"},
            {"nickname": "博主A", "xhs_id": "", "ip_location": "上海"},
            {"nickname": "博主B", "xhs_id": "2", "ip_location": ""},
        ]
        out = fx.dedupe_rows(rows)
        assert len(out) == 2
        assert out[0]["nickname"] == "博主A"
        assert out[0]["xhs_id"] == "1"  # 保留首次出现的记录

    def test_blank_nickname_dropped(self):
        rows = [{"nickname": "", "xhs_id": "1", "ip_location": ""}]
        assert fx.dedupe_rows(rows) == []

    def test_empty_rows(self):
        assert fx.dedupe_rows([]) == []


class TestWriteCsv:
    """CSV 写出：表头、列顺序、UTF-8 BOM。"""

    def test_header_and_columns(self, tmp_path):
        path = tmp_path / "out.csv"
        rows = [
            {"nickname": "博主A", "xhs_id": "abc123", "ip_location": "浙江"},
            {"nickname": "博主B", "xhs_id": "", "ip_location": "上海"},
        ]
        fx.write_csv(rows, path)

        raw = path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")  # utf-8-sig BOM，Excel 中文不乱码

        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == ["nickname", "xhs_id", "ip_location"]
            data = list(reader)

        assert data == [
            {"nickname": "博主A", "xhs_id": "abc123", "ip_location": "浙江"},
            {"nickname": "博主B", "xhs_id": "", "ip_location": "上海"},
        ]

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "out.csv"
        fx.write_csv([], path)
        assert path.exists()
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == ["nickname", "xhs_id", "ip_location"]
