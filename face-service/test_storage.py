"""face-service 存储层最小测试（2026-11-04 测试体系评测 P1-4）。

不依赖 pytest（face-service 是独立的 Python 3.10 venv，只保证运行依赖）：
用标准库 unittest，跑法：
    face-service/.venv/Scripts/python.exe -m unittest discover -s face-service -p "test_*.py"
"""

from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app import router, storage
from app.face import FaceResult


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = storage.DB_PATH
        storage.DB_PATH = Path(self._tmp.name) / "face_service_test.db"
        storage.init_db()

    def tearDown(self) -> None:
        storage.DB_PATH = self._old_db
        self._tmp.cleanup()

    def test_register_list_get_delete(self) -> None:
        storage.upsert_person("p1", "小美", np.array([0.1, 0.2, 0.3], dtype=np.float32))
        storage.upsert_person("p2", "小丽", np.array([0.4, 0.5, 0.6], dtype=np.float32))

        persons = storage.list_persons()
        self.assertEqual([p["id"] for p in persons], ["p1", "p2"])
        self.assertEqual(storage.get_person("p1")["name"], "小美")
        self.assertIsNone(storage.get_person("missing"))

        embeddings = {row["id"]: row["embedding"] for row in storage.all_embeddings()}
        np.testing.assert_allclose(embeddings["p1"], [0.1, 0.2, 0.3], rtol=1e-6)
        self.assertEqual(len(embeddings), 2)

        self.assertTrue(storage.delete_person("p1"))
        self.assertFalse(storage.delete_person("p1"))
        self.assertIsNone(storage.get_person("p1"))

    def test_upsert_updates_name_and_embedding(self) -> None:
        storage.upsert_person("p1", "旧名字", np.array([1.0, 0.0], dtype=np.float32))
        storage.upsert_person("p1", "新名字", np.array([0.0, 1.0], dtype=np.float32))

        self.assertEqual(storage.get_person("p1")["name"], "新名字")
        rows = storage.all_embeddings()
        self.assertEqual(len(rows), 1)
        np.testing.assert_allclose(rows[0]["embedding"], [0.0, 1.0], rtol=1e-6)

    def test_register_match_delete_api_handlers(self) -> None:
        """直接调路由函数（不依赖 httpx/TestClient）：注册 → 匹配 → 删除。"""
        from starlette.datastructures import UploadFile

        def upload() -> UploadFile:
            return UploadFile(file=io.BytesIO(b"fake-image"), filename="face.jpg")

        async def fake_extract(_data: bytes) -> list[FaceResult]:
            return [FaceResult(bbox=[1, 2, 3, 4], det_score=0.99, embedding=[1.0, 0.0])]

        old_extract = router._extract_in_pool
        router._extract_in_pool = fake_extract
        try:
            async def flow() -> None:
                registered = await router.register_face(
                    person_id="api-p1", person_name="接口测试", file=upload()
                )
                self.assertTrue(registered["registered"])

                matched = await router.match_face(file=upload(), top_k=3)
                self.assertEqual(matched["face_count"], 1)
                self.assertEqual(matched["matches"][0]["person_id"], "api-p1")

                deleted = await router.remove_person("api-p1")
                self.assertTrue(deleted["deleted"])
                with self.assertRaises(Exception):
                    await router.remove_person("api-p1")

            asyncio.run(flow())
        finally:
            router._extract_in_pool = old_extract


if __name__ == "__main__":
    unittest.main()
