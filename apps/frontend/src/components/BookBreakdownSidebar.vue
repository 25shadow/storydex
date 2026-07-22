<template>
  <aside class="breakdown-panel">
    <header class="breakdown-header">
      <div>
        <div class="breakdown-kicker">TEXT WORKSPACE</div>
        <h2>拆书</h2>
        <p>仅研究热榜书前 10 章，生成原创新书脑洞。</p>
      </div>
      <span class="material-symbols-rounded breakdown-header-icon">menu_book</span>
    </header>

    <section class="breakdown-content">
      <label class="breakdown-dropzone" :class="{ 'is-dragging': dragging }" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="handleDrop">
        <input ref="fileInput" type="file" accept=".txt,text/plain" @change="handleFileChange" />
        <span class="material-symbols-rounded">upload_file</span>
        <strong>{{ selectedFile ? selectedFile.name : "选择或拖入 TXT 小说" }}</strong>
        <small>支持 UTF-8 / GB18030，最大 20 MB，仅取前 10 章</small>
      </label>

      <div v-if="selectedFile" class="breakdown-file-meta">
        <span>{{ formatBytes(selectedFile.size) }}</span>
        <button type="button" title="移除文件" @click="clearFile"><span class="material-symbols-rounded">close</span></button>
      </div>

      <button class="breakdown-primary" type="button" :disabled="!selectedFile || loading" @click="startAnalysis">
        <span class="material-symbols-rounded">play_arrow</span>{{ loading ? "正在建立章节骨架..." : "开始拆书" }}
      </button>
      <p v-if="errorMessage" class="breakdown-error">{{ errorMessage }}</p>

      <section v-if="result" class="breakdown-result">
        <div class="breakdown-summary">
          <div><strong>{{ result.chapterCount }}</strong><span>章节/片段</span></div>
          <div><strong>{{ result.characterCount.toLocaleString() }}</strong><span>字</span></div>
          <div><strong>{{ result.encoding }}</strong><span>编码</span></div>
        </div>
        <div v-if="result.warnings.length" class="breakdown-warnings">
          <div v-for="warning in result.warnings" :key="warning"><span class="material-symbols-rounded">info</span>{{ warning }}</div>
        </div>
        <div class="breakdown-section-title">前 {{ result.referenceChapterLimit }} 章研究范围</div>
        <ol class="breakdown-chapters">
          <li v-for="chapter in result.selectedChapters" :key="chapter.index">
            <span class="chapter-index">{{ String(chapter.index).padStart(2, "0") }}</span>
            <span class="chapter-title">{{ chapter.title }}</span>
            <span class="chapter-count">{{ chapter.characterCount.toLocaleString() }} 字</span>
          </li>
        </ol>
        <div class="breakdown-section-title">章节研究卡</div>
        <div class="study-card" v-for="card in result.studyCards" :key="card.id">
          <strong>第 {{ card.chapterIndex }} 章 · {{ card.chapterTitle }}</strong>
          <p>{{ card.function }}</p>
        </div>
        <div class="breakdown-next-title">选择脑洞母卡</div>
        <label class="mother-card" v-for="card in result.motherCards" :key="card.id">
          <input v-model="selectedMotherCardIds" type="checkbox" :value="card.id" />
          <span>
            <strong>{{ card.title }}</strong>
            <small>{{ card.mechanism }}</small>
            <em>可用于：{{ card.useFor.join("、") }}</em>
          </span>
        </label>

        <section class="idea-form">
          <div class="breakdown-next-title">关联新书</div>
          <p>候选将链接到当前项目：{{ projectName }}</p>
          <input v-model.trim="ideaGenre" placeholder="新书题材，例如都市悬疑" />
          <input v-model.trim="ideaTone" placeholder="情绪基调，例如紧张治愈" />
          <input v-model.trim="ideaAudience" placeholder="目标读者，例如女性向连载读者" />
          <button class="breakdown-primary" type="button" :disabled="!selectedMotherCardIds.length || ideaLoading" @click="generateIdeas">
            <span class="material-symbols-rounded">auto_awesome</span>{{ ideaLoading ? "正在生成..." : "生成新书脑洞" }}
          </button>
        </section>
        <p v-if="ideaError" class="breakdown-error">{{ ideaError }}</p>
        <section v-if="ideaResult" class="idea-results">
          <div class="breakdown-next-title">原创脑洞候选</div>
          <p class="breakdown-muted">{{ ideaResult.notice }}</p>
          <article v-for="idea in ideaResult.ideas" :key="idea.id" class="idea-card">
            <strong>{{ idea.title }}</strong>
            <p>{{ idea.logline }}</p>
            <small>{{ idea.storyEngine }}</small>
            <em>{{ idea.derivationMethods.join(" · ") }}</em>
          </article>
        </section>
      </section>
    </section>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { analyzeBreakdown, generateNewBookIdeas, type BreakdownResult, type IdeaGenerationResult } from "@/api/breakdown";
import { useWorkspaceStore } from "@/stores/workspace";

const fileInput = ref<HTMLInputElement | null>(null);
const selectedFile = ref<File | null>(null);
const loading = ref(false);
const dragging = ref(false);
const errorMessage = ref("");
const result = ref<BreakdownResult | null>(null);
const selectedMotherCardIds = ref<string[]>([]);
const ideaGenre = ref("");
const ideaTone = ref("");
const ideaAudience = ref("");
const ideaLoading = ref(false);
const ideaError = ref("");
const ideaResult = ref<IdeaGenerationResult | null>(null);
const workspaceStore = useWorkspaceStore();
const projectName = computed(() => workspaceStore.currentProject?.projectName || "当前 Storydex 项目");

function choose(file: File | undefined): void {
  if (!file) return;
  errorMessage.value = "";
  result.value = null;
  ideaResult.value = null;
  selectedMotherCardIds.value = [];
  selectedFile.value = file;
}
function handleFileChange(event: Event): void { choose((event.target as HTMLInputElement).files?.[0]); }
function handleDrop(event: DragEvent): void { dragging.value = false; choose(event.dataTransfer?.files?.[0]); }
function clearFile(): void { selectedFile.value = null; result.value = null; ideaResult.value = null; selectedMotherCardIds.value = []; if (fileInput.value) fileInput.value.value = ""; }
async function startAnalysis(): Promise<void> {
  if (!selectedFile.value) return;
  loading.value = true; errorMessage.value = "";
  try {
    const buffer = await selectedFile.value.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    const response = await analyzeBreakdown(selectedFile.value.name, btoa(binary));
    result.value = response.data;
    selectedMotherCardIds.value = response.data.motherCards.map((card) => card.id);
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : "拆书分析失败，请重试。"; }
  finally { loading.value = false; }
}
async function generateIdeas(): Promise<void> {
  if (!result.value || !selectedMotherCardIds.value.length) return;
  ideaLoading.value = true; ideaError.value = "";
  try {
    const response = await generateNewBookIdeas({
      analysisId: result.value.analysisId,
      motherCardIds: selectedMotherCardIds.value,
      projectName: projectName.value,
      genre: ideaGenre.value,
      tone: ideaTone.value,
      targetAudience: ideaAudience.value
    });
    ideaResult.value = response.data;
  } catch (error) { ideaError.value = error instanceof Error ? error.message : "新书脑洞生成失败，请重试。"; }
  finally { ideaLoading.value = false; }
}
function formatBytes(bytes: number): string { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
</script>

<style scoped>
.breakdown-panel { height: 100%; display: flex; flex-direction: column; background: var(--bg-sidebar); color: var(--text-primary); }
.breakdown-header { display: flex; justify-content: space-between; gap: 12px; padding: 20px 18px 16px; border-bottom: 1px solid var(--border-subtle); }
.breakdown-kicker { color: var(--text-muted); font-size: 10px; letter-spacing: .08em; }
h2 { margin: 5px 0; font-size: 20px; }
.breakdown-header p { margin: 0; color: var(--text-muted); font-size: 12px; line-height: 1.5; }
.breakdown-header-icon { color: var(--accent); font-size: 28px; }
.breakdown-content { padding: 16px; overflow: auto; }
.breakdown-dropzone { min-height: 150px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; border: 1px dashed var(--border-strong); background: var(--bg-main); cursor: pointer; text-align: center; }
.breakdown-dropzone.is-dragging { border-color: var(--accent); background: var(--accent-soft); }
.breakdown-dropzone input { display: none; }
.breakdown-dropzone .material-symbols-rounded { font-size: 30px; color: var(--accent); }
.breakdown-dropzone strong { font-size: 13px; max-width: 100%; overflow-wrap: anywhere; }
.breakdown-dropzone small, .breakdown-muted { color: var(--text-muted); font-size: 11px; }
.breakdown-file-meta { display: flex; justify-content: space-between; align-items: center; padding: 10px 2px; color: var(--text-muted); font-size: 12px; }
.breakdown-file-meta button { border: 0; background: transparent; color: var(--text-muted); cursor: pointer; }
.breakdown-primary { width: 100%; display: flex; justify-content: center; align-items: center; gap: 6px; padding: 10px; border: 0; background: var(--accent); color: white; cursor: pointer; font-weight: 600; }
.breakdown-primary:disabled { opacity: .45; cursor: not-allowed; }
.breakdown-error { color: var(--danger); font-size: 12px; line-height: 1.5; }
.breakdown-result { margin-top: 20px; }
.breakdown-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.breakdown-summary div { padding: 10px 6px; background: var(--bg-main); text-align: center; }
.breakdown-summary strong, .breakdown-summary span { display: block; }
.breakdown-summary strong { font-size: 15px; }
.breakdown-summary span { margin-top: 3px; color: var(--text-muted); font-size: 10px; }
.breakdown-warnings { margin-top: 12px; color: var(--warning); font-size: 11px; line-height: 1.5; }
.breakdown-warnings div { display: flex; gap: 5px; margin: 5px 0; }
.breakdown-warnings .material-symbols-rounded { font-size: 15px; }
.breakdown-section-title, .breakdown-next-title { margin: 18px 0 8px; font-size: 12px; font-weight: 600; }
.breakdown-chapters { margin: 0; padding: 0; list-style: none; }
.breakdown-chapters li { display: grid; grid-template-columns: 28px 1fr auto; gap: 6px; align-items: center; padding: 7px 0; border-bottom: 1px solid var(--border-subtle); font-size: 12px; }
.chapter-index, .chapter-count { color: var(--text-muted); font-size: 10px; }
.chapter-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.breakdown-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.breakdown-tags span { padding: 4px 7px; background: var(--accent-soft); color: var(--accent); font-size: 10px; }
.study-card, .idea-card { padding: 9px 0; border-bottom: 1px solid var(--border-subtle); font-size: 11px; line-height: 1.5; }
.study-card strong, .idea-card strong { font-size: 12px; }
.study-card p, .idea-card p { margin: 4px 0; color: var(--text-muted); }
.mother-card { display: flex; gap: 8px; padding: 9px 0; border-bottom: 1px solid var(--border-subtle); cursor: pointer; font-size: 12px; }
.mother-card input { accent-color: var(--accent); margin-top: 3px; }
.mother-card strong, .mother-card small, .mother-card em, .idea-card small, .idea-card em { display: block; }
.mother-card small, .idea-card small { margin-top: 3px; color: var(--text-muted); line-height: 1.45; }
.mother-card em, .idea-card em { margin-top: 5px; color: var(--accent); font-size: 10px; font-style: normal; }
.idea-form { margin-top: 14px; }
.idea-form > p { color: var(--text-muted); font-size: 11px; line-height: 1.4; }
.idea-form input { box-sizing: border-box; width: 100%; margin: 4px 0; padding: 8px; border: 1px solid var(--border-strong); background: var(--bg-main); color: var(--text-primary); font: inherit; font-size: 12px; }
.idea-form .breakdown-primary { margin-top: 8px; }
</style>
