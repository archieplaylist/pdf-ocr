import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { BlockModel, BlockType, Job, PageModel } from "../types";
import { BLOCK_TYPES, blockMeta, percent } from "../format";
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

type Visibility = Record<BlockType, boolean>;

const ALL_VISIBLE: Visibility = {
  title: true,
  heading: true,
  para: true,
  table: true,
};

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

function BlockDetails({ block }: { block: BlockModel }) {
  const meta = blockMeta[block.type];
  const [x0, y0, x1, y1] = block.bbox;
  return (
    <div className="animate-rise">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em]">
          <span
            className="h-2 w-2 rounded-sm"
            style={{ background: `var(${meta.color})` }}
          />
          {meta.label}
        </span>
        <span className="font-mono text-[10px] text-muted">
          conf {percent(block.conf, 0)}
        </span>
        <span className="font-mono text-[10px] text-muted">
          {Math.round(x1 - x0)}×{Math.round(y1 - y0)} pt
        </span>
        {block.type === "table" && block.rows && (
          <span className="font-mono text-[10px] text-muted">
            {block.rows.length}×{Math.max(...block.rows.map((row) => row.length))}
          </span>
        )}
      </div>

      {block.type === "table" && block.rows ? (
        <div className="mt-2 max-h-52 overflow-auto rounded border border-line">
          <table className="w-full border-collapse text-xs">
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b border-line last:border-0">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      className="border-r border-line px-2 py-1 align-top last:border-0"
                    >
                      {cell || <span className="text-muted">·</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap text-sm leading-relaxed">
          {block.text || <span className="text-muted">(no text)</span>}
        </p>
      )}
    </div>
  );
}

export function PageViewer({ job, page, onPage }: Props) {
  const [cache, setCache] = useState<Record<number, PageModel>>({});
  const [loading, setLoading] = useState(true);
  const [imageLoading, setImageLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [visible, setVisible] = useState<Visibility>(ALL_VISIBLE);
  const [lowOnly, setLowOnly] = useState(false);
  const [hovered, setHovered] = useState<BlockModel | null>(null);
  const [pinned, setPinned] = useState<BlockModel | null>(null);

  const model = cache[page];

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
          setError(
            err instanceof Error ? err.message : "Could not load this page.",
          );
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
    let cancelled = false;
    setImageLoading(true);
    const image = new Image();
    image.onload = () => {
      if (!cancelled) setImageLoading(false);
    };
    image.onerror = () => {
      if (!cancelled) setImageLoading(false);
    };
    image.src = api.previewUrl(job.id, page, 130);
    return () => {
      cancelled = true;
      image.onload = null;
      image.onerror = null;
    };
  }, [job.id, page]);

  useEffect(() => {
    setHovered(null);
    setPinned(null);
  }, [page]);

  const counts = useMemo(() => {
    const tally: Record<BlockType, number> = {
      title: 0,
      heading: 0,
      para: 0,
      table: 0,
    };
    for (const block of model?.blocks ?? []) tally[block.type] += 1;
    return tally;
  }, [model]);

  const shown = useMemo(
    () =>
      (model?.blocks ?? []).filter(
        (block) => visible[block.type] && (!lowOnly || block.conf < 0.6),
      ),
    [model, visible, lowOnly],
  );

  const active = hovered ?? pinned;
  const source = model?.source ?? job.pages[page - 1]?.source ?? "native";

  return (
    <section className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <p className="label">Preview</p>
          <SourceBadge source={source} />
          <span className="font-mono text-[10px] text-muted">
            {model ? `${Math.round(model.width)}×${Math.round(model.height)} pt` : ""}
          </span>
        </div>
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

      <div className="bg-panel-2 p-3 sm:p-5">
        {loading || (model && imageLoading) ? (
          <div className="flex h-[52vh] flex-col items-center justify-center gap-3">
            <SpinnerIcon className="h-5 w-5 animate-spin text-muted" />
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
              rendering page
            </span>
          </div>
        ) : error ? (
          <div className="flex h-[52vh] flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-danger">{error}</p>
            <button
              type="button"
              className="btn btn-ghost h-8 text-xs"
              onClick={() => {
                setCache((prev) => {
                  const next = { ...prev };
                  delete next[page];
                  return next;
                });
              }}
            >
              Retry
            </button>
          </div>
        ) : model ? (
          <div className="flex justify-center">
            <div className="relative inline-block max-w-full">
              <img
                src={api.previewUrl(job.id, page, 130)}
                alt={`Rendered page ${page} of ${job.filename}`}
                className="block max-h-[68vh] w-auto max-w-full rounded-sm border border-line bg-white"
              />
              {shown.map((block, index) => {
                const [x0, y0, x1, y1] = block.bbox;
                const low = block.conf < 0.6;
                const isActive = pinned === block || hovered === block;
                const color = `var(${blockMeta[block.type].color})`;
                return (
                  <button
                    key={`${block.type}-${index}`}
                    type="button"
                    className="absolute z-0 rounded-[2px] border transition-[background-color,box-shadow] hover:z-10"
                    style={{
                      left: `${(x0 / model.width) * 100}%`,
                      top: `${(y0 / model.height) * 100}%`,
                      width: `${((x1 - x0) / model.width) * 100}%`,
                      height: `${((y1 - y0) / model.height) * 100}%`,
                      borderColor: color,
                      borderWidth: block.type === "table" ? 2 : 1,
                      backgroundColor: `color-mix(in srgb, ${color} ${
                        isActive ? 24 : 10
                      }%, transparent)`,
                      boxShadow: low
                        ? "0 0 0 1px var(--danger)"
                        : isActive
                          ? `0 0 0 1px ${color}`
                          : undefined,
                    }}
                    onMouseEnter={() => setHovered(block)}
                    onMouseLeave={() => setHovered(null)}
                    onFocus={() => setHovered(block)}
                    onBlur={() => setHovered(null)}
                    onClick={() =>
                      setPinned((current) => (current === block ? null : block))
                    }
                    aria-label={`${blockMeta[block.type].label} block${
                      block.text ? `: ${block.text.slice(0, 80)}` : ""
                    }`}
                    aria-pressed={pinned === block}
                  >
                    {low && (
                      <span className="absolute -right-1 -top-1 h-1.5 w-1.5 rounded-full bg-danger" />
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {model && shown.length === 0 && (
          <p className="mt-3 text-center font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
            no blocks match this filter
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-t border-line px-4 py-3">
        {BLOCK_TYPES.map((type) => {
          const meta = blockMeta[type];
          const on = visible[type];
          return (
            <button
              key={type}
              type="button"
              className={`chip py-1 text-[11px] ${on ? "" : "opacity-40"}`}
              onClick={() =>
                setVisible((prev) => ({ ...prev, [type]: !prev[type] }))
              }
              aria-pressed={on}
            >
              <span
                className="h-2 w-2 rounded-sm"
                style={{ background: `var(${meta.color})` }}
              />
              {meta.label}
              <span className="font-mono text-[10px] opacity-60">
                {counts[type]}
              </span>
            </button>
          );
        })}
        <button
          type="button"
          className={`chip ml-auto py-1 text-[11px] ${lowOnly ? "chip-on" : ""}`}
          onClick={() => setLowOnly((value) => !value)}
          aria-pressed={lowOnly}
        >
          <span
            className={`h-2 w-2 rounded-sm ${lowOnly ? "bg-accent-ink" : "bg-danger"}`}
          />
          low confidence
        </button>
      </div>

      <div className="min-h-[5.5rem] border-t border-line px-4 py-3">
        {active ? (
          <BlockDetails block={active} />
        ) : (
          <p className="font-mono text-[11px] text-muted">
            Hover or click a block to inspect its text.
          </p>
        )}
      </div>

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
