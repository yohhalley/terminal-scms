from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase

from minitos_core.management.commands.generate_model_diagram import render_model_diagram


class ModelDiagramTests(SimpleTestCase):
    def test_diagram_describes_layout_composition(self):
        diagram = render_model_diagram()

        self.assertIn('erDiagram', diagram)
        self.assertIn('LayoutAsset {', diagram)
        self.assertIn('LayoutVersion ||--o{ LayoutAsset', diagram)
        self.assertIn('LayoutAsset ||--o{ LinearAxis', diagram)
        self.assertIn('LinearAxis ||--o{ LinearMarker', diagram)
        self.assertIn('LayoutAsset ||--o{ BerthSegment', diagram)

    def test_command_can_render_to_stdout(self):
        output = StringIO()

        call_command('generate_model_diagram', stdout=output)

        self.assertIn('# MiniTOS model relationships', output.getvalue())
