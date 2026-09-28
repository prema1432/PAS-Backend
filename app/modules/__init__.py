"""Feature modules — one package per domain.

Every module owns the whole vertical slice of its feature::

    app/modules/<module>/
        __init__.py
        router.py     # APIRouter, declares its own prefix + tags
        schemas.py    # request/response models for this module only
        service.py    # business logic, no FastAPI imports

`app/modules/registry.py` lists the routers; `app.main` mounts them, so adding a
feature is one folder plus one registry line. Modules may import another
module's *service* (e.g. sessions uses the billing recharge helpers) but never
its router.
"""
