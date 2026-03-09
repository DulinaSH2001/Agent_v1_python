/**
 * App Data
 *
 * Central location for static site data: navigation links, social links, etc.
 * Replace or extend these with your application's real data.
 */

import type { NavLink, SocialLink } from "@/types";

// Re-export SocialLink for backward compatibility
export type { SocialLink } from "@/types";

// ─── Navigation ───────────────────────────────────────────────────────────────

/** Top-level navigation links — used by Sidebar, Header, and Footer */
export const navLinks: NavLink[] = [
    { href: "/", label: "Home", icon: "home" },
];

/** Social / external links — used by Footer */
export const socialLinks: SocialLink[] = [
    { href: "https://github.com", label: "GitHub", icon: "Github" },
    { href: "https://twitter.com", label: "Twitter", icon: "Twitter" },
    { href: "https://linkedin.com", label: "LinkedIn", icon: "Linkedin" },
];
