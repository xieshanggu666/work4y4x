"""社区航线挑战服务：版本化关卡 / 幂等飞行记录 / 审核 / 排行榜 / 解锁。

设计要点：
- 版本化关卡：挑战的每次发布落一条不可变的 ChallengeVersion 快照
  （里程碑 + 燃料/时间预算），成绩始终挂在具体版本上，旧版本记录可回放、
  结算口径可复现；current_version 单调递增，回退版本视为新版本。
- 幂等结算：飞行记录必须关联服务端执行档案（RunRecord），分数以服务端
  结算为准；submission_id 唯一约束 + 模块级锁保证双击/重试/多标签页/
  并发提交只落库一次，返回首个结果。
- 审核联动：记录默认 pending，仅 approved 进入排行榜并计入挑战解锁；
  驳回（rejected）留痕可回放但不计成绩。排行榜/解锁随审核状态实时变化。
- 挑战解锁：第 1 组默认开放，主线每通关一关（≥1 星）多开放一组
  （每组 3 个槽位，由先发布者占用）；挑战之间不再互相锁。
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import List, Optional

from sqlalchemy.exc import IntegrityError

from app.models import Challenge, ChallengeEntry, ChallengeVersion, LevelScore, RunRecord

# SQLite 单写者：序列化"查重-插入"临界区（与 scores 服务同一策略）。
_SAVE_LOCK = threading.Lock()

# 每组挑战槽位数：主线第 N 关通关（≥1 星）解锁第 N 组
GROUP_SIZE = 3

_STATUS = {"pending", "approved", "rejected"}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


class ChallengeError(ValueError):
    """挑战业务校验失败（映射为 400）。"""


class ChallengeNotFound(KeyError):
    """挑战或版本不存在（映射为 404）。"""


class EntryNotFound(KeyError):
    """飞行记录不存在（映射为 404）。"""


class RunNotFound(KeyError):
    """run_id 对应的执行档案不存在（映射为 404）。"""


class RunChallengeMismatch(ValueError):
    """执行档案不属于该挑战版本（映射为 400）。"""


# ---------------------------------------------------------------- 查询辅助

def _get_challenge(db, challenge_id: int) -> Challenge:
    ch = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    if ch is None:
        raise ChallengeNotFound(f"挑战不存在: {challenge_id}")
    return ch


def _get_version(db, challenge_id: int, version: int) -> ChallengeVersion:
    row = (db.query(ChallengeVersion)
             .filter(ChallengeVersion.challenge_id == challenge_id,
                     ChallengeVersion.version == version)
             .first())
    if row is None:
        raise ChallengeNotFound(f"挑战版本不存在: {challenge_id}@v{version}")
    return row


def current_version_row(db, challenge_id: int) -> ChallengeVersion:
    """挑战当前生效版本（供 /api/run 结算定位关卡定义）。"""
    ch = _get_challenge(db, challenge_id)
    return _get_version(db, challenge_id, ch.current_version)


def current_level(db, challenge_id: int):
    """挑战当前版本的关卡对象 + 执行档案合成键（供 /api/preview、/api/run）。"""
    from app.services.levels import challenge_level  # 延迟导入，避免循环依赖
    ch = _get_challenge(db, challenge_id)
    ver = _get_version(db, challenge_id, ch.current_version)
    return challenge_level(ch, ver), run_level_key(ch.id, ver.version)


def group_of(challenge_id: int) -> int:
    return (challenge_id - 1) // GROUP_SIZE + 1


def run_level_key(challenge_id: int, version: int) -> int:
    """挑战任务在执行档案（run_record.level_id）中的合成键。

    取负数，与主线关卡 id（正整数）天然隔离：挑战执行档案不会被误提交到
    主线成绩接口，主线执行档案也无法冒充挑战记录。
    """
    return -(challenge_id * 10000 + version)


def unlocked_groups(db) -> int:
    """已解锁的挑战组数：第 1 组默认开放，主线每通关一关（≥1 星）多开一组。"""
    rows = db.query(LevelScore).filter(LevelScore.stars >= 1).all()
    return 1 + len({r.level_id for r in rows})


def _group_map(db) -> dict:
    """challenge_id -> 组号（一次性取全表，避免逐条计算）。"""
    return {c.id: group_of(c.id) for c in db.query(Challenge).all()}


# ---------------------------------------------------------------- 发布与版本

def _validate_milestones(milestones) -> List[dict]:
    from app.services import physics  # 延迟导入，避免循环依赖
    if not isinstance(milestones, list) or not milestones:
        raise ChallengeError("至少需要一个里程碑")
    if len(milestones) > 5:
        raise ChallengeError("里程碑最多 5 个")
    planets = {b["id"] for b in physics.BODIES if b["id"] != "sun"}
    out = []
    seen = set()
    for i, ms in enumerate(milestones):
        if not isinstance(ms, dict):
            raise ChallengeError("里程碑格式错误")
        kind = ms.get("kind")
        mid = str(ms.get("id") or f"m{i + 1}")
        if mid in seen:
            raise ChallengeError(f"里程碑 id 重复: {mid}")
        seen.add(mid)
        name = str(ms.get("name") or "").strip()
        if not name:
            raise ChallengeError("里程碑缺少名称")
        item = {"id": mid, "kind": kind, "name": name[:40]}
        if kind == "proximity":
            if ms.get("planet_id") not in planets:
                raise ChallengeError(f"proximity 里程碑的行星未知: {ms.get('planet_id')}")
            item["planet_id"] = ms["planet_id"]
            item["dist"] = _bounded_float(ms.get("dist"), 0.01, 5.0, "proximity.dist")
        elif kind == "radius":
            item["r"] = _bounded_float(ms.get("r"), 1.1, 45.0, "radius.r")
        elif kind == "assist_capture":
            if ms.get("planet_id") not in planets:
                raise ChallengeError(f"assist_capture 里程碑的行星未知: {ms.get('planet_id')}")
            item["planet_id"] = ms["planet_id"]
        elif kind == "assist_then_radius":
            if ms.get("planet_id") not in planets:
                raise ChallengeError(f"assist_then_radius 里程碑的行星未知: {ms.get('planet_id')}")
            item["planet_id"] = ms["planet_id"]
            item["r"] = _bounded_float(ms.get("r"), 1.1, 45.0, "assist_then_radius.r")
        elif kind == "escape":
            item["r"] = _bounded_float(ms.get("r"), 2.0, 60.0, "escape.r")
        else:
            raise ChallengeError(f"未知里程碑类型: {kind}")
        out.append(item)
    return out


def _bounded_float(v, lo: float, hi: float, field: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ChallengeError(f"{field} 必须是数字")
    if not (lo <= f <= hi):
        raise ChallengeError(f"{field} 超出范围 [{lo}, {hi}]")
    return f


def _validate_budget(budget_dv) -> float:
    # 与内置关卡同量级：0.004 ~ 0.0115 AU/天；放宽到 [0.001, 0.03]
    return _bounded_float(budget_dv, 0.001, 0.03, "budget_dv")


def _validate_t_max(t_max) -> float:
    return _bounded_float(t_max, 100.0, 20000.0, "t_max")


def _validate_text(v, field: str, max_len: int, allow_empty: bool = False) -> str:
    s = str(v or "").strip()
    if not s and not allow_empty:
        raise ChallengeError(f"{field} 不能为空")
    if len(s) > max_len:
        raise ChallengeError(f"{field} 超长（最多 {max_len} 字）")
    return s


def create_challenge(db, *, slug: str, title: str, designer: str, brief: str,
                     milestones, budget_dv, t_max, hint: str = "") -> dict:
    """发布新挑战（含首个版本 v1）。"""
    slug = str(slug or "").strip().lower()
    if not _SLUG_RE.match(slug):
        raise ChallengeError("slug 需为 2~32 位小写字母/数字/连字符")
    title = _validate_text(title, "标题", 64)
    designer = _validate_text(designer, "设计者", 32)
    brief = _validate_text(brief, "简介", 300, allow_empty=True)
    hint = _validate_text(hint, "提示", 300, allow_empty=True)
    ms = _validate_milestones(milestones)
    budget = _validate_budget(budget_dv)
    tmax = _validate_t_max(t_max)

    with _SAVE_LOCK:
        if db.query(Challenge).filter(Challenge.slug == slug).first() is not None:
            raise ChallengeError(f"slug 已被占用: {slug}")
        ch = Challenge(slug=slug, title=title, designer=designer, brief=brief,
                       current_version=1, created_at=time.time())
        db.add(ch)
        try:
            db.flush()
        except IntegrityError:  # 并发同 slug 兜底
            db.rollback()
            raise ChallengeError(f"slug 已被占用: {slug}")
        db.add(ChallengeVersion(
            challenge_id=ch.id, version=1,
            milestones_json=json.dumps(ms, ensure_ascii=False),
            budget_dv=budget, t_max=tmax, hint=hint,
            created_at=time.time()))
        db.commit()
        db.refresh(ch)
    return challenge_summary(db, ch.id)


def publish_version(db, challenge_id: int, *, milestones, budget_dv, t_max,
                    hint: str = "") -> dict:
    """为挑战发布新版本（快照不可变，版本号 = 当前 +1）。"""
    ms = _validate_milestones(milestones)
    budget = _validate_budget(budget_dv)
    tmax = _validate_t_max(t_max)
    hint = _validate_text(hint, "提示", 300, allow_empty=True)

    with _SAVE_LOCK:
        ch = _get_challenge(db, challenge_id)
        new_ver = ch.current_version + 1
        db.add(ChallengeVersion(
            challenge_id=ch.id, version=new_ver,
            milestones_json=json.dumps(ms, ensure_ascii=False),
            budget_dv=budget, t_max=tmax, hint=hint,
            created_at=time.time()))
        ch.current_version = new_ver
        try:
            db.commit()
        except IntegrityError:  # 并发发版兜底：重取版本号重试一次
            db.rollback()
            ch = _get_challenge(db, challenge_id)
            new_ver = ch.current_version + 1
            db.add(ChallengeVersion(
                challenge_id=ch.id, version=new_ver,
                milestones_json=json.dumps(ms, ensure_ascii=False),
                budget_dv=budget, t_max=tmax, hint=hint,
                created_at=time.time()))
            ch.current_version = new_ver
            db.commit()
    return challenge_detail(db, challenge_id)


# ---------------------------------------------------------------- 视图数据

def challenge_summary(db, challenge_id: int) -> dict:
    ch = _get_challenge(db, challenge_id)
    ver = _get_version(db, challenge_id, ch.current_version)
    approved = (db.query(ChallengeEntry)
                  .filter(ChallengeEntry.challenge_id == ch.id,
                          ChallengeEntry.version == ch.current_version,
                          ChallengeEntry.status == "approved")
                  .count())
    pending = (db.query(ChallengeEntry)
                 .filter(ChallengeEntry.challenge_id == ch.id,
                         ChallengeEntry.status == "pending")
                 .count())
    return {
        "id": ch.id,
        "slug": ch.slug,
        "title": ch.title,
        "designer": ch.designer,
        "brief": ch.brief,
        "current_version": ch.current_version,
        "group": group_of(ch.id),
        "unlocked": group_of(ch.id) <= unlocked_groups(db),
        "budget_dv": ver.budget_dv,
        "t_max": ver.t_max,
        "milestones": json.loads(ver.milestones_json),
        "approved_count": approved,
        "pending_count": pending,
        "created_at": ch.created_at,
    }


def challenge_detail(db, challenge_id: int) -> dict:
    ch = _get_challenge(db, challenge_id)
    out = challenge_summary(db, challenge_id)
    vers = (db.query(ChallengeVersion)
              .filter(ChallengeVersion.challenge_id == challenge_id)
              .order_by(ChallengeVersion.version.desc())
              .all())
    out["hint"] = _get_version(db, challenge_id, ch.current_version).hint
    out["versions"] = [{
        "version": v.version,
        "budget_dv": v.budget_dv,
        "t_max": v.t_max,
        "milestones": json.loads(v.milestones_json),
        "hint": v.hint,
        "created_at": v.created_at,
    } for v in vers]
    return out


def list_challenges(db) -> dict:
    """挑战中心首页数据：全部挑战（含解锁状态）+ 已解锁组数。"""
    groups = unlocked_groups(db)
    items = []
    for ch in db.query(Challenge).order_by(Challenge.id.asc()).all():
        s = challenge_summary(db, ch.id)
        s["unlocked"] = s["group"] <= groups
        items.append(s)
    return {"unlocked_groups": groups, "group_size": GROUP_SIZE, "challenges": items}


# ---------------------------------------------------------------- 飞行记录

def _entry_view(e: ChallengeEntry) -> dict:
    return {
        "entry_id": e.id,
        "challenge_id": e.challenge_id,
        "version": e.version,
        "player": e.player,
        "ok": bool(e.ok),
        "stars": e.stars,
        "fuel_used": e.fuel_used,
        "elapsed_days": e.elapsed_days,
        "status": e.status,
        "review_note": e.review_note,
        "reviewed_at": e.reviewed_at,
        "created_at": e.created_at,
    }


def submit_entry(db, *, challenge_id: int, run_id: str, submission_id: Optional[str],
                 player: str = "") -> dict:
    """提交飞行记录（幂等）：分数以执行档案的服务端结算为准，默认待审核。

    重复 submission_id（双击/重试/多标签页/并发）返回首个受理结果，
    不重复落库、不重复进入审核队列。
    """
    player = _validate_text(player, "玩家名", 32, allow_empty=True) or "匿名飞手"
    with _SAVE_LOCK:
        if submission_id:
            dup = (db.query(ChallengeEntry)
                     .filter(ChallengeEntry.submission_id == submission_id)
                     .first())
            if dup is not None:
                return {"saved": True, "duplicated": True, **_entry_view(dup)}

        ch = _get_challenge(db, challenge_id)
        run = db.query(RunRecord).filter(RunRecord.run_uid == run_id).first()
        if run is None:
            raise RunNotFound(f"执行记录不存在: {run_id}")
        # 执行档案必须来自该挑战当前版本的任务（/api/run 以 challenge 模式结算）
        if run.level_id != run_level_key(ch.id, ch.current_version):
            raise RunChallengeMismatch("执行记录不属于该挑战当前版本")

        entry = ChallengeEntry(
            submission_id=submission_id or uuid.uuid4().hex,
            challenge_id=ch.id,
            version=ch.current_version,
            run_id=run.id,
            player=player,
            ok=1 if run.ok else 0,
            stars=run.stars,
            fuel_used=run.fuel_used,
            elapsed_days=run.elapsed_days,
            status="pending",
            created_at=time.time(),
        )
        db.add(entry)
        try:
            db.commit()
        except IntegrityError:  # 唯一约束兜底并发重复
            db.rollback()
            dup = (db.query(ChallengeEntry)
                     .filter(ChallengeEntry.submission_id == entry.submission_id)
                     .first())
            return {"saved": True, "duplicated": True, **_entry_view(dup)}
        db.refresh(entry)
        return {"saved": True, "duplicated": False, **_entry_view(entry)}


def review_entry(db, challenge_id: int, entry_id: int, action: str, note: str = "") -> dict:
    """审核飞行记录：approve / reject。审核结果即时联动排行榜与解锁。"""
    if action not in ("approve", "reject"):
        raise ChallengeError(f"未知审核动作: {action}")
    note = _validate_text(note, "审核备注", 200, allow_empty=True)
    with _SAVE_LOCK:
        e = (db.query(ChallengeEntry)
               .filter(ChallengeEntry.id == entry_id)
               .first())
        if e is None or e.challenge_id != challenge_id:
            raise EntryNotFound(f"飞行记录不存在: {entry_id}")
        e.status = "approved" if action == "approve" else "rejected"
        e.review_note = note
        e.reviewed_at = time.time()
        db.commit()
        db.refresh(e)
        return _entry_view(e)


def list_entries(db, challenge_id: int, *, status: Optional[str] = None,
                 version: Optional[int] = None, limit: int = 50) -> List[dict]:
    """挑战的飞行记录列表（新→旧），可按审核状态/版本过滤。"""
    _get_challenge(db, challenge_id)
    q = db.query(ChallengeEntry).filter(ChallengeEntry.challenge_id == challenge_id)
    if status:
        if status not in _STATUS:
            raise ChallengeError(f"未知审核状态: {status}")
        q = q.filter(ChallengeEntry.status == status)
    if version is not None:
        q = q.filter(ChallengeEntry.version == version)
    rows = q.order_by(ChallengeEntry.id.desc()).limit(max(1, min(200, limit))).all()
    return [_entry_view(e) for e in rows]


def leaderboard(db, challenge_id: int, *, version: Optional[int] = None,
                limit: int = 20) -> dict:
    """排行榜：仅已审核通过且任务达成的记录，按 星数→燃料→耗时→先后 排序。"""
    ch = _get_challenge(db, challenge_id)
    ver = version if version is not None else ch.current_version
    _get_version(db, challenge_id, ver)
    rows = (db.query(ChallengeEntry)
              .filter(ChallengeEntry.challenge_id == challenge_id,
                      ChallengeEntry.version == ver,
                      ChallengeEntry.status == "approved",
                      ChallengeEntry.ok == 1)
              .order_by(ChallengeEntry.stars.desc(),
                        ChallengeEntry.fuel_used.asc(),
                        ChallengeEntry.elapsed_days.asc(),
                        ChallengeEntry.id.asc())
              .limit(max(1, min(100, limit)))
              .all())
    return {
        "challenge_id": challenge_id,
        "version": ver,
        "entries": [{"rank": i + 1, **_entry_view(e)} for i, e in enumerate(rows)],
    }


def entry_detail(db, entry_id: int) -> dict:
    """飞行记录详情：含动作方案与轨迹（回放数据来自关联执行档案）。"""
    e = (db.query(ChallengeEntry)
           .filter(ChallengeEntry.id == entry_id)
           .first())
    if e is None:
        raise EntryNotFound(f"飞行记录不存在: {entry_id}")
    out = _entry_view(e)
    run = db.query(RunRecord).filter(RunRecord.id == e.run_id).first()
    if run is not None:
        out.update({
            "run_uid": run.run_uid,
            "reason": run.reason,
            "actions": json.loads(run.actions_json),
            "trajectory": json.loads(run.trajectory_json),
            "events": json.loads(run.events_json),
            "milestones": json.loads(run.milestones_json),
        })
    return out
