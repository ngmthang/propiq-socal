/** @type {import('tailwindcss').Config} */
export default {
    content: ["./index.html", "./src/**/*.{js,jsx}"],
    theme: {
        extend: {
            colors: {
                parchment: "#F6F1E4",
                ink: "#211E19",
                terracotta: {
                    DEFAULT: "#A6461F",
                    dark: "#7F3417",
                    light: "#C77748",
                },
                marine: {
                    DEFAULT: "#2B5C63",
                    dark: "#1E4247",
                    light: "#4C7E85",
                },
                sage: {
                    DEFAULT: "#6B7A56",
                    light: "#8B9974",
                },
                clay: "#B23B2E",
                line: "#DED4BC",
            },
            fontFamily: {
                display: ["Fraunces", "serif"],
                body: ["Public Sans", "system-ui", "sans-serif"],
                mono: ["IBM Plex Mono", "monospace"],
            },
            backgroundImage: {
                blueprint:
                    "linear-gradient(rgba(33,30,25,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(33,30,25,0.05) 1px, transparent 1px)"
            },
            backgroundSize: {
                grid: "28px 28px",
            },
        },
    },
    plugins: [],
};