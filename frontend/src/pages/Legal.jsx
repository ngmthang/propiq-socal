import {Link} from "react-router-dom";

export default function Legal() {
    return (
        <div className="mx-auto max-w-2xl px-6 py-10">
            <Link to="/" className="text-sm text-ink/50
                hover:text-ink"
            >
                ← Back
            </Link>

            <h1 className="mb-6 mt-2 font-display text-2xl
                font-semibold"
            >
                Important disclaimers
            </h1>

            <div className="space-y-6 text-sm leading-relaxed
                text-ink/70"
            >
                <section>
                    <h2 className="mb-1 font-semibold text-ink">
                        Demo data
                    </h2>
                    <p>
                        PropIQ is currently running on synthetically generated
                        property and sales data for demonstration purposes.
                        Addresses, prices, and transaction histories shown do
                        not represent real properties or real sales.
                    </p>
                </section>

                <section>
                    <h2 className="mb-1 font-semibold text-ink">
                        Not an appraisal
                    </h2>
                    <p>
                        Property valuations shown ("predicted value",
                        "current estimate", deal scores, and similar figures)
                        are outputs of an automated statistical model. They
                        are estimates only, are not appraisals, and are not a
                        substitute for a licensed appraiser, real estate
                        agent, or financial advisor. Actual sale prices can
                        differ substantially from model estimates.
                    </p>
                </section>

                <section>
                    <h2 className="mb-1 font-semibold text-ink">
                        Not financial advice
                    </h2>
                    <p>
                        Nothing on this site constitutes financial, legal, or
                        investment decisions based solely on figures shown
                        here. Consult a qualified professional before making
                        any real estate transaction.
                    </p>
                </section>

                <section>
                    <h2 className="mb-1 font-semibold text-ink">
                        Market forecasts
                    </h2>
                    <p>
                        Price trend forecasts, where shown, are model
                        projections based on historical patterns and carry on
                        guarantee of future accuracy.
                    </p>
                </section>
            </div>
        </div>
    );
}