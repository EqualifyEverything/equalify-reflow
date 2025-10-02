import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Upload, FileText } from 'lucide-react'
import { api } from '@/lib/api'

interface DocumentUploadProps {
  onUploadSuccess: (jobId: string) => void
}

export function DocumentUpload({ onUploadSuccess }: DocumentUploadProps) {
  const [file, setFile] = useState<File | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleUpload = async () => {
    if (!file) return

    setIsUploading(true)
    setError(null)

    try {
      const response = await api.submitDocument(file)
      onUploadSuccess(response.job_id)
      setFile(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setIsUploading(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-uic-navy flex items-center gap-2">
          <Upload className="h-6 w-6" />
          Upload PDF Document
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-4">
          <Input
            type="file"
            accept=".pdf"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            disabled={isUploading}
            className="flex-1"
          />
          {file && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <FileText className="h-4 w-4" />
              {file.name} ({(file.size / 1024).toFixed(1)} KB)
            </div>
          )}
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button
          onClick={handleUpload}
          disabled={!file || isUploading}
          className="bg-uic-red hover:bg-uic-red/90 w-full"
        >
          {isUploading ? 'Uploading...' : 'Upload PDF'}
        </Button>
      </CardContent>
    </Card>
  )
}
