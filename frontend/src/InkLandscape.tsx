import { useEffect, useRef, useState } from "react";

type InkLandscapeProps = {
  className?: string;
  tone?: "light" | "dark";
};

type InkSplash = {
  id: number;
  x: number;
  y: number;
  tone: "cool" | "warm";
};

/**
 * A living, code-native ink wash landscape. Pointer movement shifts the whole
 * scene while each range, bird and mist layer keeps its own slower rhythm.
 */
export default function InkLandscape({
  className = "",
  tone = "light",
}: InkLandscapeProps) {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!root) return;

    let pointerX = 0;
    let pointerY = 0;
    let scrollOffset = Math.min(window.scrollY * 0.035, 18);
    let animationFrame: number | null = null;

    const renderParallax = () => {
      animationFrame = null;
      root.style.setProperty("--ink-parallax-x", `${pointerX * 18}px`);
      root.style.setProperty("--ink-parallax-y", `${pointerY * 12}px`);
      root.style.setProperty("--ink-counter-x", `${pointerX * -10}px`);
      root.style.setProperty("--ink-counter-y", `${pointerY * -7}px`);
      root.style.setProperty("--ink-scroll-y", `${scrollOffset}px`);
    };
    const queueRender = () => {
      if (animationFrame === null) {
        animationFrame = window.requestAnimationFrame(renderParallax);
      }
    };
    const onPointerMove = (event: PointerEvent) => {
      if (reduceMotion.matches || event.pointerType === "touch") return;
      pointerX = event.clientX / Math.max(window.innerWidth, 1) - 0.5;
      pointerY = event.clientY / Math.max(window.innerHeight, 1) - 0.5;
      queueRender();
    };
    const onPointerOut = (event: PointerEvent) => {
      if (event.relatedTarget) return;
      pointerX = 0;
      pointerY = 0;
      queueRender();
    };
    const onScroll = () => {
      if (reduceMotion.matches) return;
      scrollOffset = Math.min(window.scrollY * 0.035, 18);
      queueRender();
    };
    const onMotionPreference = () => {
      if (reduceMotion.matches) {
        pointerX = 0;
        pointerY = 0;
        scrollOffset = 0;
        renderParallax();
      } else {
        scrollOffset = Math.min(window.scrollY * 0.035, 18);
        queueRender();
      }
    };

    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerout", onPointerOut, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    reduceMotion.addEventListener("change", onMotionPreference);
    renderParallax();
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerout", onPointerOut);
      window.removeEventListener("scroll", onScroll);
      reduceMotion.removeEventListener("change", onMotionPreference);
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
    };
  }, []);

  return (
    <div
      ref={rootRef}
      className={`ink-landscape ink-landscape-${tone} ${className}`.trim()}
      aria-hidden="true"
    >
      <svg viewBox="0 0 1200 430" preserveAspectRatio="xMidYMax slice">
        <g className="ink-range ink-range-far">
          <path d="M0 306c104-18 168-65 242-118 37-27 67-20 108 22 32 33 63 39 107 10 64-42 114-96 184-79 49 12 60 53 106 66 62 18 91-61 161-55 60 6 86 79 130 100 53 26 101-1 162-15v193H0Z" />
        </g>
        <g className="ink-range ink-range-mid">
          <path d="M0 354c118-20 184-93 271-90 75 3 89 67 157 67 70 0 103-142 181-139 68 2 94 117 159 122 67 5 94-90 163-83 53 5 76 72 123 86 56 17 91-18 146-19v132H0Z" />
        </g>
        <g className="ink-range ink-range-near">
          <path d="M0 382c93-2 145-44 226-48 87-4 134 54 218 42 79-11 113-78 193-70 65 6 100 73 166 75 84 3 130-80 209-58 54 15 91 51 188 50v57H0Z" />
        </g>
        <circle className="ink-sun" cx="922" cy="116" r="24" />
        <circle className="ink-sun-halo" cx="922" cy="116" r="34" />
        <path
          className="ink-ripple"
          d="M589 353c123-14 251-12 387 5M672 374c102-8 194-5 276 4M742 395c63-4 121-2 174 3"
        />
        <g className="ink-birds ink-birds-one">
          <path d="M782 116c9-9 18-9 27 0 8-8 17-8 25 0M836 91c6-6 13-6 19 0 6-6 12-6 18 0" />
        </g>
        <g className="ink-birds ink-birds-two">
          <path d="M280 134c7-7 14-7 21 0 7-7 14-7 21 0M326 112c5-5 10-5 15 0 5-5 10-5 15 0" />
        </g>
        <path className="ink-wind" d="M111 251c156-25 291-13 416 9 132 24 261 26 441-7" />
      </svg>

      <span className="ink-cloud ink-cloud-one" />
      <span className="ink-cloud ink-cloud-two" />
      <span className="ink-cloud ink-cloud-three" />
      <span className="ink-bloom ink-bloom-one" />
      <span className="ink-bloom ink-bloom-two" />
      <span className="ink-brush-flow ink-brush-flow-one" />
      <span className="ink-brush-flow ink-brush-flow-two" />
      <span className="ink-fleck ink-fleck-one" />
      <span className="ink-fleck ink-fleck-two" />
      <span className="ink-fleck ink-fleck-three" />
      <span className="ink-dust ink-dust-one" />
      <span className="ink-dust ink-dust-two" />
      <span className="ink-dust ink-dust-three" />
      <span className="ink-dust ink-dust-four" />
      <span className="ink-dust ink-dust-five" />
      <span className="ink-pointer-aura" />
    </div>
  );
}

/** A short-lived ink bloom for meaningful pointer presses across the UI. */
export function InkInteractionLayer() {
  const [splashes, setSplashes] = useState<InkSplash[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Map<number, number>());

  useEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onPointerDown = (event: PointerEvent) => {
      if (reduceMotion.matches || event.button !== 0 || event.pointerType === "touch") return;
      const target = event.target instanceof Element ? event.target : null;
      if (!target || target.closest("textarea, [contenteditable='true'], [disabled], [aria-disabled='true']")) return;
      if (!target.closest(".app-shell, .auth-shell, .account-page")) return;

      const id = ++nextId.current;
      const tone = target.closest(".button-primary, .brand-lockup, .avatar, .active-label")
        ? "warm"
        : "cool";
      setSplashes((current) => [
        ...current.slice(-6),
        { id, x: event.clientX, y: event.clientY, tone },
      ]);
      timers.current.set(
        id,
        window.setTimeout(() => {
          setSplashes((current) => current.filter((splash) => splash.id !== id));
          timers.current.delete(id);
        }, 920),
      );
    };

    window.addEventListener("pointerdown", onPointerDown, { passive: true });
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      timers.current.forEach((timer) => window.clearTimeout(timer));
      timers.current.clear();
    };
  }, []);

  return (
    <div className="ink-interaction-layer" aria-hidden="true">
      {splashes.map((splash) => (
        <span
          className={`ink-click ink-click-${splash.tone}`}
          key={splash.id}
          style={{ left: splash.x, top: splash.y }}
        >
          <i />
        </span>
      ))}
    </div>
  );
}
