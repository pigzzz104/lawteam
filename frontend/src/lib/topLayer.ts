let supportsDisplayTransitionCache: boolean | undefined;

/**
 * Probe whether the current browser actually animates discrete `display` transitions
 * (e.g. Chromium 117+ and Safari 18+; Firefox parses allow-discrete without transitioning display).
 */
export function canTransitionDisplay(): boolean {
  if (supportsDisplayTransitionCache !== undefined) {
    return supportsDisplayTransitionCache;
  }
  if (
    typeof window === "undefined" ||
    typeof document === "undefined" ||
    !window.CSS?.supports?.("transition-behavior", "allow-discrete") ||
    !document.body
  ) {
    return false;
  }
  const probe = document.createElement("div");
  probe.style.cssText =
    "transition: display 1s allow-discrete !important; display: block;";
  document.body.appendChild(probe);
  getComputedStyle(probe).display;
  probe.style.display = "none";
  supportsDisplayTransitionCache = getComputedStyle(probe).display === "block";
  probe.remove();
  return supportsDisplayTransitionCache;
}

export function supportsTopLayerExit(): boolean {
  if (typeof window === "undefined") return false;
  return Boolean(window.CSS?.supports?.("overlay", "auto")) && canTransitionDisplay();
}

/**
 * Animate a `<dialog>` out cleanly even in browsers lacking `overlay` / discrete `display` transitions.
 */
export async function closeDialogWithExitAnimation(
  dialog: HTMLDialogElement
): Promise<void> {
  if (!dialog.open) return;

  const prefersReduced =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  if (!supportsTopLayerExit() && !prefersReduced) {
    dialog.setAttribute("data-closing", "");
    const animations = dialog.getAnimations({ subtree: true });
    if (animations.length > 0) {
      await Promise.race([
        Promise.allSettled(animations.map((a) => a.finished)),
        new Promise((resolve) => window.setTimeout(resolve, 450)),
      ]);
    }
    if (!dialog.hasAttribute("data-closing")) return;
    dialog.removeAttribute("data-closing");
  }

  if (dialog.open) {
    dialog.close();
  }
}
