/**
 * The four product surfaces. One design system, four identities — accent,
 * icon grammar, and layout emphasis differ; everything else is shared.
 *
 * Auto-mode routing lives on the backend (`AUTO_ROUTE_*` in backend/.env,
 * surfaced through `/api/config` as `auto_routes`). The map here is only a
 * fallback for when config hasn't loaded yet, so the pill never renders empty.
 */

export type Section = "chat" | "learning" | "code" | "agents";

export const SECTIONS: Section[] = ["chat", "learning", "code", "agents"];

export const FALLBACK_ROUTES: Record<Section, string> = {
  chat: "grok-4-5",
  learning: "nemotron-3",
  code: "llama-4-scout",
  // Agents has no AUTO_ROUTE_* key of its own on the backend, so Auto resolves
  // it through the chat table. The specialists span every kind of work, and a
  // single section-wide default would be wrong for most of them — per-turn
  // task routing is the right answer here, which is what Auto already does
  // whenever OPENCODE_API_KEY is set.
  agents: "grok-4-5",
};

type SectionMeta = {
  name: string;
  greetingSub: string;
  placeholder: string;
  /** Sends this hint to the backend so Auto can resolve without a round trip. */
  routeKey: Section;
  suggestions: string[];
};

export const SECTION_META: Record<Section, SectionMeta> = {
  chat: {
    name: "Chat",
    greetingSub: "Ask anything. I'll keep it conversational and get to the point.",
    placeholder: "Message Loom…",
    routeKey: "chat",
    suggestions: [
      "Explain what this project does",
      "Summarise a long document",
      "Help me plan my week",
    ],
  },
  learning: {
    // "Learn" in the UI; the key stays `learning` because it is also the
    // backend's AUTO_ROUTE_LEARNING key and the `[data-section]` accent hook.
    name: "Learn",
    greetingSub:
      "Drop in a PDF or a YouTube link and we'll build it up step by step.",
    placeholder: "What do you want to understand? Paste a YouTube link…",
    routeKey: "learning",
    suggestions: [
      "Explain the CAP theorem",
      "Quiz me on Rust ownership",
      "Build a 4-week syllabus",
    ],
  },
  code: {
    name: "Code",
    greetingSub:
      "Paste a stack trace, a diff, or a half-formed idea. I have a real sandbox.",
    placeholder: "Describe the change, or paste code…",
    routeKey: "code",
    suggestions: [
      "Scaffold a FastAPI app with a health endpoint and a test",
      "Explain this regex",
      "Write a test for it",
    ],
  },
  agents: {
    name: "Agents",
    greetingSub:
      "Ten specialists, each with its own tools. Pick the one whose job this is.",
    // Never actually rendered: the section opens on the gallery, and once an
    // agent is chosen the composer takes that agent's own placeholder. It is
    // here because the meta type requires it and a missing key would be worse
    // than an unused one.
    placeholder: "Pick a specialist to begin…",
    routeKey: "agents",
    suggestions: [],
  },
};

export function greeting(now = new Date()): string {
  const h = now.getHours();
  if (h < 5) return "Still up";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

/** youtu.be/ID · watch?v=ID · /shorts/ID · /embed/ID — mirrors backend sources.py */
export const YOUTUBE_RE =
  /https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:[^\s]*&)?v=|shorts\/|embed\/|live\/)|youtu\.be\/)([A-Za-z0-9_-]{11})(?:[^\s]*)?/;

export function extractVideoId(text: string): string | null {
  return text.match(YOUTUBE_RE)?.[1] ?? null;
}
