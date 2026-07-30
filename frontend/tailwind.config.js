/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Backgrounds
        bg: "#07070a",
        "bg-deep": "#050507",
        surface: "#0d0d11",
        "surface-elevated": "#14141a",
        "surface-hover": "#1c1c24",
        // Borders
        border: "#1e1e28",
        "border-bright": "#2e2e3a",
        // Text
        text: "#f5f5f7",
        "text-secondary": "#a0a0aa",
        "text-muted": "#5a5a66",
        // Accents (teal + amber from portfolio-v2)
        accent: "hsl(172, 72%, 44%)",
        "accent-hover": "hsl(172, 72%, 52%)",
        "accent-warm": "hsl(38, 88%, 60%)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        serif: ["Georgia", "Times New Roman", "serif"],
      },
      borderRadius: {
        sm: "8px",
        md: "12px",
        lg: "16px",
        xl: "20px",
        "2xl": "24px",
      },
      animation: {
        "pulse-glow": "pulse-glow 3s ease-in-out infinite",
        "fade-in": "fade-in 0.5s ease-out",
        "slide-up": "slide-up 0.4s ease-out",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": { opacity: "0.5" },
          "50%": { opacity: "1" },
        },
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
