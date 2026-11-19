import type { Fixture, Health, Job, PageModel } from "./types";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const BASE = "/api";

function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : null,
      )
      .filter((part): part is string => Boolean(part));
    return parts.length ? parts.join("; ") : null;
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError("Cannot reach the conversion service.", 0);
  }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = detailMessage(body?.detail) ?? message;
    } catch {
      // non-JSON error body; keep the status line
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export interface CreateJobInput {
  file?: File;
  fixture?: string;
  formats: string;
  dpi: number;
  lang: string;
  layout: string;
  engine: string;
}

export const api = {
  health: () => request<Health>("/health"),

  fixtures: () =>
    request<{ fixtures: Fixture[] }>("/fixtures").then((data) => data.fixtures),

  createJob: (input: CreateJobInput) => {
    const form = new FormData();
    if (input.file) form.append("file", input.file);
    if (input.fixture) form.append("fixture", input.fixture);
    form.append("formats", input.formats);
    form.append("dpi", String(input.dpi));
    form.append("lang", input.lang);
    form.append("layout", input.layout);
    form.append("engine", input.engine);
    return request<{ id: string; status: string }>("/jobs", {
      method: "POST",
      body: form,
    });
  },

  job: (id: string) => request<Job>(`/jobs/${id}`),

  deleteJob: async (id: string) => {
    const response = await fetch(`${BASE}/jobs/${id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) {
      throw new ApiError("Could not discard the job.", response.status);
    }
  },

  blocks: (id: string, page: number) =>
    request<PageModel>(`/jobs/${id}/pages/${page}/blocks`),

  patchBlock: (id: string, page: number, blockId: string, patch: Record<string, unknown>) =>
    request<PageModel>(`/jobs/${id}/pages/${page}/blocks/${blockId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),

  replacePageBlocks: (id: string, page: number, payload: { rev: number; blocks: Record<string, unknown>[] }) =>
    request<PageModel>(`/jobs/${id}/pages/${page}/blocks`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  previewUrl: (id: string, page: number, dpi: number) =>
    `${BASE}/jobs/${id}/pages/${page}/preview.png?dpi=${dpi}`,

  downloadUrl: (id: string, format: string, mode?: string) => {
    const base = `${BASE}/jobs/${id}/download/${format}`;
    return mode ? `${base}?mode=${mode}` : base;
  },
};
