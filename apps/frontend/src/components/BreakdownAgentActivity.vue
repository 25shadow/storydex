<template>
  <section v-if="task" class="breakdown-agent-run" :class="task.status">
    <header>
      <div>
        <span class="material-symbols-rounded">smart_toy</span>
        <strong>{{ task.title }}</strong>
      </div>
      <button type="button" title="关闭任务记录" @click="breakdownAgentStore.clear">
        <span class="material-symbols-rounded">close</span>
      </button>
    </header>
    <div class="breakdown-agent-state">
      <span class="breakdown-agent-dot"></span>
      {{ statusLabel }}
    </div>
    <ol>
      <li v-for="event in task.events" :key="event.id">
        <span>{{ formatTime(event.timestamp) }}</span>
        <p>{{ event.content }}</p>
      </li>
    </ol>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useBreakdownAgentStore } from "@/stores/breakdownAgent";

const breakdownAgentStore = useBreakdownAgentStore();
const task = computed(() => breakdownAgentStore.task);
const statusLabel = computed(() => ({ running: "Agent 执行中", completed: "Agent 已完成", failed: "Agent 未完成" }[task.value?.status || "running"]));

function formatTime(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(timestamp);
}
</script>

<style scoped>
.breakdown-agent-run { margin: 14px; border: 1px solid var(--border-subtle); background: var(--bg-main); font-size: 12px; }
.breakdown-agent-run header { display: flex; align-items: center; justify-content: space-between; padding: 11px 12px; border-bottom: 1px solid var(--border-subtle); }
.breakdown-agent-run header div { display: flex; align-items: center; gap: 7px; }
.breakdown-agent-run header .material-symbols-rounded { color: var(--accent); font-size: 18px; }
.breakdown-agent-run header button { border: 0; background: transparent; color: var(--text-muted); cursor: pointer; }
.breakdown-agent-state { display: flex; align-items: center; gap: 7px; padding: 9px 12px; color: var(--text-muted); }
.breakdown-agent-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.breakdown-agent-run.failed .breakdown-agent-dot { background: var(--danger); }
.breakdown-agent-run.completed .breakdown-agent-dot { background: var(--success, #2f9e6d); }
.breakdown-agent-run ol { margin: 0; padding: 0 12px 12px; list-style: none; }
.breakdown-agent-run li { display: grid; grid-template-columns: 56px 1fr; gap: 8px; padding: 7px 0; border-top: 1px solid var(--border-subtle); }
.breakdown-agent-run li span { color: var(--text-muted); font-variant-numeric: tabular-nums; }
.breakdown-agent-run li p { margin: 0; line-height: 1.5; color: var(--text-primary); }
</style>
