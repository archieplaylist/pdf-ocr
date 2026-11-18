import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import type { Fixture, Health, Job, Layout } from "./types";
import { JobView } from "./components/JobView";
import { SetupView } from "./components/SetupView";
import { SiteHeader } from "./components/SiteHeader";
import { AlertIcon, XIcon } from "./components/Icons";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function ErrorBanner({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div
      role="alert"
      className="mb-5 flex items-start gap-3 rounded-lg border border-danger/40 bg-panel px-4 py-3 text-sm"
    >
      <AlertIcon className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
      <p className="flex-1 leading-relaxed">{message}</p>
      <button
        type="button"
        className="btn btn-ghost -mr-1 h-6 w-6 shrink-0 p-0"
        onClick={onDismiss}
        aria-label="Dismiss error"
      >
        <XIcon className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [fixtures, setFixtures] = useState<Fixture[]>([]);
  const [fixturesLoading, setFixturesLoading] = useState(true);

  const [file, setFile] = useState<File | null>(null);
  const [fixture, setFixture] = useState<Fixture | null>(null);
  const [formats, setFormats] = useState<string[]>(["docx", "xlsx"]);
  const [dpi, setDpi] = useState(300);
  const [lang, setLang] = useState("ind+eng");
  const [layout, setLayout] = useState<Layout>("auto");

  const [job, setJob] = useState<Job | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((value) => {
        if (!cancelled) {
          setHealth(value);
          setOnline(true);
        }
      })
      .catch(() => {
        if (!cancelled) setOnline(false);
      });
    api
      .fixtures()
      .then((value) => {
        if (!cancelled) setFixtures(value);
      })
      .catch(() => {
        // fixtures are optional; the upload path still works
      })
      .finally(() => {
        if (!cancelled) setFixturesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const jobId = job?.id;
  const jobStatus = job?.status;

  useEffect(() => {
    if (!jobId || (jobStatus !== "queued" && jobStatus !== "running")) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await api.job(jobId);
        if (!cancelled) {
          setJob(next);
          setOnline(true);
          setError(null);
        }
      } catch (caught) {
        if (cancelled) return;
        if (caught instanceof ApiError && caught.status === 404) {
          setJob((current) =>
            current
              ? {
                  ...current,
                  status: "error",
                  error:
                    "This job is no longer available. The server may have restarted.",
                }
              : current,
          );
          setError("The job expired or the server was restarted.");
          return;
        }
        setError(messageOf(caught));
        setOnline(false);
      }
    };
    const timer = window.setInterval(tick, 700);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId, jobStatus]);

  useEffect(() => {
    const status = job?.status;
    document.title =
      status === "done"
        ? "Done · pdfconv"
        : status === "error"
          ? "Failed · pdfconv"
          : status === "queued" || status === "running"
            ? "Converting · pdfconv"
            : "pdfconv — PDF to DOCX / XLSX";
  }, [job?.status]);

  function handleFile(next: File) {
    const looksPdf =
      next.type === "application/pdf" ||
      next.name.toLowerCase().endsWith(".pdf");
    if (!looksPdf) {
      setError(`${next.name} is not a PDF.`);
      return;
    }
    const limit = health?.max_upload_mb ?? 50;
    if (next.size > limit * 1024 * 1024) {
      setError(`${next.name} is larger than the ${limit} MB limit.`);
      return;
    }
    setFile(next);
    setFixture(null);
    setError(null);
  }

  function handleFixture(next: Fixture) {
    setFixture(next);
    setFile(null);
    setError(null);
  }

  function clearSelection() {
    setFile(null);
    setFixture(null);
  }

  function toggleFormat(format: string) {
    setFormats((prev) =>
      prev.includes(format)
        ? prev.filter((item) => item !== format)
        : [...prev, format],
    );
  }

  async function start() {
    if (!file && !fixture) {
      setError("Choose a PDF first.");
      return;
    }
    if (formats.length === 0) {
      setError("Pick at least one output format.");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const created = await api.createJob({
        file: file ?? undefined,
        fixture: fixture?.id,
        formats: formats.join(","),
        dpi,
        lang: lang.trim() || "ind+eng",
        layout,
      });
      setJob(await api.job(created.id));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setStarting(false);
    }
  }

  async function reset() {
    const current = job;
    setJob(null);
    setFile(null);
    setFixture(null);
    setError(null);
    if (current) {
      try {
        await api.deleteJob(current.id);
      } catch {
        // the job expires on its own anyway
      }
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader health={health} online={online} />

      <main className="mx-auto w-full max-w-[1360px] flex-1 px-4 pb-16 pt-6 sm:px-6 lg:px-8">
        {error && (
          <ErrorBanner message={error} onDismiss={() => setError(null)} />
        )}

        {job ? (
          <JobView job={job} onReset={reset} />
        ) : (
          <SetupView
            fixtures={fixtures}
            fixturesLoading={fixturesLoading}
            file={file}
            fixture={fixture}
            onFile={handleFile}
            onFixture={handleFixture}
            onClear={clearSelection}
            formats={formats}
            onToggleFormat={toggleFormat}
            dpi={dpi}
            onDpi={setDpi}
            lang={lang}
            onLang={setLang}
            layout={layout}
            onLayout={setLayout}
            onConvert={start}
            starting={starting}
            canConvert={Boolean(file ?? fixture)}
            maxMb={health?.max_upload_mb ?? 50}
          />
        )}
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex w-full max-w-[1360px] flex-wrap items-center justify-between gap-2 px-4 py-5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted sm:px-6 lg:px-8">
          <span>pdfconv · pymupdf · tesseract</span>
          <span>files stay local · jobs expire after 60 min</span>
        </div>
      </footer>
    </div>
  );
}
