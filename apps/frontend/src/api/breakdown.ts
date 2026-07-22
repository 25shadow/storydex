import { apiClient, unwrapEnvelope } from "@/api/client";
import type { ApiEnvelope, ApiResult } from "@/types/api";

export interface BreakdownChapter {
  index: number;
  title: string;
  startLine: number | null;
  endLine: number | null;
  characterCount: number;
  evidence: Record<string, number>;
}

export interface BreakdownResult {
  analysisId: string;
  fileName: string;
  encoding: string;
  characterCount: number;
  lineCount: number;
  chapterCount: number;
  chapters: BreakdownChapter[];
  referenceChapterLimit: number;
  selectedChapters: BreakdownChapter[];
  studyCards: StudyCard[];
  motherCards: MotherCard[];
  styleProfile?: StyleProfile;
  warnings: string[];
  status: string;
  nextStages: string[];
  latestIdeaRun?: IdeaGenerationResult;
}

export interface StudyCard {
  id: string;
  chapterIndex: number;
  chapterTitle: string;
  function: string;
  evidence: Record<string, number>;
  status: string;
  readerQuestion?: string;
  conflict?: string;
  informationShift?: string;
  relationshipShift?: string;
  endHook?: string;
}

export interface MotherCard {
  id: string;
  title: string;
  type: string;
  mechanism: string;
  useFor: string[];
  doNotReuse: string[];
}

export interface StyleProfile {
  narrativePerspective: string;
  sentenceRhythm: string;
  languageTexture: string;
  dialogueStrategy: string;
  hookTechnique: string;
  avoidReuse: string;
}

export interface NewBookIdea {
  id: string;
  title: string;
  logline: string;
  genre: string;
  tone: string;
  targetAudience: string;
  protagonist?: string;
  coreRule?: string;
  mainConflict?: string;
  longTermEngine?: string;
  tenChapterPromise?: string;
  sourceMechanism?: string;
  storyEngine: string;
  openingPlan: string;
  derivedFrom: string[];
  derivationMethods: string[];
  originalityConstraints: string[];
}

export interface IdeaGenerationResult {
  ideaRunId: string;
  analysisId: string;
  projectName: string;
  linkedProject: string;
  generationMode: string;
  notice: string;
  ideas: NewBookIdea[];
}

export interface IdeaSelectionResult {
  selectedIdeaId: string;
  projectName: string;
  idea: NewBookIdea;
  chapterStructureCount: number;
}

export interface BreakdownHistoryItem {
  analysisId: string;
  fileName: string;
  chapterCount: number;
  selectedChapterCount: number;
  status: string;
  updatedAt: number;
}

export interface BreakdownJobEvent {
  id: string;
  content: string;
  timestamp: string;
}

export interface BreakdownJob {
  jobId: string;
  status: "running" | "completed" | "failed";
  events: BreakdownJobEvent[];
  result: BreakdownResult | null;
  error: string;
}

export async function analyzeBreakdown(fileName: string, contentBase64: string): Promise<ApiResult<{ jobId: string; status: string }>> {
  const response = await apiClient.post<ApiEnvelope<{ jobId: string; status: string }>>("/breakdown/analyze", {
    fileName,
    contentBase64,
    options: { chapterPattern: "auto" }
  }, { timeout: 60000 });
  return unwrapEnvelope(response.data, "拆书分析失败。");
}

export async function fetchBreakdownJob(jobId: string): Promise<ApiResult<BreakdownJob>> {
  const response = await apiClient.get<ApiEnvelope<BreakdownJob>>(`/breakdown/jobs/${encodeURIComponent(jobId)}`);
  return unwrapEnvelope(response.data, "拆书任务状态加载失败。");
}

export async function generateNewBookIdeas(payload: {
  analysisId: string;
  motherCardIds: string[];
  projectName: string;
  genre: string;
  tone: string;
  targetAudience: string;
}): Promise<ApiResult<IdeaGenerationResult>> {
  const response = await apiClient.post<ApiEnvelope<IdeaGenerationResult>>("/breakdown/ideas/generate", payload, { timeout: 150000 });
  return unwrapEnvelope(response.data, "新书脑洞生成失败。");
}

export async function fetchBreakdownHistory(): Promise<ApiResult<{ items: BreakdownHistoryItem[] }>> {
  const response = await apiClient.get<ApiEnvelope<{ items: BreakdownHistoryItem[] }>>("/breakdown/history");
  return unwrapEnvelope(response.data, "拆书历史加载失败。");
}

export async function fetchBreakdown(analysisId: string): Promise<ApiResult<BreakdownResult>> {
  const response = await apiClient.get<ApiEnvelope<BreakdownResult>>(`/breakdown/${encodeURIComponent(analysisId)}`);
  return unwrapEnvelope(response.data, "拆书记录加载失败。");
}

export async function selectNewBookIdea(payload: {
  analysisId: string;
  ideaRunId: string;
  ideaId: string;
}): Promise<ApiResult<IdeaSelectionResult>> {
  const response = await apiClient.post<ApiEnvelope<IdeaSelectionResult>>("/breakdown/ideas/select", payload, { timeout: 150000 });
  return unwrapEnvelope(response.data, "新书脑洞确认失败。");
}
