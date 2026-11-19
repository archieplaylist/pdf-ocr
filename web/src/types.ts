export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";
export type PageSource = "native" | "ocr";
export type BlockType = "title" | "heading" | "para" | "table";
export type Layout = "auto" | "flow" | "facsimile";
export type OcrEngine = "auto" | "tesseract" | "rapidocr";

export interface Health {
  status: string;
  tesseract: boolean;
  engines?: Record<string, boolean>;
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
  edited?: boolean;
  created: number;
  updated: number;
  downloads: Record<string, string>;
}

export interface RunModel {
  text: string;
  bold?: boolean;
  italic?: boolean;
  font?: string;
  size?: number;
  color?: string;
}

export interface BlockModel {
  id: string;
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
  color?: string;
  col_widths?: number[] | null;
  indent?: number;
  space_before?: number;
  tab_stops?: number[] | null;
  edited?: boolean;
  added?: boolean;
  runs?: RunModel[] | null;
}

export interface PageModel {
  number: number;
  width: number;
  height: number;
  source: PageSource;
  scan?: boolean;
  ocr_editable?: boolean;
  rev: number;
  edited?: boolean;
  removed?: number[][];
  blocks: BlockModel[];
}
