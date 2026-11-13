"""向量存储（`services/vector/store.py`）的边界路径：降级、损坏表自愈、非法向量。

向量库是全项目最容易「静默出错」的一块：写失败只记日志、读失败按空结果降级，
所以出问题的表现是**搜不到图、管理页显示几乎全部向量缺失**，没有报错可看。
`store.py` 里大量防御分支平时不跑，本文件用假 lancedb 对象逐条钉住：

- lancedb 没装 → `is_lancedb_available()=False`（上层据此禁用向量功能，而不是崩）；
- 表列表读不出 → 按「表不存在」重建（不能把目录异常升级成全局失败）；
- 表注册存在但数据集加载失败（空骨架）→ 读路径抛 `_CorruptTableError` 由调用方降级，
  写路径**先备份再重建**（历史事故：直接 drop 会把可抢救数据一起销毁）；
- 备份目录已不存在 → 直接重建；备份失败 → 回退 drop_table；
- 写入向量维度不符 / 含 NaN·Inf → 拒绝写入（否则固定维度 schema 会静默失效）。
"""

import math
import sys
from types import SimpleNamespace

import pytest

from app.services.vector import store as vs


@pytest.fixture(autouse=True)
def _clean_connection(monkeypatch):
    monkeypatch.setattr(vs, "_db", None)
    yield
    vs._db = None


class _FakeDb:
    def __init__(self, *, list_tables=None, open_table=None, create_table=None):
        self._list = list_tables
        self._open = open_table
        self._create = create_table
        self.created: list[tuple[str, object]] = []
        self.dropped: list[str] = []

    def list_tables(self):
        if isinstance(self._list, Exception):
            raise self._list
        return self._list

    def open_table(self, name):
        if isinstance(self._open, Exception):
            raise self._open
        return self._open

    def create_table(self, name, schema=None):
        if isinstance(self._create, Exception):
            raise self._create
        self.created.append((name, schema))
        return {"name": name, "schema": schema}

    def drop_table(self, name):
        self.dropped.append(name)


class TestSmallHelpers:
    def test_utc_str_is_iso_with_timezone(self):
        value = vs._utc_str()
        assert value.endswith("+00:00")
        assert "T" in value

    def test_sql_quote_escapes_single_quotes(self):
        assert vs._sql_quote("a'b") == "a''b"
        assert vs._sql_quote("normal") == "normal"

    def test_table_name_and_dim_follow_settings(self, monkeypatch):
        monkeypatch.setattr(vs.settings, "lancedb_text_table", "t_text")
        monkeypatch.setattr(vs.settings, "lancedb_image_table", "t_img")
        monkeypatch.setattr(vs.settings, "lancedb_text_dim", 384)
        monkeypatch.setattr(vs.settings, "lancedb_image_dim", 512)

        assert vs._table_name("text") == "t_text"
        assert vs._table_name("image") == "t_img"
        assert vs._table_name("unknown") == "t_img"  # 只有 text 特殊，其余按图像处理
        assert vs._dim("text") == 384
        assert vs._dim("image") == 512

    def test_reset_connection_drops_cache(self, monkeypatch):
        monkeypatch.setattr(vs, "_db", object())
        vs.reset_connection()
        assert vs._db is None


class TestAvailabilityAndSchema:
    def test_lancedb_missing_returns_false(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "lancedb", None)  # import 会抛 ImportError
        assert vs.is_lancedb_available() is False

    def test_lancedb_present_returns_true(self):
        lancedb = pytest.importorskip("lancedb")
        assert lancedb is not None
        assert vs.is_lancedb_available() is True

    def test_schema_matches_dim_and_fields(self, monkeypatch):
        monkeypatch.setattr(vs.settings, "lancedb_image_dim", 8)
        schema = vs._schema_for("image")

        assert [f.name for f in schema] == ["id", "inspiration_id", "vector", "created_at"]
        assert schema.field("vector").type.list_size == 8  # 固定维度
        assert "fixed_size_list" in str(schema.field("vector").type)


class TestCreateTable:
    def test_concurrent_create_falls_back_to_open_table(self, monkeypatch, logs=None):
        db = _FakeDb(create_table=Exception("Table already exists: t.lance"))
        db._open = {"opened": True}

        table = vs._create_table(db, "image", "t")

        assert table == {"opened": True}  # 回退 open_table 而不是让写失败

    def test_other_create_error_propagates(self):
        db = _FakeDb(create_table=RuntimeError("磁盘满"))

        with pytest.raises(RuntimeError):
            vs._create_table(db, "image", "t")


class TestTableOpen:
    def test_list_tables_failure_falls_back_to_create(self, monkeypatch):
        db = _FakeDb(list_tables=RuntimeError("目录读不出"))
        monkeypatch.setattr(vs, "_connect", lambda: db)

        table = vs._table("image")

        assert table["name"] == vs._table_name("image")  # 按表不存在 → 新建
        assert db.created

    def test_table_names_namedtuple_supported(self, monkeypatch):
        """新版 list_tables 返回带 .tables 的命名元组，旧版返回 list，两者都要认。"""
        opened = {"name": "opened"}
        db = _FakeDb(list_tables=SimpleNamespace(tables=["t_img"]), open_table=opened)
        monkeypatch.setattr(vs, "_connect", lambda: db)
        monkeypatch.setattr(vs.settings, "lancedb_image_table", "t_img")

        assert vs._table("image") == opened

    def test_corrupt_table_raises_specific_error(self, monkeypatch):
        db = _FakeDb(list_tables=["t_img"], open_table=RuntimeError("空骨架"))
        monkeypatch.setattr(vs, "_connect", lambda: db)
        monkeypatch.setattr(vs.settings, "lancedb_image_table", "t_img")

        with pytest.raises(vs._CorruptTableError):
            vs._table("image")


class TestTableForWrite:
    def test_corrupt_table_is_backed_up_then_recreated(self, monkeypatch):
        backed_up: list[str] = []

        def _corrupt(_kind):
            raise vs._CorruptTableError("空骨架")

        def _backup(name):
            backed_up.append(name)

        monkeypatch.setattr(vs, "_table", _corrupt)
        monkeypatch.setattr(vs, "_backup_corrupt_table", _backup)
        monkeypatch.setattr(vs, "_connect", lambda: _FakeDb())
        monkeypatch.setattr(vs.settings, "lancedb_image_table", "t_img")

        table = vs._table_for_write("image")

        assert backed_up == ["t_img"]  # 先备份
        assert table["name"] == "t_img"  # 再重建


class TestBackupCorruptTable:
    def test_missing_source_dir_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vs.settings, "lancedb_dir", tmp_path)
        vs._backup_corrupt_table("t_img")  # 目录不存在：直接返回，不抛

    def test_moves_directory_aside(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vs.settings, "lancedb_dir", tmp_path)
        (tmp_path / "t_img.lance").mkdir()
        moved: list[tuple[str, str]] = []

        def _move(src, dst):
            moved.append((src, dst))

        monkeypatch.setattr(vs.shutil, "move", _move)

        vs._backup_corrupt_table("t_img")

        assert moved and "t_img.lance" in moved[0][0]
        assert ".corrupt-t_img-" in moved[0][1]

    def test_move_failure_falls_back_to_drop_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vs.settings, "lancedb_dir", tmp_path)
        (tmp_path / "t_img.lance").mkdir()
        db = _FakeDb()

        def _boom(*_args):
            raise OSError("被占用")

        monkeypatch.setattr(vs.shutil, "move", _boom)
        monkeypatch.setattr(vs, "_connect", lambda: db)

        vs._backup_corrupt_table("t_img")

        assert db.dropped == ["t_img"]  # 备份失败 → 回退 drop_table 重建

    def test_double_failure_is_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vs.settings, "lancedb_dir", tmp_path)
        (tmp_path / "t_img.lance").mkdir()

        def _boom(*_args):
            raise OSError("被占用")

        class _BadDropDb(_FakeDb):
            def drop_table(self, name):
                raise RuntimeError("也失败")

        monkeypatch.setattr(vs.shutil, "move", _boom)
        monkeypatch.setattr(vs, "_connect", lambda: _BadDropDb())

        vs._backup_corrupt_table("t_img")  # 两层失败都不外泄


class TestUpsertValidation:
    """写入前的维度/数值校验（在任何 lancedb 调用之前）。"""

    def test_wrong_dimension_is_rejected(self, monkeypatch):
        monkeypatch.setattr(vs.settings, "lancedb_image_dim", 4)
        assert vs._upsert_sync("image", "insp-1", [0.1, 0.2]) is False

    def test_nan_or_inf_is_rejected(self, monkeypatch):
        monkeypatch.setattr(vs.settings, "lancedb_image_dim", 3)
        assert vs._upsert_sync("image", "insp-1", [0.1, math.nan, 0.2]) is False
        assert vs._upsert_sync("image", "insp-1", [0.1, math.inf, 0.2]) is False

    def test_valid_vector_passes_validation(self, monkeypatch):
        """合法向量会继续走到写库（用「写库路径被调用」证明校验层放行）。"""
        monkeypatch.setattr(vs.settings, "lancedb_image_dim", 2)
        reached: list[str] = []

        def _write_path(kind):
            reached.append(kind)
            raise RuntimeError("进入写库")

        monkeypatch.setattr(vs, "_table_for_write", _write_path)

        # 写库异常按设计兜底为 False（不能把单条写入失败升级成上层异常）
        assert vs._upsert_sync("image", "insp-1", [0.5, 0.5]) is False
        assert reached == ["image"]
