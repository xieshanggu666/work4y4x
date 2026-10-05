/* 视图：社区航线挑战中心
 * 设计者发布版本化关卡（预算 + 里程碑），玩家挑战并提交飞行记录，
 * 审核通过后进入排行榜；记录可回放；挑战组随主线进度解锁。 */
window.MilestoneEditor = {
  name: "MilestoneEditor",
  props: ["items", "bodies"],
  computed: {
    planets() { return (this.bodies || []).filter(b => b.id !== "sun"); },
  },
  methods: {
    add() {
      this.items.push({ id: "m" + (this.items.length + 1), kind: "radius",
                        r: 4.2, dist: 0.18, planet_id: "mars", name: "" });
    },
    remove(i) { this.items.splice(i, 1); },
    kindLabel(k) {
      return { proximity: "近距离飞掠", radius: "抵达半径",
               assist_capture: "弹弓捕获", assist_then_radius: "弹弓后抵达半径",
               escape: "逃逸出界" }[k] || k;
    },
  },
  template: `
  <div class="ms-editor">
    <div v-for="(m, i) in items" :key="i" class="ms-row">
      <div class="ms-row-head">
        <span class="step-idx">{{ i + 1 }}</span>
        <select v-model="m.kind">
          <option value="proximity">近距离飞掠</option>
          <option value="radius">抵达半径</option>
          <option value="assist_capture">弹弓捕获</option>
          <option value="assist_then_radius">弹弓后抵达半径</option>
          <option value="escape">逃逸出界</option>
        </select>
        <button class="icon-btn danger" @click="remove(i)">✕</button>
      </div>
      <div class="ms-row-body">
        <template v-if="m.kind === 'proximity' || m.kind === 'assist_capture' || m.kind === 'assist_then_radius'">
          <label>行星</label>
          <select v-model="m.planet_id">
            <option v-for="p in planets" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
        </template>
        <template v-if="m.kind === 'proximity'">
          <label>距离 (AU)</label>
          <input type="number" v-model.number="m.dist" min="0.01" max="5" step="0.01">
        </template>
        <template v-if="m.kind === 'radius' || m.kind === 'assist_then_radius' || m.kind === 'escape'">
          <label>半径 (AU)</label>
          <input type="number" v-model.number="m.r" min="1.1" max="60" step="0.1">
        </template>
        <label>名称</label>
        <input type="text" v-model="m.name" maxlength="40" placeholder="如：穿越小行星带">
      </div>
    </div>
    <button v-if="items.length < 5" class="btn chip" @click="add">+ 添加里程碑</button>
  </div>`,
};

window.ChallengeList = {
  name: "ChallengeList",
  components: { MilestoneEditor: window.MilestoneEditor },
  props: ["bodies"],
  emits: ["back", "play"],
  data() {
    const blank = () => ({
      designer: localStorage.getItem("slingshot_designer") || "",
      title: "", slug: "", brief: "", hint: "",
      budget_kms: 10.0, t_max: 1500, milestones: [],
    });
    return {
      list: null,
      unlockedGroups: 1,
      groupSize: 3,
      error: "",
      // 详情
      detail: null,
      tab: "board",
      lb: null,
      lbVersion: null,
      entries: [],
      pending: [],
      // 表单
      showPublish: false,
      showVersion: false,
      form: blank(),
      vform: null,
      busy: false,
      // 回放
      replay: null,
      player: localStorage.getItem("slingshot_player") || "",
    };
  },
  computed: {
    challenges() { return (this.list && this.list.challenges) || []; },
    pendingCount() {
      if (!this.detail) return 0;
      return this.pending.length;
    },
    versions() { return (this.detail && this.detail.versions) || []; },
  },
  async created() { await this.reload(); },
  beforeUnmount() { this._stopReplay(); },
  methods: {
    kmps(v) { return (v * 1731.5).toFixed(1); },
    fmtDate(ts) {
      if (!ts) return "";
      const d = new Date(ts * 1000);
      const p = n => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    },
    statusLabel(s) {
      return { pending: "待审核", approved: "已通过", rejected: "已驳回" }[s] || s;
    },
    msNames(ms) { return (ms || []).map(m => m.name).join(" · "); },

    async reload() {
      this.error = "";
      try {
        this.list = await API.challenges();
        this.unlockedGroups = this.list.unlocked_groups;
        this.groupSize = this.list.group_size;
        if (this.detail) await this.open(this.detail.id, true);
      } catch (e) {
        this.error = "加载挑战失败：" + e.message;
      }
    },

    // ---- 详情 / 排行榜 / 审核队列 ----
    async open(id, keepTab) {
      try {
        this.detail = await API.challenge(id);
        this.lbVersion = this.detail.current_version;
        if (!keepTab) this.tab = "board";
        await Promise.all([this.loadBoard(), this.loadEntries(), this.loadPending()]);
      } catch (e) {
        alert("加载挑战详情失败：" + e.message);
      }
    },
    closeDetail() { this.detail = null; this._stopReplay(); this.replay = null; },
    async loadBoard() {
      if (!this.detail) return;
      this.lb = await API.leaderboard(this.detail.id, this.lbVersion);
    },
    async loadEntries() {
      if (!this.detail) return;
      this.entries = (await API.challengeEntries(this.detail.id)).entries;
    },
    async loadPending() {
      if (!this.detail) return;
      this.pending = (await API.challengeEntries(this.detail.id, "pending")).entries;
    },
    async review(entry, action) {
      let note = "";
      if (action === "reject") {
        const n = window.prompt("驳回原因（可选，留痕可溯）：", "");
        if (n === null) return;  // 取消驳回
        note = n;
      }
      try {
        await API.reviewEntry(this.detail.id, entry.entry_id, action, note);
        await this.reload();  // 审核结果联动排行榜 / 待审队列 / 卡片计数
      } catch (e) {
        alert("审核失败：" + e.message);
      }
    },

    // ---- 发布挑战 / 新版本 ----
    openPublish() {
      this.showPublish = true;
      this.error = "";
    },
    async submitPublish() {
      if (this.busy) return;
      const f = this.form;
      if (!f.milestones.length) { alert("至少添加一个里程碑"); return; }
      this.busy = true;
      try {
        const body = {
          designer: f.designer.trim(), title: f.title.trim(),
          slug: f.slug.trim().toLowerCase(), brief: f.brief.trim(),
          hint: f.hint.trim(),
          budget_dv: f.budget_kms / 1731.5,
          t_max: f.t_max,
          milestones: f.milestones.map(m => ({ ...m })),
        };
        const c = await API.createChallenge(body);
        localStorage.setItem("slingshot_designer", f.designer.trim());
        this.showPublish = false;
        this.form = {
          designer: f.designer, title: "", slug: "", brief: "", hint: "",
          budget_kms: 10.0, t_max: 1500, milestones: [],
        };
        await this.reload();
        await this.open(c.id);
      } catch (e) {
        alert("发布失败：" + e.message);
      } finally {
        this.busy = false;
      }
    },
    openVersion() {
      const d = this.detail;
      this.vform = {
        budget_kms: d.budget_dv * 1731.5,
        t_max: d.t_max,
        hint: d.hint || "",
        milestones: d.milestones.map(m => ({ ...m })),
      };
      this.showVersion = true;
    },
    async submitVersion() {
      if (this.busy || !this.vform) return;
      if (!this.vform.milestones.length) { alert("至少添加一个里程碑"); return; }
      this.busy = true;
      try {
        await API.publishVersion(this.detail.id, {
          budget_dv: this.vform.budget_kms / 1731.5,
          t_max: this.vform.t_max,
          hint: this.vform.hint.trim(),
          milestones: this.vform.milestones.map(m => ({ ...m })),
        });
        this.showVersion = false;
        await this.reload();
        alert("新版本已发布：旧版本成绩保留，排行榜按版本隔离。");
      } catch (e) {
        alert("发布失败：" + e.message);
      } finally {
        this.busy = false;
      }
    },

    // ---- 进入挑战 ----
    play() {
      if (!this.detail) return;
      localStorage.setItem("slingshot_player", this.player.trim());
      this.$emit("play", this.detail.id);
    },

    // ---- 回放 ----
    async replayEntry(entry) {
      try {
        const d = await API.entryDetail(this.detail.id, entry.entry_id);
        if (!d.trajectory || !d.trajectory.length) {
          alert("该记录没有可回放的轨迹。");
          return;
        }
        this.replay = { detail: d, time: 0, playing: true };
        this.$nextTick(() => this._startReplay());
      } catch (e) {
        alert("加载回放失败：" + e.message);
      }
    },
    _startReplay() {
      const cv = this.$refs.replayCv;
      if (!cv || !this.replay) return;
      const ctx = cv.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const rect = cv.getBoundingClientRect();
      cv.width = rect.width * dpr;
      cv.height = rect.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const w = rect.width, h = rect.height;
      // 视野适配整条轨迹
      let maxR = 1.2;
      for (const p of this.replay.detail.trajectory) {
        maxR = Math.max(maxR, Math.abs(p[0]), Math.abs(p[1]));
      }
      const scale = 0.44 * Math.min(w, h) / maxR;
      const level = { milestones: this.detail ? this.detail.milestones : [] };
      const total = this.replay.detail.elapsed_days || 1;
      const loop = () => {
        if (!this.replay) return;
        this._replayRaf = requestAnimationFrame(loop);
        ctx.clearRect(0, 0, w, h);
        System.draw(ctx, w, h, {
          bodies: this.bodies,
          view: { scale, cx: w / 2, cy: h / 2 },
          time: this.replay.time,
          traj: this.replay.detail.trajectory,
          events: this.replay.detail.events,
          level, probeGlow: 5,
        });
        if (this.replay.playing) {
          this.replay.time += total / 480;  // 约 8 秒播完
          if (this.replay.time >= total) this.replay.time = 0;  // 循环播放
        }
      };
      loop();
    },
    toggleReplayPlay() { if (this.replay) this.replay.playing = !this.replay.playing; },
    closeReplay() { this._stopReplay(); this.replay = null; },
    _stopReplay() {
      if (this._replayRaf) cancelAnimationFrame(this._replayRaf);
      this._replayRaf = null;
    },
  },
  template: `
  <div class="screen levels-screen">
    <header class="topbar">
      <div class="logo">
        <div class="logo-mark">✦</div>
        <div>
          <h1>社区航线挑战</h1>
          <p>设计者发布版本化关卡 · 飞行记录审核上榜 · 已解锁 {{ unlockedGroups }} 组</p>
        </div>
      </div>
      <div class="topbar-right">
        <button class="btn ghost" @click="$emit('back')">← 返回主线</button>
        <button class="btn primary" @click="openPublish">＋ 发布挑战</button>
      </div>
    </header>

    <p v-if="error" class="ch-error">{{ error }}</p>

    <!-- 挑战卡片 -->
    <div class="level-grid" v-if="!detail">
      <div v-for="c in challenges" :key="c.id" class="level-card ch-card"
           :class="{ locked: !c.unlocked }" @click="c.unlocked && open(c.id)">
        <div class="level-num">第 {{ c.group }} 组 · v{{ c.current_version }} · by {{ c.designer }}</div>
        <h2>{{ c.title }}</h2>
        <p class="brief">{{ c.brief || msNames(c.milestones) }}</p>
        <div class="best-meta">
          预算 {{ kmps(c.budget_dv) }} km/s · 限时 {{ Math.round(c.t_max) }} 天
          <span v-if="c.approved_count" class="replayable-tag">{{ c.approved_count }} 条成绩</span>
          <span v-if="c.pending_count" class="pending-tag">{{ c.pending_count }} 待审</span>
        </div>
        <div class="level-foot">
          <span class="stars">🏁 {{ c.milestones.length }} 个里程碑</span>
          <span v-if="!c.unlocked" class="lock-tag">🔒 通关主线第 {{ c.group }} 关解锁</span>
          <span v-else class="go-btn">查看挑战 →</span>
        </div>
      </div>
      <div v-if="!challenges.length" class="ch-empty">
        还没有社区挑战。点击右上角「＋ 发布挑战」，设计第一条航线吧！
      </div>
    </div>

    <!-- 挑战详情 -->
    <div v-else class="ch-detail">
      <div class="ch-detail-head">
        <button class="btn ghost" @click="closeDetail">← 挑战列表</button>
        <div class="ch-detail-title">
          <h2>{{ detail.title }} <span class="ch-ver">v{{ detail.current_version }}</span></h2>
          <p>{{ detail.brief || msNames(detail.milestones) }}</p>
          <p class="ch-meta">
            设计者 {{ detail.designer }} · 预算 {{ kmps(detail.budget_dv) }} km/s ·
            限时 {{ Math.round(detail.t_max) }} 天 · 🏁 {{ msNames(detail.milestones) }}
          </p>
        </div>
        <div class="ch-detail-ops">
          <input class="player-input" v-model="player" maxlength="32" placeholder="你的呼号（上榜用）">
          <button class="btn primary" @click="play">🚀 接受挑战</button>
        </div>
      </div>

      <div class="ch-tabs">
        <button :class="{ sel: tab === 'board' }" @click="tab = 'board'">🏆 排行榜</button>
        <button :class="{ sel: tab === 'review' }" @click="tab = 'review'">
          🛂 审核队列 <span v-if="pendingCount" class="pending-tag">{{ pendingCount }}</span>
        </button>
        <button :class="{ sel: tab === 'entries' }" @click="tab = 'entries'">🧾 提交记录</button>
        <button :class="{ sel: tab === 'versions' }" @click="tab = 'versions'">🧬 版本</button>
      </div>

      <!-- 排行榜 -->
      <div v-if="tab === 'board'" class="ch-panel">
        <div v-if="versions.length > 1" class="ch-lbver">
          版本
          <select v-model.number="lbVersion" @change="loadBoard">
            <option v-for="v in versions" :key="v.version" :value="v.version">v{{ v.version }}</option>
          </select>
          的排行榜（仅已审核通过）
        </div>
        <table class="ch-table" v-if="lb && lb.entries.length">
          <thead><tr><th>#</th><th>飞手</th><th>星级</th><th>燃料</th><th>耗时</th><th></th></tr></thead>
          <tbody>
            <tr v-for="e in lb.entries" :key="e.entry_id">
              <td>{{ e.rank }}</td>
              <td>{{ e.player }}</td>
              <td>
                <template v-for="i in 3" :key="i">
                  <span :class="i <= e.stars ? 'lit' : 'dim'">★</span>
                </template>
              </td>
              <td>{{ kmps(e.fuel_used) }} km/s</td>
              <td>{{ Math.round(e.elapsed_days) }} 天</td>
              <td><button class="btn mini" @click="replayEntry(e)">▶ 回放</button></td>
            </tr>
          </tbody>
        </table>
        <p v-else class="ch-empty">还没有上榜成绩。成为第一个完成这条航线的人！</p>
      </div>

      <!-- 审核队列 -->
      <div v-if="tab === 'review'" class="ch-panel">
        <p class="ch-note">审核通过的记录进入排行榜并计入挑战进度；驳回留痕可回放，不计成绩。</p>
        <div v-for="e in pending" :key="e.entry_id" class="ch-entry">
          <span class="ch-entry-main">
            <b>{{ e.player }}</b> · v{{ e.version }} ·
            <template v-for="i in 3" :key="i"><span :class="i <= e.stars ? 'lit' : 'dim'">★</span></template>
            · {{ kmps(e.fuel_used) }} km/s · {{ Math.round(e.elapsed_days) }} 天
            <em v-if="!e.ok">（未达成：仍可审核留档）</em>
          </span>
          <span class="ch-entry-ops">
            <button class="btn mini" @click="replayEntry(e)">▶ 回放</button>
            <button class="btn mini ok-btn" @click="review(e, 'approve')">✓ 通过</button>
            <button class="btn mini danger-btn" @click="review(e, 'reject')">✕ 驳回</button>
          </span>
        </div>
        <p v-if="!pending.length" class="ch-empty">审核队列已清空。</p>
      </div>

      <!-- 提交记录 -->
      <div v-if="tab === 'entries'" class="ch-panel">
        <div v-for="e in entries" :key="e.entry_id" class="ch-entry">
          <span class="ch-entry-main">
            <b>{{ e.player }}</b> · v{{ e.version }} ·
            <template v-for="i in 3" :key="i"><span :class="i <= e.stars ? 'lit' : 'dim'">★</span></template>
            · {{ kmps(e.fuel_used) }} km/s · {{ Math.round(e.elapsed_days) }} 天
            · {{ fmtDate(e.created_at) }}
            <em v-if="e.review_note">· 备注：{{ e.review_note }}</em>
          </span>
          <span class="ch-entry-ops">
            <span class="status-tag" :class="e.status">{{ statusLabel(e.status) }}</span>
            <button class="btn mini" @click="replayEntry(e)">▶ 回放</button>
          </span>
        </div>
        <p v-if="!entries.length" class="ch-empty">还没有人提交飞行记录。</p>
      </div>

      <!-- 版本 -->
      <div v-if="tab === 'versions'" class="ch-panel">
        <div class="ch-ver-head">
          <p class="ch-note">每次发布生成不可变快照：旧版本成绩与排行榜保留，结算口径可复现。</p>
          <button class="btn chip" @click="openVersion">＋ 发布新版本</button>
        </div>
        <div v-for="v in versions" :key="v.version" class="ch-entry">
          <span class="ch-entry-main">
            <b>v{{ v.version }}</b>
            <span v-if="v.version === detail.current_version" class="replayable-tag">当前</span>
            · 预算 {{ kmps(v.budget_dv) }} km/s · 限时 {{ Math.round(v.t_max) }} 天
            · 🏁 {{ msNames(v.milestones) }} · {{ fmtDate(v.created_at) }}
          </span>
        </div>
      </div>
    </div>

    <!-- 发布挑战弹窗 -->
    <div v-if="showPublish" class="modal-mask" @click.self="showPublish = false">
      <div class="modal ch-modal">
        <h2>发布社区挑战</h2>
        <p class="ch-note">关卡以「里程碑 + 燃料/时间预算」定义，发布后生成 v1 快照。</p>
        <div class="ch-form">
          <div class="ch-form-row">
            <label>设计者</label><input v-model="form.designer" maxlength="32" placeholder="你的署名">
            <label>标识 slug</label><input v-model="form.slug" maxlength="32" placeholder="如 mars-hop（小写字母/数字/连字符）">
          </div>
          <div class="ch-form-row">
            <label>标题</label><input v-model="form.title" maxlength="64" placeholder="如：火星跳跃">
            <label>燃料预算 (km/s)</label><input type="number" v-model.number="form.budget_kms" min="2" max="50" step="0.5">
          </div>
          <div class="ch-form-row">
            <label>时间预算 (天)</label><input type="number" v-model.number="form.t_max" min="100" max="20000" step="50">
            <label>简介</label><input v-model="form.brief" maxlength="300" placeholder="一句话说明这条航线">
          </div>
          <label>里程碑（依次达成即通关）</label>
          <MilestoneEditor :items="form.milestones" :bodies="bodies" />
          <label>策略提示（可选）</label>
          <input v-model="form.hint" maxlength="300" placeholder="给挑战者一点提示">
        </div>
        <div class="modal-btns">
          <button class="btn ghost" @click="showPublish = false">取消</button>
          <button class="btn primary" :disabled="busy" @click="submitPublish">
            {{ busy ? '发布中…' : '发布 v1' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 发布新版本弹窗 -->
    <div v-if="showVersion && vform" class="modal-mask" @click.self="showVersion = false">
      <div class="modal ch-modal">
        <h2>发布新版本 · v{{ detail.current_version + 1 }}</h2>
        <p class="ch-note">旧版本快照与成绩保留；新成绩按新版本口径结算。</p>
        <div class="ch-form">
          <div class="ch-form-row">
            <label>燃料预算 (km/s)</label><input type="number" v-model.number="vform.budget_kms" min="2" max="50" step="0.5">
            <label>时间预算 (天)</label><input type="number" v-model.number="vform.t_max" min="100" max="20000" step="50">
          </div>
          <label>里程碑</label>
          <MilestoneEditor :items="vform.milestones" :bodies="bodies" />
          <label>策略提示</label>
          <input v-model="vform.hint" maxlength="300">
        </div>
        <div class="modal-btns">
          <button class="btn ghost" @click="showVersion = false">取消</button>
          <button class="btn primary" :disabled="busy" @click="submitVersion">
            {{ busy ? '发布中…' : '发布 v' + (detail.current_version + 1) }}
          </button>
        </div>
      </div>
    </div>

    <!-- 回放弹窗 -->
    <div v-if="replay" class="modal-mask" @click.self="closeReplay">
      <div class="modal ch-modal replay-modal">
        <h2>▶ 飞行记录回放</h2>
        <p class="ch-meta">
          {{ replay.detail.player }} · v{{ replay.detail.version }} ·
          <template v-for="i in 3" :key="i"><span :class="i <= replay.detail.stars ? 'lit' : 'dim'">★</span></template>
          · {{ kmps(replay.detail.fuel_used) }} km/s · {{ Math.round(replay.detail.elapsed_days) }} 天
          · {{ statusLabel(replay.detail.status) }}
        </p>
        <div class="replay-canvas-wrap"><canvas ref="replayCv"></canvas></div>
        <div class="modal-btns">
          <button class="btn ghost" @click="toggleReplayPlay">{{ replay.playing ? '⏸ 暂停' : '▶ 播放' }}</button>
          <button class="btn primary" @click="closeReplay">关闭</button>
        </div>
      </div>
    </div>
  </div>`,
};
