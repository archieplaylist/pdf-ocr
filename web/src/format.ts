import type { BlockType, Layout } from "./types";

export const FIXTURE_LABELS: Record<string, string> = {
  doc1_native_2col: "Two-column article",
  doc2_native_invoice: "Native invoice",
  doc3_scan_clean: "Clean scan",
  doc4_photo_invoice: "Phone photo",
  doc5_mixed: "Mixed document",
};

export function fixtureLabel(id: string): string {
  return FIXTURE_LABELS[id] ?? id.replace(/[_-]+/g, " ");
}

export const LAYOUT_LABELS: Record<Layout, string> = {
  auto: "auto layout",
  flow: "editable text",
  facsimile: "page images",
};

export function formatSeconds(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value < 60) return `${value.toFixed(value < 10 ? 2 : 1)}s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${minutes}m ${seconds}s`;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function percent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export const blockMeta: Record<
  BlockType,
  { label: string; color: string; short: string }
> = {
  title: { label: "Title", color: "--b-title", short: "T" },
  heading: { label: "Heading", color: "--b-heading", short: "H" },
  para: { label: "Paragraph", color: "--b-para", short: "P" },
  table: { label: "Table", color: "--b-table", short: "⊞" },
};

export const BLOCK_TYPES: BlockType[] = ["title", "heading", "para", "table"];
