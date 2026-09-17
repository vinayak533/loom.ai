"use client";

import { motion } from "framer-motion";
import { SECTIONS, SECTION_ACCENT, SECTION_META, type Section } from "@/lib/sections";
import { cn } from "@/lib/cn";
import { SPRING_SOFT, useMotionOK } from "./Anim";
import { Tooltip } from "./Tooltip";

/**
 * The four modes. Each row carries its own accent so the identity reads
 * before you click — rounded/friendly glyphs for Chat and Learn, angular
 * butt-cap strokes for Code, and a node-and-edge glyph for Agents that says
 * "several things wired together" rather than "one conversation".
 *
 * Collapsed (the rail's normal state) the active mode is marked by the
 * product's rhombus rather than a generic pill, so even the navigation is
 * wearing the same mark as the trace spine's nodes.
 *
 * This is the one control that is on screen in every section, so it is the one
 * place worth spending a little motion on: the mark and its tint are shared
 * `layoutId`s, which means switching modes *travels* — the rhombus slides from
 * the section you left to the one you arrived at, changing colour on the way,
 * rather than blinking out of one row and into another. It is the only
 * cross-section transition in the app, which is exactly why it should be the
 * one that reads as continuous.
 */
// Each row sets `--nav-acc` from `SECTION_ACCENT` and the three classes
// below read it. This used to be six literal hex values that could not follow
// the token layer; now the only place an accent is spelled out is
// `lib/sections.ts`, beside the CSS it has to match.
const ROW_MARK = "bg-[rgb(var(--nav-acc))]";
const ROW_SOFT = "bg-[rgb(var(--nav-acc)/0.10)]";
const ROW_ICON = "text-[rgb(var(--nav-acc))]";

export function SectionNav({
  active,
  collapsed,
  onSelect,
}: {
  active: Section;
  collapsed?: boolean;
  onSelect: (s: Section) => void;
}) {
  const motionOK = useMotionOK();
  const travel = motionOK ? SPRING_SOFT : { duration: 0 };

  return (
    // A plain group rather than a <nav>: collapsed, this sits inside the rail's
    // own <nav>, and nesting landmarks helps nobody.
    <div role="group" aria-label="Modes" className="flex flex-col gap-1">
      {!collapsed && <p className="voice-label px-3 pb-2">Modes</p>}
      {SECTIONS.map((s) => {
        const on = s === active;
        const row = (
          <button
            key={s}
            type="button"
            onClick={() => onSelect(s)}
            aria-current={on ? "page" : undefined}
            aria-label={collapsed ? SECTION_META[s].name : undefined}
            style={{ "--nav-acc": SECTION_ACCENT[s] } as React.CSSProperties}
            className={cn(
              "relative flex items-center gap-3 rounded-ctl text-sm font-medium",
              "transition-colors duration-200 ease-out active:scale-[0.94]",
              collapsed ? "h-10 w-10 justify-center px-0" : "h-10 px-3",
              "touch:h-11",
              collapsed && "touch:w-11",
              on ? "text-ink" : "text-ink-muted hover:bg-elevated hover:text-ink",
            )}
          >
            {on && (
              <>
                <motion.span
                  layoutId={`nav-tint-${collapsed ? "rail" : "full"}`}
                  transition={travel}
                  aria-hidden
                  className={cn("absolute inset-0 rounded-ctl", ROW_SOFT)}
                />
                <motion.span
                  layoutId={`nav-mark-${collapsed ? "rail" : "full"}`}
                  transition={travel}
                  aria-hidden
                  className={cn(
                    "sigil absolute top-1/2 h-[7px] w-[7px] -translate-y-1/2",
                    collapsed ? "-left-[7px]" : "left-0",
                    ROW_MARK,
                  )}
                />
              </>
            )}
            <span className={cn("relative shrink-0 transition-colors duration-200", on && ROW_ICON)}>
              <SectionIcon section={s} />
            </span>
            {!collapsed && (
              <span className="relative whitespace-nowrap">{SECTION_META[s].name}</span>
            )}
          </button>
        );
        return collapsed ? (
          <Tooltip key={s} label={SECTION_META[s].name} side="right">
            {row}
          </Tooltip>
        ) : (
          row
        );
      })}
    </div>
  );
}

export function SectionIcon({ section }: { section: Section }) {
  if (section === "chat") {
    return (
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20.5 11.5a7.5 7.5 0 0 1-10.9 6.7L4 19.5l1.4-4.4A7.5 7.5 0 1 1 20.5 11.5Z" />
        <path d="M12.6 8.2 13.3 10l1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z" />
      </svg>
    );
  }
  if (section === "learning") {
    return (
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v14H6.5A2.5 2.5 0 0 0 4 19.5v-14Z" />
        <path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H19" />
        <path d="M11 7.5a2.4 2.4 0 0 1 3.4 3.3c-.4.4-.6.8-.6 1.3M12.7 14.4h0" />
      </svg>
    );
  }
  if (section === "code") {
    // Code — tighter stroke, butt caps, miter joins
    return (
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="butt" strokeLinejoin="miter">
        <path d="M8.5 6 3 12l5.5 6" />
        <path d="M15.5 6 21 12l-5.5 6" />
        <path d="M12.8 5.5 11.2 18.5" />
      </svg>
    );
  }
  // Agents — one node branching into three. Drawn by hand like its three
  // siblings rather than pulled from Lucide: the rail's glyphs are a matched
  // set at one stroke weight, and a library icon dropped among them reads as
  // borrowed. (Lucide is used inside the section itself, where the agents'
  // own icons come from the real icon set.)
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 12h4" />
      <path d="M14 6.5h1.5M14 12h1.5M14 17.5h1.5" />
      <path d="M10 12V6.5h4M10 12v5.5h4" />
      <circle cx="4.4" cy="12" r="1.9" />
      <circle cx="18.4" cy="6.5" r="1.7" />
      <circle cx="18.4" cy="12" r="1.7" />
      <circle cx="18.4" cy="17.5" r="1.7" />
    </svg>
  );
}
