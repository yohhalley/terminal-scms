import re
from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand


def mermaid_identifier(value):
    return re.sub(r'[^A-Za-z0-9_]', '_', value)


def model_name(model):
    return mermaid_identifier(model.__name__)


def field_type(field):
    if field.is_relation:
        return field.__class__.__name__
    return mermaid_identifier(field.get_internal_type())


def render_model_diagram(app_label='minitos_core'):
    models = list(apps.get_app_config(app_label).get_models())
    model_set = set(models)
    lines = [
        '# MiniTOS model relationships',
        '',
        'Generated from the live Django model registry. Regenerate after model changes with:',
        '',
        '```shell',
        'python manage.py generate_model_diagram --output docs/MODEL_RELATIONSHIPS.md',
        '```',
        '',
        '```mermaid',
        'erDiagram',
    ]

    for model in models:
        lines.append(f'    {model_name(model)} {{')
        for field in model._meta.local_fields:
            markers = []
            if field.primary_key:
                markers.append('PK')
            if field.is_relation:
                markers.append('FK')
            suffix = f" {','.join(markers)}" if markers else ''
            lines.append(f'        {field_type(field)} {mermaid_identifier(field.name)}{suffix}')
        lines.append('    }')

    relationships = set()
    for model in models:
        for field in [*model._meta.local_fields, *model._meta.local_many_to_many]:
            related = getattr(field, 'related_model', None)
            if related not in model_set:
                continue
            label = mermaid_identifier(field.name)
            if field.many_to_many:
                relation = f'    {model_name(model)} }}o--o{{ {model_name(related)} : "{label}"'
            elif field.one_to_one:
                relation = f'    {model_name(related)} ||--o| {model_name(model)} : "{label}"'
            else:
                parent_cardinality = 'o|' if field.null else '||'
                relation = f'    {model_name(related)} {parent_cardinality}--o{{ {model_name(model)} : "{label}"'
            relationships.add(relation)
    lines.extend(sorted(relationships))
    lines.extend(['```', ''])
    return '\n'.join(lines)


class Command(BaseCommand):
    help = 'Render the minitos_core Django schema as a Mermaid entity relationship diagram.'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='Optional Markdown file path. Defaults to stdout.')

    def handle(self, *args, **options):
        content = render_model_diagram()
        if options['output']:
            output = Path(options['output'])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding='utf-8')
            self.stdout.write(self.style.SUCCESS(f'Model diagram written to {output}'))
            return
        self.stdout.write(content)
