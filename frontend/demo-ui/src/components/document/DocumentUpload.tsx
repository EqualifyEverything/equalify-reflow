import { useState, useRef } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Upload, FileText } from 'lucide-react'
import { api } from '@/lib/api'

interface DocumentUploadProps {
  onUploadSuccess: (jobId: string) => void
}

export function DocumentUpload({ onUploadSuccess }: DocumentUploadProps) {
  const [file, setFile] = useState<File | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [skipPiiScan, setSkipPiiScan] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleUpload = async () => {
    if (!file) return

    setIsUploading(true)
    setError(null)

    try {
      const response = await api.submitDocument(file, {
        skipPiiScan,
        skipReason: skipPiiScan ? 'Skipped via demo UI' : undefined,
      })
      onUploadSuccess(response.job_id)
      setFile(null)
      setSkipPiiScan(false)
      // Clear the file input element
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
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
            ref={fileInputRef}
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

        <div className="flex items-center space-x-2">
          <Checkbox
            id="skip-pii"
            checked={skipPiiScan}
            onCheckedChange={(checked) => setSkipPiiScan(checked === true)}
            disabled={isUploading}
          />
          <Label
            htmlFor="skip-pii"
            className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
          >
            Skip PII scan (for trusted documents)
          </Label>
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
