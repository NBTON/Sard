"use client";

import React from "react";
import { Artifact, Lang } from "@/types";
import { ArtifactPanel } from "./ArtifactPanel";

interface ArtifactModalProps {
  artifact: Artifact | null;
  onClose: () => void;
  lang: Lang;
  onRevise?: (artifact: Artifact, instruction: string) => void;
}

/**
 * Backward-compatible modal wrapper. The dockable workspace uses
 * ArtifactPanel directly (mode="panel"); this keeps the old blocking-modal
 * call sites working by rendering the same panel in floating mode.
 */
export function ArtifactModal({ artifact, onClose, lang, onRevise }: ArtifactModalProps) {
  if (!artifact) return null;
  return <ArtifactPanel artifact={artifact} lang={lang} mode="modal" onClose={onClose} onRevise={onRevise} />;
}
