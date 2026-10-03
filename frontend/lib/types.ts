export type Scene = "exam" | "meeting";
export type Backend = "original" | "jev";
export type Agent = "auto" | "ability" | "essay" | "interview";
export type PracticeAction = "run" | "analyze" | "outline" | "draft" | "critique" | "start" | "answer" | "follow_up";
export interface Question {
  id: string; label?: string; number?: string; page?: number; preview?: string;
  status: string; answer?: string; explanation?: string; needed?: string;
}
export interface Artifact {
  kind: string; agent?: Agent | "unknown"; question?: string; reason?: string;
  questions?: Question[]; transcript?: string;
  stages?: { id: string; label: string; text: string }[];
  next_actions?: { id: PracticeAction; label: string }[];
}
export interface Reply {
  text: string; status?: string; error?: string; scene?: string; note?: string;
  exam_backend?: Backend; archived_id?: string; rejected_files?: string[]; artifacts?: Artifact[];
}
export interface TaskRecord {
  id: string; request_id?: string; session_id?: string; scene?: string;
  status: "pending" | "running" | "interrupted" | "done" | "error";
  result?: Reply | null; error?: string; created_at?: string; created?: number;
  media?: { index: number; name: string; type: "image" | "audio"; url: string }[];
}
export interface Progress {
  steps?: { id: string; state: "pending" | "active" | "done" | "error" }[];
  finished?: boolean; error?: boolean; batch?: { total: number; done: number };
}
export interface ChatInput {
  sessionId: string; requestId: string; text: string; scene: Scene; backend: Backend;
  files?: File[]; event?: Record<string, unknown>;
}
export interface KnowledgeDocument { id: string; title: string; version?: string; content?: string; created?: string; chunks?: number }
export interface Resource { id: string; title: string; scene: string; created: string; content?: string; has_transcript?: boolean }
export interface Transcript {
  id: string; text: string; content?: string; summary?: string; report?: string;
  speakers: { id: number; label: string; turns?: number }[];
  utterances: { speaker: number; text: string; start?: number; end?: number }[];
  verification?: { status?: string };
}
