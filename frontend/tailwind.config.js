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
        background: "rgb(var(--md-sys-color-surface-rgb) / <alpha-value>)",
        foreground: "rgb(var(--md-sys-color-on-surface-rgb) / <alpha-value>)",
        primary: {
          DEFAULT: "rgb(var(--md-sys-color-primary-rgb) / <alpha-value>)",
          foreground: "rgb(var(--md-sys-color-on-primary-rgb) / <alpha-value>)",
          container: "rgb(var(--md-sys-color-primary-container-rgb) / <alpha-value>)",
          "on-container": "rgb(var(--md-sys-color-on-primary-container-rgb) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "rgb(var(--md-sys-color-secondary-rgb) / <alpha-value>)",
          foreground: "rgb(var(--md-sys-color-on-secondary-rgb) / <alpha-value>)",
          container: "rgb(var(--md-sys-color-secondary-container-rgb) / <alpha-value>)",
          "on-container": "rgb(var(--md-sys-color-on-secondary-container-rgb) / <alpha-value>)",
        },
        tertiary: {
          DEFAULT: "rgb(var(--md-sys-color-tertiary-rgb) / <alpha-value>)",
          container: "rgb(var(--md-sys-color-tertiary-container-rgb) / <alpha-value>)",
          "on-container": "rgb(var(--md-sys-color-on-tertiary-container-rgb) / <alpha-value>)",
        },
        error: {
          DEFAULT: "rgb(var(--md-sys-color-error-rgb) / <alpha-value>)",
          foreground: "rgb(var(--md-sys-color-on-error-rgb) / <alpha-value>)",
          container: "rgb(var(--md-sys-color-error-container-rgb) / <alpha-value>)",
          "on-container": "rgb(var(--md-sys-color-on-error-container-rgb) / <alpha-value>)",
        },
        surface: {
          DEFAULT: "rgb(var(--md-sys-color-surface-rgb) / <alpha-value>)",
          lowest: "rgb(var(--md-sys-color-surface-container-lowest-rgb) / <alpha-value>)",
          low: "rgb(var(--md-sys-color-surface-container-low-rgb) / <alpha-value>)",
          container: "rgb(var(--md-sys-color-surface-container-rgb) / <alpha-value>)",
          high: "rgb(var(--md-sys-color-surface-container-high-rgb) / <alpha-value>)",
          highest: "rgb(var(--md-sys-color-surface-container-highest-rgb) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "rgb(var(--md-sys-color-surface-container-rgb) / <alpha-value>)",
          foreground: "rgb(var(--md-sys-color-on-surface-variant-rgb) / <alpha-value>)",
        },
        border: "rgb(var(--md-sys-color-outline-variant-rgb) / <alpha-value>)",
        outline: "rgb(var(--md-sys-color-outline-rgb) / <alpha-value>)",
      },
    },
  },
  plugins: [],
};
