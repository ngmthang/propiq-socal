import {useState} from "react";
import {Link, useNavigate} from "react-router-dom";
import {useAuth} from "../context/AuthContext.jsx";
import {authApi} from "../api/client.js";

export default function Register() {
    const [fullName, setFullName] = useState("");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const {login} = useAuth();
    const navigate = useNavigate();

    async function handleSubmit(e) {
        e.preventDefault();
        setError("");
        setLoading(true);
        try {
            const res = await authApi.register({
                full_name: fullName, email, password,
            });
            login(res.data.access_token, res.data.user);
            navigate("/", {replace: true});
        } catch(err) {
            if (err.response?.status === 409) {
                setError("An account with this email already exists.");
            } else if (err.response?.status === 422) {
                setError("Password must be at least 8 characters.");
            } else {
                setError("Couldn't reach PropIQ - is the API running?");
            }
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="flex min-h-screen items-center justify-center px-4">
            <div className="w-full max-w-sm">
                <div className="mb-8 flex items-center gap-2">
                    <div className="flex h-9 w-9 items-center justify-center
                                    rounded-lg bg-terracotta font-display text-lg font-semibold text-parchment"
                    >
                        P
                    </div>
                    <span className="font-display text-xl font-semibold">PropIQ</span>
                </div>

                <h1 className="mb-1 font-display text-2xl font-semibold">Create account</h1>
                <p className="mb-6 text-sm text-ink/60">
                    Southern California property intelligence.
                </p>

                <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
                    <div>
                        <label className="field-label" htmlFor="fullName">
                            Full name
                        </label>
                        <input
                            id="fullName"
                            type="text"
                            className="field-input"
                            value={fullName}
                            onChange={(e) => setFullName(e.target.value)}
                            placeholder="Jane Smith"
                            autoFocus
                            required
                        />
                    </div>

                    <div>
                        <label className="field-label" htmlFor="email">
                            Email
                        </label>
                        <input
                            id="email"
                            type="email"
                            className="field-input"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            placeholder="you@example.com"
                            required
                        />
                    </div>

                    <div>
                        <label className="field-label" htmlFor="password">
                            Password
                        </label>
                        <input
                            id="password"
                            type="password"
                            className="field-input"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder="At least 8 characters"
                            minLength={8}
                            required
                        />
                    </div>

                    {error && <p className="text-sm text-clay">{error}</p>}

                    <button type="submit" disabled={loading} className="btn-primary w-full">
                        {loading ? "Creating account..." : "Create account"}
                    </button>

                    <p className="text-center text-sm text-ink/60">
                        Already have an account?{" "}
                        <Link to="/login" className="text-terracotta hover:underline">
                            Sign in
                        </Link>
                    </p>
                </form>
            </div>
        </div>
    );
}