import time

from sqlalchemy import (Column, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)

from app.core.database import Base


class LevelScore(Base):
    """最佳成绩汇总（旧存档表）。结构保持不变，历史存档可直接沿用。"""
    __tablename__ = "level_score"
    __table_args__ = (UniqueConstraint("level_id", name="uq_level"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    level_id = Column(Integer, nullable=False, index=True)
    stars = Column(Integer, nullable=False, default=0)
    fuel_used = Column(Float, nullable=False, default=0.0)
    elapsed_days = Column(Float, nullable=False, default=0.0)


class RunRecord(Base):
    """一次任务执行的完整档案：动作方案 + 结算结果 + 轨迹。

    /api/run 每次执行落库一条，成绩记录通过外键关联到它，
    使每条最佳成绩都能溯源到产生它的那次发射，并支持轨迹回放。
    """
    __tablename__ = "run_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_uid = Column(String(36), nullable=False, unique=True, index=True)
    level_id = Column(Integer, nullable=False, index=True)
    ok = Column(Integer, nullable=False, default=0)
    reason = Column(String(64), nullable=False, default="")
    stars = Column(Integer, nullable=False, default=0)
    fuel_used = Column(Float, nullable=False, default=0.0)
    elapsed_days = Column(Float, nullable=False, default=0.0)
    actions_json = Column(Text, nullable=False, default="[]")
    trajectory_json = Column(Text, nullable=False, default="[]")
    events_json = Column(Text, nullable=False, default="[]")
    milestones_json = Column(Text, nullable=False, default="[]")
    created_at = Column(Float, nullable=False, default=time.time)


class ScoreRecord(Base):
    """一条成绩提交记录。

    submission_id 是客户端生成的幂等键：同一提交（双击/重试/多标签页）
    重复到达时只落库一次，返回首个结果。
    """
    __tablename__ = "score_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    submission_id = Column(String(64), nullable=False, unique=True, index=True)
    run_id = Column(Integer, ForeignKey("run_record.id"), nullable=True, index=True)
    level_id = Column(Integer, nullable=False, index=True)
    stars = Column(Integer, nullable=False, default=0)
    fuel_used = Column(Float, nullable=False, default=0.0)
    elapsed_days = Column(Float, nullable=False, default=0.0)
    source = Column(String(16), nullable=False, default="run")  # run=关联执行 / legacy=旧版客户端
    created_at = Column(Float, nullable=False, default=time.time)
