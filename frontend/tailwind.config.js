/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      borderRadius: {
        xs: "var(--md-sys-shape-corner-xs)",
        sm: "var(--md-sys-shape-corner-sm)",
        md: "var(--md-sys-shape-corner-md)",
        lg: "var(--md-sys-shape-corner-lg)",
        xl: "var(--md-sys-shape-corner-xl)",
        full: "var(--md-sys-shape-corner-full)",
      },
      colors: {
        background: "var(--md-sys-color-surface)",
        foreground: "var(--md-sys-color-on-surface)",
        primary: {
          DEFAULT: "var(--md-sys-color-primary)",
          foreground: "var(--md-sys-color-on-primary)",
          container: "var(--md-sys-color-primary-container)",
          "on-container": "var(--md-sys-color-on-primary-container)",
        },
        secondary: {
          DEFAULT: "var(--md-sys-color-secondary)",
          foreground: "var(--md-sys-color-on-secondary)",
          container: "var(--md-sys-color-secondary-container)",
          "on-container": "var(--md-sys-color-on-secondary-container)",
        },
        tertiary: {
          DEFAULT: "var(--md-sys-color-tertiary)",
          container: "var(--md-sys-color-tertiary-container)",
          "on-container": "var(--md-sys-color-on-tertiary-container)",
        },
        error: {
          DEFAULT: "var(--md-sys-color-error)",
          foreground: "var(--md-sys-color-on-error)",
          container: "var(--md-sys-color-error-container)",
          "on-container": "var(--md-sys-color-on-error-container)",
        },
        surface: {
          DEFAULT: "var(--md-sys-color-surface)",
          lowest: "var(--md-sys-color-surface-container-lowest)",
          low: "var(--md-sys-color-surface-container-low)",
          container: "var(--md-sys-color-surface-container)",
          high: "var(--md-sys-color-surface-container-high)",
          highest: "var(--md-sys-color-surface-container-highest)",
        },
        muted: {
          DEFAULT: "var(--md-sys-color-surface-container)",
          foreground: "var(--md-sys-color-on-surface-variant)",
        },
        border: "var(--md-sys-color-outline-variant)",
        outline: "var(--md-sys-color-outline)",
      },
    },
  },
  plugins: [],
};
