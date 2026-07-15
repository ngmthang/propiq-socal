import { NavLink } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";

const links = [
    {to: "/", label: "Properties", end: true},
    {to: "/projects", label: "Projects"},
    {to: "/admin", label: "Admin"},
];

export default function AppShell({children}) {
    const {logout} = useAuth();

    return (
        <div className="flex min-h-screen">
            <aside className="flex w-60 shrink-0 flex-col justify-between border-r border-line bg-parchment/80 px-5 py-6">
                <div>
                    <div className="mb-8 flex items-center gap-2">
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-terracotta font-display text-base font-semibold text-parchment">
                            P
                        </div>
                        <span className="font-display text-lg font-semibold">PropIQ</span>
                    </div>

                    <nav className="flex flex-col gap-1">
                        {links.map((l) => (
                            <NavLink
                                key={l.to}
                            to={l.to}
                            end={l.end}
                            className={({isActive}) =>
                                `rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                                isActive
                                    ? "bg-terracotta/10 text-terracotta"
                                    : "text-ink/60 hover:bg-ink/5 hover:text-ink"
                                }`
                            }>
                                {l.label}
                            </NavLink>
                        ))}
                    </nav>
                </div>

                <button
                    onClick={logout}
                    className="rounded-lg px-3 py-2 text-left text-sm font-medium text-ink/50 transition-colors hover:bg-ink/5 hover:text-ink"
                >
                    Sign out
                </button>
            </aside>

            <main className="flex-1 overflow-y-auto">{children}</main>
        </div>
    );
}