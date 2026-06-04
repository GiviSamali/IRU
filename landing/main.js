(() => {
    const founder = document.querySelector("[data-founder-orbit]");
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    if (!founder || reduceMotion.matches) {
        return;
    }

    const setPointerGlow = (event) => {
        const rect = founder.getBoundingClientRect();
        const x = ((event.clientX - rect.left) / rect.width) * 100;
        const y = ((event.clientY - rect.top) / rect.height) * 100;

        founder.style.setProperty("--mx", `${Math.max(0, Math.min(100, x)).toFixed(2)}%`);
        founder.style.setProperty("--my", `${Math.max(0, Math.min(100, y)).toFixed(2)}%`);
    };

    const resetPointerGlow = () => {
        founder.style.setProperty("--mx", "50%");
        founder.style.setProperty("--my", "36%");
    };

    founder.addEventListener("pointermove", setPointerGlow);
    founder.addEventListener("pointerleave", resetPointerGlow);
})();
