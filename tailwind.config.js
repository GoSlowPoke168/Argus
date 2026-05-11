/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        darkBase: '#0a0a0f',
        hackerGreen: '#00ff41',
        alertRed: '#ff3333',
        cyberBlue: '#00e5ff'
      }
    },
  },
  plugins: [],
}
