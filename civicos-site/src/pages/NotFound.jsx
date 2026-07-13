import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <div className="bg-[#060d1f] min-h-screen flex items-center justify-center px-6 pt-16">
      <div className="text-center max-w-md">
        <p className="text-8xl font-black text-white/5 mb-2 select-none">404</p>
        <h1 className="text-3xl font-black text-white mb-3 -mt-8">Page not found</h1>
        <p className="text-white/50 mb-8 leading-relaxed">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="flex flex-wrap gap-3 justify-center">
          <Link to="/"
            className="px-6 py-3 bg-blue-600 hover:bg-blue-500 text-white font-semibold rounded-xl transition-all hover:shadow-lg hover:shadow-blue-500/25">
            Go home
          </Link>
          <Link to="/building-blocks"
            className="px-6 py-3 bg-white/8 hover:bg-white/14 text-white font-semibold rounded-xl border border-white/12 transition-all">
            Building Blocks
          </Link>
        </div>
      </div>
    </div>
  )
}
