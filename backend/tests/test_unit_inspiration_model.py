"""Inspiration 模型关联删除与 SQLite 外键约束回归测试。"""

from sqlalchemy import func, select, text

from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.models.inspiration import (
    AIAnalysisLog,
    AIAnalysisTag,
    AIQualityReview,
    FavoriteEvent,
    Inspiration,
    QualityCorrection,
)
from app.models.person import Blogger, InspirationBlogger, InspirationModel, Model
from app.models.tag import InspirationTag, Tag


async def test_sqlite_foreign_keys_are_enabled(client):
    async with async_session() as db:
        assert (await db.execute(text("PRAGMA foreign_keys"))).scalar() == 1


async def test_delete_inspiration_cascades_all_child_rows(client):
    """passive_deletes=True 后，物理删除素材应由数据库级联清空子表。"""
    async with async_session() as db:
        insp = Inspiration(
            source_type="manual_upload",
            file_path="images/test/cascade.jpg",
            media_type="image",
        )
        tag = Tag(name="cascade-test-tag", category="item")
        blogger = Blogger(name="cascade-test-blogger")
        model = Model(name="cascade-test-model")
        db.add_all([insp, tag, blogger, model])
        await db.flush()

        log = AIAnalysisLog(
            inspiration_id=insp.id,
            model_name="m",
            log_type="analysis",
        )
        db.add(log)
        await db.flush()
        db.add_all(
            [
                InspirationTag(inspiration_id=insp.id, tag_id=tag.id),
                InspirationBlogger(inspiration_id=insp.id, blogger_id=blogger.id),
                InspirationModel(inspiration_id=insp.id, model_id=model.id),
                AIAnalysisTag(log_id=log.id, tag_id=tag.id),
                AIQualityReview(log_id=log.id, result="approved"),
                QualityCorrection(
                    inspiration_id=insp.id,
                    original_status="rejected",
                    corrected_status="approved",
                ),
                FavoriteEvent(inspiration_id=insp.id, action="favorite"),
                InspirationFaceDetection(
                    inspiration_id=insp.id,
                    face_index=0,
                    embedding=b"test",
                ),
            ]
        )
        await db.commit()
        insp_id = insp.id

        await db.delete(await db.get(Inspiration, insp_id))
        await db.commit()

        async with async_session() as check:
            for model_cls in (
                InspirationTag,
                InspirationBlogger,
                InspirationModel,
                AIAnalysisLog,
                AIAnalysisTag,
                AIQualityReview,
                QualityCorrection,
                FavoriteEvent,
                InspirationFaceDetection,
            ):
                count = (
                    await check.execute(select(func.count()).select_from(model_cls))
                ).scalar_one()
                assert count == 0, f"{model_cls.__name__} 留下了孤儿行"
