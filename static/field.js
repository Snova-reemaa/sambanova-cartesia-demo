'use strict';

// The green light in the background follows the pointer, slowly. It eases a
// few percent of the way there each frame, so it drifts after the hand rather
// than sticking to it, and stops drawing once it has caught up.
(() => {
  const root = document.documentElement;
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  let tx = innerWidth * 0.32, ty = innerHeight * 0.34;
  let x = tx, y = ty;
  let moving = false;

  const place = () => {
    root.style.setProperty('--mx', `${x.toFixed(1)}px`);
    root.style.setProperty('--my', `${y.toFixed(1)}px`);
  };
  place();
  if (reduce) return;

  const EASE = 0.045;
  const tick = () => {
    x += (tx - x) * EASE;
    y += (ty - y) * EASE;
    place();
    if (Math.abs(tx - x) + Math.abs(ty - y) > 0.6) requestAnimationFrame(tick);
    else moving = false;
  };

  addEventListener('pointermove', (e) => {
    tx = e.clientX;
    ty = e.clientY;
    if (!moving) { moving = true; requestAnimationFrame(tick); }
  }, { passive: true });
})();
