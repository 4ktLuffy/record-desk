import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
import document_reader as reader

DOCUMENT_DEPS=all(importlib.util.find_spec(m) is not None for m in ('pypdfium2','PIL','reportlab'))


class ReaderProtocolTests(unittest.TestCase):
    def test_timeout_is_actionable(self):
        with patch.dict('os.environ',{'RECORD_DESK_READER':'portable'}),patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired('reader',90)):
            with self.assertRaisesRegex(ValueError,'90 seconds'):reader.read('example.pdf',Path('.'))

    def test_bad_output_rejected(self):
        process=subprocess.CompletedProcess([],0,stdout=b'{"pages": [9]}')
        with patch.dict('os.environ',{'RECORD_DESK_READER':'portable'}),patch.object(subprocess,'run',return_value=process):
            with self.assertRaisesRegex(ValueError,'invalid'):reader.read('example.pdf',Path('.'))


@unittest.skipUnless(DOCUMENT_DEPS,'Install optional reader packages and reportlab for integration tests')
class PortableReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.state=patch.object(core,'STATE',self.root/'state');self.state.start();core.init()
        self.mode=patch.dict('os.environ',{'RECORD_DESK_READER':'portable'});self.mode.start()

    def tearDown(self):
        self.mode.stop();self.state.stop();self.temp.cleanup()

    def pdf(self,pages=1):
        from reportlab.pdfgen.canvas import Canvas
        path=self.root/'synthetic.pdf';canvas=Canvas(str(path))
        for _ in range(pages):
            canvas.drawString(72,700,'SYNTHETIC INVOICE X-17 TOTAL USD 115.00');canvas.showPage()
        canvas.save();return path

    def test_pdf_text_and_local_read_never_calls_provider(self):
        path=self.pdf();ident=core.add(path)[0]
        with patch.object(core,'llm',side_effect=AssertionError('Unexpected provider call')):
            result=core.read_local(ident)
        self.assertIn('USD 115.00',result['raw'])
        self.assertEqual(result['status'],'imported')
        self.assertEqual(result['fields'],{})

    def test_page_limit(self):
        with self.assertRaisesRegex(ValueError,'20 pages'):reader.portable(self.pdf(21))

    def test_source_change_is_blocked(self):
        path=self.pdf();ident=core.add(path)[0];path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Source changed'):core.read_local(ident)

    @unittest.skipUnless(reader.shutil.which('tesseract'),'Tesseract is not installed')
    def test_printed_image_and_scanned_pdf(self):
        from PIL import Image, ImageDraw, ImageFont
        from reportlab.pdfgen.canvas import Canvas
        image=Image.new('RGB',(1000,220),'white')
        ImageDraw.Draw(image).text((30,60),'INVOICE X17 TOTAL USD 115.00',font=ImageFont.load_default(size=40),fill='black')
        path=self.root/'scan.png';image.save(path);image.close()
        self.assertIn('115.00',reader.read(path,core.STATE)[0])
        pdf=self.root/'scan.pdf';canvas=Canvas(str(pdf),pagesize=(1000,220));canvas.drawImage(str(path),0,0,width=1000,height=220);canvas.save()
        self.assertIn('115.00',reader.read(pdf,core.STATE)[0])
