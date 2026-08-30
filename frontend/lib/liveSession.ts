/**
 * Where the *currently open* conversation is remembered.
 *
 * This is deliberately not `localStorage`. The pointer to the live session was
 * kept there, which meant it outlived the browser session: close the browser,
 * come back tomorrow, and the app reopened straight into whatever you were
 * last saying instead of a clean composer. Nobody asked for that — the
 * conversation was never lost, it is in the session shelf either way — and it
 * is the opposite of what every comparable product does on a fresh visit.
 *
 * The distinction the app actually needs is between two things `localStorage`
 * cannot tell apart:
 *
 *   · **a refresh mid-conversation**, which must keep you exactly where you
 *     were — an accidental F5 during a long run should not throw the thread
 *     away;
 *   · **a fresh visit**, which should land on a new, empty conversation.
 *
 * `sessionStorage` draws precisely that line: it survives reloads within a tab
 * and is discarded when the browsing session ends. So the live pointer lives
 * here, and everything durable — the sessions themselves, their titles, pins
 * and archives — stays on the server where it belongs and remains reachable
 * from the history shelf.
 *
 * Every call is guarded. `sessionStorage` throws on access in a sandboxed
 * iframe and in Safari's private mode, and losing the pointer is a far smaller
 * problem than failing to boot.
 */

const memory = new Map<string, string>();

function store(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function readLive(key: string): string | null {
  const s = store();
  if (!s) return memory.get(key) ?? null;
  try {
    return s.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}

export function writeLive(key: string, value: string): void {
  memory.set(key, value);
  try {
    store()?.setItem(key, value);
  } catch {
    /* quota or a blocked store; the in-memory copy still covers this tab */
  }
}

export function clearLive(key: string): void {
  memory.delete(key);
  try {
    store()?.removeItem(key);
  } catch {
    /* nothing to do — the value is already unreachable */
  }
}

/**
 * Drop keys the previous, `localStorage`-backed scheme left behind.
 *
 * Without this every existing browser keeps a stale pointer to a conversation
 * it will now never open, and the bug's own artefacts sit in storage forever.
 * Safe to call on every boot: removing an absent key is a no-op.
 */
export function forgetPersistedLive(...keys: string[]): void {
  if (typeof window === "undefined") return;
  try {
    for (const key of keys) window.localStorage.removeItem(key);
  } catch {
    /* a blocked localStorage means there is nothing stale in it either */
  }
}
