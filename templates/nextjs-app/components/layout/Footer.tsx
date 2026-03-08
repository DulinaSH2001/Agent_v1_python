/**
 * @component Footer
 * @description Site footer with navigation links, social links, and copyright.
 * Renders a responsive two-column layout: nav links on the left, social icons on the right.
 *
 * @example
 * ```tsx
 * // Basic usage — uses default navLinks and socialLinks from lib/data
 * import { Footer } from "@/components/layout/Footer";
 *
 * export default function Layout({ children }) {
 *   return (
 *     <div className="flex flex-col min-h-screen">
 *       <main className="flex-1">{children}</main>
 *       <Footer />
 *     </div>
 *   );
 * }
 *
 * // Custom links
 * <Footer
 *   navLinks={[
 *     { href: "/about", label: "About" },
 *     { href: "/blog", label: "Blog" },
 *     { href: "/contact", label: "Contact" },
 *   ]}
 *   socialLinks={[
 *     { href: "https://github.com/myorg", label: "GitHub", icon: "Github" },
 *   ]}
 *   companyName="Acme Corp"
 * />
 * ```
 */

import Link from "next/link";
import { Github, Twitter, Linkedin, ExternalLink } from "lucide-react";
import { navLinks as defaultNavLinks, socialLinks as defaultSocialLinks } from "@/lib/data";
import type { SocialLink } from "@/lib/data";
import type { NavLink } from "@/types";
import { cn } from "@/lib/utils";

const socialIconMap: Record<string, React.ComponentType<{ className?: string }>> = {
    Github,
    Twitter,
    Linkedin,
};

interface FooterProps {
    navLinks?: NavLink[];
    socialLinks?: SocialLink[];
    companyName?: string;
    className?: string;
}

/**
 * Site footer component.
 * Accepts optional overrides for navLinks, socialLinks, and companyName.
 */
export function Footer({
    navLinks = defaultNavLinks,
    socialLinks = defaultSocialLinks,
    companyName = "Your Company",
    className,
}: FooterProps) {
    const currentYear = new Date().getFullYear();

    return (
        <footer className={cn("border-t bg-background", className)}>
            <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
                <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
                    {/* Nav links */}
                    <nav className="flex flex-wrap justify-center gap-x-6 gap-y-2 sm:justify-start">
                        {navLinks.map((link) => (
                            <Link
                                key={link.href}
                                href={link.href}
                                className="text-sm text-muted-foreground transition-colors hover:text-foreground"
                            >
                                {link.label}
                            </Link>
                        ))}
                    </nav>

                    {/* Social links */}
                    <div className="flex items-center gap-3">
                        {socialLinks.map((link) => {
                            const Icon = socialIconMap[link.icon] ?? ExternalLink;
                            return (
                                <a
                                    key={link.href}
                                    href={link.href}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    aria-label={link.label}
                                    className="text-muted-foreground transition-colors hover:text-foreground"
                                >
                                    <Icon className="h-4 w-4" />
                                </a>
                            );
                        })}
                    </div>
                </div>

                {/* Copyright */}
                <p className="mt-6 text-center text-xs text-muted-foreground sm:text-left">
                    &copy; {currentYear} {companyName}. All rights reserved.
                </p>
            </div>
        </footer>
    );
}
