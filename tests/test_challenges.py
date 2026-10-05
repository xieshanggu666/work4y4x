"""社区航线挑战测试：版本化发布 / 幂等结算 / 审核联动 / 排行榜 / 回放 / 解锁。"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 测试使用独立数据库文件（须在导入 app 模块前设置）
os.environ["SLINGSHOT_DB_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="slingshot_ch_test_"), "test.db")

import pytest
from fastapi import HTTPException

from app.core.database import Base, SessionLocal, engine
from app.models import Challenge, ChallengeEntry, ChallengeVersion, LevelScore, RunRecord, ScoreRecord
from app.api import router as api


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for t in (ChallengeEntry, ChallengeVersion, Challenge,
                  ScoreRecord, RunRecord, LevelScore):
            db.query(t).delete()
        db.commit()
        yield
    finally:
        db.close()


# 一个可解的挑战关卡：点火 0.0017 AU/天（90°）即可越过 1.4 AU（参考主线第 1 关）
CH_DEF = dict(
    slug="mars-hop", title="火星跳跃", designer="设计者A", brief="越过 1.4 AU 即可",
    milestones=[{"id": "m1", "kind": "radius", "r": 1.4, "name": "越过 1.4 AU"}],
    budget_dv=0.004, t_max=900, hint="切向点火即可",
)
SOLVE_ACTIONS = [api.Action(type="burn", angle=90.0, dv=0.0017)]


def _create(**over):
    body = {**CH_DEF, **over}
    return api.create_challenge(api.ChallengeCreateRequest(**body))


def _run_challenge(challenge_id, actions=None):
    """以挑战模式执行一次任务，返回结算结果（含 run_id）。"""
    req = api.SimRequest(challenge_id=challenge_id,
                         actions=actions if actions is not None else SOLVE_ACTIONS)
    return api.run(req)


def _submit(challenge_id, run_id, sub, player="飞手甲"):
    return api.submit_entry(challenge_id, api.ChallengeEntryRequest(
        run_id=run_id, submission_id=sub, player=player))


def _unlock_mainline(level_id, stars=1):
    db = SessionLocal()
    db.add(LevelScore(level_id=level_id, stars=stars, fuel_used=0.001,
                      elapsed_days=100.0))
    db.commit()
    db.close()


# ---------- 发布与版本化 ----------

def test_create_challenge_with_first_version():
    c = _create()
    assert c["id"] and c["current_version"] == 1
    assert c["budget_dv"] == CH_DEF["budget_dv"]
    assert c["milestones"][0]["kind"] == "radius"
    assert c["unlocked"] is True  # 第 1 组默认开放
    detail = api.challenge_detail(c["id"])
    assert len(detail["versions"]) == 1 and detail["versions"][0]["version"] == 1


def test_create_challenge_validation():
    with pytest.raises(HTTPException) as e1:  # slug 非法
        _create(slug="Bad Slug!")
    assert e1.value.status_code == 400
    with pytest.raises(HTTPException) as e2:  # 预算超范围
        _create(slug="ok-slug", budget_dv=0.5)
    assert e2.value.status_code == 400
    with pytest.raises(HTTPException) as e3:  # 未知里程碑类型
        _create(slug="ok-slug", milestones=[{"kind": "warp", "name": "x"}])
    assert e3.value.status_code == 400
    with pytest.raises(HTTPException) as e4:  # 没有里程碑
        _create(slug="ok-slug", milestones=[])
    assert e4.value.status_code == 400


def test_create_challenge_slug_unique():
    _create()
    with pytest.raises(HTTPException) as e:
        _create()  # 同 slug 重复发布
    assert e.value.status_code == 400


def test_publish_new_version_keeps_old_snapshot():
    c = _create()
    d = api.publish_version(c["id"], api.ChallengeVersionRequest(
        milestones=[{"id": "m1", "kind": "radius", "r": 2.0, "name": "越过 2 AU"}],
        budget_dv=0.006, t_max=1200, hint="新版本"))
    assert d["current_version"] == 2
    assert d["budget_dv"] == 0.006
    assert len(d["versions"]) == 2
    # 旧版本快照保留（结算口径可复现）
    v1 = [v for v in d["versions"] if v["version"] == 1][0]
    assert v1["budget_dv"] == CH_DEF["budget_dv"]
    assert v1["milestones"][0]["r"] == 1.4
    # 详情展示当前版本
    assert d["milestones"][0]["r"] == 2.0


def test_versioned_leaderboards_isolated():
    """同一挑战不同版本的排行榜互相隔离。"""
    c = _create()
    cid = c["id"]
    r1 = _run_challenge(cid)
    _submit(cid, r1["run_id"], "v1-sub")
    api.publish_version(cid, api.ChallengeVersionRequest(
        milestones=CH_DEF["milestones"], budget_dv=0.006, t_max=900))
    r2 = _run_challenge(cid)
    _submit(cid, r2["run_id"], "v2-sub")
    # 各自版本审核通过
    e1 = api.challenge_entries(cid, version=1)["entries"][0]
    e2 = api.challenge_entries(cid, version=2)["entries"][0]
    api.review_entry(cid, e1["entry_id"], api.ChallengeReviewRequest(action="approve"))
    api.review_entry(cid, e2["entry_id"], api.ChallengeReviewRequest(action="approve"))
    lb1 = api.challenge_leaderboard(cid, version=1)
    lb2 = api.challenge_leaderboard(cid)  # 默认当前版本
    assert [e["entry_id"] for e in lb1["entries"]] == [e1["entry_id"]]
    assert [e["entry_id"] for e in lb2["entries"]] == [e2["entry_id"]]
    assert lb1["entries"][0]["version"] == 1
    assert lb2["entries"][0]["version"] == 2


# ---------- 挑战模式执行与幂等结算 ----------

def test_challenge_run_settles_with_version_key():
    c = _create()
    r = _run_challenge(c["id"])
    assert r["ok"] and r["stars"] == 3 and r["challenge_id"] == c["id"]
    assert r["version"] == 1
    db = SessionLocal()
    run = db.query(RunRecord).filter(RunRecord.run_uid == r["run_id"]).one()
    assert run.level_id < 0  # 挑战执行档案使用负数合成键，与主线隔离
    db.close()


def test_challenge_preview_uses_current_version():
    c = _create()
    r = api.preview(api.SimRequest(challenge_id=c["id"], actions=SOLVE_ACTIONS))
    assert r["ok"] and "stars" not in r  # 预览不评分
    with pytest.raises(HTTPException) as e:
        api.preview(api.SimRequest(challenge_id=9999, actions=[]))
    assert e.value.status_code == 404


def test_submit_entry_idempotent():
    c = _create()
    r = _run_challenge(c["id"])
    a = _submit(c["id"], r["run_id"], "sub-1")
    b = _submit(c["id"], r["run_id"], "sub-1")
    assert a["entry_id"] == b["entry_id"]
    assert b["duplicated"] is True and a["duplicated"] is False
    assert a["status"] == "pending"
    db = SessionLocal()
    assert db.query(ChallengeEntry).count() == 1
    db.close()


def test_submit_entry_concurrent_single_record():
    """同一提交并发到达（双击/重试/多标签页）：只落库一条，结果一致。"""
    c = _create()
    r = _run_challenge(c["id"])
    results = []

    def worker():
        results.append(_submit(c["id"], r["run_id"], "race-1"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(x["saved"] for x in results)
    assert len({x["entry_id"] for x in results}) == 1
    db = SessionLocal()
    assert db.query(ChallengeEntry).count() == 1
    db.close()


def test_submit_entry_server_side_settlement():
    """结算以服务端执行档案为准：提交体不携带分数，无法伪造。"""
    c = _create()
    r = _run_challenge(c["id"])
    e = _submit(c["id"], r["run_id"], "settle-1")
    assert e["stars"] == r["stars"] == 3
    assert abs(e["fuel_used"] - r["fuel_used"]) < 1e-9
    # 失败任务的记录：ok=False、0 星，审核通过也不进排行榜
    r_fail = _run_challenge(c["id"], actions=[])
    ef = _submit(c["id"], r_fail["run_id"], "settle-2")
    assert ef["ok"] is False and ef["stars"] == 0
    api.review_entry(c["id"], ef["entry_id"], api.ChallengeReviewRequest(action="approve"))
    assert api.challenge_leaderboard(c["id"])["entries"] == []


def test_submit_entry_rejects_bad_run():
    c = _create()
    with pytest.raises(HTTPException) as e1:  # 执行档案不存在
        _submit(c["id"], "nonexistent", "s1")
    assert e1.value.status_code == 404
    # 主线关卡的执行档案不能冒充挑战记录
    mainline = api.run(api.SimRequest(level_id=1, actions=SOLVE_ACTIONS))
    with pytest.raises(HTTPException) as e2:
        _submit(c["id"], mainline["run_id"], "s2")
    assert e2.value.status_code == 400
    # 发新版本后，旧版本的执行档案不能再提交（版本口径一致）
    r_old = _run_challenge(c["id"])
    api.publish_version(c["id"], api.ChallengeVersionRequest(
        milestones=CH_DEF["milestones"], budget_dv=0.005, t_max=900))
    with pytest.raises(HTTPException) as e3:
        _submit(c["id"], r_old["run_id"], "s3")
    assert e3.value.status_code == 400


def test_challenge_run_not_counted_as_mainline_score():
    """挑战执行档案（负数合成键）无法提交到主线成绩接口。"""
    c = _create()
    r = _run_challenge(c["id"])
    with pytest.raises(HTTPException) as e:
        api.save_score(api.ScoreRequest(level_id=1, run_id=r["run_id"]))
    assert e.value.status_code == 400


# ---------- 审核联动：排行榜 / 解锁 ----------

def test_review_gates_leaderboard():
    """pending/rejected 不进排行榜，approve 后进入，驳回后移出。"""
    c = _create()
    cid = c["id"]
    r = _run_challenge(cid)
    e = _submit(cid, r["run_id"], "rev-1")
    assert api.challenge_leaderboard(cid)["entries"] == []  # 待审核不上榜
    # 驳回 → 留痕但不上榜
    rej = api.review_entry(cid, e["entry_id"], api.ChallengeReviewRequest(
        action="reject", note="轨迹异常"))
    assert rej["status"] == "rejected" and rej["review_note"] == "轨迹异常"
    assert api.challenge_leaderboard(cid)["entries"] == []
    assert len(api.challenge_entries(cid)["entries"]) == 1  # 记录保留可溯源
    # 改判通过 → 上榜
    api.review_entry(cid, e["entry_id"], api.ChallengeReviewRequest(action="approve"))
    lb = api.challenge_leaderboard(cid)["entries"]
    assert len(lb) == 1 and lb[0]["rank"] == 1 and lb[0]["player"] == "飞手甲"


def test_leaderboard_ordering():
    """星数优先，同星比燃料，再比耗时。"""
    c = _create()
    cid = c["id"]
    # 三星（省燃料）vs 一星（烧光预算内更多燃料）
    r3 = _run_challenge(cid)
    e3 = _submit(cid, r3["run_id"], "ord-3", player="省燃料选手")
    r1 = _run_challenge(cid, actions=[
        api.Action(type="burn", angle=90.0, dv=0.0017),
        api.Action(type="burn", angle=0.0, dv=0.0015),
    ])
    e1 = _submit(cid, r1["run_id"], "ord-1", player="费燃料选手")
    assert e1["stars"] < e3["stars"]
    for eid in (e3["entry_id"], e1["entry_id"]):
        api.review_entry(cid, eid, api.ChallengeReviewRequest(action="approve"))
    lb = api.challenge_leaderboard(cid)["entries"]
    assert [x["player"] for x in lb] == ["省燃料选手", "费燃料选手"]
    assert lb[0]["stars"] > lb[1]["stars"]


def test_review_unknown_entry_404():
    c = _create()
    with pytest.raises(HTTPException) as e:
        api.review_entry(c["id"], 9999, api.ChallengeReviewRequest(action="approve"))
    assert e.value.status_code == 404
    r = _run_challenge(c["id"])
    ent = _submit(c["id"], r["run_id"], "x-1")
    with pytest.raises(HTTPException) as e2:  # 非法审核动作
        api.review_entry(c["id"], ent["entry_id"],
                         api.ChallengeReviewRequest(action="maybe"))
    assert e2.value.status_code == 400


def test_review_cross_challenge_rejected():
    """不能通过挑战 B 的接口审核挑战 A 的飞行记录。"""
    ca = _create()
    cb = _create(slug="ch-b", title="挑战B")
    r = _run_challenge(ca["id"])
    ent = _submit(ca["id"], r["run_id"], "cross-1")
    with pytest.raises(HTTPException) as e:
        api.review_entry(cb["id"], ent["entry_id"],
                         api.ChallengeReviewRequest(action="approve"))
    assert e.value.status_code == 404
    # 记录仍为待审核，未被跨挑战接口改动
    got = api.challenge_entries(ca["id"])["entries"][0]
    assert got["status"] == "pending"


# ---------- 解锁联动 ----------

def test_unlock_follows_mainline_progress():
    """第 1 组默认开放；主线每通关一关多开放一组。"""
    made = [_create(slug=f"ch-{i}", title=f"挑战{i}") for i in range(1, 5)]
    lst = {c["id"]: c for c in api.challenges()["challenges"]}
    assert api.challenges()["unlocked_groups"] == 1
    assert [lst[c["id"]]["unlocked"] for c in made] == [True, True, True, False]
    # 主线第 1 关通关 → 第 2 组开放
    _unlock_mainline(1)
    lst = {c["id"]: c for c in api.challenges()["challenges"]}
    assert lst[made[3]["id"]]["unlocked"] is True


# ---------- 回放 ----------

def test_entry_detail_replay():
    c = _create()
    r = _run_challenge(c["id"])
    e = _submit(c["id"], r["run_id"], "replay-1")
    d = api.challenge_entry_detail(c["id"], e["entry_id"])
    assert d["ok"] and d["trajectory"] and d["actions"][0]["type"] == "burn"
    assert d["events"] and d["milestones"]
    assert d["run_uid"] == r["run_id"]
    with pytest.raises(HTTPException) as e1:
        api.challenge_entry_detail(c["id"], 9999)
    assert e1.value.status_code == 404
    # 记录不属于该挑战 → 404
    c2 = _create(slug="other-ch", title="另一个")
    with pytest.raises(HTTPException) as e2:
        api.challenge_entry_detail(c2["id"], e["entry_id"])
    assert e2.value.status_code == 404
