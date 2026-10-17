# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-enforcing account client for the proposed backend adapter contract.

Production routes, OAuth client ID and trust keys intentionally have no defaults.
Only release-owned code may provide an approved Contract; no remote config,
environment override, .blend property or server-supplied signing key is trusted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .entitlements import EntitlementError, verify
from .storage import CredentialVault, LocalStore, StorageError, WindowsProtectedVault
import os

API_ORIGIN = 'https://api.cloth-next.de'
UNAVAILABLE = 'PolyWorks accounts are not available yet. You can sign in later and keep using Cloth NeXt.'


class AccountError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES.get(code, 'The account service could not complete this request.'))


MESSAGES = {
    'not_ready': UNAVAILABLE,
    'network_unavailable': 'Account service unavailable. Your saved license is preserved.',
    'sign_in_required': 'Sign in to your PolyWorks account first.',
    'invalid_code': 'This activation code is invalid.',
    'code_redeemed': 'This activation code has already been redeemed.',
    'code_expired': 'This activation code has expired.',
    'code_unavailable': 'This activation code is currently unavailable.',
    'verification_pending': 'Purchase Verification Pending',
    'purchase_not_verified': 'The server could not verify this purchase. Your saved license is preserved.',
    'authorization_pending': 'Complete sign-in in your browser.',
    'access_denied': 'Sign-in was declined.',
    'expired_token': 'The sign-in request expired. Please start again.',
    'slow_down': 'Waiting for the account service.',
    'invalid_response': 'The account service returned an unsupported response.',
    'invalid_entitlement': 'License verification failed. Your saved license is preserved.',
    'invalid_grant': 'Your session expired. Please sign in again.',
}


@dataclass(frozen=True)
class Contract:
    approved: bool = False
    client_id: str = ''
    issuer: str = ''
    routes: dict = field(default_factory=dict)
    browser_urls: dict = field(default_factory=dict)
    browser_origins: tuple = ()
    keys: dict = field(default_factory=dict)
    devices_supported: bool = False

    def require(self):
        if not (self.approved and self.client_id and self.issuer and self.keys
                and all(k in self.routes for k in ('device', 'token', 'revoke', 'account', 'entitlement', 'redeem', 'purchase'))):
            raise AccountError('not_ready')

    def browser_url(self, action):
        self.require()
        return self.validate_browser(self.browser_urls.get(action, ''))

    def validate_browser(self, url):
        parsed = urllib.parse.urlsplit(url)
        origin = f'{parsed.scheme}://{parsed.netloc}'
        if (origin not in self.browser_origins or parsed.scheme != 'https'
                or parsed.username or parsed.password or parsed.fragment):
            raise AccountError('invalid_response')
        return url


# Required production contract has not been supplied. Deliberately inert.
PRODUCTION_CONTRACT = Contract()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpTransport:
    def __init__(self, contract):
        self.contract = contract

    def request(self, operation, payload=None, access_token=None, idempotency_key=None):
        self.contract.require()
        route = self.contract.routes.get(operation, '')
        # Exact origin; never leak tokens/codes across redirects or absolute URLs.
        if not route.startswith('/') or route.startswith('//') or any(c in route for c in ('\\', '?', '#', '\r', '\n')):
            raise AccountError('not_ready')
        headers = {'Accept': 'application/json'}
        if access_token:
            headers['Authorization'] = 'Bearer ' + access_token
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        data = None
        if payload is not None:
            if operation in ('device', 'token', 'revoke'):
                data = urllib.parse.urlencode(payload).encode()
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
            else:
                data = json.dumps(payload).encode()
                headers['Content-Type'] = 'application/json'
        try:
            req = urllib.request.Request(API_ORIGIN+route, data=data, headers=headers)
            opener = urllib.request.build_opener(_NoRedirect())
            try:
                response = opener.open(req, timeout=12)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                raw = response.read(1024*1024+1)
                if len(raw) > 1024*1024:
                    raise AccountError('invalid_response')
                if response.status == 204:
                    return {}
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise AccountError('invalid_response')
                if response.status >= 400:
                    code = result.get('error')
                    if response.status == 401:
                        code = 'sign_in_required'
                    raise AccountError(code if code in MESSAGES else 'network_unavailable')
                if response.status != 200:
                    raise AccountError('invalid_response')
                return result
        except AccountError:
            raise
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise AccountError('network_unavailable') from exc


@dataclass
class DeviceAuthorization:
    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    expires_at: float
    interval: float
    next_poll: float


class AccountClient:
    def __init__(self, store: LocalStore, *, contract=PRODUCTION_CONTRACT, vault=None, transport=None,
                 clock=time.time, monotonic=time.monotonic):
        self.store = store
        self.contract = contract
        self._installation = None
        self._vault = vault
        self.transport = transport or HttpTransport(contract)
        self.clock = clock
        self.monotonic = monotonic
        self._access = None
        self._access_expires = 0
        self._device = None
        self._cancel_sign_in = threading.Event()
        self.verification_pending = False

    @property
    def installation(self):
        if self._installation is None:
            self._installation = self.store.installation_id()
        return self._installation

    @property
    def vault(self):
        if self._vault is None:
            self._vault = (WindowsProtectedVault(self.store.root, self.installation) if os.name == 'nt'
                           else CredentialVault(self.installation))
        return self._vault

    def local_license(self):
        token = self.store.read_entitlement()
        if token is None:
            return None
        return verify(token, keys=self.contract.keys, issuer=self.contract.issuer,
                      installation_id=self.installation, now=self.clock())

    def status(self):
        # Called on demand, not on each UI redraw. No network request.
        result = dict(signed_in=False, email='', tier='Not activated',
                      verification='Not activated', devices=None, backend_ready=False)
        try:
            self.contract.require()
            result['backend_ready'] = True
        except AccountError:
            pass
        try:
            license = self.local_license()
            if license:
                result.update(tier=license.tier, verification='License Activated (verified offline)')
        except (EntitlementError, StorageError, OSError, ValueError):
            result['verification'] = 'Saved entitlement could not be verified; file preserved'
        try:
            session = self.vault.load()
            if isinstance(session, dict) and session.get('refresh_token') and session.get('email'):
                result.update(signed_in=True, email=session['email'])
        except (StorageError, OSError):
            result['session_status'] = 'OS credential vault unavailable'
        if self.verification_pending:
            result['verification'] = 'Purchase Verification Pending'
        return result

    def begin_sign_in(self):
        self.contract.require()
        self._cancel_sign_in.clear()
        # Verify vault availability before creating a device authorization.
        self.vault.load()
        result = self.transport.request('device', {'client_id': self.contract.client_id,
            'scope': 'account license offline_access', 'installation_id': self.installation})
        try:
            code, user = result['device_code'], result['user_code']
            expiry, interval = result['expires_in'], result.get('interval', 5)
            if (not isinstance(code, str) or not code or not isinstance(user, str) or not user
                    or type(expiry) is not int or not 0 < expiry <= 1800
                    or type(interval) is not int or not 1 <= interval <= 60):
                raise ValueError()
            url = self.contract.validate_browser(result['verification_uri'])
            now = self.monotonic()
            if self._cancel_sign_in.is_set():
                raise AccountError('access_denied')
            self._device = DeviceAuthorization(code, user, url, now+expiry, interval, now+interval)
            return self._device
        except (KeyError, ValueError, TypeError) as exc:
            raise AccountError('invalid_response') from exc

    def poll_sign_in(self):
        device = self._device
        if device is None or self.monotonic() >= device.expires_at:
            self._device = None
            raise AccountError('expired_token')
        if self.monotonic() < device.next_poll:
            raise AccountError('authorization_pending')
        device.next_poll = self.monotonic()+device.interval
        try:
            result = self.transport.request('token', {'client_id': self.contract.client_id,
                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': device.device_code})
        except AccountError as exc:
            if exc.code == 'slow_down':
                device.interval += 5
                device.next_poll = self.monotonic()+device.interval
            elif exc.code in ('access_denied', 'expired_token'):
                self._device = None
            raise
        self._accept_tokens(result)
        self._device = None
        return self.status()

    def _accept_tokens(self, result, previous=None):
        try:
            access = result['access_token']
            refresh = result.get('refresh_token') or (previous or {}).get('refresh_token')
            expiry = result['expires_in']
            if (result.get('token_type', '').lower() != 'bearer' or not isinstance(access, str) or not access
                    or not isinstance(refresh, str) or not refresh or type(expiry) is not int or not 0 < expiry <= 86400
                    or any(c in access for c in ('\r', '\n'))):
                raise ValueError()
            if self._cancel_sign_in.is_set():
                raise AccountError('access_denied')
            # A rotation can invalidate the previous refresh token immediately.
            # Preserve its replacement before the independent profile request.
            self.vault.save(dict(refresh_token=refresh, email=(previous or {}).get('email', '')))
            self._access, self._access_expires = access, self.clock()+expiry
            profile = self.transport.request('account', access_token=access)
            if not isinstance(profile.get('email'), str) or '@' not in profile['email']:
                raise ValueError()
            self.vault.save(dict(refresh_token=refresh, email=profile['email']))
        except (KeyError, ValueError, TypeError) as exc:
            raise AccountError('invalid_response') from exc

    def _authenticated(self):
        self.contract.require()
        if self._access and self.clock() < self._access_expires-30:
            return self._access
        session = self.vault.load()
        if not isinstance(session, dict) or not session.get('refresh_token'):
            raise AccountError('sign_in_required')
        result = self.transport.request('token', {'client_id': self.contract.client_id,
            'grant_type': 'refresh_token', 'refresh_token': session['refresh_token']})
        self._accept_tokens(result, session)
        return self._access

    def synchronize(self):
        result = self.transport.request('entitlement', {'installation_id': self.installation},
                                        access_token=self._authenticated())
        token = result.get('entitlement')
        try:
            entitlement = verify(token, keys=self.contract.keys, issuer=self.contract.issuer,
                                 installation_id=self.installation, now=self.clock())
        except EntitlementError as exc:
            raise AccountError('invalid_entitlement') from exc
        self.store.write_entitlement(token)
        self.verification_pending = False
        return entitlement

    def activate(self, code):
        access = self._authenticated()  # Authentication precedes handling the code.
        if not isinstance(code, str) or not code.strip() or len(code) > 512:
            raise AccountError('invalid_code')
        result = self.transport.request('redeem', {'code': code.strip(), 'installation_id': self.installation},
            access_token=access, idempotency_key=str(uuid.uuid4()))
        if result.get('status') != 'redeemed':
            raise AccountError('invalid_response')
        # A redemption acknowledgement is not an activated license.
        return self.synchronize()

    def verify_purchase(self, detected):
        access = self._authenticated()
        if not detected:
            raise AccountError('purchase_not_verified')
        self.verification_pending = True
        result = self.transport.request('purchase', {'product': 'cloth-next',
            'installation_id': self.installation, 'local_purchase_detected': True}, access_token=access)
        if result.get('status') == 'pending':
            raise AccountError('verification_pending')
        if result.get('status') != 'verified':
            self.verification_pending = False
            raise AccountError('purchase_not_verified')
        return self.synchronize()

    def devices(self):
        if not self.contract.devices_supported:
            return None
        result = self.transport.request('devices', access_token=self._authenticated())
        value = result.get('devices')
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise AccountError('invalid_response')
        return value

    def sign_out(self):
        self._cancel_sign_in.set()
        self._device = None
        self._access, self._access_expires = None, 0
        self.verification_pending = False
        session = self.vault.load()
        # Clear local credentials even if remote revocation is unavailable.
        self.vault.clear()
        if session and session.get('refresh_token'):
            try:
                self.transport.request('revoke', {'client_id': self.contract.client_id,
                    'token': session['refresh_token'], 'token_type_hint': 'refresh_token'})
            except AccountError:
                return 'Signed out locally. Server revocation could not be confirmed.'
        return 'Signed out'

    def cancel_sign_in(self):
        self._cancel_sign_in.set()
        self._device = None
