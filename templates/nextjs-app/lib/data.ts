/**
 * App Data
 *
 * Central location for static site data: navigation links, social links, etc.
 * Replace or extend these with your application's real data.
 */

import type { NavLink } from "@/types";

// ─── Navigation ───────────────────────────────────────────────────────────────

/** Top-level navigation links — used by Sidebar, Header, and Footer */
export const navLinks: NavLink[] = [
    { href: "/", label: "Dashboard", icon: "LayoutDashboard" },
    { href: "/analytics", label: "Analytics", icon: "BarChart3" },
    { href: "/users", label: "Users", icon: "Users" },
    { href: "/settings", label: "Settings", icon: "Settings" },
];

// ─── Social Links ─────────────────────────────────────────────────────────────

export interface SocialLink {
    href: string;
    label: string;
    /** lucide icon name */
    icon: string;
}

/** Social / external links — used by Footer */
export const socialLinks: SocialLink[] = [
    { href: "https://github.com", label: "GitHub", icon: "Github" },
    { href: "https://twitter.com", label: "Twitter", icon: "Twitter" },
    { href: "https://linkedin.com", label: "LinkedIn", icon: "Linkedin" },
];
