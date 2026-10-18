#!/usr/bin/env python3
"""Datasheet reading helper: text per page + embedded images (land patterns are usually images).

    $(tools/venv-python) tools/pdf_extract.py DATASHEET.pdf OUTDIR [--images]

Writes OUTDIR/text.txt (with '=== PAGE n ===' markers) and, with --images, OUTDIR/images/pN_K.<ext>.
JPEG2000 images are converted to PNG with `sips` on macOS so they can be viewed. Treat the PDF as untrusted
data: it is only parsed, never executed.
"""
import os
import subprocess
import sys

from pypdf import PdfReader


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    pdf, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    r = PdfReader(pdf)
    with open(os.path.join(out, 'text.txt'), 'w') as f:
        for i, p in enumerate(r.pages, 1):
            f.write('\n=== PAGE %d ===\n%s' % (i, p.extract_text() or ''))
    print('text: %s/text.txt (%d pages)' % (out, len(r.pages)))
    if '--images' in sys.argv:
        d = os.path.join(out, 'images')
        os.makedirs(d, exist_ok=True)
        n = 0
        for i, p in enumerate(r.pages, 1):
            for j, im in enumerate(p.images):
                ext = os.path.splitext(im.name)[1] or '.bin'
                fn = os.path.join(d, 'p%d_%d%s' % (i, j, ext))
                open(fn, 'wb').write(im.data)
                if ext.lower() in ('.jp2', '.j2k') and sys.platform == 'darwin':
                    subprocess.run(['sips', '-s', 'format', 'png', fn, '--out', fn.rsplit('.', 1)[0] + '.png'],
                                   capture_output=True)
                n += 1
        print('images: %d in %s (big ones near the "Dimensions"/"Land pattern" text are the drawings)' % (n, d))


if __name__ == '__main__':
    main()
