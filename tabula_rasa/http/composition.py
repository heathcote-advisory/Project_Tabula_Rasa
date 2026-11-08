"""Infrastructure imports and lifecycle live here, never in resource handlers."""

from contextlib import contextmanager
import json
import os
import secrets

from sqlalchemy import text

from tabula_rasa.application.services import ApplicationServices
from tabula_rasa.errors import StorageConflictError, StorageError, StorageValidationError
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.hosted import open_alpha_database
from tabula_rasa.infrastructure.postgres.migrations import head_revision
from tabula_rasa.infrastructure.postgres.runtime import Database, DatabaseError

from .context import DenyContext, TrustedDevelopmentContext


@contextmanager
def translate_database_errors():
    try:
        yield
    except DatabaseError as error:
        code = error.code
    else:
        return
    if code in {"duplicate", "concurrent_write"}:
        raise StorageConflictError(code=code) from None
    if code in {"reference", "invariant", "required"}:
        raise StorageValidationError(code=code) from None
    raise StorageError(code="database_exhausted" if code == "database_exhausted" else "unavailable") from None


class PostgresRuntime:
    """One pool; compose a fresh service graph after resolving each request."""

    def __init__(self, database, resolver, *, service_factory, maps=None, identity=None, connections=None,
                 workspace_bootstrap=True, maps_quota=None):
        self.database = database
        self.resolve_context = resolver
        self.service_factory = service_factory
        self.maps = maps
        self.maps_quota = maps_quota
        self.identity = identity
        self.connections = connections
        from tabula_rasa.infrastructure.postgres.support import PostgresSupport
        from .identity import NamedIdentity
        self.support = PostgresSupport(database, identity.sessions) if isinstance(identity, NamedIdentity) else None
        from tabula_rasa.infrastructure.postgres.beta import PostgresBeta
        self.beta = PostgresBeta(database) if identity is not None else None
        from tabula_rasa.infrastructure.postgres.customer_lifecycle import PostgresCustomerLifecycle
        self.lifecycle = PostgresCustomerLifecycle(database) if identity is not None else None
        from tabula_rasa.application.workspaces import WorkspaceService
        from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository
        self.workspaces = (WorkspaceService(PostgresWorkspaceRepository(database), bootstrap_enabled=workspace_bootstrap)
                           if identity is not None else None)

    def services(self, context) -> ApplicationServices:
        with translate_database_errors():
            return self.service_factory(context)

    def operation(self):
        # Error translation only; repositories own transaction boundaries.
        return translate_database_errors()

    def prepare_customer_export(self, context, include_documents):
        from tabula_rasa.infrastructure.postgres.customer_export import prepare_customer_export
        return prepare_customer_export(self, context, include_documents)

    def record_customer_export(self, context, request_id, status):
        from tabula_rasa.infrastructure.postgres.customer_export import record_export
        record_export(self.database, context.entity_id, request_id, status)

    def audit_operation(self, context, request_id, source):
        from tabula_rasa.infrastructure.postgres.audit import AuditContext, audit_context
        from .identity import NamedIdentity
        named = isinstance(self.resolve_context, NamedIdentity) and self.identity is self.resolve_context
        return audit_context(self.database, AuditContext(
            'user' if named else 'system',
            context.actor_id if named or context.trusted_development else 'internal-application',
            source, context.entity_id, correlation_id=request_id,
        ))

    @contextmanager
    def document_upload_operation(self, context, document_id):
        """Reject overlapping creates for one entity/ID across HTTP workers."""
        with translate_database_errors(), self.database.transaction() as connection:
            scope = json.dumps([context.entity_id, document_id])
            acquired = connection.scalar(text(
                'SELECT pg_try_advisory_xact_lock(hashtextextended(:scope,304))'
            ), {'scope': scope})
            if not acquired:
                raise StorageConflictError(code='concurrent_write')
            yield

    def readiness_error(self):
        health = self.database.health()
        if health.get("connected") is True and health.get("revision") == head_revision():
            return None
        return health.get("code") or "unavailable"

    def ready(self):
        return self.readiness_error() is None

    def close(self):
        try:
            if self.identity is not None:
                self.identity.close()
        finally:
            try:
                if self.maps is not None:
                    self.maps.close()
            finally:
                try:
                    if self.maps_quota is not None:
                        self.maps_quota.close()
                finally:
                    self.database.dispose()


def open_runtime(settings):
    if settings.trusted_entity_id and settings.trusted_entity_id != "alpha-synthetic":
        raise RuntimeError("Default trusted runtime requires the explicit alpha-synthetic fixture.")
    database = None
    identity = None
    maps = None
    maps_quota = None
    try:
        from .migrated_configuration import load_migrated_bindings
        migrated = load_migrated_bindings(settings, dict(os.environ))
        database = (open_alpha_database(dict(os.environ)) if settings.hosted_alpha
                    else Database(DatabaseSettings.from_environment(required=True)))
        from .alpha_documents import load_alpha_document_store
        document_store = load_alpha_document_store(settings, dict(os.environ))
        if document_store is not None:
            from tabula_rasa.infrastructure.postgres.documents import PostgresReceiptRepository
            PostgresReceiptRepository(database, document_store.entity_id,
                store_kind="google_drive", store_scope_key=document_store.scope_key).validate_binding()
        connections = None
        resolver = (
            TrustedDevelopmentContext(settings.trusted_entity_id, secrets.token_urlsafe(32),
                                      hosted=settings.hosted_alpha,
                                      entity_ids=() if settings.hosted_alpha else ('beta-synthetic',))
            if settings.trusted_entity_id else DenyContext()
        )
        if settings.access_mode == "named_user":
            from tabula_rasa.infrastructure.oidc import OidcProvider, OidcSettings
            from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository
            from .identity import NamedIdentity
            oidc = OidcSettings.from_environment(settings, dict(os.environ))
            sessions = PostgresSessionRepository(database, settings.environment, oidc.configuration_hash)
            sessions.configure()
            identity = NamedIdentity(settings, sessions, OidcProvider(oidc))
            resolver = identity
            from tabula_rasa.infrastructure.connection_secrets import ConnectionSecrets
            from .connections import TenantConnections
            connections = TenantConnections(database, settings.environment,
                ConnectionSecrets.from_environment(dict(os.environ)))
            from .drive_oauth import CALLBACK_PATH, CustomerDriveOAuth, load_customer_oauth
            customer_oauth = load_customer_oauth(dict(os.environ), settings.public_origin)
            if customer_oauth is not None:
                if connections.secrets is None:
                    raise RuntimeError('Customer Drive requires protected persistent grant custody.')
                connections.oauth = CustomerDriveOAuth(connections, customer_oauth,
                                                       settings.public_origin + CALLBACK_PATH)
        def services(context):
            if connections is not None:
                store = connections.document_store(context)
                return create_postgres_services(database, context.entity_id,
                    document_store=store, document_store_kind=store.store_kind if store is not None else None)
            if document_store is None:
                return create_postgres_services(database, context.entity_id)
            if context.entity_id != document_store.entity_id or not context.trusted_development:
                raise StorageValidationError(code="forbidden")
            return create_postgres_services(database, context.entity_id,
                document_store=document_store, document_store_kind="google_drive")

        service_factory = services
        if migrated is not None:
            from .migrated import MigratedProfileServices
            entity_profiles, profile_secrets = migrated
            service_factory = MigratedProfileServices(database, entity_profiles=entity_profiles,
                                                      secrets=profile_secrets, connections=connections)
            service_factory.validate()
        from .maps import environment_maps
        from .maps_quota import environment_maps_quota
        maps = environment_maps(dict(os.environ))
        maps_quota = environment_maps_quota(dict(os.environ))
        runtime = PostgresRuntime(database, resolver, service_factory=service_factory,
                                  maps=maps, identity=identity, connections=connections,
                                  workspace_bootstrap=migrated is None, maps_quota=maps_quota)
        if migrated is not None and not runtime.ready():
            raise RuntimeError('The migrated database is not ready.')
        if settings.trusted_entity_id:
            # Only explicit seed tooling creates synthetic entities.
            for context in resolver.contexts:
                runtime.services(context)
        return runtime
    except BaseException as error:
        try:
            if identity is not None:
                identity.close()
        finally:
            try:
                if maps is not None:
                    maps.close()
            finally:
                try:
                    if maps_quota is not None:
                        maps_quota.close()
                finally:
                    if database is not None:
                        database.dispose()
        if not isinstance(error, Exception):
            raise
        # Startup failures must not retain raw configuration/provider text.
    raise RuntimeError("HTTP runtime startup failed. Check configuration and dependency health.")


def open_migrated_runtime(settings, *, resolve_context, entity_profiles, profile_secrets, database_settings=None,
                          connection_manager=None):
    """Bind the shared application to accepted profiles, with normal authorization.

    The deployment supplies its existing authenticated context resolver and
    trusted configuration. Cutover policy never selects an identity resolver.
    This factory neither activates authority nor enables the synthetic resolver.
    """
    from .identity import NamedIdentity
    commercial = settings.environment == 'production' or settings.access_mode == 'named_user'
    if (settings.trusted_entity_id or not callable(resolve_context)
            or commercial and (not isinstance(resolve_context, NamedIdentity) or resolve_context.settings != settings)):
        raise RuntimeError("Migrated runtime requires an explicit authorization resolver without fixture context.")
    if isinstance(resolve_context, NamedIdentity) and settings.access_mode != "named_user":
        raise RuntimeError("Named identity requires explicit named-user access mode.")
    from .migrated import MigratedProfileServices

    database = None
    try:
        database = Database(database_settings or DatabaseSettings.from_environment(required=True))
        if commercial:
            from tabula_rasa.infrastructure.postgres.identity import database_identity
            if (resolve_context.sessions.environment != settings.environment
                    or database_identity(resolve_context.sessions.database) != database_identity(database)):
                raise RuntimeError('Identity and ledger database bindings must agree.')
        if commercial:
            from tabula_rasa.infrastructure.connection_secrets import ConnectionSecrets
            from .connections import TenantConnections
            connection_manager = connection_manager or TenantConnections(database, settings.environment,
                ConnectionSecrets.from_environment(dict(os.environ)))
        services = MigratedProfileServices(database, entity_profiles=entity_profiles, secrets=profile_secrets,
                                           connections=connection_manager)
        services.validate()
        from .maps import load_mileage_maps
        from .maps_quota import load_maps_quota
        runtime = PostgresRuntime(database, resolve_context, service_factory=services,
                                  maps=load_mileage_maps(profile_secrets),
                                  maps_quota=load_maps_quota(profile_secrets),
                                  identity=resolve_context if isinstance(resolve_context, NamedIdentity) else None,
                                  connections=connection_manager, workspace_bootstrap=False)
        if not runtime.ready():
            raise RuntimeError("The migrated database is not ready.")
        return runtime
    except Exception:
        if database is not None:
            database.dispose()
    # Raise outside the handler: raw provider/configuration exceptions can
    # contain credentials or ledger values and must not survive as context.
    raise RuntimeError("Migrated HTTP runtime startup failed. Check authority, bindings and dependency health.")
