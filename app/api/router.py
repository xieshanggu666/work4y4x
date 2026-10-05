"""游戏 API：系统信息 / 动作预览 / 任务执行 / 成绩记录与回放 / 社区航线挑战。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from app.core.database import SessionLocal
from app.services import physics
from app.services import scores as scores_svc
from app.services import challenges as challenges_svc
from app.services.levels import LEVELS, LEVEL_BY_ID

router = APIRouter(prefix="/api")

_PLANET_IDS = {b["id"] for b in physics.BODIES if b["id"] != "sun"}
_ACTION_TYPES = {"coast", "burn", "slingshot"}


class Action(BaseModel):
    type: str
    days: Optional[float] = None
    angle: Optional[float] = None
    dv: Optional[float] = None
    planet_id: Optional[str] = None
    b: Optional[float] = None


class SimRequest(BaseModel):
    level_id: Optional[int] = None
    challenge_id: Optional[int] = None   # 挑战模式：按该挑战当前版本结算
    actions: List[Action] = Field(default_factory=list)


class ScoreRequest(BaseModel):
    """成绩提交：新版携带 run_id + submission_id（幂等键）；

    旧版客户端只带 stars/fuel_used/elapsed_days，照常受理（source=legacy）。
    """
    level_id: int
    run_id: Optional[str] = None
    submission_id: Optional[str] = None
    stars: Optional[int] = Field(default=None, ge=0, le=3)
    fuel_used: float = 0.0
    elapsed_days: float = 0.0


class ChallengeCreateRequest(BaseModel):
    slug: str
    title: str
    designer: str
    brief: str = ""
    milestones: List[dict] = Field(default_factory=list)
    budget_dv: float
    t_max: float
    hint: str = ""


class ChallengeVersionRequest(BaseModel):
    milestones: List[dict] = Field(default_factory=list)
    budget_dv: float
    t_max: float
    hint: str = ""


class ChallengeEntryRequest(BaseModel):
    """挑战飞行记录提交：run_id 关联执行档案 + submission_id 幂等键。"""
    run_id: str
    submission_id: Optional[str] = None
    player: Optional[str] = None


class ChallengeReviewRequest(BaseModel):
    action: str           # approve / reject
    note: str = ""


def _validate(actions: List[Action]) -> List[dict]:
    out = []
    for a in actions:
        if a.type not in _ACTION_TYPES:
            raise HTTPException(400, f"未知动作类型: {a.type}")
        if a.type == "coast":
            out.append({"type": "coast", "days": max(0.0, a.days or 0.0)})
        elif a.type == "burn":
            out.append({"type": "burn", "angle": float(a.angle or 0.0),
                        "dv": max(0.0, min(0.02, a.dv or 0.0))})
        elif a.type == "slingshot":
            if a.planet_id not in _PLANET_IDS:
                raise HTTPException(400, f"未知行星: {a.planet_id}")
            out.append({"type": "slingshot", "planet_id": a.planet_id,
                        "b": max(-0.2, min(0.2, a.b or 0.0))})
    return out


def _sim_response(lv, actions, with_stars: bool):
    r = physics.integrate(lv, actions)
    resp = {
        "ok": r["ok"],
        "reason": r["reason"],
        "elapsed_days": r["elapsed_days"],
        "fuel_used": r["fuel_used"],
        "budget_dv": r["budget_dv"],
        "milestones": r["milestones"],
        "events": r["events"],
        "trajectory": r["trajectory"],
    }
    if with_stars:
        stars = physics.stars_for(lv, r["fuel_used"], r["ok"])
        resp["stars"] = stars
    return resp


@router.get("/system")
def system_info():
    bodies = []
    for b in physics.BODIES:
        if b["id"] == "sun":
            bodies.append({"id": b["id"], "name": b["name"], "color": b["color"],
                           "radius": b["radius"]})
        else:
            bodies.append({"id": b["id"], "name": b["name"], "color": b["color"],
                           "radius": b["radius"], "orbit": b["orbit"],
                           "period": b["period"], "theta0": b["theta0"],
                           "soi": physics._soi_radius(b["id"])})
    levels = [{"id": lv["id"], "name": lv["name"], "brief": lv["brief"],
               "milestones": lv["milestones"], "budget_dv": lv["budget_dv"],
               "t_max": lv["t_max"], "hint": lv["hint"]} for lv in LEVELS]
    with SessionLocal() as db:
        scores = scores_svc.best_summaries(db)
    return {"bodies": bodies, "levels": levels, "scores": scores}


def _resolve_level(db, req: SimRequest):
    """定位结算关卡：主线关卡，或挑战当前版本（返回 (level, run_level_id)）。

    run_level_id 是写入执行档案的标识：主线为关卡 id，挑战为负数合成键。
    """
    if req.challenge_id is not None:
        try:
            return challenges_svc.current_level(db, req.challenge_id)
        except challenges_svc.ChallengeNotFound as e:
            raise HTTPException(404, str(e))
    if req.level_id is None:
        raise HTTPException(400, "缺少 level_id 或 challenge_id")
    lv = LEVEL_BY_ID.get(req.level_id)
    if lv is None:
        raise HTTPException(404, "关卡不存在")
    return lv, lv["id"]


@router.post("/preview")
def preview(req: SimRequest):
    with SessionLocal() as db:
        lv, _ = _resolve_level(db, req)
    return _sim_response(lv, _validate(req.actions), with_stars=False)


@router.post("/run")
def run(req: SimRequest):
    with SessionLocal() as db:
        lv, run_level_id = _resolve_level(db, req)
        actions = _validate(req.actions)
        resp = _sim_response(lv, actions, with_stars=True)
        # 执行档案落库：成绩记录通过 run_id 关联到本次执行，实现可溯源与回放
        rec = scores_svc.record_run(db, run_level_id, actions, resp)
        resp["run_id"] = rec.run_uid
        if req.challenge_id is not None:
            resp["challenge_id"] = req.challenge_id
            resp["version"] = int(str(lv["id"]).split("v")[-1])
        return resp


@router.post("/score")
def save_score(req: ScoreRequest):
    if LEVEL_BY_ID.get(req.level_id) is None:
        raise HTTPException(404, "关卡不存在")
    if req.run_id is None and req.stars is None:
        raise HTTPException(400, "缺少 run_id 或 stars")
    try:
        with SessionLocal() as db:
            return scores_svc.save_score(
                db,
                level_id=req.level_id,
                submission_id=req.submission_id,
                run_id=req.run_id,
                stars=req.stars,
                fuel_used=req.fuel_used,
                elapsed_days=req.elapsed_days,
            )
    except scores_svc.RunNotFound as e:
        raise HTTPException(404, str(e))
    except scores_svc.RunLevelMismatch as e:
        raise HTTPException(400, str(e))


@router.get("/scores/{level_id}/records")
def score_records(level_id: int, limit: int = 20):
    """某关的成绩提交记录（新→旧），用于成绩追溯。"""
    if LEVEL_BY_ID.get(level_id) is None:
        raise HTTPException(404, "关卡不存在")
    with SessionLocal() as db:
        return {"records": scores_svc.list_records(db, level_id, limit=max(1, min(100, limit)))}


@router.get("/records/{record_id}")
def record_detail(record_id: int):
    """成绩记录详情：含动作方案与轨迹（关联执行档案时），供前端回放。"""
    with SessionLocal() as db:
        detail = scores_svc.record_detail(db, record_id)
    if detail is None:
        raise HTTPException(404, "成绩记录不存在")
    return detail


# ---------------------------------------------------------------- 社区航线挑战

def _challenge_errors(e):
    """挑战业务异常 → HTTP 状态码。"""
    if isinstance(e, (challenges_svc.ChallengeNotFound,
                      challenges_svc.EntryNotFound,
                      challenges_svc.RunNotFound)):
        return HTTPException(404, str(e))
    return HTTPException(400, str(e))


@router.get("/challenges")
def challenges():
    """挑战中心首页：全部挑战（含解锁状态）+ 已解锁组数。"""
    with SessionLocal() as db:
        return challenges_svc.list_challenges(db)


@router.post("/challenges")
def create_challenge(req: ChallengeCreateRequest):
    """设计者发布新挑战（含首个版本 v1）。"""
    try:
        with SessionLocal() as db:
            return challenges_svc.create_challenge(
                db, slug=req.slug, title=req.title, designer=req.designer,
                brief=req.brief, milestones=req.milestones,
                budget_dv=req.budget_dv, t_max=req.t_max, hint=req.hint)
    except (challenges_svc.ChallengeError, challenges_svc.ChallengeNotFound) as e:
        raise _challenge_errors(e)


@router.get("/challenges/{challenge_id}")
def challenge_detail(challenge_id: int):
    """挑战详情：当前版本关卡定义 + 历史版本列表。"""
    try:
        with SessionLocal() as db:
            return challenges_svc.challenge_detail(db, challenge_id)
    except challenges_svc.ChallengeNotFound as e:
        raise _challenge_errors(e)


@router.post("/challenges/{challenge_id}/versions")
def publish_version(challenge_id: int, req: ChallengeVersionRequest):
    """发布新版本：旧版本快照保留，成绩口径可复现。"""
    try:
        with SessionLocal() as db:
            return challenges_svc.publish_version(
                db, challenge_id, milestones=req.milestones,
                budget_dv=req.budget_dv, t_max=req.t_max, hint=req.hint)
    except (challenges_svc.ChallengeError, challenges_svc.ChallengeNotFound) as e:
        raise _challenge_errors(e)


@router.post("/challenges/{challenge_id}/entries")
def submit_entry(challenge_id: int, req: ChallengeEntryRequest):
    """提交飞行记录（幂等）：分数以执行档案的服务端结算为准，默认待审核。"""
    try:
        with SessionLocal() as db:
            return challenges_svc.submit_entry(
                db, challenge_id=challenge_id, run_id=req.run_id,
                submission_id=req.submission_id, player=req.player or "")
    except (challenges_svc.ChallengeError, challenges_svc.ChallengeNotFound,
            challenges_svc.RunNotFound, challenges_svc.RunChallengeMismatch) as e:
        raise _challenge_errors(e)


@router.get("/challenges/{challenge_id}/entries")
def challenge_entries(challenge_id: int, status: Optional[str] = None,
                      version: Optional[int] = None, limit: int = 50):
    """飞行记录列表（新→旧），可按审核状态/版本过滤（审核队列用 status=pending）。"""
    try:
        with SessionLocal() as db:
            return {"entries": challenges_svc.list_entries(
                db, challenge_id, status=status, version=version, limit=limit)}
    except (challenges_svc.ChallengeError, challenges_svc.ChallengeNotFound) as e:
        raise _challenge_errors(e)


@router.post("/challenges/{challenge_id}/entries/{entry_id}/review")
def review_entry(challenge_id: int, entry_id: int, req: ChallengeReviewRequest):
    """审核飞行记录：approve → 进排行榜并计入解锁；reject → 留痕不计成绩。"""
    try:
        with SessionLocal() as db:
            return challenges_svc.review_entry(db, challenge_id, entry_id,
                                               req.action, req.note)
    except (challenges_svc.ChallengeError, challenges_svc.EntryNotFound) as e:
        raise _challenge_errors(e)


@router.get("/challenges/{challenge_id}/leaderboard")
def challenge_leaderboard(challenge_id: int, version: Optional[int] = None,
                          limit: int = 20):
    """排行榜：仅已审核通过且任务达成的记录。"""
    try:
        with SessionLocal() as db:
            return challenges_svc.leaderboard(db, challenge_id, version=version,
                                              limit=limit)
    except challenges_svc.ChallengeNotFound as e:
        raise _challenge_errors(e)


@router.get("/challenges/{challenge_id}/entries/{entry_id}")
def challenge_entry_detail(challenge_id: int, entry_id: int):
    """飞行记录详情：动作方案 + 轨迹（供回放）。"""
    try:
        with SessionLocal() as db:
            detail = challenges_svc.entry_detail(db, entry_id)
    except challenges_svc.EntryNotFound as e:
        raise _challenge_errors(e)
    if detail["challenge_id"] != challenge_id:
        raise HTTPException(404, "飞行记录不属于该挑战")
    return detail
