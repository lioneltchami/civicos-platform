/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        navy: {
          900: '#060d1f',
          800: '#0a1628',
          700: '#0f1f38',
          600: '#162847',
        },
        brand: {
          500: '#2563eb',
          400: '#3b82f6',
          300: '#93c5fd',
        },
      },
    },
  },
  plugins: [],
}
