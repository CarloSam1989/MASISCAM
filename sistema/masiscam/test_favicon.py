from pathlib import Path

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from PIL import Image


class FaviconTests(SimpleTestCase):
    def test_simbolo_original_sin_texto_y_formatos(self):
        original = Image.open(finders.find('masiscam/img/logo_transparente.png')).convert('RGBA')
        symbol = original.crop((145, 119, 644, 601))
        expected = Image.new('RGBA', (540, 540))
        expected.alpha_composite(symbol, ((540-symbol.width)//2, (540-symbol.height)//2))
        for size in (32, 180, 192):
            actual = Image.open(finders.find(f'masiscam/img/favicon-{size}.png')).convert('RGBA')
            self.assertEqual(actual.size, (size, size))
            self.assertEqual(actual.tobytes(), expected.resize((size, size), Image.Resampling.LANCZOS).tobytes())
        icon = Image.open(finders.find('masiscam/img/favicon.ico'))
        self.assertEqual(icon.ico.sizes(), {(16,16), (32,32), (48,48), (64,64)})

    def test_referencias_publicas_y_sistema(self):
        html = render_to_string('includes/favicon.html')
        self.assertIn('favicon.ico', html)
        self.assertIn('apple-touch-icon', html)
        root = Path(__file__).resolve().parents[1]
        templates = [root/'templates/web/home.html', root/'templates/web/consulta.html',
                     root/'templates/accounts/login.html', root/'masiscam/templates/masiscam/base.html']
        for path in templates:
            text = path.read_text(encoding='utf-8')
            self.assertEqual(text.count("include 'includes/favicon.html'"), 1, str(path))
            self.assertNotIn('data:image/svg+xml', text)
