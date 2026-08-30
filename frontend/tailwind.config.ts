import type { Config } from "tailwindcss";

/**
 * Visual identity, in one place.
 *
 * Dark only, and neutral: the base is true black, and the ramp below layers
 * plain white tints on top of it, so no surface picks up a colour cast.
 * Surfaces stay translucent by default — a panel is a tint plus a blur, not an
 * opaque rectangle butted against its neighbour, so depth reads as depth
 * without the floor having to be lit.
 *
 * Three accent registers, deliberately unequal:
 *   accent      — the section's dominant colour, used everywhere
 *   accent.alt  — its partner, for gradients and the second half of a pair
 *   accent.rare — reserved. It appears on exactly two things: the auto-router's
 *                 indicator and the notices it emits. Nothing else may use it,
 *                 which is what makes it read as meaningful rather than
 *                 decorative.
 * green/red stay reserved for diffs and stderr so the accent never competes
 * with them semantically.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // A media query, not a width. Whether a control needs a finger-sized hit
      // area is a question about the *pointer*, not about how wide the window
      // is: a 1024px tablet is touch and a 380px desktop window is not. Sizing
      // by breakpoint gets both of those backwards, which is why the composer
      // could be 34px on a phone and the rail 40px behind a mouse.
      screens: {
        touch: { raw: "(pointer: coarse)" },
      },
      colors: {
        // Surface ramp. `base` is the floor everything is painted on — pure
        // black, the way ChatGPT's dark theme is: one hex value, no hue, no
        // ramp. Everything above it is a plain white tint, which keeps the
        // whole ramp hueless as it climbs.
        //
        // The tints are what keep the app legible on a floor with nowhere left
        // to go: a panel can no longer be told from the page by being lighter
        // *than a near-black*, so each step is pitched to land on a real value
        // — surface ≈ #0D0D0D, elevated ≈ #121212, raised ≈ #1A1A1A — which is
        // the "surfaces stay lifted above pure black" half of the black theme.
        base: "#000000",
        deep: "#000000", // nothing sits below the floor any more
        surface: "rgba(255,255,255,0.051)",
        elevated: "rgba(255,255,255,0.071)",
        raised: "rgba(255,255,255,0.102)",
        overlay: "#1C1C1C",
        // Sunken wells — code blocks, inputs, the terminal bed. On a black
        // floor an inset works by *removing* a surface's tint rather than by
        // darkening the page, so it has to bite harder than it used to.
        inset: "rgba(0,0,0,0.45)",
        // Opaque equivalents, for the few places a blur is too expensive or a
        // child needs to occlude what is behind it. These are the tints above
        // resolved against `base`, so swapping between them is invisible.
        "surface-solid": "#0D0D0D",
        "elevated-solid": "#121212",
        "raised-solid": "#1A1A1A",
        // Hairlines carry more of the layout now that the floor is black and
        // the panels above it are only a few percent lifted, so both steps are
        // a little stronger than they were on the old near-black.
        line: "rgba(255,255,255,0.08)",
        "line-strong": "rgba(255,255,255,0.145)",
        // The third step, for a field that has focus. Hairlines now carry
        // rest -> hover -> focus as one neutral progression (0.08 / 0.145 /
        // 0.22). Focus used to jump out of this ramp and into the section
        // accent, which on Code meant a saturated green box around the
        // composer — the loudest thing on a screen whose whole premise is that
        // nothing shouts. Confirmation should be a shade, not an alarm.
        "line-focus": "rgba(255,255,255,0.22)",
        ink: {
          // Softened a touch from the old near-black ramp: at 18:1 on pure
          // black, near-white body text glares. This still clears every step
          // of WCAG AAA and reads calmer against the floor.
          DEFAULT: "#E9EAEE", // 17.5:1 on base
          muted: "#A8ADB8", //  9.3:1
          faint: "#7B808C", //  5.3:1
          dim: "#4C505A", //  non-text only
        },
        // `accent` resolves per section from --acc, set by data-section.
        accent: {
          DEFAULT: "rgb(var(--acc) / <alpha-value>)",
          alt: "rgb(var(--acc-2) / <alpha-value>)",
          rare: "rgb(var(--acc-rare) / <alpha-value>)",
          ink: "rgb(var(--acc-ink) / <alpha-value>)",
          soft: "rgb(var(--acc) / 0.10)",
          line: "rgb(var(--acc) / 0.26)",
          ring: "rgb(var(--acc) / 0.42)",
          glow: "rgb(var(--acc) / 0.30)",
          // Kept so pre-existing call sites keep resolving; both follow --acc.
          dim: "rgb(var(--acc) / 0.13)",
          hover: "rgb(var(--acc-2) / <alpha-value>)",
        },
        "rare-soft": "rgb(var(--acc-rare) / 0.12)",
        "rare-line": "rgb(var(--acc-rare) / 0.30)",
        brand: "#8AA9FF",
        add: "#5BE0A0",
        "add-bg": "rgba(91,224,160,0.12)",
        del: "#F0736E",
        "del-bg": "rgba(240,115,110,0.11)",
        warn: "#F0B54A",
      },
      borderRadius: {
        card: "var(--r-card)",
        ctl: "var(--r-ctl)",
        bubble: "var(--r-bubble)",
        panel: "18px",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        // The typographic voices. Each one is a size *and* a leading *and* a
        // tracking, because that triple is what actually distinguishes them.
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
        /** Agent prose: the widest measure and the most air in the app. */
        prose: ["1rem", { lineHeight: "1.72", letterSpacing: "-0.003em" }],
        /** What the user said: tighter, denser, a touch smaller. */
        said: ["0.9375rem", { lineHeight: "1.55", letterSpacing: "-0.006em" }],
        /** Reasoning: mono, small, loose leading so it reads as a transcript. */
        reason: ["0.75rem", { lineHeight: "1.75", letterSpacing: "0" }],
        /** Machine output: mono, tight, dense. */
        machine: ["0.75rem", { lineHeight: "1.55", letterSpacing: "-0.01em" }],
        /** Section labels and eyebrows. */
        label: ["0.625rem", { lineHeight: "1", letterSpacing: "0.14em" }],
        /** The greeting. */
        display: ["2.25rem", { lineHeight: "1.08", letterSpacing: "-0.035em" }],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.035) inset",
        e1: "0 1px 2px rgba(0,0,0,0.45)",
        e2: "0 4px 14px -4px rgba(0,0,0,0.55), 0 1px 2px rgba(0,0,0,0.4)",
        lift: "0 16px 40px -12px rgba(0,0,0,0.72), 0 2px 8px rgba(0,0,0,0.45)",
        // Floating surfaces: a dark cast plus an inner top highlight, which is
        // what sells "hovering above" rather than "cut into".
        // On a black floor a cast shadow has almost nothing left to darken, so
        // the inner top highlight is doing most of the lifting here.
        float:
          "0 24px 60px -20px rgba(0,0,0,0.82), 0 2px 10px rgba(0,0,0,0.5), 0 1px 0 0 rgba(255,255,255,0.065) inset",
        glow: "0 0 0 1px rgb(var(--acc) / 0.22), 0 0 26px -6px rgb(var(--acc) / 0.34)",
        "glow-rare":
          "0 0 0 1px rgb(var(--acc-rare) / 0.26), 0 0 24px -6px rgb(var(--acc-rare) / 0.36)",
      },
      transitionTimingFunction: {
        // M3 emphasized-decelerate; the one curve used for entrances.
        out: "cubic-bezier(.2,0,0,1)",
        io: "cubic-bezier(.4,0,.2,1)",
      },
      keyframes: {
        caret: { "0%,49%": { opacity: "1" }, "50%,100%": { opacity: "0" } },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        // Auto-mode orb
        autoBreathe: {
          "0%,100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
        // Ambient field: two very slow, offset drifts. Transform-only so the
        // compositor handles them without a paint.
        driftA: {
          "0%,100%": { transform: "translate3d(0,0,0) scale(1)" },
          "50%": { transform: "translate3d(3%,-4%,0) scale(1.12)" },
        },
        driftB: {
          "0%,100%": { transform: "translate3d(0,0,0) scale(1.06)" },
          "50%": { transform: "translate3d(-4%,3%,0) scale(1)" },
        },
      },
      animation: {
        caret: "caret 1.05s steps(1) infinite",
        shimmer: "shimmer 2.2s linear infinite",
        "auto-breathe": "autoBreathe 2.6s cubic-bezier(.4,0,.2,1) infinite",
        "drift-a": "driftA 26s cubic-bezier(.4,0,.2,1) infinite",
        "drift-b": "driftB 34s cubic-bezier(.4,0,.2,1) infinite",
      },
    },
  },
  plugins: [],
};

export default config;
