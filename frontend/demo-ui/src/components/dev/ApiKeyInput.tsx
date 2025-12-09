import { useState, useEffect } from 'react'
import { Key, Check, Eye, EyeOff } from 'lucide-react'
import { setApiKey } from '@/lib/api'

export function ApiKeyInput() {
  const [key, setKey] = useState('')
  const [saved, setSaved] = useState(false)
  const [showKey, setShowKey] = useState(false)

  useEffect(() => {
    // Load existing key from localStorage on mount
    const existingKey = localStorage.getItem('api_key') || ''
    setKey(existingKey)
  }, [])

  const handleSave = () => {
    setApiKey(key)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSave()
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Key className="w-4 h-4 text-white/70" />
      <div className="relative">
        <input
          type={showKey ? 'text' : 'password'}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="API Key"
          className="bg-white/10 text-white text-sm px-3 py-1.5 rounded border border-white/20
                     placeholder:text-white/50 focus:outline-none focus:border-white/50 w-40"
        />
        <button
          onClick={() => setShowKey(!showKey)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-white/50 hover:text-white/80"
        >
          {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
        </button>
      </div>
      <button
        onClick={handleSave}
        className={`text-sm px-3 py-1.5 rounded transition-colors ${
          saved
            ? 'bg-green-500 text-white'
            : 'bg-white/20 text-white hover:bg-white/30'
        }`}
      >
        {saved ? <Check className="w-4 h-4" /> : 'Save'}
      </button>
    </div>
  )
}
