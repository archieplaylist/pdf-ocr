import { useState } from "react";
import type { Health } from "../types";
import { MoonIcon, BrandMark, SunIcon } from "./Icons";

interface Props {
  health: Health | null;
  online: boolean | null;
}

function engineStatus(health: Health | null, online: boolean | null) {
  if (online === false) {
    return { tone: "danger", text: "api offline" as const };
  }
  if (!health) {
    return { tone: "muted", text: "connecting" as const };
  }
  if (!health.tesseract) {
    return { tone: "danger", text: "tesseract missing" as const };
  }
  return { tone: "ok", text: "engine ready" as const };
}

export function SiteHeader({ health, online }: Props) {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  const status = engineStatus(health, online);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("pdfconv-theme", next ? "dark" : "light");
    } catch {
      // storage can be unavailable in private mode
    }
  }

  const dotClass =
    status.tone === "ok"
      ? "bg-ok"
      : status.tone === "danger"
        ? "bg-danger"
        : "bg-muted animate-pulse-dot";

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-paper/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-[1360px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <a href="/" className="flex items-center gap-2.5">
          <BrandMark className="h-7 w-7 text-ink" />
          <span className="font-mono text-sm font-semibold tracking-tight">
            pdfconv
          </span>
          <span className="hidden rounded border border-line px-1.5 py-px font-mono text-[10px] uppercase tracking-[0.12em] text-muted sm:inline">
            web
          </span>
        </a>

        <div className="flex items-center gap-1.5">
          <div
            className="mr-1 hidden items-center gap-2 rounded-full border border-line bg-panel px-2.5 py-1 sm:flex"
            title={
              health?.tesseract
                ? "The conversion engine is available."
                : "Tesseract OCR was not found on the server."
            }
          >
            <span className={`h-1.5 w-1.5 rounded-full ${dotClass}`} />
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
              {status.text}
            </span>
          </div>

          <button
            type="button"
            onClick={toggleTheme}
            className="btn btn-ghost h-9 w-9 p-0"
            aria-label={dark ? "Use light theme" : "Use dark theme"}
            title={dark ? "Use light theme" : "Use dark theme"}
          >
            {dark ? (
              <SunIcon className="h-4 w-4" />
            ) : (
              <MoonIcon className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>
    </header>
  );
}
