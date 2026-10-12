# MiniTOS development rules

## Model and API contract changes

Whenever a change touches `minitos_core/models.py`, `serializers.py`, `views.py`,
`urls.py`, or a migration:

1. Review the affected model, serializer, view, and URL together as one contract.
2. Add or update focused regression tests for changed behavior, validation,
   permissions, parent-resource scoping, serialization, and error responses.
3. Check `TEST_COVERAGE_AUDIT.md` and update it when coverage or known gaps change.
4. Run migration drift detection:
   `python3 manage.py makemigrations --check --dry-run`.
5. Run the focused tests while developing, then run the complete suite before
   finishing: `python3 manage.py test`.
6. Do not treat demonstration scripts as coverage. Automated tests must use
   Django `TestCase`/`APITestCase` and be discoverable by `manage.py test`.

## TIC4.0 semantic review

Whenever a model change introduces or changes terminal, CHE, cargo, carrier,
equipment, activity, power, maintenance, health, location, or process semantics:

1. Review the current official TIC4.0 definitions and latest applicable release
   in the TIC4.0 Confluence publication space.
2. Update `docs/TIC40_ALIGNMENT.md` with the review date, applicable TIC4.0
   concepts, MiniTOS mappings, and any deliberate extension or mismatch.
3. Keep TIC4.0 facts orthogonal. Do not collapse independent power, activity,
   availability, dispatch, maintenance, and health facts into one status enum.
4. Add contract tests for the mapping and for invalid combinations that MiniTOS
   rejects. Tests validate the recorded mapping; they do not replace reviewing
   the live publication because TIC4.0 evolves independently of this repository.
5. Highlight unresolved or deliberate deviations in the change summary.

If a full test cannot be run, state exactly which verification is missing and why.
