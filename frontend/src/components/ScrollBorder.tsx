import { useEffect, useState } from "react";

/**
 * Scroll indicator that runs along the window border instead of a scrollbar.
 * The native scrollbar is hidden (see styles.css); these four fixed bars sit on
 * the window edges and fill clockwise as the page is scrolled, so the border
 * itself acts as the progress "slider".
 *
 * top edge:    fills left -> right   (0% -> 25%)
 * right edge:  fills top -> bottom   (25% -> 50%)
 * bottom edge: fills right -> left   (50% -> 75%)
 * left edge:   fills bottom -> top   (75% -> 100%)
 */
export default function ScrollBorder() {
  const [progress, setProgress] = useState(0); // 0..1

  useEffect(() => {
    const onScroll = () => {
      const doc = document.documentElement;
      const max = doc.scrollHeight - doc.clientHeight;
      setProgress(max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  // Split the single 0..1 progress into the four edges (each worth 25%).
  const seg = (start: number) => Math.min(1, Math.max(0, (progress - start) / 0.25));
  const top = seg(0);
  const right = seg(0.25);
  const bottom = seg(0.5);
  const left = seg(0.75);

  return (
    <div className="scroll-border" aria-hidden="true">
      <span className="sb sb-top" style={{ transform: `scaleX(${top})` }} />
      <span className="sb sb-right" style={{ transform: `scaleY(${right})` }} />
      <span className="sb sb-bottom" style={{ transform: `scaleX(${bottom})` }} />
      <span className="sb sb-left" style={{ transform: `scaleY(${left})` }} />
    </div>
  );
}
