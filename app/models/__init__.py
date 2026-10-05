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


class Challenge(Base):
    """社区航线挑战：设计者发布的关卡系列，按版本演进。

    每个挑战可发布多个版本（ChallengeVersion），current_version 指向当前
    生效版本；玩家成绩与排行榜始终挂在具体版本上，旧版本记录保留可回放。
    """
    __tablename__ = "challenge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(32), nullable=False, unique=True, index=True)
    title = Column(String(64), nullable=False)
    designer = Column(String(32), nullable=False)
    brief = Column(String(300), nullable=False, default="")
    current_version = Column(Integer, nullable=False, default=1)
    created_at = Column(Float, nullable=False, default=time.time)


class ChallengeVersion(Base):
    """挑战的一个版本：完整关卡定义快照（里程碑 + 燃料/时间预算）。

    (challenge_id, version) 唯一；发布后不可变 —— 改关卡即发新版本，
    保证旧成绩的结算口径（预算/里程碑）永远可复现。
    """
    __tablename__ = "challenge_version"
    __table_args__ = (UniqueConstraint("challenge_id", "version",
                                       name="uq_challenge_version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    challenge_id = Column(Integer, ForeignKey("challenge.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    milestones_json = Column(Text, nullable=False, default="[]")
    budget_dv = Column(Float, nullable=False)
    t_max = Column(Float, nullable=False)
    hint = Column(String(300), nullable=False, default="")
    created_at = Column(Float, nullable=False, default=time.time)


class ChallengeEntry(Base):
    """一条挑战飞行记录（成绩提交）。

    - 幂等：submission_id 唯一，重复提交（双击/重试/多标签页）返回首个结果；
    - 结算：数值一律取自关联的执行档案（RunRecord），客户端报数不参与结算；
    - 审核：pending → approved/rejected，仅 approved 进入排行榜并计入解锁。
    """
    __tablename__ = "challenge_entry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    submission_id = Column(String(64), nullable=False, unique=True, index=True)
    challenge_id = Column(Integer, ForeignKey("challenge.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    run_id = Column(Integer, ForeignKey("run_record.id"), nullable=False, index=True)
    player = Column(String(32), nullable=False, default="匿名飞手")
    ok = Column(Integer, nullable=False, default=0)
    stars = Column(Integer, nullable=False, default=0)
    fuel_used = Column(Float, nullable=False, default=0.0)
    elapsed_days = Column(Float, nullable=False, default=0.0)
    status = Column(String(16), nullable=False, default="pending", index=True)
    review_note = Column(String(200), nullable=False, default="")
    reviewed_at = Column(Float, nullable=True)
    created_at = Column(Float, nullable=False, default=time.time)
