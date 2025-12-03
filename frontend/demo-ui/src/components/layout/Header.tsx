import { Badge } from '@/components/ui/badge'
import { ApiKeyInput } from '@/components/dev/ApiKeyInput'

export function Header() {
  return (
    <header className="bg-uic-navy text-white py-4 px-6 flex items-center justify-between shadow-md">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 bg-white rounded-full flex items-center justify-center">
            <span className="text-uic-navy font-bold text-xl">UIC</span>
          </div>
          <h1 className="text-2xl font-bold text-white">
            Equalify PDF Converter
          </h1>
        </div>
        <Badge variant="destructive" className="demo-badge">
          DEMO ONLY
        </Badge>
      </div>
      <div className="flex items-center gap-6">
        <ApiKeyInput />
        <div className="text-sm opacity-75">
          Developer Testing Interface
        </div>
      </div>
    </header>
  )
}
