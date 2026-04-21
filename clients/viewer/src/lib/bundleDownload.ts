import JSZip from 'jszip';

import type { PipelineViewerResult } from '@/types/pipeline-viewer';

/**
 * Build a zip containing the selected version's markdown plus a `figures/`
 * folder of PNGs. Markdown already references `figures/{ref_id}.png` relative
 * paths (see `src/services/pipeline_viewer.py::_replace_image_placeholders`),
 * so no rewriting is needed — the zip is self-contained as-is.
 */
export async function buildMarkdownBundle(
  result: PipelineViewerResult,
  version: string,
  baseName: string,
): Promise<Blob> {
  const markdown = result.versions[version];
  if (!markdown) {
    throw new Error(`No markdown for version "${version}"`);
  }

  const zip = new JSZip();
  zip.file(`${baseName}-${version}.md`, markdown);

  const figuresDir = zip.folder('figures');
  if (figuresDir) {
    for (const fig of result.figures) {
      if (!fig.image_base64) continue;
      figuresDir.file(`${fig.ref_id}.png`, fig.image_base64, { base64: true });
    }
  }

  return zip.generateAsync({ type: 'blob' });
}

export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
