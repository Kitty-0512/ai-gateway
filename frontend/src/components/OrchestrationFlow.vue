<script setup>
import { computed } from 'vue'

const props = defineProps({
  flow: { type: Object, default: null },
  streaming: { type: Boolean, default: false },
  currentLabel: { type: String, default: '' },
})

const STATUS = {
  done: { icon: 'CircleCheck', cls: 'ok', text: '完成' },
  running: { icon: 'Loading', cls: 'running', text: '进行中' },
  failed: { icon: 'CircleClose', cls: 'fail', text: '失败' },
  pending: { icon: 'More', cls: 'pending', text: '待执行' },
}

function st(status) {
  return STATUS[status] || STATUS.pending
}

function fmtMs(ms) {
  if (ms == null) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

const hasFlow = computed(() => !!props.flow)
</script>

<template>
  <div v-if="hasFlow" class="orch-flow neu-card">
    <div class="orch-title">
      <span class="orch-title-text">分析流程</span>
      <span v-if="streaming" class="orch-live">
        <span class="live-dot" /> 实时执行中
      </span>
    </div>

    <div class="orch-track">
      <!-- 用户问题 -->
      <div class="node node-pill">
        <span class="pill-text">用户问题</span>
      </div>
      <div class="connector" />

      <!-- Intent Router -->
      <div class="node node-stage" :class="st(flow.router).cls">
        <el-icon class="node-ic" :class="{ spin: flow.router === 'running' }">
          <component :is="st(flow.router).icon" />
        </el-icon>
        <div class="node-main">
          <div class="node-name">Intent Router</div>
          <div v-if="flow.routerLabel" class="node-sub">{{ flow.routerLabel }}</div>
        </div>
        <span class="node-tag" :class="st(flow.router).cls">{{ st(flow.router).text }}</span>
      </div>
      <div class="connector" />

      <!-- Planner -->
      <div class="node node-stage" :class="st(flow.planner).cls">
        <el-icon class="node-ic" :class="{ spin: flow.planner === 'running' }">
          <component :is="st(flow.planner).icon" />
        </el-icon>
        <div class="node-main">
          <div class="node-name">Planner</div>
          <div class="node-sub" v-if="flow.stepCount">已规划 {{ flow.stepCount }} 步</div>
        </div>
        <span class="node-tag" :class="st(flow.planner).cls">{{ st(flow.planner).text }}</span>
      </div>
      <div class="connector" />

      <!-- Steps -->
      <div class="steps-box">
        <div
          v-for="s in flow.steps"
          :key="s.step"
          class="step-row"
          :class="st(s.status).cls"
        >
          <span class="step-index">Step {{ s.step }}</span>
          <el-icon class="node-ic" :class="{ spin: s.status === 'running' }">
            <component :is="st(s.status).icon" />
          </el-icon>
          <span class="step-name">{{ s.label }}</span>
          <span class="step-spacer" />
          <span v-if="s.duration != null && s.status !== 'running'" class="step-dur">{{ fmtMs(s.duration) }}</span>
        </div>
        <div v-if="!flow.steps.length" class="step-row pending">
          <el-icon class="node-ic"><More /></el-icon>
          <span class="step-name">等待规划…</span>
        </div>
      </div>

      <!-- Synthesizer -->
      <template v-if="flow.showSynth">
        <div class="connector" />
        <div class="node node-stage" :class="st(flow.synth).cls">
          <el-icon class="node-ic" :class="{ spin: flow.synth === 'running' }">
            <component :is="st(flow.synth).icon" />
          </el-icon>
          <div class="node-main">
            <div class="node-name">Synthesizer</div>
            <div class="node-sub">综合多工具结果</div>
          </div>
          <span v-if="flow.synthDuration != null" class="step-dur">{{ fmtMs(flow.synthDuration) }}</span>
          <span v-else class="node-tag" :class="st(flow.synth).cls">{{ st(flow.synth).text }}</span>
        </div>
      </template>

      <div class="connector" />
      <!-- 最终分析 -->
      <div class="node node-pill final">
        <span class="pill-text">最终分析结果</span>
      </div>
    </div>

    <div v-if="streaming && currentLabel" class="orch-current">
      <span class="live-dot" /> {{ currentLabel }}
    </div>
  </div>
</template>

<style scoped>
/* ── Soft Neumorphism card ── */
.orch-flow {
  padding: 16px 18px;
  margin-bottom: 12px;
}
.neu-card {
  background: #ffffff;
  border: 1px solid #eef2f7;
  border-radius: 16px;
  box-shadow:
    6px 6px 16px rgba(148, 163, 184, 0.14),
    -6px -6px 16px rgba(255, 255, 255, 0.9);
}

.orch-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.orch-title-text {
  font-size: 13px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: -0.01em;
}
.orch-live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #2563eb;
  font-weight: 500;
}
.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #2563eb;
  animation: pulse 1.4s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.8); }
}

.orch-track {
  display: flex;
  flex-direction: column;
  align-items: stretch;
}

/* Connector line */
.connector {
  width: 2px;
  height: 14px;
  background: linear-gradient(#cbd5e1, #cbd5e1);
  margin: 0 auto;
  border-radius: 2px;
}

/* Pill nodes (user question / final) */
.node-pill {
  align-self: center;
  padding: 7px 16px;
  border-radius: 999px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
}
.node-pill.final {
  background: #eff6ff;
  border-color: #dbeafe;
}
.pill-text {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}
.node-pill.final .pill-text { color: #2563eb; }

/* Stage nodes (soft neumorphism containers) */
.node-stage {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 12px;
  background: #ffffff;
  border: 1px solid #eef2f7;
  box-shadow:
    3px 3px 8px rgba(148, 163, 184, 0.12),
    -3px -3px 8px rgba(255, 255, 255, 0.85);
}
.node-main { flex: 1; min-width: 0; }
.node-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}
.node-sub {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 1px;
}

.node-ic { font-size: 16px; flex-shrink: 0; }
.node-stage.ok .node-ic,
.step-row.ok .node-ic { color: #059669; }
.node-stage.running .node-ic,
.step-row.running .node-ic { color: #2563eb; }
.node-stage.fail .node-ic,
.step-row.fail .node-ic { color: #dc2626; }
.node-stage.pending .node-ic,
.step-row.pending .node-ic { color: #cbd5e1; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.node-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 6px;
}
.node-tag.ok { background: #d1fae5; color: #059669; }
.node-tag.running { background: #dbeafe; color: #2563eb; }
.node-tag.fail { background: #fee2e2; color: #dc2626; }
.node-tag.pending { background: #f1f5f9; color: #94a3b8; }

/* Steps box */
.steps-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #eef2f7;
  box-shadow: inset 2px 2px 6px rgba(148, 163, 184, 0.12),
              inset -2px -2px 6px rgba(255, 255, 255, 0.7);
}
.step-row {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 12px;
  border-radius: 10px;
  background: #ffffff;
  border: 1px solid #eef2f7;
  box-shadow: 2px 2px 5px rgba(148, 163, 184, 0.1),
              -2px -2px 5px rgba(255, 255, 255, 0.8);
}
.step-index {
  font-size: 11px;
  font-weight: 700;
  color: #64748b;
  min-width: 46px;
}
.step-name {
  font-size: 13px;
  font-weight: 500;
  color: #334155;
}
.step-spacer { flex: 1; }
.step-dur {
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}

.orch-current {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #e2e8f0;
  font-size: 12px;
  color: #2563eb;
  font-weight: 500;
}
</style>
