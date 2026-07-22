<template>
  <aside class="breakdown-history-panel">
    <header>
      <div>
        <div class="history-kicker">REFERENCE LIBRARY</div>
        <h2>拆书记录</h2>
      </div>
      <button type="button" title="刷新拆书记录" @click="loadHistory"><span class="material-symbols-rounded">refresh</span></button>
    </header>
    <div v-if="loading" class="history-state">正在读取拆书记录...</div>
    <div v-else-if="errorMessage" class="history-state is-error">{{ errorMessage }}</div>
    <div v-else-if="items.length === 0" class="history-state">完成一次 AI 拆书后，记录会显示在这里。</div>
    <ol v-else class="history-list">
      <li v-for="item in items" :key="item.analysisId">
        <button type="button" :class="{ active: props.activeAnalysisId === item.analysisId }" @click="loadBreakdown(item.analysisId)">
          <span class="material-symbols-rounded">auto_stories</span>
          <div>
            <strong>{{ item.fileName }}</strong>
            <small>研究前 {{ item.selectedChapterCount }} 章 · 共 {{ item.chapterCount }} 章</small>
            <time>{{ formatTime(item.updatedAt) }}</time>
          </div>
        </button>
        <button class="history-delete" type="button" title="删除拆书记录" @click="deleteItem(item)">
          <span class="material-symbols-rounded">delete</span>
        </button>
      </li>
    </ol>
    <section class="history-note">
      <span class="material-symbols-rounded">link</span>
      <p>新书脑洞会链接到当前项目的参考资料，不会改写正文。</p>
    </section>
  </aside>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { deleteBreakdown, fetchBreakdownHistory, type BreakdownHistoryItem } from "@/api/breakdown";

const props = defineProps<{ activeAnalysisId?: string }>();
const emit = defineEmits<{ (event: "load-breakdown", analysisId: string): void }>();

const items = ref<BreakdownHistoryItem[]>([]);
const loading = ref(false);
const errorMessage = ref("");

async function loadHistory(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const response = await fetchBreakdownHistory();
    items.value = response.data.items;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "拆书历史加载失败。";
  } finally {
    loading.value = false;
  }
}

function loadBreakdown(analysisId: string): void {
  emit("load-breakdown", analysisId);
}

async function deleteItem(item: BreakdownHistoryItem): Promise<void> {
  if (!window.confirm(`删除拆书记录“${item.fileName}”？此操作会删除分析结果和脑洞候选。`)) return;
  errorMessage.value = "";
  try {
    await deleteBreakdown(item.analysisId);
    items.value = items.value.filter((entry) => entry.analysisId !== item.analysisId);
    if (props.activeAnalysisId === item.analysisId) emit("load-breakdown", "");
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "删除拆书记录失败。";
  }
}

function formatTime(value: number): string {
  return value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}

onMounted(() => { void loadHistory(); });
</script>

<style scoped>
.breakdown-history-panel { height: 100%; display: flex; flex-direction: column; background: var(--bg-sidebar); color: var(--text-primary); }
header { display: flex; justify-content: space-between; align-items: flex-start; padding: 20px 18px 16px; border-bottom: 1px solid var(--border-subtle); }
.history-kicker { color: var(--text-muted); font-size: 10px; letter-spacing: .08em; }
h2 { margin: 5px 0 0; font-size: 18px; }
button { border: 0; background: transparent; color: var(--text-muted); cursor: pointer; }
.history-state { padding: 20px 18px; color: var(--text-muted); font-size: 12px; line-height: 1.6; }
.history-state.is-error { color: var(--danger); }
.history-list { margin: 0; padding: 0; overflow: auto; list-style: none; }
.history-list li { display: flex; align-items: stretch; border-bottom: 1px solid var(--border-subtle); }
.history-list li > button:first-child { display: grid; grid-template-columns: 22px 1fr; flex: 1; min-width: 0; gap: 8px; padding: 13px 8px 13px 16px; color: inherit; text-align: left; }
.history-list li > button:first-child:hover, .history-list li > button:first-child.active { background: var(--accent-soft); }
.history-delete { width: 34px; color: var(--text-muted); opacity: 0; }
.history-list li:hover .history-delete { opacity: 1; }
.history-delete:hover { color: var(--danger); background: var(--accent-soft); }
.history-list .material-symbols-rounded { color: var(--accent); font-size: 18px; }
.history-list strong, .history-list small, .history-list time { display: block; }
.history-list strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.history-list small, .history-list time { margin-top: 4px; color: var(--text-muted); font-size: 10px; }
.history-note { display: flex; gap: 8px; margin-top: auto; padding: 14px 16px; border-top: 1px solid var(--border-subtle); color: var(--text-muted); font-size: 11px; line-height: 1.5; }
.history-note .material-symbols-rounded { color: var(--accent); font-size: 16px; }
.history-note p { margin: 0; }
</style>
