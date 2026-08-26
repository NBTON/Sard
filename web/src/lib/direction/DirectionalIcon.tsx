"use client";
import React from "react";
import { useDirection } from "./DirectionContext";

interface DirectionalIconProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
  /**
   * Action type:
   * 'flip' = 180deg mirror along horizontal axis (chevrons, back arrows, list markers)
   * 'rotate' = 180deg spin
   * 'none' = preserve orientation regardless of RTL/LTR
   */
  behavior?: "flip" | "rotate" | "none";
  className?: string;
  style?: React.CSSProperties;
}

export function DirectionalIcon({
  children,
  behavior = "flip",
  className = "",
  style = {},
  ...rest
}: DirectionalIconProps) {
  const { dir } = useDirection();
  const isRtl = dir === "rtl";

  let transform = "none";
  if (behavior === "flip" && isRtl) {
    transform = "scaleX(-1)";
  } else if (behavior === "rotate" && isRtl) {
    transform = "rotate(180deg)";
  }

  return (
    <span
      className={`directional-icon-wrapper ${className}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        transform,
        transformOrigin: "center center",
        transition: "transform 480ms cubic-bezier(0.34, 1.56, 0.64, 1)",
        willChange: "transform",
        ...style,
      }}
      aria-hidden="true"
      {...rest}
    >
      {children}
    </span>
  );
}
