"""Local document reading in a time-bounded child process. No provider calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def capabilities(state):
    mode=os.getenv('RECORD_DESK_READER','auto')
    native=sys.platform=='darwin' and (Path(state)/'ocr').is_file()
    backend='native' if mode=='native' or (mode=='auto' and native) else 'portable'
    pdf=importlib.util.find_spec('pypdfium2') is not None
    images=importlib.util.find_spec('PIL') is not None
    tess=bool(shutil.which('tesseract'))
    return dict(backend=backend,pdf_text_ready=native if backend=='native' else pdf,
                image_ocr_ready=native if backend=='native' else images and tess,
                scanned_pdf_ready=native if backend=='native' else pdf and images and tess)


def read(path,state):
    mode=os.getenv('RECORD_DESK_READER','auto')
    if mode not in ('auto','native','portable'):raise ValueError('Reader must be auto, native, or portable.')
    info=capabilities(state)
    if info['backend']=='native':
        if sys.platform!='darwin' or not (Path(state)/'ocr').is_file():
            raise ValueError('Native reader requires macOS and ./start.sh.')
        command=[str(Path(state)/'ocr'),str(path)]
    else:
        command=[sys.executable,str(Path(__file__).resolve()),str(path)]
    try:
        process=subprocess.run(command,capture_output=True,timeout=90)
    except subprocess.TimeoutExpired:
        raise ValueError('Document reading exceeded 90 seconds. Try a smaller document.') from None
    if process.returncode:
        try:message=json.loads(process.stdout).get('error')
        except (ValueError,AttributeError):message=None
        raise ValueError(message or 'Document reading failed. Check the file and local reader setup.')
    try:
        pages=json.loads(process.stdout)['pages']
        if not isinstance(pages,list) or not pages or len(pages)>20 or any(not isinstance(p,str) for p in pages):raise ValueError()
        if sum(map(len,pages))>35000:raise ValueError()
        return pages
    except (KeyError,TypeError,ValueError):
        raise ValueError('Reader output is invalid or exceeds 35,000 characters. Use a smaller document.') from None


def ocr(image):
    from PIL import ImageOps
    if not shutil.which('tesseract'):raise ValueError('Install Tesseract and add it to PATH for image or scanned-PDF reading.')
    if image.width*image.height>40_000_000:raise ValueError('Image exceeds 40 million pixels.')
    with tempfile.TemporaryDirectory(prefix='record-desk-ocr-') as temp:
        path=Path(temp)/'page.png'
        corrected=ImageOps.exif_transpose(image).convert('RGB')
        try:corrected.save(path)
        finally:corrected.close()
        process=subprocess.run(['tesseract',str(path),'stdout','-l',os.getenv('RECORD_DESK_OCR_LANG','eng')],capture_output=True,timeout=30)
        if process.returncode:raise ValueError('Tesseract could not read the page. Check installed language data and file quality.')
        return process.stdout.decode('utf-8',errors='replace')


def portable(path):
    path=Path(path)
    if path.stat().st_size>25*1024*1024:raise ValueError('Document exceeds 25 MB.')
    if path.suffix.lower()=='.heic':raise ValueError('Portable HEIC reading is not supported. Export as PNG or JPEG, or use the Mac reader.')
    pages=[]
    if path.suffix.lower()=='.pdf':
        try:import pypdfium2 as pdfium
        except ImportError:raise ValueError('Install requirements-documents.txt to read PDFs locally.') from None
        with pdfium.PdfDocument(path) as document:
            if not 1<=len(document)<=20:raise ValueError('Use PDFs with 1–20 pages. Split larger bundles first.')
            for page in document:
                try:
                    textpage=page.get_textpage()
                    try:text=textpage.get_text_bounded()
                    finally:textpage.close()
                    if not text.strip():
                        width,height=page.get_size()
                        if width<=0 or height<=0:raise ValueError('Invalid PDF page dimensions.')
                        scale=min(2.5,2800/max(width,height))
                        bitmap=page.render(scale=scale)
                        try:
                            with bitmap.to_pil() as image:text=ocr(image)
                        finally:bitmap.close()
                    pages.append(text)
                    if sum(map(len,pages))>35000:raise ValueError('Extracted text exceeds 35,000 characters.')
                finally:page.close()
    else:
        if path.suffix.lower() not in ('.png','.jpg','.jpeg'):raise ValueError('Unsupported portable document format.')
        try:from PIL import Image
        except ImportError:raise ValueError('Install requirements-documents.txt for image reading.') from None
        with Image.open(path) as image:
            if getattr(image,'n_frames',1)>1:raise ValueError('Use single-frame images.')
            pages=[ocr(image)]
    return pages


if __name__=='__main__':
    try:print(json.dumps(dict(pages=portable(sys.argv[1]))))
    except ValueError as error:
        print(json.dumps(dict(error=str(error))));sys.exit(1)
    except Exception:
        print(json.dumps(dict(error='Local reader could not process this file. Check dependencies, encryption, and file format.')));sys.exit(1)
