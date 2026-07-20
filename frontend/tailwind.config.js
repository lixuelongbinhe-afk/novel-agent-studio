/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#182026",
        mist: "#e9f0ef",
        brass: "#b88a44",
        tide: "#28666e",
        plum: "#6d4b63"
      }
    }
  },
  plugins: []
};

