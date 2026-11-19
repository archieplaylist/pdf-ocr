import type { Layout, OcrEngine } from "../types";
import { ArrowRightIcon, CheckIcon, SpinnerIcon } from "./Icons";

interface Props {
  formats: string[];
  onToggleFormat: (format: string) => void;
  dpi: number;
  onDpi: (dpi: number) => void;
  lang: string;
  onLang: (lang: string) => void;
  layout: Layout;
  onLayout: (layout: Layout) => void;
  engine: OcrEngine;
  onEngine: (engine: OcrEngine) => void;
  onConvert: () => void;
  starting: boolean;
  canConvert: boolean;
}

const FORMATS = [
  {
    value: "docx",
    title: "DOCX",
    tag: "auto layout",
    blurb:
      "Headings, paragraphs and real tables. Low-confidence OCR runs are highlighted.",
  },
  {
    value: "pdf",
    title: "PDF",
    tag: "rebuilt or original",
    blurb:
      "A PDF export. 'rebuilt' reflows the extracted text; 'original layout' keeps the source page.",
  },
  {
    value: "xlsx",
    title: "XLSX",
    tag: "one sheet / table",
    blurb:
      "Every detected table becomes a worksheet. Documents without tables dump their text.",
  },
];

const DPI_OPTIONS = [150, 200, 300, 400];
const LANGS = ["ind+eng", "eng", "ind"];

const ENGINES: { value: OcrEngine; title: string; blurb: string }[] = [
  {
    value: "auto",
    title: "Auto",
    blurb:
      "RapidOCR (PP-OCRv6) when installed, otherwise Tesseract. Best default for real scans.",
  },
  {
    value: "rapidocr",
    title: "RapidOCR",
    blurb:
      "PP-OCRv6 ONNX models. Strongest on phone photos, skew and low contrast; runs offline on CPU.",  },
  {
    value: "tesseract",
    title: "Tesseract",
    blurb:
      "System Tesseract 5 with word-level confidence. Fast on clean, straight scans.",
  },
];

const LAYOUTS: { value: Layout; title: string; blurb: string }[] = [
  {
    value: "auto",
    title: "Auto",
    blurb:
      "Scanned pages keep their original page image; pages with real text are rebuilt.",
  },
  {
    value: "flow",
    title: "Editable",
    blurb: "Rebuild every page as text, headings, paragraphs and tables.",
  },
  {
    value: "facsimile",
    title: "Images",
    blurb: "Insert every page as a page-sized image. Best visual fidelity.",
  },
];

export function OptionsPanel({
  formats,
  onToggleFormat,
  dpi,
  onDpi,
  lang,
  onLang,
  layout,
  onLayout,
  engine,
  onEngine,
  onConvert,
  starting,
  canConvert,
}: Props) {
  return (
    <section className="card flex flex-col p-5 sm:p-6">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <p className="label">Output</p>
          <h2 className="mt-1 text-lg font-semibold tracking-tight">
            Build a document
          </h2>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
          {formats.length} of {FORMATS.length} selected
        </span>
      </div>

      <div className="mt-5 space-y-2" role="group" aria-label="Output formats">
        {FORMATS.map((format) => {
          const on = formats.includes(format.value);
          return (
            <button
              key={format.value}
              type="button"
              role="checkbox"
              aria-checked={on}
              onClick={() => onToggleFormat(format.value)}
              className={`flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors ${
                on
                  ? "border-ink bg-paper"
                  : "border-line bg-panel hover:border-muted"
              }`}
            >
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors ${
                  on ? "border-ink bg-ink text-paper" : "border-line"
                }`}
              >
                {on && <CheckIcon className="h-3 w-3" strokeWidth={2.4} />}
              </span>
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-sm font-semibold">{format.title}</span>
                  <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
                    {format.tag}
                  </span>
                </span>
                <span className="mt-1 block text-xs leading-relaxed text-muted">
                  {format.blurb}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <div className="flex items-center justify-between gap-3">
          <span className="label" id="layout-label">
            Page layout
          </span>
          <span className="font-mono text-[10px] text-muted">DOCX + PDF</span>
        </div>
        <div
          role="radiogroup"
          aria-labelledby="layout-label"
          className="mt-2 grid grid-cols-3 gap-1 rounded-lg border border-line bg-paper p-1"
        >
          {LAYOUTS.map((option) => {
            const on = layout === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => onLayout(option.value)}
                className={`rounded-md px-1 py-1.5 text-xs transition-colors ${
                  on
                    ? "bg-ink text-paper"
                    : "text-muted hover:bg-panel-2 hover:text-ink"
                }`}
              >
                {option.title}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          {LAYOUTS.find((option) => option.value === layout)?.blurb}
        </p>
      </div>

      <div className="mt-4 border-t border-line pt-4">
        <div className="flex items-center justify-between gap-3">
          <label className="label" htmlFor="dpi-group">
            OCR resolution
          </label>
          <span className="font-mono text-[10px] text-muted">scanned pages only</span>
        </div>
        <div
          id="dpi-group"
          role="radiogroup"
          aria-label="OCR resolution in DPI"
          className="mt-2 grid grid-cols-4 gap-1 rounded-lg border border-line bg-paper p-1"
        >
          {DPI_OPTIONS.map((option) => {
            const on = dpi === option;
            return (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => onDpi(option)}
                className={`rounded-md px-1 py-1.5 font-mono text-xs transition-colors ${
                  on
                    ? "bg-ink text-paper"
                    : "text-muted hover:bg-panel-2 hover:text-ink"
                }`}
              >
                {option}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          Higher DPI reads small print better and takes longer. 300 is the default.
        </p>
      </div>

      <div className="mt-4 border-t border-line pt-4">
        <div className="flex items-center justify-between gap-3">
          <span className="label" id="engine-label">
            OCR engine
          </span>
          <span className="font-mono text-[10px] text-muted">scanned pages only</span>
        </div>
        <div
          role="radiogroup"
          aria-labelledby="engine-label"
          className="mt-2 grid grid-cols-3 gap-1 rounded-lg border border-line bg-paper p-1"
        >
          {ENGINES.map((option) => {
            const on = engine === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => onEngine(option.value)}
                className={`rounded-md px-1 py-1.5 text-xs transition-colors ${
                  on
                    ? "bg-ink text-paper"
                    : "text-muted hover:bg-panel-2 hover:text-ink"
                }`}
              >
                {option.title}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          {ENGINES.find((option) => option.value === engine)?.blurb}
        </p>
      </div>

      <div className="mt-4">
        <label className="label" htmlFor="lang">
          OCR languages
        </label>
        <div className="mt-2 flex gap-2">
          <input
            id="lang"
            className="input flex-1"
            value={lang}
            onChange={(event) => onLang(event.target.value)}
            placeholder="ind+eng"
            spellCheck={false}
            autoComplete="off"
          />
          <div className="flex items-center gap-1">
            {LANGS.map((option) => (
              <button
                key={option}
                type="button"
                className={`chip px-2 py-1 font-mono text-[10px] ${
                  lang === option ? "chip-on" : ""
                }`}
                onClick={() => onLang(option)}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          Tesseract language packs must be installed on the server. RapidOCR's
          PP-OCRv6 model covers Indonesian and English without language packs.
        </p>
      </div>

      <div className="mt-auto pt-5">
        <button
          type="button"
          className="btn btn-primary h-11 w-full text-sm"
          onClick={onConvert}
          disabled={!canConvert || starting}
        >
          {starting ? (
            <>
              <SpinnerIcon className="h-4 w-4 animate-spin" />
              Starting…
            </>
          ) : (
            <>
              Convert PDF
              <ArrowRightIcon className="h-4 w-4" />
            </>
          )}
        </button>
        {formats.length === 0 && (
          <p className="mt-2 text-center text-xs text-danger">
            Pick at least one output format.
          </p>
        )}
        <p className="mt-3 text-center font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
          runs locally · jobs expire after 60 min
        </p>
      </div>
    </section>
  );
}
