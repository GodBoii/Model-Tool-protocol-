"use client";

import { useEffect, useRef, useState } from "react";

interface Heading {
  id: string;
  title: string;
}

interface DocScrollSpyProps {
  headings: Heading[];
}

export function DocScrollSpy({ headings }: DocScrollSpyProps) {
  const [activeId, setActiveId] = useState<string>(headings[0]?.id ?? "");
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    if (headings.length === 0) return;

    const handleIntersect = (entries: IntersectionObserverEntry[]) => {
      // Find all visible headings and pick the topmost one
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);

      if (visible.length > 0) {
        setActiveId(visible[0].target.id);
      }
    };

    observerRef.current = new IntersectionObserver(handleIntersect, {
      rootMargin: "-10% 0px -75% 0px",
      threshold: 0,
    });

    headings.forEach(({ id }) => {
      const el = document.getElementById(id);
      if (el) observerRef.current?.observe(el);
    });

    return () => observerRef.current?.disconnect();
  }, [headings]);

  const handleClick = (id: string) => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveId(id);
    }
  };

  if (headings.length === 0) return null;

  return (
    <nav
      aria-label="On this page"
      className="hidden xl:flex flex-col fixed right-8 top-1/2 -translate-y-1/2 z-40 gap-0"
    >
      {/* Label */}
      <div
        className="mb-6 flex items-center gap-2 opacity-0 hover:opacity-100 transition-opacity duration-500"
        style={{ justifyContent: "flex-end" }}
      >
        <span className="text-[9px] font-bold tracking-[0.3em] uppercase text-white/20">
          On this page
        </span>
      </div>

      {/* Items */}
      {headings.map((h) => {
        const isActive = activeId === h.id;
        const isHovered = hoveredId === h.id;
        const highlighted = isActive || isHovered;

        return (
          <button
            key={h.id}
            onClick={() => handleClick(h.id)}
            onMouseEnter={() => setHoveredId(h.id)}
            onMouseLeave={() => setHoveredId(null)}
            aria-label={h.title}
            suppressHydrationWarning
            className="group relative flex items-center justify-end py-[7px] pr-0 focus:outline-none"
          >
            {/* Title — slides in from right on hover/active */}
            <span
              className="text-[12px] font-medium tracking-tight mr-3 whitespace-nowrap transition-all duration-300 pointer-events-none"
              style={{
                opacity: highlighted ? 1 : 0,
                transform: highlighted ? "translateX(0)" : "translateX(6px)",
                color: isActive ? "#facc15" : "rgba(255,255,255,0.7)",
              }}
            >
              {h.title}
            </span>

            {/* The line */}
            <div
              className="relative flex items-center transition-all duration-300"
              style={{
                width: highlighted ? (isActive ? 48 : 36) : 20,
              }}
            >
              {/* Glow layer for active */}
              {isActive && (
                <div
                  className="absolute inset-0 rounded-full blur-sm"
                  style={{ background: "#facc1540" }}
                />
              )}
              <div
                className="relative w-full rounded-full transition-all duration-300"
                style={{
                  height: isActive ? 3 : isHovered ? 2 : 1.5,
                  background: isActive
                    ? "#facc15"
                    : isHovered
                    ? "rgba(255,255,255,0.55)"
                    : "rgba(255,255,255,0.18)",
                  boxShadow: isActive
                    ? "0 0 8px 1px rgba(250,204,21,0.5)"
                    : "none",
                }}
              />
            </div>
          </button>
        );
      })}
    </nav>
  );
}
