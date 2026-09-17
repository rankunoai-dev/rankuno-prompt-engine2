/**
 * Smooth scrolling for the reading pages (Overview, Atlas) only. Never on
 * pages with AntD tables or virtual lists, and never under reduced motion.
 */
import { useEffect } from "react";
import Lenis from "lenis";
import { useReducedMotion } from "@/app/ThemeProvider";

export function useLenis(enabled = true): void {
    const reduced = useReducedMotion();
    useEffect(() => {
        if (!enabled || reduced || typeof window === "undefined") return;
        if (import.meta.env.MODE === "test") return;
        const lenis = new Lenis({ duration: 0.9, smoothWheel: true });
        let frame = 0;
        const raf = (time: number) => {
            lenis.raf(time);
            frame = requestAnimationFrame(raf);
        };
        frame = requestAnimationFrame(raf);
        return () => {
            cancelAnimationFrame(frame);
            lenis.destroy();
        };
    }, [enabled, reduced]);
}
