import {useState} from "react";
import {Link, useNavigate} from "react-router-dom";
import {useAuth} from "../context/AuthContext.jsx";
import {authApi} from "../api/client.js";

export default function Login() {
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
            const res = await authApi.login({email, password});
            login(res.data.access_token, res.data.user);
            navigate("/", {replace: true});
        } catch(err) {
            setError(
                err.response?.status === 401
                    ? "Incorrect email or password."
                    : "Couldn't reach PropIQ - is the API running?"
            );
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

                <h1 className="mb-1 font-display text-2xl font-semibold">Sign in</h1>
                <p className="mb-6 text-sm text-ink/60">
                    Southern California property intelligence.
                </p>

                <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
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
                            autoFocus
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
                            placeholder="********"
                            required
                        />
                    </div>

                    {error && <p className="text-sm text-clay">{error}</p>}

                    <button type="submit" disabled={loading} className="btn-primary w-full">
                        {loading ? "Signing in..." : "Sign in"}
                    </button>

                    <p className="text-center text-sm text-ink/60">
                        No account?{" "}
                        <Link to="/register" className="text-terracotta hover:underline">
                            Create one
                        </Link>
                    </p>
                </form>
            </div>
        </div>
    );
}