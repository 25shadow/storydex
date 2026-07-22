<template>
  <aside class="breakdown-panel">
    <header class="breakdown-header">
      <div>
        <div class="breakdown-kicker">TEXT WORKSPACE</div>
        <h2>拆书</h2>
        <p>交由拆书规划 Agent 研究热榜书前 10 章，生成原创新书脑洞。</p>
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
        <span class="material-symbols-rounded">smart_toy</span>{{ loading ? "拆书规划 Agent 正在研究前十章..." : "交给拆书规划 Agent" }}
      </button>
      <p v-if="loading && !selectedFile" class="breakdown-muted">正在加载拆书记录...</p>
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
        <button v-if="result.status === 'partial'" class="breakdown-primary" type="button" :disabled="loading" @click="resumeRhythm">
          <span class="material-symbols-rounded">resume</span>继续生成逐章节奏档案
        </button>
        <div class="breakdown-section-title">前 {{ result.referenceChapterLimit }} 章研究范围</div>
        <ol class="breakdown-chapters">
          <li v-for="chapter in result.selectedChapters" :key="chapter.index">
            <span class="chapter-index">{{ String(chapter.index).padStart(2, "0") }}</span>
            <span class="chapter-title">{{ chapter.title }}</span>
            <span class="chapter-count">{{ chapter.characterCount.toLocaleString() }} 字</span>
          </li>
        </ol>
        <div class="breakdown-section-title">章节研究卡</div>
        <button class="study-card" :class="{ active: activeStudyCardId === card.id }" v-for="card in result.studyCards" :key="card.id" type="button" @click="toggleStudyCard(card.id)">
          <strong>第 {{ card.chapterIndex }} 章 · {{ card.chapterTitle }}</strong>
          <p>{{ card.function }}</p>
          <div v-if="activeStudyCardId === card.id" class="card-details">
            <p><b>读者问题：</b>{{ card.readerQuestion }}</p>
            <p><b>冲突：</b>{{ card.conflict }}</p>
            <p><b>信息变化：</b>{{ card.informationShift }}</p>
            <p><b>关系变化：</b>{{ card.relationshipShift }}</p>
            <p><b>章末钩子：</b>{{ card.endHook }}</p>
          </div>
        </button>
        <section v-if="result.styleProfile" class="style-profile">
          <div class="breakdown-section-title">写作风格研究</div>
          <p><b>叙事视角：</b>{{ result.styleProfile.narrativePerspective }}</p>
          <p><b>句式节奏：</b>{{ result.styleProfile.sentenceRhythm }}</p>
          <p><b>语言质感：</b>{{ result.styleProfile.languageTexture }}</p>
          <p><b>对白策略：</b>{{ result.styleProfile.dialogueStrategy }}</p>
          <p><b>钩子技巧：</b>{{ result.styleProfile.hookTechnique }}</p>
        </section>
        <div class="breakdown-next-title">选择脑洞母卡</div>
        <label class="mother-card" :class="{ active: activeMotherCardId === card.id }" v-for="card in result.motherCards" :key="card.id" @click="activeMotherCardId = card.id">
          <input v-model="selectedMotherCardIds" type="checkbox" :value="card.id" />
          <span>
            <strong>{{ card.title }}</strong>
            <small>{{ card.mechanism }}</small>
            <em>可用于：{{ card.useFor.join("、") }}</em>
            <span v-if="activeMotherCardId === card.id" class="card-details"><b>不可复用：</b>{{ card.doNotReuse.join("、") }}</span>
          </span>
        </label>

        <section class="idea-form">
          <div class="breakdown-next-title">关联新书</div>
          <p>候选会自动关联到当前项目：{{ projectName }}</p>
          <button class="breakdown-primary" type="button" :disabled="!selectedMotherCardIds.length || ideaLoading" @click="generateIdeas">
            <span class="material-symbols-rounded">auto_awesome</span>{{ ideaLoading ? "正在生成..." : "生成新书脑洞" }}
          </button>
        </section>
        <p v-if="ideaError" class="breakdown-error">{{ ideaError }}</p>
        <section v-if="ideaResult" class="idea-results">
          <div class="breakdown-next-title">原创脑洞候选</div>
          <p class="breakdown-muted">{{ ideaResult.notice }}</p>
          <div v-for="idea in ideaResult.ideas" :key="idea.id" class="idea-card" :class="{ active: activeIdeaId === idea.id }" @click="toggleIdea(idea.id)">
            <strong>{{ idea.title }}</strong>
            <p><b>题材：</b>{{ idea.genre || "待补充" }}</p>
            <p><b>主角：</b>{{ idea.protagonist || "待补充" }}</p>
            <p><b>核心规则：</b>{{ idea.coreRule || idea.logline }}</p>
            <p><b>主冲突：</b>{{ idea.mainConflict || idea.logline }}</p>
            <p><b>长期发动机：</b>{{ idea.longTermEngine || idea.storyEngine }}</p>
            <p><b>前十章承诺：</b>{{ idea.tenChapterPromise || idea.openingPlan }}</p>
            <em>{{ idea.sourceMechanism || idea.derivationMethods.join(" · ") }}</em>
            <div v-if="activeIdeaId === idea.id" class="card-details">
              <p><b>立项提示：</b>以主脑洞为核心，按已关联的十章结构节奏规划原创章节。</p>
            </div>
            <button
              class="idea-confirm"
              type="button"
              :disabled="ideaSelecting || selectedIdeaId === idea.id"
              @click.stop="selectIdea(idea.id)"
            >
              {{ selectedIdeaId === idea.id ? "已设为主脑洞并带入十章结构" : ideaSelecting ? "正在确认..." : "设为本书主脑洞" }}
            </button>
          </div>
        </section>
      </section>
    </section>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import axios from "axios";
import { analyzeBreakdown, fetchBreakdown, fetchBreakdownJob, generateNewBookIdeas, retryBreakdownRhythm, selectNewBookIdea, type BreakdownResult, type IdeaGenerationResult } from "@/api/breakdown";
import { useWorkspaceStore } from "@/stores/workspace";
import { useBreakdownAgentStore } from "@/stores/breakdownAgent";

const props = defineProps<{ analysisId?: string }>();

const fileInput = ref<HTMLInputElement | null>(null);
const selectedFile = ref<File | null>(null);
const loading = ref(false);
const dragging = ref(false);
const errorMessage = ref("");
const result = ref<BreakdownResult | null>(null);
const selectedMotherCardIds = ref<string[]>([]);
const activeStudyCardId = ref("");
const activeMotherCardId = ref("");
const activeIdeaId = ref("");
const ideaLoading = ref(false);
const ideaError = ref("");
const ideaResult = ref<IdeaGenerationResult | null>(null);
const ideaSelecting = ref(false);
const selectedIdeaId = ref("");
const workspaceStore = useWorkspaceStore();
const breakdownAgentStore = useBreakdownAgentStore();
const projectName = computed(() => workspaceStore.currentProject?.projectName || "当前 Storydex 项目");

function choose(file: File | undefined): void {
  if (!file) return;
  errorMessage.value = "";
  result.value = null;
  ideaResult.value = null;
  selectedIdeaId.value = "";
  selectedMotherCardIds.value = [];
  activeStudyCardId.value = "";
  activeMotherCardId.value = "";
  selectedFile.value = file;
}
function handleFileChange(event: Event): void { choose((event.target as HTMLInputElement).files?.[0]); }
function handleDrop(event: DragEvent): void { dragging.value = false; choose(event.dataTransfer?.files?.[0]); }
function clearFile(): void { selectedFile.value = null; result.value = null; ideaResult.value = null; selectedIdeaId.value = ""; selectedMotherCardIds.value = []; activeStudyCardId.value = ""; activeMotherCardId.value = ""; activeIdeaId.value = ""; if (fileInput.value) fileInput.value.value = ""; }
async function startAnalysis(): Promise<void> {
  if (!selectedFile.value) return;
  loading.value = true; errorMessage.value = "";
  breakdownAgentStore.start("拆书规划 Agent", `已接收《${selectedFile.value.name}》，开始解析前十章。`);
  breakdownAgentStore.report("已提交章节研究、抽象节奏档案、母卡与风格研究任务。");
  try {
    const buffer = await selectedFile.value.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    const response = await analyzeBreakdown(selectedFile.value.name, btoa(binary));
    const analysis = await waitForBreakdownJob(response.data.jobId);
    result.value = analysis;
    selectedMotherCardIds.value = analysis.motherCards.map((card) => card.id);
    if (analysis.status === "partial") {
      breakdownAgentStore.fail("章节研究已保存，逐章节奏档案可继续生成。");
    } else {
      breakdownAgentStore.finish(`前十章研究完成，已生成 ${analysis.studyCards.length} 张章节研究卡和逐章节奏档案。`);
    }
  } catch (error) {
    errorMessage.value = axios.isAxiosError(error)
      ? String(error.response?.data?.error?.message || error.response?.data?.detail || "AI 拆书分析失败，请重试。")
      : error instanceof Error ? error.message : "拆书分析失败，请重试。";
    breakdownAgentStore.fail(errorMessage.value);
  }
  finally { loading.value = false; }
}
async function waitForBreakdownJob(jobId: string): Promise<BreakdownResult> {
  let displayedEventCount = 0;
  while (true) {
    const job = await fetchBreakdownJob(jobId);
    for (const event of job.data.events.slice(displayedEventCount)) {
      breakdownAgentStore.report(event.content);
    }
    displayedEventCount = job.data.events.length;
    if ((job.data.status === "completed" || job.data.status === "partial") && job.data.result) return job.data.result;
    if (job.data.status === "failed") throw new Error(job.data.error || "AI 拆书分析失败，请重试。");
    await new Promise<void>((resolve) => window.setTimeout(resolve, 1000));
  }
}
async function resumeRhythm(): Promise<void> {
  if (!result.value || loading.value) return;
  loading.value = true;
  errorMessage.value = "";
  breakdownAgentStore.start("拆书规划 Agent", "已读取保存的章节研究，继续生成逐章节奏档案。");
  try {
    const response = await retryBreakdownRhythm(result.value.analysisId);
    const analysis = await waitForBreakdownJob(response.data.jobId);
    result.value = analysis;
    if (analysis.status === "partial") breakdownAgentStore.fail("节奏档案暂未完成，可再次继续，不会重跑章节研究。");
    else breakdownAgentStore.finish("逐章节奏档案已完成，拆书记录已更新。");
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "继续节奏档案失败。";
    breakdownAgentStore.fail(errorMessage.value);
  } finally {
    loading.value = false;
  }
}
async function generateIdeas(): Promise<void> {
  if (!result.value || !selectedMotherCardIds.value.length) return;
  ideaLoading.value = true; ideaError.value = "";
  breakdownAgentStore.start("新书脑洞 Agent", "已接收选择的脑洞母卡，开始生成原创立项候选。");
  try {
    const response = await generateNewBookIdeas({
      analysisId: result.value.analysisId,
      motherCardIds: selectedMotherCardIds.value,
      projectName: projectName.value,
      genre: "",
      tone: "",
      targetAudience: ""
    });
    ideaResult.value = response.data;
    selectedIdeaId.value = "";
    breakdownAgentStore.finish(`已生成 ${response.data.ideas.length} 条原创立项候选。`);
  } catch (error) {
    ideaError.value = axios.isAxiosError(error)
      ? String(error.response?.data?.error?.message || error.response?.data?.detail || "AI 脑洞生成失败，请检查模型配置。")
      : error instanceof Error ? error.message : "新书脑洞生成失败，请重试。";
    breakdownAgentStore.fail(ideaError.value);
  }
  finally { ideaLoading.value = false; }
}
async function selectIdea(ideaId: string): Promise<void> {
  if (!result.value || !ideaResult.value || ideaSelecting.value) return;
  ideaSelecting.value = true;
  ideaError.value = "";
  breakdownAgentStore.start("拆书规划 Agent", "已确认原创立项，正在按参考书的抽象逐章节奏生成本书前十章规划。");
  try {
    const response = await selectNewBookIdea({
      analysisId: result.value.analysisId,
      ideaRunId: ideaResult.value.ideaRunId,
      ideaId
    });
    selectedIdeaId.value = response.data.selectedIdeaId;
    breakdownAgentStore.finish(`新书前十章规划已关联当前项目，共 ${response.data.chapterStructureCount} 章。`);
  } catch (error) {
    ideaError.value = axios.isAxiosError(error)
      ? String(error.response?.data?.error?.message || error.response?.data?.detail || "新书脑洞确认失败。")
      : error instanceof Error ? error.message : "新书脑洞确认失败。";
    breakdownAgentStore.fail(ideaError.value);
  } finally {
    ideaSelecting.value = false;
  }
}
async function loadSavedBreakdown(analysisId: string): Promise<void> {
  if (!analysisId || result.value?.analysisId === analysisId) return;
  loading.value = true;
  errorMessage.value = "";
  ideaResult.value = null;
  selectedIdeaId.value = "";
  ideaError.value = "";
  activeStudyCardId.value = "";
  activeMotherCardId.value = "";
  activeIdeaId.value = "";
  try {
    const response = await fetchBreakdown(analysisId);
    result.value = response.data;
    ideaResult.value = response.data.latestIdeaRun ?? null;
    selectedFile.value = null;
    selectedMotherCardIds.value = response.data.motherCards.map((card) => card.id);
  } catch (error) {
    errorMessage.value = axios.isAxiosError(error)
      ? String(error.response?.data?.error?.message || error.response?.data?.detail || "拆书记录加载失败。")
      : error instanceof Error ? error.message : "拆书记录加载失败。";
  } finally {
    loading.value = false;
  }
}
watch(() => props.analysisId, (analysisId) => { void loadSavedBreakdown(analysisId || ""); }, { immediate: true });
function toggleStudyCard(id: string): void { activeStudyCardId.value = activeStudyCardId.value === id ? "" : id; }
function toggleIdea(id: string): void { activeIdeaId.value = activeIdeaId.value === id ? "" : id; }
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
.study-card, .idea-card { display: block; box-sizing: border-box; width: 100%; padding: 9px 0; border: 0; border-bottom: 1px solid var(--border-subtle); background: transparent; color: var(--text-primary); font: inherit; font-size: 11px; line-height: 1.5; text-align: left; cursor: pointer; }
.study-card:hover, .study-card.active, .idea-card:hover, .idea-card.active, .mother-card.active { background: var(--accent-soft); }
.study-card strong, .idea-card strong { font-size: 12px; }
.study-card p, .idea-card p { margin: 4px 0; color: var(--text-muted); }
.mother-card { display: flex; gap: 8px; padding: 9px 0; border-bottom: 1px solid var(--border-subtle); cursor: pointer; font-size: 12px; }
.mother-card input { accent-color: var(--accent); margin-top: 3px; }
.mother-card strong, .mother-card small, .mother-card em, .idea-card small, .idea-card em { display: block; }
.mother-card small, .idea-card small { margin-top: 3px; color: var(--text-muted); line-height: 1.45; }
.mother-card em, .idea-card em { margin-top: 5px; color: var(--accent); font-size: 10px; font-style: normal; }
.card-details { display: block; margin-top: 7px; padding: 8px; background: var(--bg-main); color: var(--text-muted); font-size: 11px; line-height: 1.45; }
.card-details p { margin: 4px 0; }
.idea-form { margin-top: 14px; }
.idea-form > p { color: var(--text-muted); font-size: 11px; line-height: 1.4; }
.idea-form .breakdown-primary { margin-top: 8px; }
.style-profile { margin: 18px 0; padding: 12px; background: var(--bg-main); border-left: 2px solid var(--accent); color: var(--text-muted); font-size: 12px; line-height: 1.55; }
.style-profile .breakdown-section-title { margin-top: 0; color: var(--text-primary); }
.style-profile p { margin: 7px 0; }
.idea-confirm { margin-top: 8px; padding: 6px 8px; border: 1px solid var(--accent); background: transparent; color: var(--accent); cursor: pointer; font: inherit; font-size: 11px; }
.idea-confirm:disabled { opacity: .65; cursor: default; }
</style>
