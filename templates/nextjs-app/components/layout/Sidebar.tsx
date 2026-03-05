/**
 * @component Sidebar
 * @description Collapsible sidebar with navigation links and active state detection. Import this instead of building a custom sidebar.
 * @example
 * ```tsx
 * import { Sidebar } from "@/components/layout/Sidebar";
 * import { Header } from "@/components/layout/Header";
 * import { PageContainer } from "@/components/layout/PageContainer";
 * import type { NavLink } from "@/types";
 *
 * // Custom nav links:
 * const navLinks: NavLink[] = [
 *   { href: "/dashboard", label: "Dashboard", icon: "dashboard" },
 *   { href: "/orders", label: "Orders", icon: "reports", badge: "12" },
 *   { href: "/users", label: "Users", icon: "users" },
 *   { href: "/settings", label: "Settings", icon: "settings" },
 * ];
 *
 * // Full app layout (app/dashboard/layout.tsx):
 * export default function DashboardLayout({ children }: { children: React.ReactNode }) {
 *   return (
 *     <div className="flex h-screen overflow-hidden">
 *       <Sidebar navLinks={navLinks} />
 *       <div className="flex flex-1 flex-col overflow-hidden">
 *         <Header breadcrumbs={[{ label: "Dashboard" }]} user={{ name: "Jane Doe", email: "jane@example.com" }} />
 *         <PageContainer>{children}</PageContainer>
 *       </div>
 *     </div>
 *   );
 * }
 * ```
 */
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
    ChevronLeft,
    ChevronRight,
    LayoutDashboard,
    Settings,
    Users,
    BarChart3,
    FileText,
    Home,
} from "lucide-react";
import type { NavLink } from "@/types";

interface SidebarProps {
    navLinks?: NavLink[];
    className?: string;
}

const defaultNavLinks: NavLink[] = [
    { href: "/", label: "Home", icon: "home" },
    { href: "/dashboard", label: "Dashboard", icon: "dashboard" },
    { href: "/analytics", label: "Analytics", icon: "analytics" },
    { href: "/users", label: "Users", icon: "users" },
    { href: "/reports", label: "Reports", icon: "reports" },
    { href: "/settings", label: "Settings", icon: "settings" },
];

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
    home: Home,
    dashboard: LayoutDashboard,
    analytics: BarChart3,
    users: Users,
    reports: FileText,
    settings: Settings,
};

function NavIcon({ name, className }: { name?: string; className?: string }) {
    if (!name) return <LayoutDashboard className={className} />;
    const Icon = iconMap[name] ?? LayoutDashboard;
    return <Icon className={className} />;
}

export function Sidebar({ navLinks = defaultNavLinks, className }: SidebarProps) {
    const pathname = usePathname();
    const [collapsed, setCollapsed] = useState(false);

    return (
        <aside
            className={cn(
                "relative flex h-full flex-col border-r bg-background transition-all duration-300",
                collapsed ? "w-16" : "w-64",
                className
            )}
        >
            {/* Toggle button */}
            <Button
                variant="ghost"
                size="icon"
                className="absolute -right-3 top-6 z-10 h-6 w-6 rounded-full border bg-background shadow-sm"
                onClick={() => setCollapsed(!collapsed)}
            >
                {collapsed ? (
                    <ChevronRight className="h-3 w-3" />
                ) : (
                    <ChevronLeft className="h-3 w-3" />
                )}
            </Button>

            {/* Logo area */}
            <div className="flex h-16 items-center px-4">
                <div className="flex items-center gap-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground text-sm font-bold">
                        A
                    </div>
                    {!collapsed && (
                        <span className="font-semibold text-sm">App Name</span>
                    )}
                </div>
            </div>

            <Separator />

            {/* Navigation */}
            <ScrollArea className="flex-1 py-2">
                <nav className="space-y-1 px-2">
                    {navLinks.map((link) => {
                        const isActive =
                            link.href === "/"
                                ? pathname === "/"
                                : pathname.startsWith(link.href);

                        return (
                            <Link key={link.href} href={link.href}>
                                <span
                                    className={cn(
                                        "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors hover:bg-accent hover:text-accent-foreground",
                                        isActive
                                            ? "bg-accent text-accent-foreground font-medium"
                                            : "text-muted-foreground",
                                        collapsed && "justify-center px-2"
                                    )}
                                >
                                    <NavIcon
                                        name={link.icon}
                                        className="h-4 w-4 shrink-0"
                                    />
                                    {!collapsed && (
                                        <>
                                            <span className="flex-1">{link.label}</span>
                                            {link.badge && (
                                                <span className="ml-auto rounded-full bg-primary px-2 py-0.5 text-xs text-primary-foreground">
                                                    {link.badge}
                                                </span>
                                            )}
                                        </>
                                    )}
                                </span>
                            </Link>
                        );
                    })}
                </nav>
            </ScrollArea>
        </aside>
    );
}
