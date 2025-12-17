import { useState, useRef } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Upload, FileText, ArrowRight } from 'lucide-react'
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
    <Card className="overflow-hidden">
      <CardHeader className="bg-gradient-to-r from-uic-blue to-blue-800 text-white">
        <CardTitle className="flex items-center gap-3 text-white">
          <div className="p-2 bg-white/20 rounded-lg">
            <Upload className="h-5 w-5" />
          </div>
          Upload PDF Document
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6 p-8">
        <div className="flex flex-col gap-4">
          <label className="w-full border-2 border-dashed border-gray-300 rounded-xl p-8 hover:border-uic-blue/50 transition-colors bg-gray-50/50 cursor-pointer flex flex-col items-center justify-center gap-4">
            <div className="p-4 bg-uic-blue/10 rounded-full">
              <Upload className="h-8 w-8 text-uic-blue" />
            </div>
            <div className="text-center">
              <p className="text-lg font-semibold text-uic-blue">Click to choose a file</p>
              <p className="text-sm text-gray-500 mt-1">or drag and drop a PDF here</p>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              disabled={isUploading}
              className="sr-only"
            />
          </label>
          {file && (
            <div className="flex items-center gap-3 text-base text-gray-700 bg-green-50 border border-green-200 px-5 py-4 rounded-xl">
              <FileText className="h-6 w-6 text-green-600" />
              <span className="font-semibold">{file.name}</span>
              <span className="text-gray-500">({(file.size / 1024).toFixed(1)} KB)</span>
            </div>
          )}
        </div>

        <div className="flex items-center space-x-3 p-4 bg-gray-50 rounded-lg">
          <Checkbox
            id="skip-pii"
            checked={skipPiiScan}
            onCheckedChange={(checked) => setSkipPiiScan(checked === true)}
            disabled={isUploading}
            className="h-5 w-5"
          />
          <Label
            htmlFor="skip-pii"
            className="text-base font-medium text-gray-700 cursor-pointer"
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
          className="w-full h-14 text-lg"
          size="lg"
        >
          {isUploading ? (
            'Uploading...'
          ) : (
            <>
              Upload PDF
              <ArrowRight className="ml-2 h-6 w-6" />
            </>
          )}
        </Button>
      </CardContent>
    </Card>
  )
}
