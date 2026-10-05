const API = {
  async _req(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { const j = await r.json(); msg = j.detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  },
  system() { return this._req("/api/system"); },
  preview(level_id, actions) {
    return this._req("/api/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level_id, actions }),
    });
  },
  run(level_id, actions) {
    return this._req("/api/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level_id, actions }),
    });
  },
  // 提交成绩：run_id 关联服务端执行档案（可溯源），submission_id 为幂等键
  saveScore(level_id, run_id, submission_id) {
    return this._req("/api/score", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level_id, run_id, submission_id }),
    });
  },
  records(level_id) { return this._req(`/api/scores/${level_id}/records`); },
  record(record_id) { return this._req(`/api/records/${record_id}`); },

  // ---------- 社区航线挑战 ----------
  challenges() { return this._req("/api/challenges"); },
  challenge(id) { return this._req(`/api/challenges/${id}`); },
  createChallenge(body) {
    return this._req("/api/challenges", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  publishVersion(id, body) {
    return this._req(`/api/challenges/${id}/versions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  // 挑战模式执行任务/预览：按挑战当前版本结算
  runChallenge(challenge_id, actions) {
    return this._req("/api/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ challenge_id, actions }),
    });
  },
  previewChallenge(challenge_id, actions) {
    return this._req("/api/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ challenge_id, actions }),
    });
  },
  // 提交飞行记录：run_id 关联执行档案（服务端结算），submission_id 为幂等键
  submitEntry(challenge_id, run_id, submission_id, player) {
    return this._req(`/api/challenges/${challenge_id}/entries`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id, submission_id, player }),
    });
  },
  challengeEntries(id, status) {
    const q = status ? `?status=${status}` : "";
    return this._req(`/api/challenges/${id}/entries${q}`);
  },
  reviewEntry(id, entry_id, action, note) {
    return this._req(`/api/challenges/${id}/entries/${entry_id}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, note }),
    });
  },
  leaderboard(id, version) {
    const q = version ? `?version=${version}` : "";
    return this._req(`/api/challenges/${id}/leaderboard${q}`);
  },
  entryDetail(id, entry_id) {
    return this._req(`/api/challenges/${id}/entries/${entry_id}`);
  },
};
