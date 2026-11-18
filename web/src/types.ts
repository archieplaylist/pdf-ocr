export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";
export type PageSource = "native" | "ocr";
export type BlockType = "title" | "heading" | "para" | "table";
export type Layout = "auto" | "flow" | "facsimile";

export interface Health {
  status: string;
  tesseract: boolean;
  max_upload_mb: number;
}

export interface Fixture {
  id: string;
  name: string;
  size_kb: number;
  pages: number;
}

export interface PageProgress {
  number: number;
  source: PageSource;
  scan: boolean;
  blocks: number;
  tables: number;
  seconds: number;
}

export interface Job {
  id: string;
  status: JobStatus;
  filename: string;
  source: "upload" | "fixture";
  formats: string[];
  dpi: number;
  lang: string;
  layout: Layout;
  pages_total: number;
  pages_done: number;
  pages: PageProgress[];
  elapsed: number;
  sec_per_page: number;
  mean_conf: number;
  error: string | null;
  created: number;
  updated: number;
  downloads: Record<string, string>;
}

export interface BlockModel {
  type: BlockType;
  bbox: [number, number, number, number];
  text: string;
  size: number;
  conf: number;
  rows?: string[][] | null;
  align?: "left" | "center" | "right";
  bold?: boolean;
  italic?: boolean;
  font?: string;
  col_widths?: number[] | null;
}

export interface PageModel {
  number: number;
  width: number;
  height: number;
  source: PageSource;
  scan?: boolean;
  blocks: BlockModel[];
}
