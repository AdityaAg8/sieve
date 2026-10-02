"use client";

import { useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";

type Direction = "up" | "left" | "right" | "scale";

const DIRECTION_CLASS: Record<Direction, string> = {
  up: "",
  left: "rv-left",
  right: "rv-right",
  scale: "rv-scale",
};

/**
 * A block that enters when it scrolls into view. One observer per element, disconnected on
 * first sight; the CSS in `globals.css` owns the motion. `stagger` is written to the children
 * as `--reveal-delay` so a grid of cards arrives one after another.
 *
 * Under reduced motion the element is rendered settled from first paint — the observer never
 * starts, and the stylesheet already shows `.reveal` at full opacity.
 *
 * The children are assumed static: delays are written once on mount and the observer stops after
 * the first sighting, so a list that streams in later would arrive undelayed and, after the
 * sighting, invisible. Wrap a live list in something else.
 */
export function Reveal({
  as: Tag = "div",
  direction = "up",
  delayMs = 0,
  stagger,
  className = "",
  children,
  ...rest
}: {
  as?: "div" | "section" | "ul" | "ol" | "li" | "dl" | "article" | "aside" | "figure";
  direction?: Direction;
  delayMs?: number;
  /** Per-child delay, in milliseconds. Children then carry `.reveal` themselves. */
  stagger?: number;
  className?: string;
  children: React.ReactNode;
} & Omit<React.HTMLAttributes<HTMLElement>, "className" | "children">) {
  const ref = useRef<HTMLElement>(null);
  const reduceMotion = useReducedMotion() ?? false;
  // The tag is chosen at runtime, so the ref is typed against the common ancestor once here
  // rather than against each intrinsic element the union could name.
  const Element = Tag as React.ElementType;

  useEffect(() => {
    const element = ref.current;
    if (!element || reduceMotion) return;

    if (typeof stagger === "number") {
      Array.from(element.children).forEach((child, index) => {
        (child as HTMLElement).style.setProperty("--reveal-delay", `${index * stagger}ms`);
      });
    }

    if (typeof IntersectionObserver === "undefined") {
      element.classList.add("is-visible");
      for (const child of Array.from(element.children)) child.classList.add("is-visible");
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const target = entry.target as HTMLElement;
          target.classList.add("is-visible");
          for (const child of Array.from(target.children)) child.classList.add("is-visible");
          observer.unobserve(target);
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [reduceMotion, stagger]);

  const own = typeof stagger === "number" ? "" : `reveal ${DIRECTION_CLASS[direction]}`;

  return (
    <Element
      ref={ref}
      className={`${own} ${className}`.trim()}
      style={delayMs ? ({ "--reveal-delay": `${delayMs}ms` } as React.CSSProperties) : undefined}
      {...rest}
    >
      {children}
    </Element>
  );
}
