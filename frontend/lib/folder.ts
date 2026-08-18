"use client";

/**
 * Opening a folder from the user's own machine.
 *
 * The browser will not hand out a directory without the person choosing it in
 * the OS picker, and that is the whole security model here — there is no path
 * where this reads a folder the user did not select. Two ways in:
 *
 *   · `showDirectoryPicker()` (Chromium, and Safari 18+) gives a handle we walk
 *     ourselves, so a directory can be skipped *before* its contents are ever
 *     read — which is what keeps a 900 MB `node_modules` from being touched at
 *     all rather than read and then discarded;
 *   · `<input type="file" webkitdirectory>` everywhere else. The browser has
 *     already enumerated the whole tree by the time we see it, so exclusions
 *     there are a filter rather than a pruning — same result, more work done.
 *
 * Either way what comes back is a flat list of `{ path, file }` with paths
 * relative to the chosen folder, which is exactly what the sandbox import
 * endpoint wants.
 */

/**
 * Directories never uploaded. Mirrors `IMPORT_EXCLUDE_DIRS` in
 * `backend/app/tools/workspace.py`, which enforces the same list server-side —
 * this copy exists so the pruning happens before the bytes are read, not after.
 */
export const EXCLUDED_DIRS = new Set([
  "node_modules",
  ".git",
  ".next",
  ".nuxt",
  ".svelte-kit",
  ".turbo",
  ".cache",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".venv",
  "venv",
  "env",
  "dist",
  "build",
  "target",
  ".gradle",
  ".idea",
  ".vscode-server",
]);

/** Matches `MAX_IMPORT_FILE_BYTES` server-side. Larger files are skipped. */
export const MAX_FILE_BYTES = 2_000_000;

/** Matches `MAX_IMPORT_FILES`. A tree past this is not a project. */
export const MAX_FILES = 4000;

export type PickedFile = { path: string; file: File };

export type FolderPick = {
  /** The chosen folder's own name, for the "opened X" line. */
  name: string;
  files: PickedFile[];
  /** Directory names pruned, with how many times each was hit. */
  skippedDirs: Record<string, number>;
  /** Files skipped for being over the per-file ceiling. */
  skippedLarge: string[];
  /** True when the walk stopped at `MAX_FILES`. */
  truncated: boolean;
  bytes: number;
};

const emptyPick = (name: string): FolderPick => ({
  name,
  files: [],
  skippedDirs: {},
  skippedLarge: [],
  truncated: false,
  bytes: 0,
});

/** Whether the native directory picker is available in this browser. */
export function directoryPickerSupported(): boolean {
  return typeof window !== "undefined" && "showDirectoryPicker" in window;
}

/**
 * Open the native folder picker and walk what the user chose.
 *
 * Returns null when the picker is dismissed — a cancelled dialog is not an
 * error and must not raise one into the UI.
 */
export async function pickDirectory(
  onProgress?: (found: number) => void,
): Promise<FolderPick | null> {
  const picker = (window as unknown as {
    showDirectoryPicker?: (opts?: { mode?: string }) => Promise<FileSystemDirectoryHandle>;
  }).showDirectoryPicker;
  if (!picker) return null;

  let handle: FileSystemDirectoryHandle;
  try {
    handle = await picker({ mode: "read" });
  } catch {
    return null; // dismissed, or permission refused
  }

  const pick = emptyPick(handle.name);
  await walk(handle, "", pick, onProgress);
  return pick;
}

async function walk(
  dir: FileSystemDirectoryHandle,
  prefix: string,
  pick: FolderPick,
  onProgress?: (found: number) => void,
): Promise<void> {
  if (pick.truncated) return;

  // `values()` is an async iterator on the handle; it is not in the DOM lib
  // types every toolchain ships, hence the cast rather than a global d.ts.
  const entries = (dir as unknown as {
    values: () => AsyncIterable<FileSystemHandle>;
  }).values();

  for await (const entry of entries) {
    if (pick.files.length >= MAX_FILES) {
      pick.truncated = true;
      return;
    }

    if (entry.kind === "directory") {
      if (EXCLUDED_DIRS.has(entry.name)) {
        pick.skippedDirs[entry.name] = (pick.skippedDirs[entry.name] ?? 0) + 1;
        continue;
      }
      await walk(
        entry as FileSystemDirectoryHandle,
        prefix ? `${prefix}/${entry.name}` : entry.name,
        pick,
        onProgress,
      );
      continue;
    }

    let file: File;
    try {
      file = await (entry as FileSystemFileHandle).getFile();
    } catch {
      continue; // a file that vanished or cannot be read is not fatal
    }

    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (file.size > MAX_FILE_BYTES) {
      pick.skippedLarge.push(path);
      continue;
    }
    pick.files.push({ path, file });
    pick.bytes += file.size;
    onProgress?.(pick.files.length);
  }
}

/**
 * The fallback path: a `webkitdirectory` input has already produced the whole
 * `FileList`, including everything we would have pruned, so filtering is all
 * that is left to do.
 */
export function pickFromInput(list: FileList): FolderPick {
  const first = list[0] as (File & { webkitRelativePath?: string }) | undefined;
  const rootName = first?.webkitRelativePath?.split("/")[0] ?? "folder";
  const pick = emptyPick(rootName);

  for (const raw of Array.from(list)) {
    if (pick.files.length >= MAX_FILES) {
      pick.truncated = true;
      break;
    }
    const file = raw as File & { webkitRelativePath?: string };
    const full = file.webkitRelativePath || file.name;
    // Drop the chosen folder's own name: the import lands its *contents* in the
    // workspace root, the same as unzipping into a project directory.
    const path = full.split("/").slice(1).join("/") || file.name;
    const segments = path.split("/");

    const excluded = segments.slice(0, -1).find((s) => EXCLUDED_DIRS.has(s));
    if (excluded) {
      pick.skippedDirs[excluded] = (pick.skippedDirs[excluded] ?? 0) + 1;
      continue;
    }
    if (file.size > MAX_FILE_BYTES) {
      pick.skippedLarge.push(path);
      continue;
    }
    pick.files.push({ path, file });
    pick.bytes += file.size;
  }

  return pick;
}

/**
 * Split a pick into upload batches.
 *
 * Batching is what makes progress real rather than a spinner: each batch is one
 * request that either lands or does not, so the bar moves on completed work.
 * The caps are per-request comfort, well inside the server's own ceilings.
 */
export function batchFiles(
  files: PickedFile[],
  maxFiles = 40,
  maxBytes = 6_000_000,
): PickedFile[][] {
  const batches: PickedFile[][] = [];
  let current: PickedFile[] = [];
  let bytes = 0;

  for (const entry of files) {
    if (current.length && (current.length >= maxFiles || bytes + entry.file.size > maxBytes)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(entry);
    bytes += entry.file.size;
  }
  if (current.length) batches.push(current);
  return batches;
}

/** "node_modules, .git and 2 more" — the skip line under the progress bar. */
export function describeSkips(pick: FolderPick): string | null {
  const names = Object.keys(pick.skippedDirs);
  const parts: string[] = [];
  if (names.length) {
    const shown = names.slice(0, 3).join(", ");
    parts.push(
      names.length > 3 ? `${shown} and ${names.length - 3} more` : shown,
    );
  }
  if (pick.skippedLarge.length) {
    parts.push(
      `${pick.skippedLarge.length} file${pick.skippedLarge.length === 1 ? "" : "s"} over 2 MB`,
    );
  }
  if (!parts.length) return null;
  return `Skipped ${parts.join(" · ")}`;
}
