import { defineStore } from "pinia";

export type BreakdownAgentStatus = "running" | "completed" | "failed";

export interface BreakdownAgentEvent {
  id: string;
  content: string;
  timestamp: number;
}

export interface BreakdownAgentTask {
  id: string;
  title: string;
  status: BreakdownAgentStatus;
  startedAt: number;
  updatedAt: number;
  events: BreakdownAgentEvent[];
  analysisId?: string;
}

export const useBreakdownAgentStore = defineStore("breakdownAgent", {
  state: (): { task: BreakdownAgentTask | null } => ({ task: null }),
  getters: {
    hasTask: (state): boolean => state.task !== null,
    isRunning: (state): boolean => state.task?.status === "running"
  },
  actions: {
    start(title: string, firstEvent: string, analysisId = ""): void {
      const now = Date.now();
      this.task = {
        id: `${now}-${Math.random().toString(36).slice(2, 8)}`,
        title,
        status: "running",
        startedAt: now,
        updatedAt: now,
        events: [{ id: `${now}-0`, content: firstEvent, timestamp: now }],
        analysisId: analysisId || undefined
      };
    },
    report(content: string): void {
      if (!this.task) return;
      if (this.task.events[this.task.events.length - 1]?.content === content) return;
      const now = Date.now();
      this.task.events.push({ id: `${now}-${this.task.events.length}`, content, timestamp: now });
      this.task.updatedAt = now;
    },
    finish(content: string): void {
      if (!this.task) return;
      this.report(content);
      this.task.status = "completed";
    },
    fail(content: string): void {
      if (!this.task) return;
      this.report(content);
      this.task.status = "failed";
    },
    clear(): void {
      this.task = null;
    },
    setAnalysisId(analysisId: string): void {
      if (this.task) this.task.analysisId = String(analysisId || "").trim() || undefined;
    }
  }
});
