import { useRef, useState } from "react";
import type { DragEvent, ChangeEvent, KeyboardEvent } from "react";
import type { Fixture } from "../types";
import { formatFileSize } from "../format";
import { FileIcon, ScanIcon, UploadIcon, XIcon } from "./Icons";

interface Props {
  file: File | null;
  fixture: Fixture | null;
  onFile: (file: File) => void;
  onClear: () => void;
  maxMb?: number;
}

export function Dropzone({ file, fixture, onFile, onClear, maxMb = 50 }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  const selected = file ?? fixture;

  function openPicker() {
    inputRef.current?.click();
  }

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const picked = event.target.files?.[0];
    if (picked) onFile(picked);
    event.target.value = "";
  }

  function handleKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openPicker();
    }
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    depth.current += 1;
    setOver(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setOver(false);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    depth.current = 0;
    setOver(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) onFile(dropped);
  }

  return (
    <div>
      <div
        role={selected ? undefined : "button"}
        tabIndex={selected ? -1 : 0}
        aria-label={selected ? undefined : "Choose a PDF to convert"}
        onClick={selected ? undefined : openPicker}
        onKeyDown={selected ? undefined : handleKey}
        onDragEnter={handleDragEnter}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`relative flex min-h-[21rem] flex-col items-center justify-center overflow-hidden rounded-xl border-2 border-dashed p-7 text-center outline-none transition-colors ${
          selected ? "" : "cursor-pointer"
        } ${
          over
            ? "border-accent bg-accent-soft"
            : selected
              ? "border-line bg-panel"
              : "border-line bg-panel hover:border-muted"
        }`}
      >
        <span className="pointer-events-none absolute left-3 top-3 h-3 w-3 border-l border-t border-line" />
        <span className="pointer-events-none absolute right-3 top-3 h-3 w-3 border-r border-t border-line" />
        <span className="pointer-events-none absolute bottom-3 left-3 h-3 w-3 border-b border-l border-line" />
        <span className="pointer-events-none absolute bottom-3 right-3 h-3 w-3 border-b border-r border-line" />

        {selected ? (
          <div className="flex w-full max-w-md flex-col items-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-line bg-paper">
              {file ? (
                <FileIcon className="h-6 w-6 text-accent" />
              ) : (
                <ScanIcon className="h-6 w-6 text-accent" />
              )}
            </div>
            <p className="mt-4 w-full truncate text-sm font-semibold">
              {file ? file.name : fixture?.name}
            </p>
            <p className="mt-1 font-mono text-[11px] text-muted">
              {file
                ? `${formatFileSize(file.size)} · PDF`
                : `bundled fixture · ${fixture?.pages} page${fixture?.pages === 1 ? "" : "s"}`}
            </p>
            <div className="mt-5 flex items-center gap-2">
              <button
                type="button"
                className="btn btn-ghost h-8 px-3 text-xs"
                onClick={openPicker}
              >
                <UploadIcon className="h-3.5 w-3.5" />
                Replace
              </button>
              <button
                type="button"
                className="btn btn-ghost h-8 px-3 text-xs"
                onClick={onClear}
              >
                <XIcon className="h-3.5 w-3.5" />
                Remove
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-line bg-paper">
              <UploadIcon className="h-6 w-6 text-accent" />
            </div>
            <p className="mt-4 text-base font-semibold">
              {over ? "Drop it" : "Drop a PDF here"}
            </p>
            <p className="mt-1 text-sm text-muted">
              or <span className="text-ink underline underline-offset-2">browse</span>{" "}
              · up to {maxMb} MB
            </p>
            <p className="mt-6 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
              <span className="h-1 w-1 rounded-full bg-ok" />
              native pages skip OCR · scans use tesseract
            </p>
          </>
        )}

        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={handleChange}
          tabIndex={-1}
        />
      </div>
    </div>
  );
}
