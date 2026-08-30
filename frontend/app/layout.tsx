import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Instrument_Sans } from "next/font/google";
import "./globals.css";

/**
 * Two typefaces, loaded via next/font so there is never a system-font
 * fallback flash.
 *
 * Instrument Sans carries the interface and the agent's prose: it has more
 * character than a neutral grotesk — a narrower cap height, a distinctive `g`
 * and `a` — without ever becoming a display face at 15px. IBM Plex Mono is the
 * machine voice: paths, terminal output, tool arguments, numerals. Plex is
 * deliberately warmer and more humanist than the usual JetBrains default, which
 * is the point — the two voices are meant to be told apart at a glance rather
 * than blend.
 *
 * Weights are pinned rather than variable-full so the payload stays small: the
 * system only ever uses four sans weights and two mono weights.
 */
const sans = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  weight: ["400", "500"],
  display: "swap",
});

/**
 * `app/icon.png` and `app/apple-icon.png` sit beside this file, so Next emits
 * the favicon <link> tags itself — there is deliberately no `icons` key here.
 * Both are generated from `design/asset/` with the tile's pure-black bezel
 * cropped off and the corners re-rounded into real alpha, so the mark reads on
 * a browser tab of any colour rather than as a black square.
 */
export const metadata: Metadata = {
  title: "Loom — Coding Agent",
  description: "An AI coding agent with a sandboxed shell, filesystem, and web search.",
};

export const viewport: Viewport = {
  themeColor: "#000000",
  width: "device-width",
  initialScale: 1,
  /**
   * Let the on-screen keyboard shrink the *layout* viewport rather than only
   * scrolling over it. Without this, Android Chrome's default
   * (`resizes-visual`) leaves `100dvh` at its full height while the keyboard
   * covers the bottom third of it — and the bottom third is where the composer
   * lives, so typing hid the thing you were typing into. iOS ignores the hint,
   * which is why `useKeyboardInset` exists as well.
   */
  interactiveWidget: "resizes-content",
  /** Draw into the notch; the safe-area insets below give the padding back. */
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body className="h-full overflow-hidden">{children}</body>
    </html>
  );
}
