# MiniTOS AI Assistant Instructions

## Project Overview

MiniTOS is a Django REST framework-based terminal operations system for managing container logistics. Core responsibility: model the physical and operational constraints of container terminal operations (stacking, equipment movement, task management).

**Key insight:** This is a spatial/hierarchical system where containers exist in specific 3D positions within blocks → piles → positions (tier). Validation must enforce physical realism (no floating containers, size restrictions by bay).

## Architecture Patterns

### Hierarchical Data Model
**Structure:** Location → Block → Pile → Position (tier)
- **Locations:** Terminal, inland depot, vessel, truck, train
- **Blocks:** Transfer zones, entry points, automated/manual stacking blocks with constraints (20ft, 40ft, 45ft bay assignments)
- **Piles:** Physical stacks at specific coordinates (bay, row), store containers (max tier: 20)
- **Positions:** Individual tier slots within piles, hold OneToOne `ContainerVisit` references

**Pattern:** Block.save() auto-generates pile/position grid using `max_bays × max_rows × max_tier`. Never manually create piles for a block.

### Container Assignment Rules
- **Pile.can_accept_container():** Validates ISO type (20/40/45 ft) against bay restrictions (odd bays→20ft only, even bays→40/45ft)
- **Position validation:** Enforces "no gaps" rule—containers must stack contiguously from tier 1 upward
- **Constraints:** `allows_in`, `allows_out` restrict insertion/removal per pile; `requires_power`, `is_imdg` track special handling needs

**Critical:** `Position.save()` validates physical rules before persistence. Never bypass `validate_physical_rules()`.

### API Structure (Nested Routers)
Endpoints follow `drf-nested-routers` pattern:
```
/locations/
/locations/{location_pk}/blocks/
/locations/{location_pk}/blocks/{block_pk}/piles/
/locations/{location_pk}/blocks/{block_pk}/piles/{pile_pk}/positions/
/locations/{location_pk}/container_visits/
/locations/{location_pk}/ches/ (equipment)
/locations/{location_pk}/work_queues/{work_queue_pk}/orders/
```

**ViewSet pattern:** Use `get_queryset()` to filter by parent (`location_pk`, `block_pk`, etc. from `self.kwargs`). See [BlockViewSet](minitos_core/views.py#L74) for example.

## Developer Workflows

### Running & Testing
```bash
python manage.py migrate          # Apply database migrations
python manage.py runserver        # Start dev server (http://localhost:8000)
python manage.py test minitos_core  # Run test suite
```

### Adding a Model Feature
1. Modify [minitos_core/models.py](minitos_core/models.py)
2. Create serializer in [minitos_core/serializers.py](minitos_core/serializers.py) (inherit `ModelSerializer`, specify fields)
3. Add ViewSet in [minitos_core/views.py](minitos_core/views.py) (inherit `ModelViewSet` or `viewsets.ViewSet`)
4. Register in [minitos_core/urls.py](minitos_core/urls.py) (use nested routers if parent exists)
5. Create migration: `python manage.py makemigrations`

### Debugging Validation Failures
Check `Position.validate_physical_rules()` and `Position.save()` for constraint violations. Use Django shell to inspect:
```bash
python manage.py shell
>>> from minitos_core.models import Position, Pile
>>> Position.objects.filter(tier=5).first().validate_physical_rules()
```

## Critical Conventions (Non-Standard)

### Signal-Driven Pile Generation
**Block creation auto-generates piles via `post_save` signal** ([signals.py](minitos_core/signals.py)). This duplicates logic in `Block.save()`. **Action:** Consolidate into single source (prefer `Block.save()` override, remove redundant signal).

### Serializer Partial Updates
Custom `partial_update()` methods in [PileSerializer](minitos_core/serializers.py#L74) and [PositionSerializer](minitos_core/serializers.py#L44) force `partial=True`. Use `PATCH` for updates, not `PUT`.

### Position.container_visit Relationship
Uses **OneToOneField** (not ForeignKey), meaning one position per container. Query via `position.container_visit` or `container_visit.position`.

### Integer Range Validation
Custom `IntegerRangeField` enforces min/max in form validation (e.g., bay 1–100, tier 1–20). Check usage in [Block](minitos_core/models.py#L173) and [Position](minitos_core/models.py#L267).

## Integration Points

### Equipment Management (CHE)
- **Che model:** Tracks equipment (RTG, STS, shuttle carrier) with status (Idle/Working/Offline/Breakdown/Maintenance)
- **Current position:** `Che.current_block`, `x_position`, `y_position` tie to block coordinates
- **Task assignment:** Via `Order` → `OrderLeg` (links order, equipment, start/end piles)

### Work Queues & Orders
- **WorkQueue:** Logical task container (assigned user, location, active status)
- **Order types:** Move In, Move Out, Housekeeping, Shift
- **OrderLeg:** Maps equipment movement (che → from_position → to_position with distance)

**Use case:** Move order creates Order (Move In/Out) → OrderLegs (split across equipment movements) → CHE status updates.

### External Dependencies
- **Django 4.2.20, DRF 3.16.0**: Check version compatibility before using new APIs
- **django-filter 25.1, drf-nested-routers 0.94.1**: Filterable ViewSets, nested URL routing
- **MySQL** (mysqlclient 2.2.7): Production database; migrations reference specific MySQL features

## Common Patterns & Gotchas

1. **Always use Location filter:** Most models require location context. Add `location_pk` filter in ViewSet.
2. **Avoid cascade deletes on RESTRICT:** Foreign keys with `on_delete=RESTRICT` (e.g., Location, EquipmentType) prevent orphaning; plan deletion carefully.
3. **ISO Type codes:** 4-char codes (20ft/40ft/45ft + height), stored in ISOType model—don't hardcode strings.
4. **Block regeneration:** Block.save() deletes/recreates all piles. Batch updates cautiously to preserve position data.
5. **Position tier constraints:** Manually validate gaps before assignment in order processing logic.

## Cursor Rules (Agent Prompt)

You are an expert in Python, Django, and scalable web application development.

Key Principles

- Write clear, technical responses with precise Django examples.
- Use Django's built-in features and tools wherever possible to leverage its full capabilities.
- Prioritize readability and maintainability; follow Django's coding style guide (PEP 8 compliance).
- Use descriptive variable and function names; adhere to naming conventions (e.g., lowercase with underscores for functions and variables).
- Structure your project in a modular way using Django apps to promote reusability and separation of concerns.

Django/Python

- Use Django’s class-based views (CBVs) for more complex views; prefer function-based views (FBVs) for simpler logic.
- Leverage Django’s ORM for database interactions; avoid raw SQL queries unless necessary for performance.
- Use Django’s built-in user model and authentication framework for user management.
- Utilize Django's form and model form classes for form handling and validation.
- Follow the MVT (Model-View-Template) pattern strictly for clear separation of concerns.
- Use middleware judiciously to handle cross-cutting concerns like authentication, logging, and caching.

Error Handling and Validation

- Implement error handling at the view level and use Django's built-in error handling mechanisms.
- Use Django's validation framework to validate form and model data.
- Prefer try-except blocks for handling exceptions in business logic and views.
- Customize error pages (e.g., 404, 500) to improve user experience and provide helpful information.
- Use Django signals to decouple error handling and logging from core business logic.

Dependencies

- Django
- Django REST Framework (for API development)
- Celery (for background tasks)
- Redis (for caching and task queues)
- PostgreSQL or MySQL (preferred databases for production)

Django-Specific Guidelines

- Use Django templates for rendering HTML and DRF serializers for JSON responses.
- Keep business logic in models and forms; keep views light and focused on request handling.
- Use Django's URL dispatcher (urls.py) to define clear and RESTful URL patterns.
- Apply Django's security best practices (e.g., CSRF protection, SQL injection protection, XSS prevention).
- Use Django’s built-in tools for testing (unittest and pytest-django) to ensure code quality and reliability.
- Leverage Django’s caching framework to optimize performance for frequently accessed data.
- Use Django’s middleware for common tasks such as authentication, logging, and security.

Performance Optimization

- Optimize query performance using Django ORM's select_related and prefetch_related for related object fetching.
- Use Django’s cache framework with backend support (e.g., Redis or Memcached) to reduce database load.
- Implement database indexing and query optimization techniques for better performance.
- Use asynchronous views and background tasks (via Celery) for I/O-bound or long-running operations.
- Optimize static file handling with Django’s static file management system (e.g., WhiteNoise or CDN integration).

Key Conventions

1. Follow Django's "Convention Over Configuration" principle for reducing boilerplate code.
2. Prioritize security and performance optimization in every stage of development.
3. Maintain a clear and logical project structure to enhance readability and maintainability.

Refer to Django documentation for best practices in views, models, forms, and security considerations.

## Key Files Reference
- **Models:** [minitos_core/models.py](minitos_core/models.py) (637 lines, core domain logic)
- **Serializers:** [minitos_core/serializers.py](minitos_core/serializers.py) (custom fields, SerializerMethodFields)
- **Views:** [minitos_core/views.py](minitos_core/views.py) (nested ViewSets, queryset filters)
- **URLs:** [minitos_core/urls.py](minitos_core/urls.py) (router registration, nested patterns)
- **Django Settings:** [minitos_site/settings.py](minitos_site/settings.py) (DEBUG=True dev config, installed apps)
