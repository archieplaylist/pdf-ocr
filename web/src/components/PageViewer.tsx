import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { BlockModel, BlockType, Job, PageModel, RunModel } from "../types";
import { BLOCK_TYPES, blockMeta } from "../format";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  SpinnerIcon,
} from "./Icons";

interface Props {
  job: Job;
  page: number;
  onPage: (page: number) => void;
}

type ViewMode = "document" | "page";
type EditorMode = "edit" | "preview";

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

function SourceBadge({ source }: { source: "native" | "ocr" }) {
  return (
    <span
      className={`rounded border px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.1em] ${
        source === "ocr"
          ? "border-accent/40 bg-accent-soft text-accent"
          : "border-line bg-panel-2 text-muted"
      }`}
    >
      {source}
    </span>
  );
}

function escapeHtml(value: unknown): string {
  if (value == null) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function rgbToHex(value: string): string {
  const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
  if (!match) return value.startsWith("#") ? value : "";
  const toHex = (part: string) => Number(part).toString(16).padStart(2, "0");
  return `#${toHex(match[1])}${toHex(match[2])}${toHex(match[3])}`;
}

function runsToHtml(block: BlockModel): string {
  const runs =
    block.runs && block.runs.length
      ? block.runs
      : [
          {
            text: block.text,
            bold: block.bold,
            italic: block.italic,
            font: block.font,
            size: block.size,
            color: block.color,
          },
        ];
  return runs
    .map((run) => {
      const styles: string[] = [];
      if (run.bold) styles.push("font-weight:bold");
      if (run.italic) styles.push("font-style:italic");
      if (run.color) styles.push(`color:${run.color}`);
      if (run.size) styles.push(`font-size:${run.size}px`);
      if (run.font) styles.push(`font-family:${run.font}`);
      const inner = escapeHtml(run.text || "").replace(/\n/g, "<br>");
      return styles.length
        ? `<span style="${styles.join(";")}">${inner}</span>`
        : inner;
    })
    .join("");
}

function nodeToRuns(node: Node, inherited: Partial<RunModel>): RunModel[] {
  const out: RunModel[] = [];
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.nodeValue || "";
    if (text) out.push({ text, ...inherited });
    return out;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return out;
  const el = node as HTMLElement;
  const tag = el.tagName.toLowerCase();
  if (tag === "br") {
    out.push({ text: "\n", ...inherited });
    return out;
  }
  const next: Partial<RunModel> = { ...inherited };
  if (tag === "b" || tag === "strong") next.bold = true;
  if (tag === "i" || tag === "em") next.italic = true;
  if (el.style.fontWeight === "bold" || parseInt(el.style.fontWeight) >= 600)
    next.bold = true;
  if (el.style.fontStyle === "italic") next.italic = true;
  const fs = parseFloat(el.style.fontSize);
  if (!isNaN(fs) && fs > 0) next.size = fs;
  const hex = rgbToHex(el.style.color);
  if (hex) next.color = hex;
  const ff = el.style.fontFamily
    ? el.style.fontFamily.split(",")[0].replace(/['"]/g, "").trim()
    : "";
  if (ff) next.font = ff;

  for (const child of Array.from(el.childNodes)) {
    out.push(...nodeToRuns(child, next));
  }
  return out;
}

function htmlToRuns(el: HTMLElement): RunModel[] {
  const collected = nodeToRuns(el, {});
  const merged: RunModel[] = [];
  for (const run of collected) {
    const last = merged[merged.length - 1];
    if (
      last &&
      last.bold === run.bold &&
      last.italic === run.italic &&
      last.color === run.color &&
      last.size === run.size &&
      last.font === run.font
    ) {
      last.text += run.text;
    } else {
      merged.push({ ...run });
    }
  }
  return merged.length ? merged : [{ text: el.innerText || "" }];
}

function blockToHtml(block: BlockModel, editable = false): string {
  if (block.type === "table") {
    const rows = (block.rows || [])
      .map(
        (row) =>
          `<tr>${row
            .map((cell) =>
              editable
                ? `<td contenteditable="true" style="border:1px solid #999;padding:2px 4px;">${escapeHtml(cell)}</td>`
                : `<td style="border:1px solid #999;padding:4px 6px;">${escapeHtml(cell)}</td>`,
            )
            .join("")}</tr>`,
      )
      .join("");
    const attrs = editable
      ? ` data-block-id="${block.id}" data-block-type="table"`
      : "";
    return `<table${attrs} style="border-collapse:collapse;width:100%;margin:8px 0;">${rows}</table>`;
  }
  if (block.type === "image") {
    const [x0 = 0, y0 = 0, x1 = 100, y1 = 100] = block.bbox || [];
    const w = Math.round(x1 - x0);
    const h = Math.round(y1 - y0);
    const attrs = editable
      ? ` data-block-id="${block.id}" data-block-type="image" contenteditable="false"`
      : "";
    return `<div${attrs} style="border:1px dashed #aaa;background:rgba(0,0,0,0.03);padding:12px;margin:8px 0;text-align:center;color:#888;font-size:12px;font-family:monospace;border-radius:3px;">[Image · ${w}×${h} pt]</div>`;
  }
  const tag =
    block.type === "title" ? "h1" : block.type === "heading" ? "h2" : "p";
  const align = block.align ? `text-align:${block.align};` : "";
  const size = block.size ? `font-size:${block.size}px;` : "";
  const weight = block.bold ? "font-weight:bold;" : "";
  const style = block.italic ? "font-style:italic;" : "";
  const color = block.color ? `color:${block.color};` : "";
  const font = block.font ? `font-family:${block.font};` : "";
  const content =
    block.runs && block.runs.length
      ? runsToHtml(block)
      : escapeHtml(block.text || "");
  const attrs = editable
    ? ` data-block-id="${block.id}" data-block-type="${block.type}"`
    : "";
  return `<${tag}${attrs} style="${align}${size}${weight}${style}${color}${font}margin:6px 0;">${content}</${tag}>`;
}

function buildPreviewHtml(model: PageModel | undefined, job: Job, page: number): string {
  if (!model) return "<p>No content</p>";
  const blocks = model.blocks.map((b) => blockToHtml(b, false)).join("");
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Preview - ${escapeHtml(job.filename)}</title>
<style>
  body { font-family: Arial, sans-serif; max-width: 800px; margin: 2rem auto; padding: 1rem; line-height: 1.5; color: #191713; }
  h1 { font-size: 1.6em; margin: 1em 0 0.5em; }
  h2 { font-size: 1.3em; margin: 0.8em 0 0.4em; }
  p { margin: 0.5em 0; }
  table { border-collapse: collapse; width: 100%; }
  td { border: 1px solid #999; padding: 4px 6px; }
  .page-break { border-top: 2px dashed #ccc; margin: 2rem 0; padding-top: 0.5rem; font-family: monospace; font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.1em; }
</style></head><body>
<div style="font-family:monospace;font-size:10px;color:#888;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:1rem;">Preview · page ${page} of ${job.pages_total}</div>
${blocks}
</body></html>`;
}

export function PageViewer({ job, page, onPage }: Props) {
  const [cache, setCache] = useState<Record<number, PageModel>>({});
  const [draft, setDraft] = useState<Record<number, PageModel>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error" | "conflict"
  >("idle");
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    const stored = window.localStorage.getItem("pdfconv.viewMode");
    return stored === "page" ? "page" : "document";
  });
  const [editorMode, setEditorMode] = useState<EditorMode>("edit");
  const [zoom, setZoom] = useState<number>(() => {
    const stored = window.localStorage.getItem("pdfconv.zoom");
    const parsed = stored ? Number(stored) : 1;
    return Number.isFinite(parsed) ? Math.min(Math.max(parsed, 0.5), 4) : 1;
  });

  // Undo/redo stacks persisted across page switches (keyed by page number)
  const undoStacksRef = useRef<Map<number, PageModel[]>>(new Map());
  const redoStacksRef = useRef<Map<number, PageModel[]>>(new Map());
  const hasPendingUndoRef = useRef(false);

  const pendingRef = useRef<PageModel | null>(null);
  const inFlightRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const editorRef = useRef<HTMLDivElement | null>(null);
  const focusedRef = useRef<string | null>(null);

  const model = draft[page] ?? cache[page];

  const getUndoStack = useCallback(() => {
    if (!undoStacksRef.current.has(page)) {
      undoStacksRef.current.set(page, []);
      redoStacksRef.current.set(page, []);
    }
    return undoStacksRef.current.get(page)!;
  }, [page]);

  const getRedoStack = useCallback(() => {
    if (!redoStacksRef.current.has(page)) {
      redoStacksRef.current.set(page, []);
    }
    return redoStacksRef.current.get(page)!;
  }, [page]);

  const pushUndo = useCallback(
    (snapshot: PageModel) => {
      const undo = getUndoStack();
      undo.push(snapshot);
      if (undo.length > 100) undo.shift();
      getRedoStack().length = 0;
    },
    [getUndoStack, getRedoStack],
  );

  const persistZoom = (value: number) => {
    setZoom(value);
    window.localStorage.setItem("pdfconv.zoom", String(value));
  };
  const persistViewMode = (value: ViewMode) => {
    setViewMode(value);
    window.localStorage.setItem("pdfconv.viewMode", value);
  };

  const flush = useCallback(async () => {
    if (inFlightRef.current) return;
    const pending = pendingRef.current;
    if (!pending) return;
    inFlightRef.current = true;
    setSaveState("saving");
    try {
      const saved = await api.replacePageBlocks(job.id, page, {
        rev: pending.rev,
        blocks: pending.blocks.map((block) => ({
          id: block.id,
          type: block.type,
          bbox: block.bbox,
          text: block.runs ? block.runs.map((run) => run.text).join("") : block.text,
          size: block.size,
          conf: block.conf,
          rows: block.rows,
          align: block.align,
          bold: block.bold,
          italic: block.italic,
          font: block.font,
          color: block.color,
          indent: block.indent,
          space_before: block.space_before,
          tab_stops: block.tab_stops,
          col_widths: block.col_widths,
          runs: block.runs ?? null,
        })),
      });
      pendingRef.current = null;
      setCache((prev) => ({ ...prev, [page]: saved }));
      setDraft((prev) => {
        const next = { ...prev };
        delete next[page];
        return next;
      });
      setSaveState("saved");
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : "Could not save.";
      if (message.toLowerCase().includes("changed")) {
        setSaveState("conflict");
        undoStacksRef.current.clear();
        redoStacksRef.current.clear();
        try {
          const fresh = await api.blocks(job.id, page);
          setCache((prev) => ({ ...prev, [page]: fresh }));
          setDraft((prev) => {
            const next = { ...prev };
            delete next[page];
            return next;
          });
        } catch {
          // keep conflict state
        }
      } else {
        setSaveState("error");
      }
    } finally {
      inFlightRef.current = false;
      if (pendingRef.current) void flush();
    }
  }, [job.id, page]);

  const scheduleSave = useCallback(
    (next: PageModel, immediate = false) => {
      pendingRef.current = next;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      if (immediate) {
        void flush();
      } else {
        timerRef.current = window.setTimeout(() => {
          timerRef.current = null;
          void flush();
        }, 800);
      }
    },
    [flush],
  );

  const undo = useCallback(() => {
    const undoStack = getUndoStack();
    if (undoStack.length === 0 || !model) return;
    const redoStack = getRedoStack();
    redoStack.push(model);
    const prev = undoStack.pop()!;
    setDraft((prevDraft) => ({ ...prevDraft, [page]: prev }));
    pendingRef.current = prev;
    scheduleSave(prev, true);
    setSaveState("idle");
    hasPendingUndoRef.current = false;
  }, [model, page, getUndoStack, getRedoStack, scheduleSave]);

  const redo = useCallback(() => {
    const redoStack = getRedoStack();
    if (redoStack.length === 0 || !model) return;
    const undoStack = getUndoStack();
    undoStack.push(model);
    const next = redoStack.pop()!;
    setDraft((prevDraft) => ({ ...prevDraft, [page]: next }));
    pendingRef.current = next;
    scheduleSave(next, true);
    setSaveState("idle");
  }, [model, page, getUndoStack, getRedoStack, scheduleSave]);

  useEffect(() => {
    if (cache[page]) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .blocks(job.id, page)
      .then((loaded) => {
        if (!cancelled) setCache((prev) => ({ ...prev, [page]: loaded }));
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load this page.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [job.id, page, cache]);

  useEffect(() => {
    setSelected(null);
    hasPendingUndoRef.current = false;
  }, [page]);

  // Sync editor DOM when model loads/changes externally (not while focused).
  useEffect(() => {
    if (!model || !editorRef.current || viewMode !== "document" || editorMode !== "edit") return;
    if (focusedRef.current) return;
    const container = editorRef.current;
    container.innerHTML = model.blocks.map((block) => blockToHtml(block, true)).join("");
  }, [model, viewMode, editorMode]);

  // Keyboard shortcuts for undo/redo
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if ((e.key === "z" && e.shiftKey) || e.key === "y") {
        e.preventDefault();
        redo();
      }
    };
    const el = editorRef.current;
    if (el) el.addEventListener("keydown", handler);
    return () => {
      if (el) el.removeEventListener("keydown", handler);
    };
  }, [undo, redo]);

  const commitDocument = useCallback(() => {
    if (!model || !editorRef.current) return;
    const container = editorRef.current;
    const blocks: BlockModel[] = [];
    for (const el of Array.from(container.children)) {
      const htmlEl = el as HTMLElement;
      let blockId = htmlEl.dataset.blockId;
      const original = blockId ? model.blocks.find((b) => b.id === blockId) : undefined;
      const type = (htmlEl.dataset.blockType as BlockType | "image") || "para";
      if (!blockId) {
        blockId = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
        htmlEl.dataset.blockId = blockId;
        htmlEl.dataset.blockType = type;
      }
      if (htmlEl.tagName.toLowerCase() === "table") {
        const rows: string[][] = [];
        for (const tr of Array.from(htmlEl.querySelectorAll("tr"))) {
          const cells = Array.from(tr.querySelectorAll("td,th")).map(
            (td) => (td as HTMLElement).innerText || "",
          );
          rows.push(cells);
        }
        blocks.push({
          ...(original || {}),
          id: blockId,
          type: "table",
          bbox: original?.bbox || [72, 72, 500, 120],
          text: "",
          size: original?.size || 10.5,
          conf: original?.conf ?? 1,
          rows,
          align: original?.align || "left",
          bold: original?.bold || false,
          italic: original?.italic || false,
          font: original?.font || "",
          color: original?.color || "",
          edited: true,
          added: original?.added || !original,
          runs: null,
        });
      } else if (type === "image" || htmlEl.dataset.blockType === "image") {
        if (original) {
          blocks.push(original);
        }
      } else {
        const tag = htmlEl.tagName.toLowerCase();
        const inferredType: BlockType =
          tag === "h1" ? "title" : tag === "h2" ? "heading" : "para";
        const runs = htmlToRuns(htmlEl);
        const text = runs.map((r) => r.text).join("");
        blocks.push({
          ...(original || {}),
          id: blockId,
          type: type || inferredType,
          bbox: original?.bbox || [72, 72, 500, 90],
          text,
          size: original?.size || 10.5,
          conf: original?.conf ?? 1,
          rows: null,
          align: (htmlEl.style.textAlign as BlockModel["align"]) || original?.align || "left",
          bold: original?.bold || false,
          italic: original?.italic || false,
          font: original?.font || "",
          color: original?.color || "",
          edited: true,
          added: original?.added || !original,
          runs,
        });
      }
    }
    if (blocks.length === 0) return;
    // Push undo snapshot before committing (only if not already pending)
    if (!hasPendingUndoRef.current && model) {
      pushUndo(model);
      hasPendingUndoRef.current = true;
    }
    const next: PageModel = { ...model, blocks };
    setDraft((prev) => ({ ...prev, [page]: next }));
    pendingRef.current = next;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      void flush();
      hasPendingUndoRef.current = false;
    }, 800);
    setSaveState("idle");
  }, [model, page, flush, pushUndo]);

  function addBlock(type: BlockType) {
    if (!model) return;
    if (!hasPendingUndoRef.current) {
      pushUndo(model);
      hasPendingUndoRef.current = true;
    }
    const newBlock: BlockModel = {
      id: `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
      type,
      bbox: [72, 72, 500, 90],
      text: type === "table" ? "" : "New block",
      size: 10.5,
      conf: 1,
      rows: type === "table" ? [["", ""]] : null,
      align: "left",
      bold: type === "title" || type === "heading",
      italic: false,
      font: "",
      color: "",
      indent: 0,
      space_before: 0,
      edited: true,
      added: true,
      runs: null,
    };
    const next: PageModel = {
      ...model,
      blocks: [...model.blocks, newBlock],
    };
    setDraft((prev) => ({ ...prev, [page]: next }));
    pendingRef.current = next;
    scheduleSave(next, true);
    setSelected(newBlock.id);
    setTimeout(() => hasPendingUndoRef.current = false, 900);
  }

  function deleteBlock(blockId: string) {
    if (!model) return;
    pushUndo(model);
    const next: PageModel = {
      ...model,
      blocks: model.blocks.filter((block) => block.id !== blockId),
    };
    setDraft((prev) => ({ ...prev, [page]: next }));
    pendingRef.current = next;
    scheduleSave(next, true);
    if (selected === blockId) setSelected(null);
    hasPendingUndoRef.current = false;
  }

  const zoomIn = () => {
    const idx = ZOOM_STEPS.findIndex((z) => z >= zoom);
    const next = ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, idx + 1)];
    persistZoom(next);
  };
  const zoomOut = () => {
    const idx = ZOOM_STEPS.findIndex((z) => z >= zoom);
    const next = ZOOM_STEPS[Math.max(0, idx - 1)];
    persistZoom(next);
  };
  const zoomReset = () => persistZoom(1);

  const openPreviewInNewTab = () => {
    if (focusedRef.current) {
      commitDocument();
    }
    const currentModel = pendingRef.current || model;
    const html = buildPreviewHtml(currentModel, job, page);
    const blob = new Blob([html], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
  };

  const source = model?.source ?? job.pages[page - 1]?.source ?? "native";

  return (
    <section className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <p className="label">Editor</p>
          <SourceBadge source={source} />
          <span className="font-mono text-[10px] text-muted">
            {model ? `${Math.round(model.width)}×${Math.round(model.height)} pt` : ""}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded border border-line bg-paper p-0.5">
            <button
              type="button"
              className="btn btn-ghost h-7 px-2 text-xs"
              onClick={undo}
              disabled={getUndoStack().length === 0}
              title="Undo (Ctrl+Z)"
            >
              Undo
            </button>
            <button
              type="button"
              className="btn btn-ghost h-7 px-2 text-xs"
              onClick={redo}
              disabled={getRedoStack().length === 0}
              title="Redo (Ctrl+Y)"
            >
              Redo
            </button>
          </div>
          <div className="flex items-center gap-1 rounded border border-line bg-paper p-0.5">
            <button
              type="button"
              className="btn btn-ghost h-7 w-7 p-0 text-xs"
              onClick={zoomOut}
              aria-label="Zoom out"
            >
              −
            </button>
            <span className="min-w-12 text-center font-mono text-[11px] text-muted">
              {Math.round(zoom * 100)}%
            </span>
            <button
              type="button"
              className="btn btn-ghost h-7 w-7 p-0 text-xs"
              onClick={zoomIn}
              aria-label="Zoom in"
            >
              +
            </button>
            <button
              type="button"
              className="btn btn-ghost h-7 px-2 text-xs"
              onClick={zoomReset}
            >
              Fit
            </button>
          </div>
          <div className="flex items-center gap-1 rounded border border-line bg-paper p-0.5">
            <button
              type="button"
              className={`btn btn-ghost h-7 px-2.5 text-xs ${viewMode === "document" && editorMode === "edit" ? "bg-ink text-paper" : ""}`}
              onClick={() => {
                persistViewMode("document");
                setEditorMode("edit");
              }}
            >
              Edit
            </button>
            <button
              type="button"
              className={`btn btn-ghost h-7 px-2.5 text-xs ${viewMode === "document" && editorMode === "preview" ? "bg-ink text-paper" : ""}`}
              onClick={() => {
                if (focusedRef.current) {
                  commitDocument();
                  focusedRef.current = null;
                }
                persistViewMode("document");
                setEditorMode("preview");
              }}
            >
              Preview
            </button>
            <button
              type="button"
              className={`btn btn-ghost h-7 px-2.5 text-xs ${viewMode === "page" ? "bg-ink text-paper" : ""}`}
              onClick={() => {
                if (focusedRef.current) {
                  commitDocument();
                  focusedRef.current = null;
                }
                persistViewMode("page");
              }}
            >
              Page
            </button>
          </div>
          <button
            type="button"
            className="btn btn-ghost h-7 px-2 text-xs"
            onClick={openPreviewInNewTab}
            title="Open a read-only preview in a new tab"
          >
            New tab
          </button>
          <span
            className={`font-mono text-[10px] uppercase tracking-[0.12em] ${
              saveState === "saving"
                ? "text-accent"
                : saveState === "saved"
                  ? "text-ok"
                  : saveState === "conflict"
                    ? "text-danger"
                    : saveState === "error"
                      ? "text-danger"
                      : "text-muted"
            }`}
          >
            {saveState === "saving"
              ? "saving…"
              : saveState === "saved"
                ? "saved"
                : saveState === "conflict"
                  ? "conflict"
                  : saveState === "error"
                    ? "save failed"
                    : "idle"}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="btn btn-ghost h-7 w-7 p-0"
              onClick={() => onPage(page - 1)}
              disabled={page <= 1}
              aria-label="Previous page"
            >
              <ChevronLeftIcon className="h-4 w-4" />
            </button>
            <span className="min-w-20 text-center font-mono text-[11px] text-muted">
              page {page} / {job.pages_total}
            </span>
            <button
              type="button"
              className="btn btn-ghost h-7 w-7 p-0"
              onClick={() => onPage(page + 1)}
              disabled={page >= job.pages_total}
              aria-label="Next page"
            >
              <ChevronRightIcon className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {viewMode === "document" && editorMode === "edit" && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-4 py-2">
          <p className="label mr-2">Add</p>
          {BLOCK_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              className="chip py-1 text-[11px]"
              onClick={() => addBlock(type)}
              disabled={!model}
            >
              + {blockMeta[type].label}
            </button>
          ))}
          {selected && (
            <button
              type="button"
              className="chip ml-auto py-1 text-[11px] text-danger"
              onClick={() => selected && deleteBlock(selected)}
            >
              Delete block
            </button>
          )}
        </div>
      )}

      {loading ? (
        <div className="flex h-[52vh] flex-col items-center justify-center gap-3">
          <SpinnerIcon className="h-5 w-5 animate-spin text-muted" />
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
            rendering page
          </span>
        </div>
      ) : error ? (
        <div className="flex h-[52vh] flex-col items-center justify-center gap-2 text-center">
          <p className="text-sm text-danger">{error}</p>
        </div>
      ) : model ? (
        viewMode === "document" ? (
          editorMode === "preview" ? (
            <div
              className="overflow-auto bg-paper"
              style={{ height: "62vh" }}
            >
              <div
                style={{
                  transform: `scale(${zoom})`,
                  transformOrigin: "top left",
                  width: `${100 / zoom}%`,
                  minHeight: "100%",
                  padding: "1rem 1.25rem",
                }}
              >
                <div
                  className="preview-content"
                  dangerouslySetInnerHTML={{
                    __html: model.blocks.map((b) => blockToHtml(b, false)).join(""),
                  }}
                />
              </div>
            </div>
          ) : (
            <div
              className="overflow-auto bg-paper"
              style={{ height: "62vh" }}
              onWheel={(event) => {
                if (event.ctrlKey || event.metaKey) {
                  event.preventDefault();
                  if (event.deltaY < 0) zoomIn();
                  else zoomOut();
                }
              }}
            >
              <div
                style={{
                  transform: `scale(${zoom})`,
                  transformOrigin: "top left",
                  width: `${100 / zoom}%`,
                  minHeight: "100%",
                  padding: "1rem 1.25rem",
                }}
              >
                <div
                  ref={editorRef}
                  className="outline-none"
                  style={{ minHeight: "40vh" }}
                  contentEditable
                  suppressContentEditableWarning
                  onFocus={() => {
                    focusedRef.current = "doc";
                  }}
                  onBlur={() => {
                    focusedRef.current = null;
                    commitDocument();
                    hasPendingUndoRef.current = false;
                  }}
                  onInput={() => {
                    if (!hasPendingUndoRef.current && model) {
                      pushUndo(model);
                      hasPendingUndoRef.current = true;
                    }
                    commitDocument();
                  }}
                  onPointerDown={(e) => {
                    const target = (e.target as HTMLElement).closest("[data-block-id]") as HTMLElement | null;
                    if (target && target.dataset.blockId) {
                      setSelected(target.dataset.blockId);
                    }
                  }}
                />
              </div>
            </div>
          )
        ) : (
          <div className="bg-panel-2 p-3 sm:p-5">
            <div className="flex justify-center">
              <div className="relative inline-block max-w-full">
                <img
                  src={api.previewUrl(job.id, page, 130)}
                  alt={`Rendered page ${page} of ${job.filename}`}
                  className="block max-h-[68vh] w-auto max-w-full rounded-sm border border-line bg-white"
                  style={{ transform: `scale(${zoom})`, transformOrigin: "top left" }}
                />
              </div>
            </div>
          </div>
        )
      ) : null}

      {saveState === "conflict" && (
        <p className="border-t border-line px-4 py-2 text-xs text-danger">
          The page changed on the server. Reload to continue editing.
        </p>
      )}

      {job.pages_total > 1 && (
        <div className="flex gap-2 overflow-x-auto border-t border-line bg-panel px-4 py-3">
          {Array.from({ length: job.pages_total }, (_, index) => index + 1).map(
            (number) => (
              <button
                key={number}
                type="button"
                onClick={() => onPage(number)}
                aria-label={`Go to page ${number}`}
                aria-current={number === page ? "page" : undefined}
                className={`relative shrink-0 overflow-hidden rounded border transition-colors ${
                  number === page
                    ? "border-accent ring-1 ring-accent"
                    : "border-line hover:border-muted"
                }`}
              >
                <img
                  src={api.previewUrl(job.id, number, 45)}
                  alt=""
                  className="block h-20 w-auto min-w-14 bg-white"
                  loading="lazy"
                />
                <span className="absolute bottom-0 right-0 bg-ink/80 px-1 font-mono text-[9px] text-paper">
                  {number}
                </span>
              </button>
            ),
          )}
        </div>
      )}
    </section>
  );
}
