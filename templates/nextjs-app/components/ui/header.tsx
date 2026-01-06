import Link from "next/link";
import { Button } from "./button";

export function Header() {
    return (
        <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
            <div className="container flex h-16 items-center justify-between">
                <div className="flex items-center gap-6 md:gap-10">
                    <Link href="/" className="flex items-center space-x-2">
                        <span className="inline-block font-bold text-xl">Antigravity</span>
                    </Link>
                    <nav className="hidden md:flex gap-6">
                        <Link href="/" className="text-sm font-medium transition-colors hover:text-primary">
                            Home
                        </Link>
                        <Link href="/products" className="text-sm font-medium text-muted-foreground transition-colors hover:text-primary">
                            Products
                        </Link>
                    </nav>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="ghost" size="sm">Log in</Button>
                    <Button size="sm">Get Started</Button>
                </div>
            </div>
        </header>
    );
}
