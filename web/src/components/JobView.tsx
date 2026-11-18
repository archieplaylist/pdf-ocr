import { useState } from "react";
import { api } from "../api";
import type { Job, PageSource } from "../types";
import { LAYOUT_LABELS, formatSeconds, percent } from "../format";
import { PageViewer } from "./PageViewer";
import {
  AlertIcon,
  CheckIcon,
  DownloadIcon,
  FileIcon,
  RefreshIcon,
  SpinnerIcon,
  TableIcon,
} from "./Icons";

interface Props {
  job: Job;
  onReset: () => void;
}

const STATUS_LABEL: Record<Job["status"], string> = {
  queued: "Queued",
  running: "Converting",
  done: "Done",
  error: "Failed",
  cancelled: "Cancelled",
};

function StatusPill({ status }: { status: Job["status"] }) {
  const dot =
    status === "done"
      ? "bg-ok"
      : status === "error" || status === "cancelled"
        ? "bg-danger"
        : status === "running"
          ? "bg-accent animate-pulse-dot"
          : "bg-muted";
  const text =
    status === "done"
      ? "text-ok"
      : status === "error" || status === "cancelled"
        ? "text-danger"
        : status === "running"
          ? "text-accent"
          : "text-muted";
  return (
    <span
      className={`flex shrink-0 items-center gap-1.5 rounded-full border border-line bg-panel px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em] ${text}`}
      role="status"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {STATUS_LABEL[status]}
    </span>
  );
}

function SourceBadge({ source }: { source: PageSource }) {
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

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "warn" | "ok";
}) {
  return (
    <div className="card p-3.5">
      <p className="label">{label}</p>
      <p
        className={`mt-1.5 font-mono text-2xl font-medium tabular-nums tracking-tight ${
          tone === "warn" ? "text-danger" : tone === "ok" ? "text-ok" : ""
        }`}
      >
        {value}
      </p>
      <p className="mt-0.5 text-[11px] text-muted">{hint}</p>
    </div>
  );
}

function ProgressPanel({ job }: { job: Job }) {
  const pct =
    job.pages_total > 0
      ? Math.min(100, (job.pages_done / job.pages_total) * 100)
      : 0;
  const current = Math.min(job.pages_done + 1, job.pages_total);

  return (
    <section className="card p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-accent animate-pulse-dot" />
            <h2 className="text-lg font-semibold tracking-tight">
              {job.status === "queued" ? "Waiting for a worker" : "Reading the document"}
            </h2>
          </div>
          <p className="mt-1.5 text-sm text-muted">
            {job.status === "queued"
              ? "Another conversion is running. This one starts as soon as a slot frees up."
              : `Page ${current} of ${job.pages_total} — native pages are instant, scans take a few seconds.`}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-2xl font-medium tabular-nums tracking-tight">
            {formatSeconds(job.elapsed)}
          </p>
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
            elapsed
          </p>
        </div>
      </div>

      <div className="mt-5">
        <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
          {job.status === "queued" ? (
            <div className="absolute inset-y-0 w-1/3 rounded-full bg-accent animate-indeterminate" />
          ) : (
            <div
              className="h-full rounded-full bg-accent transition-[width] duration-500"
              style={{ width: `${Math.max(pct, 2)}%` }}
            />
          )}
        </div>
        <div className="mt-1.5 flex justify-between font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
          <span>
            {job.pages_done} / {job.pages_total} pages
          </span>
          <span>{Math.round(pct)}%</span>
        </div>
      </div>

      <div className="mt-5 max-h-80 overflow-auto rounded-lg border border-line bg-paper">
        <table className="w-full border-collapse text-left">
          <thead className="sticky top-0 z-10 bg-panel-2">
            <tr className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted">
              <th className="px-3 py-2 font-medium">page</th>
              <th className="px-3 py-2 font-medium">source</th>
              <th className="px-3 py-2 text-right font-medium">blocks</th>
              <th className="px-3 py-2 text-right font-medium">tables</th>
              <th className="px-3 py-2 text-right font-medium">time</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs tabular-nums">
            {job.pages.map((page) => (
              <tr key={page.number} className="border-t border-line">
                <td className="px-3 py-2">{String(page.number).padStart(2, "0")}</td>
                <td className="px-3 py-2">
                  <SourceBadge source={page.source} />
                </td>
                <td className="px-3 py-2 text-right">{page.blocks}</td>
                <td className="px-3 py-2 text-right">{page.tables}</td>
                <td className="px-3 py-2 text-right text-muted">
                  {page.seconds.toFixed(2)}s
                </td>
              </tr>
            ))}
            {job.status === "running" && job.pages_done < job.pages_total && (
              <tr className="border-t border-line text-muted">
                <td className="px-3 py-2" colSpan={5}>
                  <span className="flex items-center gap-2">
                    <SpinnerIcon className="h-3.5 w-3.5 animate-spin" />
                    processing page {current}
                  </span>
                </td>
              </tr>
            )}
            {job.status === "queued" && (
              <tr className="text-muted">
                <td className="px-3 py-3" colSpan={5}>
                  <span className="flex items-center gap-2">
                    <SpinnerIcon className="h-3.5 w-3.5 animate-spin" />
                    queued
                  </span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
        ocr at {job.dpi} dpi · {job.lang}
      </p>
    </section>
  );
}

function ErrorPanel({ job, onReset }: { job: Job; onReset: () => void }) {
  return (
    <section className="card p-5 sm:p-6">
      <div className="flex items-center gap-2 text-danger">
        <AlertIcon className="h-5 w-5" />
        <h2 className="text-lg font-semibold tracking-tight">
          {job.status === "cancelled" ? "Conversion cancelled" : "Conversion failed"}
        </h2>
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed">
        {job.error ?? "The conversion stopped unexpectedly."}
      </p>
      <p className="mt-2 text-xs leading-relaxed text-muted">
        {job.status === "cancelled"
          ? "The job was discarded and its files were removed."
          : "Nothing was written. Check that the PDF is not encrypted and that the tesseract language data is installed."}
      </p>
      <button type="button" className="btn btn-primary mt-5" onClick={onReset}>
        <RefreshIcon className="h-4 w-4" />
        Start over
      </button>
    </section>
  );
}

function PageList({
  job,
  page,
  onPage,
}: {
  job: Job;
  page: number;
  onPage: (page: number) => void;
}) {
  return (
    <section className="card p-4">
      <div className="flex items-baseline justify-between">
        <p className="label">Pages</p>
        <span className="font-mono text-[10px] text-muted">{job.pages_total}</span>
      </div>
      <div className="mt-2 max-h-[21rem] space-y-0.5 overflow-auto pr-1">
        {job.pages.map((item) => {
          const active = item.number === page;
          return (
            <button
              key={item.number}
              type="button"
              onClick={() => onPage(item.number)}
              aria-current={active ? "page" : undefined}
              className={`flex w-full items-center gap-2 rounded-r border-l-2 px-2 py-1.5 text-left transition-colors ${
                active
                  ? "border-accent bg-panel-2"
                  : "border-transparent hover:bg-panel-2"
              }`}
            >
              <span className="font-mono text-[11px] tabular-nums">
                {String(item.number).padStart(2, "0")}
              </span>
              <SourceBadge source={item.source} />
              <span className="ml-auto font-mono text-[10px] tabular-nums text-muted">
                {item.blocks}b · {item.tables}t · {item.seconds.toFixed(1)}s
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function DownloadPanel({ job }: { job: Job }) {
  const done = job.status === "done";
  return (
    <section className="card p-4 sm:p-5">
      <div className="flex items-baseline justify-between">
        <p className="label">Exports</p>
        {done && (
          <span className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.1em] text-ok">
            <CheckIcon className="h-3 w-3" />
            ready
          </span>
        )}
      </div>

      {done ? (
        <div className="mt-3 space-y-2">
          {job.formats.includes("docx") && (
            <a
              className="btn btn-accent w-full justify-between"
              href={api.downloadUrl(job.id, "docx")}
              download
            >
              <span className="flex items-center gap-2">
                <FileIcon className="h-4 w-4" />
                {job.filename.replace(/\.pdf$/i, "")}.docx
              </span>
              <DownloadIcon className="h-4 w-4" />
            </a>
          )}
          {job.formats.includes("xlsx") && (
            <a
              className="btn btn-ghost w-full justify-between border-line"
              href={api.downloadUrl(job.id, "xlsx")}
              download
            >
              <span className="flex items-center gap-2">
                <TableIcon className="h-4 w-4" />
                {job.filename.replace(/\.pdf$/i, "")}.xlsx
              </span>
              <DownloadIcon className="h-4 w-4" />
            </a>
          )}
          <a
            className="flex items-center justify-between rounded px-1 py-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-muted transition-colors hover:text-ink"
            href={api.downloadUrl(job.id, "json")}
            download
          >
            document model · json
            <DownloadIcon className="h-3.5 w-3.5" />
          </a>
          <p className="pt-1 text-[11px] leading-relaxed text-muted">
            DOCX keeps headings, paragraphs and real tables; low-confidence OCR
            runs are highlighted in yellow.
          </p>
        </div>
      ) : (
        <p className="mt-3 text-xs leading-relaxed text-muted">
          Exports appear here as soon as the conversion finishes.
        </p>
      )}
    </section>
  );
}

function JobDetails({ job }: { job: Job }) {
  const native = job.pages.filter((page) => page.source === "native").length;
  const ocr = job.pages.filter((page) => page.source === "ocr").length;
  return (
    <section className="card p-4 sm:p-5">
      <p className="label">Job</p>
      <dl className="mt-3 space-y-2 text-xs">
        <div className="flex justify-between gap-3">
          <dt className="text-muted">Pages</dt>
          <dd className="font-mono text-[11px]">
            {native} native · {ocr} ocr
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-muted">OCR settings</dt>
          <dd className="font-mono text-[11px]">
            {job.dpi} dpi · {job.lang}
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-muted">Layout</dt>
          <dd className="font-mono text-[11px]">
            {LAYOUT_LABELS[job.layout] ?? job.layout}
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-muted">Source</dt>
          <dd className="font-mono text-[11px]">{job.source}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-muted">Job id</dt>
          <dd className="font-mono text-[11px] text-muted">{job.id}</dd>
        </div>
      </dl>
    </section>
  );
}

export function JobView({ job, onReset }: Props) {
  const [page, setPage] = useState(1);
  const done = job.status === "done";

  const blocks = job.pages.reduce((sum, item) => sum + item.blocks, 0);
  const tables = job.pages.reduce((sum, item) => sum + item.tables, 0);
  const native = job.pages.filter((item) => item.source === "native").length;
  const ocr = job.pages.filter((item) => item.source === "ocr").length;

  return (
    <div className="animate-rise space-y-4">
      <section className="card p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-line bg-paper">
              <FileIcon className="h-5 w-5 text-accent" />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold tracking-tight">
                {job.filename}
              </h1>
              <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
                <span>{job.pages_total} pages</span>
                <span aria-hidden="true">·</span>
                <span>{job.dpi} dpi</span>
                <span aria-hidden="true">·</span>
                <span>{job.lang}</span>
                <span aria-hidden="true">·</span>
                <span>{LAYOUT_LABELS[job.layout] ?? job.layout}</span>
                <span aria-hidden="true">·</span>
                <span>{job.formats.join(" + ")}</span>
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <StatusPill status={job.status} />
            <button
              type="button"
              className="btn btn-ghost h-8 px-2.5 text-xs"
              onClick={onReset}
            >
              <RefreshIcon className="h-3.5 w-3.5" />
              New conversion
            </button>
          </div>
        </div>

        {(job.status === "queued" || job.status === "running") && (
          <div className="mt-4">
            <div className="relative h-1 w-full overflow-hidden rounded-full bg-panel-2">
              {job.status === "queued" ? (
                <div className="absolute inset-y-0 w-1/4 rounded-full bg-accent animate-indeterminate" />
              ) : (
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-500"
                  style={{
                    width: `${Math.max(
                      (job.pages_done / Math.max(job.pages_total, 1)) * 100,
                      2,
                    )}%`,
                  }}
                />
              )}
            </div>
          </div>
        )}
      </section>

      {done && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            label="Pages"
            value={String(job.pages_total)}
            hint={`${native} native · ${ocr} ocr`}
          />
          <Stat
            label="Total time"
            value={formatSeconds(job.elapsed)}
            hint={`${job.sec_per_page.toFixed(2)}s per page`}
          />
          <Stat
            label="Blocks"
            value={String(blocks)}
            hint={`${tables} table${tables === 1 ? "" : "s"} found`}
          />
          <Stat
            label="Mean confidence"
            value={percent(job.mean_conf)}
            hint="OCR text, native = 100%"
            tone={job.mean_conf < 0.8 ? "warn" : "ok"}
          />
        </div>
      )}

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          {job.status === "error" || job.status === "cancelled" ? (
            <ErrorPanel job={job} onReset={onReset} />
          ) : done ? (
            <PageViewer job={job} page={page} onPage={setPage} />
          ) : (
            <ProgressPanel job={job} />
          )}
        </div>

        <aside className="space-y-4">
          {done && <PageList job={job} page={page} onPage={setPage} />}
          <DownloadPanel job={job} />
          <JobDetails job={job} />
        </aside>
      </div>
    </div>
  );
}
