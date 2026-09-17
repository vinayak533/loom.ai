"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import type { FileNode } from "@/lib/events";
import { cn, shortPath } from "@/lib/cn";

/**
 * Live file tree for the session sandbox.
 *
 * Two jobs. It *reports*: when a `file_changed` event lands, `flash[path]` gets
 * a fresh timestamp and the matching row plays a one-shot colour + scale pop,
 * driven by a keyed animation so repeated edits to the same file re-fire it.
 * And it *acts*: a row can be renamed, deleted, or have a new file or folder
 * created inside it, through the right-click menu every code editor has trained
 * people to reach for — plus a hover affordance, because a context menu nobody
 * knows about is not a feature.
 *
 * The mutations themselves are the caller's: this component owns the naming UI
 * (inline inputs, not dialogs) and hands back a completed intent.
 */

export type TreeActions = {
  onRename: (path: string, name: string) => Promise<void>;
  onDelete: (path: string) => Promise<void>;
  onCreate: (parentDir: string, name: string, kind: "file" | "dir") => Promise<void>;
};

/** What the inline input at the top of a folder is currently for. */
type Draft =
  | { mode: "rename"; path: string; initial: string }
  | { mode: "create"; parent: string; kind: "file" | "dir" };

type MenuState = {
  path: string;
  isDir: boolean;
  x: number;
  y: number;
};

export function FileTree({
  nodes,
  root,
  flash,
  activePath,
  changed,
  onSelect,
  actions,
  idle = false,
}: {
  nodes: FileNode[];
  root: string;
  flash: Record<string, number>;
  activePath: string | null;
  changed: Record<string, unknown>;
  onSelect: (path: string) => void;
  /** Absent while there is no session to act on — the tree stays read-only. */
  actions?: TreeActions;
  /**
   * This session has done work before, but its sandbox is not running now.
   *
   * The distinction is the whole point of the flag. An empty tree looks the
   * same in both cases and means opposite things: on a new session there is
   * genuinely nothing yet, and on a returning one there is a whole workspace
   * that simply is not mounted this second — the sandbox is reaped when it
   * goes idle, and switching sections is enough to do it. Showing the
   * new-session copy there told the user their files were gone.
   */
  idle?: boolean;
}) {
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Folders forced open because something is being created inside them. */
  const [forceOpen, setForceOpen] = useState<Record<string, number>>({});

  const openMenu = (state: MenuState) => {
    setError(null);
    setMenu(state);
  };

  const startCreate = (parent: string, kind: "file" | "dir") => {
    setMenu(null);
    setDraft({ mode: "create", parent, kind });
    setForceOpen((f) => ({ ...f, [parent]: Date.now() }));
  };

  const startRename = (path: string) => {
    setMenu(null);
    setDraft({ mode: "rename", path, initial: path.split("/").pop() ?? "" });
  };

  const commit = async (name: string) => {
    if (!draft || !actions) return;
    const clean = name.trim();
    // An empty name, or a rename to the name it already has — both mean the
    // user changed their mind, and neither is worth a round trip. This is also
    // what makes the input's blur-to-commit safe to click away from.
    if (!clean || (draft.mode === "rename" && clean === draft.initial)) {
      setDraft(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (draft.mode === "rename") {
        await actions.onRename(draft.path, clean);
      } else {
        await actions.onCreate(draft.parent, clean, draft.kind);
      }
      setDraft(null);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (path: string) => {
    if (!actions) return;
    setMenu(null);
    setBusy(true);
    setError(null);
    try {
      await actions.onDelete(path);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const empty = nodes.length === 0;

  return (
    <div className="py-1">
      <div className="flex items-center gap-1 px-3 pb-1">
        <span className="min-w-0 flex-1 truncate font-mono text-2xs text-ink-faint">
          {root}
        </span>
        {actions && (
          <>
            <RootButton
              label="New file in the workspace root"
              onClick={() => startCreate(root, "file")}
            >
              <path d="M6 3h7l5 5v13H6z" />
              <path d="M13 3v5h5" />
              <path d="M12 12v5m-2.5-2.5h5" />
            </RootButton>
            <RootButton
              label="New folder in the workspace root"
              onClick={() => startCreate(root, "dir")}
            >
              <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h7A1.5 1.5 0 0 1 19 10v7a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 3 17z" />
              <path d="M11 11.5v5m-2.5-2.5h5" />
            </RootButton>
          </>
        )}
      </div>

      {/* A draft aimed at the root sits above the tree, where the entry it is
          about to create will appear. */}
      {draft?.mode === "create" && draft.parent === root && (
        <DraftRow
          depth={0}
          kind={draft.kind}
          busy={busy}
          onCommit={commit}
          onCancel={() => setDraft(null)}
        />
      )}

      {empty ? (
        <p className="px-3 py-4 text-xs leading-relaxed text-ink-faint">
          {idle
            ? "The sandbox is idle, so there is nothing to list right now. Your files are still there — it restarts on the next command, and the tree comes back with it."
            : "The sandbox filesystem appears here once the agent runs its first command — or as soon as you open a folder with the + button."}
        </p>
      ) : (
        <Nodes
          nodes={nodes}
          depth={0}
          flash={flash}
          activePath={activePath}
          changed={changed}
          onSelect={onSelect}
          onMenu={actions ? openMenu : undefined}
          draft={draft}
          forceOpen={forceOpen}
          busy={busy}
          onCommit={commit}
          onCancelDraft={() => setDraft(null)}
        />
      )}

      {error && (
        <p className="mx-3 mt-2 rounded-ctl border border-del/30 bg-del-bg px-2.5 py-1.5 text-2xs text-del">
          {error}
        </p>
      )}

      <AnimatePresence>
        {menu && actions && (
          <ContextMenu
            state={menu}
            onClose={() => setMenu(null)}
            onOpenFile={() => {
              onSelect(menu.path);
              setMenu(null);
            }}
            onNewFile={() => startCreate(menu.path, "file")}
            onNewFolder={() => startCreate(menu.path, "dir")}
            onRename={() => startRename(menu.path)}
            onDelete={() => void remove(menu.path)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

type NodesProps = {
  nodes: FileNode[];
  depth: number;
  flash: Record<string, number>;
  activePath: string | null;
  changed: Record<string, unknown>;
  onSelect: (path: string) => void;
  onMenu?: (state: MenuState) => void;
  draft: Draft | null;
  forceOpen: Record<string, number>;
  busy: boolean;
  onCommit: (name: string) => void;
  onCancelDraft: () => void;
};

function Nodes(props: NodesProps) {
  return (
    <ul>
      {props.nodes.map((node) => (
        <TreeRow key={node.path} node={node} {...props} />
      ))}
    </ul>
  );
}

function TreeRow({ node, ...props }: NodesProps & { node: FileNode }) {
  const {
    depth,
    flash,
    activePath,
    changed,
    onSelect,
    onMenu,
    draft,
    forceOpen,
    busy,
    onCommit,
    onCancelDraft,
  } = props;

  const [open, setOpen] = useState(depth < 1);
  const isDir = node.type === "dir";
  const flashedAt = flash[node.path];
  const isActive = activePath === node.path;
  const wasChanged = node.path in changed;
  const renaming = draft?.mode === "rename" && draft.path === node.path;
  const creatingHere =
    draft?.mode === "create" && draft.parent === node.path ? draft : null;

  // Open ancestors of a file that just changed so the flash is visible.
  useEffect(() => {
    if (isDir && Object.keys(flash).some((p) => p.startsWith(node.path + "/"))) {
      setOpen(true);
    }
  }, [flash, isDir, node.path]);

  // A folder you are creating something inside opens itself; you asked to put
  // something there, so that is where you are looking.
  const forced = forceOpen[node.path];
  useEffect(() => {
    if (forced) setOpen(true);
  }, [forced]);

  if (renaming) {
    return (
      <li>
        <DraftRow
          depth={depth}
          kind={isDir ? "dir" : "file"}
          initial={draft.initial}
          busy={busy}
          onCommit={onCommit}
          onCancel={onCancelDraft}
        />
      </li>
    );
  }

  return (
    <li>
      <div className="group/row relative">
        <motion.button
          type="button"
          // Re-keying on the flash timestamp restarts the animation on every edit.
          key={flashedAt ? `${node.path}-${flashedAt}` : node.path}
          initial={flashedAt ? { backgroundColor: "rgba(109,139,255,0.28)", scale: 1.015 } : false}
          animate={{ backgroundColor: "rgba(109,139,255,0)", scale: 1 }}
          transition={{ duration: 1.1, ease: "easeOut" }}
          onClick={() => (isDir ? setOpen((o) => !o) : onSelect(node.path))}
          onContextMenu={
            onMenu
              ? (e) => {
                  e.preventDefault();
                  onMenu({ path: node.path, isDir, x: e.clientX, y: e.clientY });
                }
              : undefined
          }
          style={{ paddingLeft: 12 + depth * 12 }}
          className={cn(
            "flex w-full items-center gap-1.5 py-[3px] pr-3 text-left text-xs",
            "transition-colors hover:bg-elevated",
            isActive && "bg-accent-dim",
          )}
        >
          {isDir ? (
            <motion.span
              animate={{ rotate: open ? 90 : 0 }}
              transition={{ duration: 0.15 }}
              className="w-2.5 shrink-0 text-ink-faint"
            >
              ›
            </motion.span>
          ) : (
            <span className="w-2.5 shrink-0" />
          )}
          <span
            className={cn(
              "truncate font-mono",
              isDir ? "text-ink-muted" : isActive ? "text-accent" : "text-ink/75",
              wasChanged && !isActive && "text-ink",
            )}
          >
            {node.name}
          </span>
          {wasChanged && !isDir && (
            <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
          )}
        </motion.button>

        {/* The discoverable half of the context menu. Right-click is the
            convention; this is how someone finds out the convention applies. */}
        {onMenu && (
          <button
            type="button"
            aria-label={`Actions for ${node.name}`}
            onClick={(e) => {
              e.stopPropagation();
              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
              onMenu({ path: node.path, isDir, x: rect.right, y: rect.bottom });
            }}
            // `group-hover` never fires without a pointer, so on a phone this
            // control simply did not exist and a file could not be renamed or
            // deleted at all. On a coarse pointer it is always there.
            className="absolute right-1 top-1/2 hidden h-5 w-5 -translate-y-1/2 place-items-center
                       rounded-md text-ink-faint opacity-0 transition-opacity duration-150
                       hover:bg-raised hover:text-ink focus-visible:opacity-100
                       group-hover/row:grid group-hover/row:opacity-100
                       touch:grid touch:h-9 touch:w-9 touch:opacity-100"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <circle cx="5" cy="12" r="1.6" />
              <circle cx="12" cy="12" r="1.6" />
              <circle cx="19" cy="12" r="1.6" />
            </svg>
          </button>
        )}
      </div>

      <AnimatePresence initial={false}>
        {isDir && open && (creatingHere || (node.children && node.children.length > 0)) && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ type: "spring", stiffness: 420, damping: 38 }}
            className="overflow-hidden"
          >
            {creatingHere && (
              <DraftRow
                depth={depth + 1}
                kind={creatingHere.kind}
                busy={busy}
                onCommit={onCommit}
                onCancel={onCancelDraft}
              />
            )}
            <Nodes {...props} nodes={node.children ?? []} depth={depth + 1} />
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

/**
 * The inline naming input, used for both renaming an entry and creating one.
 *
 * Inline rather than a dialog because that is what every file tree does, and
 * because the row it replaces is the answer to "which thing am I naming".
 */
function DraftRow({
  depth,
  kind,
  initial,
  busy,
  onCommit,
  onCancel,
}: {
  depth: number;
  kind: "file" | "dir";
  initial?: string;
  busy: boolean;
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial ?? "");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
    // Select the stem, not the extension — renaming `App.tsx` is almost always
    // about the `App`.
    const dot = (initial ?? "").lastIndexOf(".");
    if (initial && dot > 0) input.current?.setSelectionRange(0, dot);
    else input.current?.select();
  }, [initial]);

  return (
    <div
      className="flex items-center gap-1.5 py-[3px] pr-3"
      style={{ paddingLeft: 12 + depth * 12 }}
    >
      <span className="w-2.5 shrink-0 text-center text-2xs text-ink-faint" aria-hidden>
        {kind === "dir" ? "▸" : "·"}
      </span>
      <input
        ref={input}
        value={value}
        disabled={busy}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onCommit(value);
          } else if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            onCancel();
          }
        }}
        onBlur={() => onCommit(value)}
        placeholder={kind === "dir" ? "folder name" : "file name"}
        spellCheck={false}
        className="min-w-0 flex-1 rounded-[5px] border border-accent-line bg-inset px-1.5 py-0.5
                   font-mono text-xs text-ink outline-none placeholder:text-ink-faint
                   disabled:opacity-60"
      />
    </div>
  );
}

/**
 * The context menu. Rendered `fixed` at the pointer, so a menu opened on the
 * last row of a scrolling panel is not clipped by it, and flipped up when it
 * would otherwise run off the bottom of the window.
 */
function ContextMenu({
  state,
  onClose,
  onOpenFile,
  onNewFile,
  onNewFolder,
  onRename,
  onDelete,
}: {
  state: MenuState;
  onClose: () => void;
  onOpenFile: () => void;
  onNewFile: () => void;
  onNewFolder: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const rows = 1 + (state.isDir ? 2 : 0) + 1;
  const height = rows * 32 + 46;
  const top =
    typeof window !== "undefined" && state.y + height > window.innerHeight
      ? Math.max(8, state.y - height)
      : state.y;
  const left =
    typeof window !== "undefined"
      ? Math.min(state.x, window.innerWidth - 190)
      : state.x;

  return (
    <>
      <div className="fixed inset-0 z-40" onMouseDown={onClose} onContextMenu={(e) => {
        e.preventDefault();
        onClose();
      }} />
      <motion.div
        role="menu"
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.98 }}
        transition={{ duration: 0.12, ease: [0.2, 0, 0, 1] }}
        style={{ top, left }}
        className="glass fixed z-50 w-[11.5rem] origin-top-left overflow-hidden rounded-panel p-1 shadow-lift"
      >
        <p className="truncate px-2 pb-1 pt-1 font-mono text-[10px] text-ink-faint">
          {state.path.split("/").pop()}
        </p>

        {!state.isDir && <MenuRow label="Open" onClick={onOpenFile} />}
        {state.isDir && <MenuRow label="New file…" onClick={onNewFile} />}
        {state.isDir && <MenuRow label="New folder…" onClick={onNewFolder} />}
        <MenuRow label="Rename…" onClick={onRename} />

        <div className="my-1 h-px bg-line" />

        {confirming ? (
          <MenuRow label="Delete — are you sure?" tone="danger" onClick={onDelete} />
        ) : (
          <MenuRow label="Delete" tone="danger" onClick={() => setConfirming(true)} />
        )}
      </motion.div>
    </>
  );
}

function MenuRow({
  label,
  tone,
  onClick,
}: {
  label: string;
  tone?: "danger";
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={cn(
        "flex w-full items-center rounded-ctl px-2 py-1.5 text-left font-sans text-xs",
        "transition-colors duration-150",
        tone === "danger"
          ? "text-del hover:bg-del-bg"
          : "text-ink-muted hover:bg-elevated hover:text-ink",
      )}
    >
      {label}
    </button>
  );
}

function RootButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-tip={label}
      aria-label={label}
      className="grid h-5 w-5 touch:h-9 touch:w-9 shrink-0 place-items-center rounded-md text-ink-faint
                 transition-colors duration-150 hover:bg-elevated hover:text-ink"
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        {children}
      </svg>
    </button>
  );
}

function message(err: unknown): string {
  if (!(err instanceof Error)) return "That did not work.";
  // Strip the `404 Not Found — ` prefix `json()` puts on HTTP failures; the
  // detail behind it is the sentence worth showing.
  return err.message.replace(/^\d+\s+[\w ]+\s+—\s+/, "");
}

export function ChangedFilesList({
  changed,
  activePath,
  onSelect,
  root,
}: {
  changed: Record<string, { change: string; at: number }>;
  activePath: string | null;
  onSelect: (path: string) => void;
  root: string;
}) {
  const paths = Object.keys(changed).sort((a, b) => changed[b].at - changed[a].at);
  if (!paths.length) return null;

  return (
    <div className="border-t border-line pt-2">
      <div className="px-3 pb-1 text-2xs uppercase tracking-wider text-ink-faint">
        Changed
      </div>
      <ul>
        <AnimatePresence initial={false}>
          {paths.map((p) => (
            <motion.li
              key={p}
              layout
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={{ type: "spring", stiffness: 400, damping: 34 }}
            >
              <button
                type="button"
                onClick={() => onSelect(p)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-[3px] text-left text-xs hover:bg-elevated",
                  activePath === p && "bg-accent-dim",
                )}
              >
                <span
                  className={cn(
                    "font-mono text-2xs",
                    changed[p].change === "created" ? "text-add" : "text-warn",
                  )}
                >
                  {changed[p].change === "created" ? "A" : "M"}
                </span>
                <span className="truncate font-mono text-ink/75">
                  {shortPath(p, root)}
                </span>
              </button>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}
