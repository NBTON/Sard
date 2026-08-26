"use client";
import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useRef,
  useCallback,
  useTransition,
} from "react";
import { Lang } from "@/types";
import { getStoredLang, setStoredLang } from "@/lib/storage";

export type Direction = "rtl" | "ltr";

interface DirectionContextValue {
  lang: Lang;
  dir: Direction;
  isTransitioning: boolean;
  transitionPhase: "idle" | "measuring" | "animating";
  toggleDirection: (targetLang?: Lang) => void;
  setLanguage: (lang: Lang) => void;
  registerAnimatedElement: (id: string, el: HTMLElement | null) => void;
  unregisterAnimatedElement: (id: string) => void;
}

const DirectionContext = createContext<DirectionContextValue | null>(null);

// Easing cubic-beziers
const TRAVEL_EASING = "cubic-bezier(0.22, 1, 0.36, 1)";
const STAGE_PITCH_EASING = "cubic-bezier(0.33, 1, 0.68, 1)";
const BASE_DURATION = 580; // ms

export function DirectionProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("ar");
  const [dir, setDirState] = useState<Direction>("rtl");
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [transitionPhase, setTransitionPhase] = useState<"idle" | "measuring" | "animating">("idle");
  const [, startTransition] = useTransition();

  // Registry of elements to FLIP animate
  const elementsRef = useRef<Map<string, HTMLElement>>(new Map());
  // Pre-flip snapshot storage
  const snapshotRef = useRef<Map<string, DOMRect>>(new Map());
  // Timeout references
  const animTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Initialize from storage on mount
  useEffect(() => {
    const saved = getStoredLang();
    const initialDir: Direction = saved === "en" ? "ltr" : "rtl";
    setLangState(saved);
    setDirState(initialDir);
    document.documentElement.lang = saved;
    document.documentElement.dir = initialDir;
  }, []);

  const registerAnimatedElement = useCallback((id: string, el: HTMLElement | null) => {
    if (el) {
      elementsRef.current.set(id, el);
    } else {
      elementsRef.current.delete(id);
    }
  }, []);

  const unregisterAnimatedElement = useCallback((id: string) => {
    elementsRef.current.delete(id);
  }, []);

  // Main Choreographed Bidirectional Transition Execution
  const toggleDirection = useCallback(
    (targetLang?: Lang) => {
      const nextLang: Lang = targetLang || (lang === "ar" ? "en" : "ar");
      if (nextLang === lang && !targetLang) return;
      const nextDir: Direction = nextLang === "en" ? "ltr" : "rtl";

      // Check if user prefers reduced motion
      const prefersReducedMotion =
        typeof window !== "undefined" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      if (prefersReducedMotion) {
        // Instant non-animated swap
        setLangState(nextLang);
        setDirState(nextDir);
        setStoredLang(nextLang);
        document.documentElement.lang = nextLang;
        document.documentElement.dir = nextDir;
        return;
      }

      if (isTransitioning) return; // Prevent double-trigger collision

      // Step 1: FIRST - Capture Bounding Rects of all registered elements
      snapshotRef.current.clear();
      elementsRef.current.forEach((el, id) => {
        if (el && document.body.contains(el)) {
          snapshotRef.current.set(id, el.getBoundingClientRect());
        }
      });

      // Capture all elements with [data-dir-animate] automatically
      const autoAnimateNodes = document.querySelectorAll<HTMLElement>("[data-dir-animate]");
      autoAnimateNodes.forEach((node, index) => {
        const autoKey = node.getAttribute("data-dir-id") || `auto_node_${index}`;
        snapshotRef.current.set(autoKey, node.getBoundingClientRect());
      });

      setIsTransitioning(true);
      setTransitionPhase("measuring");

      // Direction vector: LTR -> RTL (+1), RTL -> LTR (-1)
      const deltaDir = nextDir === "rtl" ? 1 : -1;
      document.documentElement.setAttribute("data-direction-switching", "true");
      document.documentElement.style.setProperty("--dir-delta", String(deltaDir));

      // Step 2: UPDATE STATE & DOM ROOT
      startTransition(() => {
        setLangState(nextLang);
        setDirState(nextDir);
        setStoredLang(nextLang);
        document.documentElement.lang = nextLang;
        document.documentElement.dir = nextDir;
      });

      // Step 3: LAST, INVERT & PLAY in next microtask / rAF after React commit
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setTransitionPhase("animating");

          // Play Stage Turn Animation on Root Shell
          const stageContainer = document.querySelector<HTMLElement>(".sard-stage-viewport");
          if (stageContainer) {
            stageContainer.animate(
              [
                {
                  transform: `perspective(1800px) rotateY(${deltaDir * -2.4}deg) translateZ(-14px) scale(0.996)`,
                  filter: "brightness(1.02)",
                },
                {
                  transform: "perspective(1800px) rotateY(0deg) translateZ(0px) scale(1)",
                  filter: "brightness(1)",
                },
              ],
              {
                duration: BASE_DURATION,
                easing: STAGE_PITCH_EASING,
                fill: "forwards",
              }
            );
          }

          // Trigger Light Sweep Highlight
          const sweepBeam = document.querySelector<HTMLElement>(".sard-direction-sweep");
          if (sweepBeam) {
            sweepBeam.animate(
              [
                {
                  transform: `translateX(${deltaDir * -100}%)`,
                  opacity: 0.6,
                },
                {
                  transform: `translateX(${deltaDir * 100}%)`,
                  opacity: 0,
                },
              ],
              {
                duration: BASE_DURATION - 80,
                easing: "cubic-bezier(0.4, 0, 0.2, 1)",
                fill: "forwards",
              }
            );
          }

          // Step 4: FLIP Animate individual nodes with calculated stagger
          const viewportWidth = window.innerWidth;
          let nodeIndex = 0;

          // Animate Registered Map Elements
          elementsRef.current.forEach((el, id) => {
            if (!el || !document.body.contains(el)) return;
            const firstRect = snapshotRef.current.get(id);
            if (!firstRect) return;

            const lastRect = el.getBoundingClientRect();
            const deltaX = firstRect.left - lastRect.left;
            const deltaY = firstRect.top - lastRect.top;

            // Only animate if position actually shifted meaningfully
            if (Math.abs(deltaX) > 1 || Math.abs(deltaY) > 1) {
              // Calculate stagger based on horizontal distance from new reading origin
              const originX = nextDir === "rtl" ? viewportWidth - lastRect.right : lastRect.left;
              const staggerRatio = Math.min(Math.max(originX / viewportWidth, 0), 1);
              const staggerDelay = 15 + staggerRatio * 90 + (nodeIndex % 4) * 12;

              el.animate(
                [
                  {
                    transform: `translate3d(${deltaX}px, ${deltaY}px, 0)`,
                    willChange: "transform",
                  },
                  {
                    transform: "translate3d(0, 0, 0)",
                    willChange: "auto",
                  },
                ],
                {
                  duration: BASE_DURATION,
                  delay: staggerDelay,
                  easing: TRAVEL_EASING,
                  fill: "both",
                }
              );
              nodeIndex++;
            }
          });

          // Animate Auto-detected Nodes with [data-dir-animate]
          autoAnimateNodes.forEach((node, index) => {
            const autoKey = node.getAttribute("data-dir-id") || `auto_node_${index}`;
            const firstRect = snapshotRef.current.get(autoKey);
            if (!firstRect) return;

            const lastRect = node.getBoundingClientRect();
            const deltaX = firstRect.left - lastRect.left;
            const deltaY = firstRect.top - lastRect.top;

            if (Math.abs(deltaX) > 1 || Math.abs(deltaY) > 1) {
              const explicitStagger = node.getAttribute("data-dir-stagger");
              const staggerDelay = explicitStagger
                ? parseInt(explicitStagger, 10)
                : 20 + (index * 24);

              // Check if node is marked for 3D card tilt
              const isCard = node.getAttribute("data-dir-animate") === "card";
              const keyframes = isCard
                ? [
                    {
                      transform: `translate3d(${deltaX}px, ${deltaY}px, 0) rotateY(${deltaDir * 4}deg) scale(0.99)`,
                    },
                    {
                      transform: "translate3d(0, 0, 0) rotateY(0deg) scale(1)",
                    },
                  ]
                : [
                    {
                      transform: `translate3d(${deltaX}px, ${deltaY}px, 0)`,
                    },
                    {
                      transform: "translate3d(0, 0, 0)",
                    },
                  ];

              node.animate(keyframes, {
                duration: BASE_DURATION,
                delay: staggerDelay,
                easing: TRAVEL_EASING,
                fill: "both",
              });
            }
          });

          // Step 5: Clean up after animation finishes
          if (animTimeoutRef.current) clearTimeout(animTimeoutRef.current);
          animTimeoutRef.current = setTimeout(() => {
            setIsTransitioning(false);
            setTransitionPhase("idle");
            document.documentElement.removeAttribute("data-direction-switching");
          }, BASE_DURATION + 160);
        });
      });
    },
    [lang, isTransitioning]
  );

  const setLanguage = useCallback(
    (newLang: Lang) => {
      if (newLang === lang) return;
      toggleDirection(newLang);
    },
    [lang, toggleDirection]
  );

  return (
    <DirectionContext.Provider
      value={{
        lang,
        dir,
        isTransitioning,
        transitionPhase,
        toggleDirection,
        setLanguage,
        registerAnimatedElement,
        unregisterAnimatedElement,
      }}
    >
      {children}
    </DirectionContext.Provider>
  );
}

export function useDirection() {
  const ctx = useContext(DirectionContext);
  if (!ctx) {
    throw new Error("useDirection must be used within a DirectionProvider");
  }
  return ctx;
}
