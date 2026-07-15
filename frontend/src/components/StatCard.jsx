export default function StatCard({label, value, sub}) {
    return (
        <div className="panel p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink/45">
                {label}
            </p>
            <p className="mt-1.5 font-display text-2xl font-semibold text-ink">
                {value}
            </p>
            {sub && <p className="mt-0.5 text-xs text-ink/50">{sub}</p>}
        </div>
    );
}