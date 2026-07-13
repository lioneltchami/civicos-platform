import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-[#060d1f] flex items-center justify-center px-6">
          <div className="text-center max-w-md">
            <p className="text-5xl mb-6">⚠️</p>
            <h1 className="text-2xl font-black text-white mb-3">Something went wrong</h1>
            <p className="text-white/50 mb-8 text-sm leading-relaxed">
              An unexpected error occurred. Please refresh the page or contact us if the problem persists.
            </p>
            <button
              onClick={() => window.location.reload()}
              className="px-6 py-3 bg-blue-600 hover:bg-blue-500 text-white font-semibold rounded-xl transition-all"
            >
              Refresh page
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
