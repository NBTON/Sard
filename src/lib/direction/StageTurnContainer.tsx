"use client";
import React from "react";
import { useDirection } from "./DirectionContext";

export function StageTurnContainer({
  children,
  className = "",
  style = {},
}: {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  const { dir, isTransitioning } = useDirection();

  return (
    <div
      className={`sard-stage-viewport ${className}`}
      data-dir={dir}
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        position: "relative",
        overflow: "hidden",
        perspective: "1800px",
        transformStyle: "preserve-3d",
        backfaceVisibility: "hidden",
        willChange: isTransitioning ? "transform, filter" : "auto",
        ...style,
      }}
    >
      {/* Directional Ambient Highlight Sheen that sweeps across reading axis */}
      <div
        className="sard-direction-sweep"
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 40,
          opacity: 0,
          background:
            "linear-gradient(90deg, transparent 0%, rgba(250, 247, 241, 0.45) 50%, transparent 100%)",
          mixBlendMode: "overlay",
        }}
      />
      {children}
    </div>
  );
}
