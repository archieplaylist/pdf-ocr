import type { Fixture, Layout, OcrEngine } from "../types";
import { fixtureLabel } from "../format";
import { Dropzone } from "./Dropzone";
import { OptionsPanel } from "./OptionsPanel";

interface Props {
  fixtures: Fixture[];
  fixturesLoading: boolean;
  file: File | null;
  fixture: Fixture | null;
  onFile: (file: File) => void;
  onFixture: (fixture: Fixture) => void;
  onClear: () => void;
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
  maxMb?: number;
}

const SPEC = [
  ["native pages", "text layer"],
  ["scanned pages", "ocr · 300 dpi"],
  ["ruled tables", "cell by cell"],
  ["output", "docx · xlsx"],
];

const STEPS = [
  {
    number: "01",
    title: "Read",
    body: "Native text is copied exactly. Scans are deskewed, binarised and OCR'd.",
  },
  {
    number: "02",
    title: "Structure",
    body: "Columns, headings, paragraphs and ruled tables are rebuilt in reading order.",
  },
  {
    number: "03",
    title: "Export",
    body: "Grab a DOCX, an XLSX per table, or the raw document model as JSON.",
  },
];

export function SetupView(props: Props) {
  const { fixtures, fixturesLoading, fixture, onFixture } = props;

  return (
    <div className="animate-rise">
      <div className="mb-7 flex flex-col gap-6 border-b border-line pb-7 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="label flex items-center gap-2">
            <span className="h-1.5 w-1.5 bg-accent" />
            Local PDF pipeline
          </p>
          <h1 className="mt-3 max-w-2xl text-4xl font-semibold leading-[1.03] tracking-tight sm:text-5xl">
            PDF in.
            <br className="sm:hidden" /> <span className="text-accent">Structure</span>{" "}
            out.
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-relaxed text-muted">
            Native text is lifted straight from the page, scans are deskewed and
            OCR&rsquo;d, and ruled tables are read cell by cell. Everything becomes
            an editable DOCX or a clean XLSX.
          </p>
        </div>

        <dl className="hidden w-64 shrink-0 space-y-2 border-l border-line pl-5 lg:block">
          {SPEC.map(([term, value]) => (
            <div key={term} className="flex items-baseline justify-between gap-3">
              <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
                {term}
              </dt>
              <dd className="font-mono text-[11px] text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <Dropzone
            file={props.file}
            fixture={props.fixture}
            onFile={props.onFile}
            onClear={props.onClear}
            maxMb={props.maxMb}
          />

          <div className="mt-4">
            <div className="flex items-center justify-between gap-3">
              <p className="label">No file handy? Try a fixture</p>
              <span className="font-mono text-[10px] text-muted">
                {fixturesLoading ? "loading…" : `${fixtures.length} bundled`}
              </span>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {fixturesLoading &&
                [0, 1, 2, 3, 4].map((index) => (
                  <span
                    key={index}
                    className="h-7 w-28 animate-pulse rounded-full bg-panel-2"
                  />
                ))}
              {fixtures.map((item) => {
                const on = fixture?.id === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onFixture(item)}
                    className={`chip ${on ? "chip-on" : ""}`}
                    title={item.name}
                    aria-pressed={on}
                  >
                    <span>{fixtureLabel(item.id)}</span>
                    <span className="ml-0.5 font-mono text-[10px] opacity-60">
                      {item.pages}p
                    </span>
                  </button>
                );
              })}
            </div>
            <p className="mt-3 text-xs leading-relaxed text-muted">
              Fixtures ship with the project: a native two-column page, two
              invoices, a clean scan, a phone photo and a mixed PDF.
            </p>
          </div>

          <div className="mt-5 grid gap-2 sm:grid-cols-3">
            {STEPS.map((step) => (
              <div
                key={step.number}
                className="rounded-lg border border-line bg-panel p-3"
              >
                <p className="font-mono text-[10px] tracking-[0.12em] text-accent">
                  {step.number}
                </p>
                <p className="mt-1 text-xs font-semibold">{step.title}</p>
                <p className="mt-1 text-[11px] leading-relaxed text-muted">
                  {step.body}
                </p>
              </div>
            ))}
          </div>
        </div>

        <OptionsPanel
          formats={props.formats}
          onToggleFormat={props.onToggleFormat}
          dpi={props.dpi}
          onDpi={props.onDpi}
          lang={props.lang}
          onLang={props.onLang}
          layout={props.layout}
          onLayout={props.onLayout}
          engine={props.engine}
          onEngine={props.onEngine}
          onConvert={props.onConvert}
          starting={props.starting}
          canConvert={props.canConvert}
        />
      </div>
    </div>
  );
}
