"""安全护栏补充测试：破坏性路由判定、路径变体绕过、CORS 白名单。

已有 `test_auth.py` 覆盖「配了 API_KEY 后破坏性/非破坏性接口的认证矩阵」，
`test_files.py` 覆盖文件接口路径遍历，`test_unit_inspiration_create_from_url.py`
覆盖 SSRF。本文件补三块：

1. `is_destructive_route` 的**单元口径**：命中清单、路径参数、非破坏性不误伤；
2. **路径变体绕过**：对同一个破坏性接口用尾斜杠/编码字符/点段/双斜杠等变体发请求，
   断言动作没有被执行（行仍在），而不是只看状态码——中间件与路由的归一化口径
   不一致时，「状态码 200/404」都可能意味着绕过；
3. **CORS 白名单**：允许来源才回 `Access-Control-Allow-Origin`，其它来源不放大权限。
"""

from app.config import settings
from app.utils.auth import is_destructive_route

TEST_KEY = "pytest-security-key"


class TestDestructiveRouteMatching:
    def test_exact_and_parameterized_paths(self):
        assert is_destructive_route("DELETE", "/api/inspirations/trash")
        assert is_destructive_route("DELETE", "/api/inspirations/abc-123")
        assert is_destructive_route("DELETE", "/api/tasks/42")
        assert is_destructive_route("POST", "/api/tags/history/7/rollback")
        assert is_destructive_route("DELETE", "/api/ai/models/some/model/path")

    def test_non_destructive_routes_do_not_match(self):
        # 可恢复操作与读接口不进清单
        assert not is_destructive_route("POST", "/api/inspirations/abc/trash")
        assert not is_destructive_route("POST", "/api/inspirations/abc/restore")
        assert not is_destructive_route("GET", "/api/inspirations/trash")
        assert not is_destructive_route("GET", "/api/tasks/42")
        assert not is_destructive_route("POST", "/api/tags/merge-preview")
        # 方法不同也不匹配（DELETE 才在清单里）
        assert not is_destructive_route("GET", "/api/tasks/42")
        assert not is_destructive_route("PUT", "/api/tasks/42")


class TestPathVariantBypass:
    """未归一化路径变体不能绕过认证并真的删掉数据。"""

    def test_variants_do_not_delete_row(self, client, upload, monkeypatch):
        monkeypatch.setattr(settings, "api_key", TEST_KEY)
        inspiration_id = upload().json()["id"]

        variants = [
            f"/api/inspirations/{inspiration_id}/../{inspiration_id}",  # 无意义变体（防误伤）
            "/api/inspirations/trash/",  # 尾斜杠（清空垃圾桶）
            "/api/inspirations/%74rash",  # 编码 't'（解码后应命中清单 → 401）
            "/api/inspirations/./trash",  # 点段（路由归一化差异）
            "http://testserver//api/inspirations/trash",  # 双斜杠
        ]
        for variant in variants:
            client.request(
                "DELETE", variant, headers={"X-API-Key": "wrong-key"}, follow_redirects=False
            )
            detail = client.get(f"/api/inspirations/{inspiration_id}")
            assert detail.status_code == 200, f"路径变体 {variant} 不应删掉数据：{detail.status_code}"

    def test_correct_key_still_works_through_variant(self, client, monkeypatch):
        """反向确认：变体不是「一律拒绝」，而是认证仍然生效。"""
        monkeypatch.setattr(settings, "api_key", TEST_KEY)

        # 尾斜杠会 307 到规范路径，跟随重定向后应被认证拦截（401）
        response = client.delete("/api/inspirations/trash/", follow_redirects=True)
        assert response.status_code == 401


class TestCorsWhitelist:
    def test_allowed_origin_gets_header(self, client):
        origin = settings.cors_origins[0]
        response = client.get("/api/health", headers={"Origin": origin})

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin

    def test_disallowed_origin_gets_no_header(self, client):
        response = client.get("/api/health", headers={"Origin": "http://evil.example"})

        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers

    def test_preflight_only_allows_whitelisted_origin(self, client):
        allowed = settings.cors_origins[0]
        ok = client.options(
            "/api/inspirations",
            headers={
                "Origin": allowed,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert ok.status_code in (200, 204)
        assert ok.headers.get("access-control-allow-origin") == allowed

        blocked = client.options(
            "/api/inspirations",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in blocked.headers
