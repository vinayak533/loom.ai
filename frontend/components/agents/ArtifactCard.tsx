"use client";

import { useState } from "react";
import { cn } from "@/lib/cn";
import { CitationList } from "./AgentOutputs";

/**
 * Structured tool output, rendered as the thing it is.
 *
 * A tool result carries two channels: prose for the model, and — when there is
 * something better than prose to show — an `artifact` payload for the UI. This
 * component is the whole of that second channel, so adding a new artifact kind
 * is a case here and nothing else.
 */
export type Artifact =
  | {
      kind: "image";
      data_url: string;
      asset_id: string;
      width: number;
      height: number;
      prompt?: string;
      negative_prompt?: string | null;
      aspect_ratio?: string;
      model?: string;
      seed?: string;
      provider?: string;
      derived_from?: string;
    }
  | {
      kind: "citations";
      sources: Array<{ url: string; domain: string; tier: string; reason: string }>;
    }
  | { kind: "handoff"; [key: string]: unknown };

export function ArtifactCard({ artifact }: { artifact: Artifact }) {
  if (artifact.kind === "image") return <ImageArtifact artifact={artifact} />;
  if (artifact.kind === "citations") {
    return (
      <div className="rounded-card border border-line bg-surface p-3.5">
        <CitationList sources={artifact.sources} />
      </div>
    );
  }
  // Handoffs arrive as their own `agent_handoff` frame and render as a
  // HandoffCard on the thread; there is nothing to draw from the tool result.
  return null;
}

function ImageArtifact({
  artifact,
}: {
  artifact: Extract<Artifact, { kind: "image" }>;
}) {
  const [zoomed, setZoomed] = useState(false);

  return (
    <figure className="overflow-hidden rounded-card border border-line bg-surface">
      <button
        type="button"
        onClick={() => setZoomed((z) => !z)}
        className="block w-full bg-surface-solid"
        aria-label={zoomed ? "Shrink the image" : "Show the image at full size"}
      >
        {/* A data URI from our own backend, so `next/image` would add a
            loader round trip for a picture that is already in memory. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={artifact.data_url}
          alt={artifact.prompt ?? "Generated image"}
          className={cn(
            "mx-auto block transition-[max-height,width,opacity] duration-300",
            zoomed ? "max-h-none w-full" : "max-h-[26rem] w-auto object-contain",
          )}
        />
      </button>

      <figcaption className="space-y-2 p-3.5">
        {artifact.prompt && (
          <p className="font-sans text-[0.8125rem] leading-relaxed text-ink-muted">
            {artifact.prompt}
          </p>
        )}
        {artifact.negative_prompt && (
          <p className="font-sans text-2xs leading-relaxed text-ink-faint">
            <span className="text-ink-subtle">negative:</span>{" "}
            {artifact.negative_prompt}
          </p>
        )}

        {/* The generation parameters, verbatim from the provider. The seed in
            particular is the only way to reproduce this exact image. */}
        <dl className="flex flex-wrap gap-x-4 gap-y-1 pt-0.5">
          <Meta label="size" value={`${artifact.width}×${artifact.height}`} />
          {artifact.aspect_ratio && <Meta label="ratio" value={artifact.aspect_ratio} />}
          {artifact.model && <Meta label="model" value={artifact.model} />}
          {artifact.provider && <Meta label="provider" value={artifact.provider} />}
          {artifact.seed && <Meta label="seed" value={artifact.seed} />}
          <Meta label="asset" value={artifact.asset_id} />
          {artifact.derived_from && (
            <Meta label="resized from" value={artifact.derived_from} />
          )}
        </dl>

        <div className="flex flex-wrap gap-2 pt-1">
          <a
            href={artifact.data_url}
            download={`${artifact.asset_id}.png`}
            className="rounded-ctl border border-line px-2.5 py-1 font-sans text-2xs
                       text-ink-muted transition-colors duration-200 hover:bg-raised hover:text-ink"
          >
            Download PNG
          </a>
          {artifact.prompt && (
            <CopyButton value={artifact.prompt} label="Copy prompt" />
          )}
        </div>
      </figcaption>
    </figure>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="voice-label">{label}</dt>
      <dd className="voice-machine text-ink-muted">{value}</dd>
    </div>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        } catch {
          // Clipboard denied; the text is on screen and selectable.
        }
      }}
      className={cn(
        "rounded-ctl border border-line px-2.5 py-1 font-sans text-2xs text-ink-muted",
        "transition-colors duration-200 hover:bg-raised hover:text-ink",
        copied && "border-add/40 text-add",
      )}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

/** Pull the artifact out of a tool result's `meta`, if it has one. */
export function artifactOf(meta: unknown): Artifact | null {
  if (!meta || typeof meta !== "object") return null;
  const candidate = (meta as { artifact?: unknown }).artifact;
  if (!candidate || typeof candidate !== "object") return null;
  const kind = (candidate as { kind?: unknown }).kind;
  return typeof kind === "string" ? (candidate as Artifact) : null;
}
