"""Read-only diagnostic for `python -m pretix shell < server_preflight.py`.

Reports versions and migration names only. Does not read credentials, order
secrets, customer records or environment variables. Does not alter the database.
"""
import importlib.metadata
import json
import platform

import django
import pretix
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

app_label = "pretix_gocardless"
executor = MigrationExecutor(connection)
leaves = executor.loader.graph.leaf_nodes(app_label)
plan = executor.migration_plan(leaves)
applied = MigrationRecorder(connection).applied_migrations()
print(json.dumps({
    "python": platform.python_version(),
    "pretix": pretix.__version__,
    "django": django.get_version(),
    "plugin": importlib.metadata.version("pretix-gocardless"),
    "database_backend": connection.vendor,
    "applied_plugin_migrations": sorted(name for app, name in applied if app == app_label),
    "pending_migrations": [{"app": migration.app_label, "name": migration.name, "backwards": backwards}
                           for migration, backwards in plan],
}, indent=2))
