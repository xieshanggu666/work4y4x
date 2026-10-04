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
};
