const { createApp } = Vue;

const app = createApp({
  data() {
    return {
      view: "levels",          // levels | game | challenges
      levelId: null,
      challenge: null,         // 挑战模式：挑战详情（含当前版本关卡定义）
      challengeLevel: null,    // 挑战模式：组装成关卡对象供 GameView 使用
      system: null,
      scores: {},
    };
  },
  components: { LevelSelect, GameView, ChallengeList },
  async created() {
    try {
      this.system = await API.system();
      this.scores = this.system.scores || {};
    } catch (e) {
      this.system = null;
    }
  },
  computed: {
    level() {
      if (!this.system) return null;
      return this.system.levels.find(l => l.id === this.levelId) || null;
    },
    // 挑战模式下的关卡对象：来自挑战当前版本快照（预算 + 里程碑）
    activeLevel() {
      return this.challenge ? this.challengeLevel : this.level;
    },
  },
  methods: {
    enter(id) {
      if (id > 5) return;
      this.challenge = null;
      this.challengeLevel = null;
      this.levelId = id;
      this.view = "game";
    },
    back() {
      // 挑战模式返回挑战中心，否则回关卡选择
      const toChallenges = !!this.challenge;
      this.challenge = null;
      this.challengeLevel = null;
      this.view = toChallenges ? "challenges" : "levels";
    },
    openChallenges() { this.view = "challenges"; },
    async playChallenge(challengeId) {
      // 进入挑战：拉取当前版本关卡定义，组装为 GameView 可用的关卡对象
      try {
        const d = await API.challenge(challengeId);
        this.challenge = d;
        this.challengeLevel = {
          id: `c${d.id}v${d.current_version}`,
          name: `${d.title} · v${d.current_version}`,
          brief: d.brief,
          milestones: d.milestones,
          budget_dv: d.budget_dv,
          t_max: d.t_max,
          hint: d.hint,
        };
        this.view = "game";
      } catch (e) {
        alert("进入挑战失败：" + e.message);
      }
    },
    onScore(s) {
      // 合并权威最佳成绩（含可回放记录 id），联动星级与关卡解锁
      const cur = this.scores[s.level_id] || {};
      this.scores = { ...this.scores, [s.level_id]: { ...cur, ...s } };
    },
  },
  template: `
    <LevelSelect v-if="view === 'levels'" :system="system" :scores="scores"
                 @enter="enter" @challenges="openChallenges" />
    <ChallengeList v-else-if="view === 'challenges'" :bodies="system ? system.bodies : []"
                   @back="back" @play="playChallenge" />
    <GameView v-else-if="view === 'game' && activeLevel"
              :key="activeLevel.id"
              :level="activeLevel" :bodies="system.bodies" :scores="scores"
              :challenge="challenge"
              @back="back" @enter="enter" @score="onScore" />
  `,
});

app.mount("#app");
