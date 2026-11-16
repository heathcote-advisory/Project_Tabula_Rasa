"""Cookie transport and named context; provider/storage details stay composed outside."""

from urllib.parse import urlsplit

from tabula_rasa.application.identity import AuthenticatedSession, IdentityError, access_capabilities

from .context import RequestContext
from .models.common import EntityInfo, SessionInfo, SetupSessionInfo


class NamedIdentity:
    def __init__(self, settings, sessions, provider):
        self.settings, self.sessions, self.provider = settings, sessions, provider
        self.secure = urlsplit(settings.public_origin).scheme == "https"
        prefix = "__Host-tabula_" if self.secure else "tabula_dev_"
        self.session_cookie, self.login_cookie, self.csrf_cookie = (prefix + name for name in ("session", "login", "csrf"))
        # Noncredential diagnostic reference; never used for authentication.
        self.login_reference_cookie = prefix + 'login_reference'

    def authenticated(self, request, *, foreground=False):
        session = getattr(request.state, 'named_session', None)
        if foreground or not isinstance(session, AuthenticatedSession):
            session = self.sessions.validate(request.cookies.get(self.session_cookie, ""), foreground=foreground)
            request.state.named_session = session
        return session

    def __call__(self, request):
        # Only Origin/CSRF-protected explicit foreground activity renews idle.
        # A rejected command or passive fetch cannot keep the session alive.
        session = self.authenticated(request)
        if session.selected is None:
            raise IdentityError("forbidden")
        selected = session.selected
        return RequestContext(session.user_id, selected.organization_id, selected.entity_id,
                              access_capabilities(selected), self.scope(session), session.csrf_token)

    @staticmethod
    def scope(session):
        selected = session.selected
        return session.cache_id + (":" + str(selected.authorization_version) + ':' + str(selected.lifecycle_version)
            + ':' + str(selected.settings_version) if selected else ":setup")

    def info(self, session, *, business_creation_available=False):
        common = dict(actor_id=session.user_id, cache_scope=self.scope(session), csrf_token=session.csrf_token,
                      trusted_development=False, business_creation_available=business_creation_available)
        if session.selected is None:
            return SetupSessionInfo(**common)
        selected = session.selected
        return SessionInfo(**common, organization_id=selected.organization_id,
                           entity=EntityInfo(id=selected.entity_id, name=selected.name, currency_code=selected.currency_code,
                               organization_id=selected.organization_id, organization_name=selected.organization_name,
                               role=selected.role),
                           capabilities=sorted(access_capabilities(selected)), closure_expires_at=selected.closure_expires_at)

    def set_cookie(self, response, name, value, max_age):
        response.set_cookie(name, value, max_age=max_age, httponly=True, secure=self.secure,
                            samesite="lax", path="/")

    def clear(self, response, *, login_only=False, preserve_login=False):
        names = (self.login_cookie, self.login_reference_cookie) if login_only else (
            self.session_cookie, self.login_cookie, self.login_reference_cookie, self.csrf_cookie)
        if preserve_login:
            names = (self.session_cookie, self.csrf_cookie)
        for name in names:
            response.delete_cookie(name, secure=self.secure, httponly=True, samesite="lax", path="/")

    def close(self):
        self.provider.close()
