"""Account contract tests use local fakes and ephemeral TEST signing keys only."""
import base64
from dataclasses import replace
import json
import os
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from cloth_next.polyworks.client import (AccountClient, AccountError, Contract,
    PRODUCTION_CONTRACT, HttpTransport)
from cloth_next.polyworks.entitlements import EntitlementError, verify
from cloth_next.polyworks.storage import LocalStore, CredentialVault, StorageError
from cloth_next.polyworks.display import publish_status, read_status


class Vault:
    def __init__(self):
        self.value = None
    def load(self):
        return self.value
    def save(self, value):
        self.value = value
    def clear(self):
        self.value = None


class Transport:
    def __init__(self):
        self.calls = []
        self.responses = {}
    def request(self, operation, payload=None, **kwargs):
        self.calls.append((operation, payload, kwargs))
        result = self.responses[operation]
        if isinstance(result, Exception):
            raise result
        return result


def segment(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')


@pytest.fixture
def account(tmp_path):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    contract = Contract(approved=True, client_id='TEST-CLIENT', issuer='TEST-ISSUER',
        keys={'TEST-KEY': public}, routes={k: '/test/'+k for k in ('device','token','revoke','account','entitlement','redeem','purchase')},
        browser_urls={'register':'https://account.example.test/register','manage':'https://account.example.test/manage'},
        browser_origins=('https://account.example.test',))
    vault, transport = Vault(), Transport()
    clock = [1000]
    client = AccountClient(LocalStore(tmp_path), contract=contract, vault=vault, transport=transport,
                           clock=lambda: clock[0], monotonic=lambda: clock[0])
    claims = dict(iss='TEST-ISSUER', aud='cloth-next', product='cloth-next', sub='customer1', jti='license1',
                  tier='Personal', installation_id=client.installation, iat=900, nbf=900, exp=2000)
    def sign(changes=None, header=None):
        h = segment(header or dict(alg='EdDSA', typ='JWT', kid='TEST-KEY'))
        p = segment({**claims, **(changes or {})})
        signature = private.sign(f'{h}.{p}'.encode())
        return f'{h}.{p}.'+base64.urlsafe_b64encode(signature).decode().rstrip('=')
    transport.responses.update(account={'email':'artist@example.test'},
        token={'access_token':'TEST-ACCESS', 'refresh_token':'TEST-REFRESH', 'token_type':'Bearer', 'expires_in':300},
        device={'device_code':'TEST-DEVICE', 'user_code':'TEST-CODE', 'verification_uri':'https://account.example.test/verify', 'expires_in':300,'interval':5},
        entitlement={'entitlement':sign()}, redeem={'status':'redeemed'}, purchase={'status':'verified'}, revoke={})
    return SimpleNamespace(client=client, vault=vault, transport=transport, contract=contract, sign=sign, clock=clock)


def verify_test(account, token):
    return verify(token, keys=account.contract.keys, issuer=account.contract.issuer,
                  installation_id=account.client.installation, now=1000)


@pytest.mark.parametrize('tier', ['Personal','Commercial'])
def test_offline_signature_and_real_tier(account, tier):
    assert verify_test(account, account.sign({'tier':tier})).tier == tier


@pytest.mark.parametrize('changes', [
    {'iss':'evil'}, {'product':'other'}, {'aud':'other'}, {'tier':'Enterprise'},
    {'installation_id':'another-device'}, {'exp':999}, {'nbf':1001}, {'iat':1001},
    {'exp':True}, {'exp':2000.0}, {'sub':''}, {'jti':None}, {'exp':900},
])
def test_signed_but_invalid_claims_are_rejected(account, changes):
    with pytest.raises(EntitlementError):
        verify_test(account, account.sign(changes))


@pytest.mark.parametrize('header', [dict(alg='none',typ='JWT',kid='TEST-KEY'),
    dict(alg='HS256',typ='JWT',kid='TEST-KEY'), dict(alg='EdDSA',typ='JWT',kid='unknown'),
    dict(alg='EdDSA',typ='JWT',kid='TEST-KEY',jku='https://evil.test/keys'),
    dict(alg='EdDSA',typ='JWT',kid='TEST-KEY',crit=['extra'])])
def test_untrusted_or_unsupported_headers_are_rejected(account, header):
    with pytest.raises(EntitlementError):
        verify_test(account, account.sign(header=header))


def test_payload_and_signature_tampering(account):
    h,p,s = account.sign().split('.')
    for token in (h+'.'+segment({'tier':'Commercial'})+'.'+s, h+'.'+p+'.'+('A' if s[0]!='A' else 'B')+s[1:]):
        with pytest.raises(EntitlementError):
            verify_test(account, token)


@pytest.mark.parametrize('token', [None, '', 'x.y', 'x.y.z', '..', 'x'*65537],
                         ids=['none','empty','two-segments','invalid-segments','empty-segments','oversized'])
def test_malformed_entitlements(account, token):
    with pytest.raises(EntitlementError):
        verify_test(account, token)


def test_device_login_poll_interval_and_success(account):
    device = account.client.begin_sign_in()
    assert device.user_code == 'TEST-CODE'
    with pytest.raises(AccountError, match='Complete sign-in'):
        account.client.poll_sign_in()
    assert len(account.transport.calls) == 1
    account.clock[0] += 5
    assert account.client.poll_sign_in()['signed_in']
    assert account.vault.value == dict(refresh_token='TEST-REFRESH',email='artist@example.test')
    assert 'TEST-ACCESS' not in json.dumps(account.vault.value)


def test_device_slow_down_expiry_and_cancel(account):
    device = account.client.begin_sign_in()
    account.clock[0] += 5
    account.transport.responses['token'] = AccountError('slow_down')
    with pytest.raises(AccountError):
        account.client.poll_sign_in()
    assert device.interval == 10
    account.clock[0] += 400
    with pytest.raises(AccountError) as exc:
        account.client.poll_sign_in()
    assert exc.value.code == 'expired_token'
    account.client.begin_sign_in()
    account.client.cancel_sign_in()
    with pytest.raises(AccountError):
        account.client.poll_sign_in()
    assert account.vault.value is None


def test_rotated_refresh_token_survives_profile_network_failure(account):
    signed_in(account)
    account.transport.responses['token']['refresh_token'] = 'ROTATED-TEST-REFRESH'
    account.transport.responses['account'] = AccountError('network_unavailable')
    with pytest.raises(AccountError):
        account.client.synchronize()
    assert account.vault.value['refresh_token'] == 'ROTATED-TEST-REFRESH'
    assert account.vault.value['email'] == 'artist@example.test'


def test_cancel_during_token_response_does_not_save_session(account):
    account.client.begin_sign_in()
    account.clock[0] += 5
    original = account.transport.request
    def request(operation, *args, **kwargs):
        result = original(operation, *args, **kwargs)
        if operation == 'token':
            account.client.cancel_sign_in()
        return result
    account.transport.request = request
    with pytest.raises(AccountError):
        account.client.poll_sign_in()
    assert account.vault.value is None


def test_device_browser_redirect_to_unknown_origin_is_rejected(account):
    account.transport.responses['device']['verification_uri'] = 'https://evil.test/verify'
    with pytest.raises(AccountError):
        account.client.begin_sign_in()


def test_activation_requires_authentication_before_sending_code(account):
    with pytest.raises(AccountError) as exc:
        account.client.activate('secret-code')
    assert exc.value.code == 'sign_in_required'
    assert not account.transport.calls


def signed_in(account):
    account.vault.value = dict(refresh_token='TEST-REFRESH',email='artist@example.test')


def test_activation_redemption_then_verified_entitlement(account):
    signed_in(account)
    assert account.client.activate('TEST-ACTIVATION').tier == 'Personal'
    operations = [c[0] for c in account.transport.calls]
    assert operations == ['token','account','redeem','entitlement']
    redeem = account.transport.calls[2]
    assert redeem[2]['access_token'] == 'TEST-ACCESS' and redeem[2]['idempotency_key']
    assert account.client.local_license().license_id == 'license1'


@pytest.mark.parametrize('error', ['invalid_code','code_redeemed','code_expired','code_unavailable','network_unavailable'])
def test_activation_failures_preserve_existing_license(account, error):
    signed_in(account)
    old = account.sign()
    account.client.store.write_entitlement(old)
    account.transport.responses['redeem'] = AccountError(error)
    with pytest.raises(AccountError) as exc:
        account.client.activate('TEST-CODE')
    assert exc.value.code == error
    assert account.client.store.read_entitlement() == old


def test_redeemed_code_without_entitlement_never_fabricates_success(account):
    signed_in(account)
    account.transport.responses['entitlement'] = AccountError('network_unavailable')
    with pytest.raises(AccountError):
        account.client.activate('TEST-CODE')
    assert account.client.store.read_entitlement() is None


def test_tampered_sync_does_not_replace_valid_local_entitlement(account):
    signed_in(account)
    original = account.sign()
    account.client.store.write_entitlement(original)
    account.transport.responses['entitlement'] = {'entitlement':account.sign({'product':'other'})}
    with pytest.raises(AccountError) as exc:
        account.client.synchronize()
    assert exc.value.code == 'invalid_entitlement'
    assert account.client.store.read_entitlement() == original


def test_offline_mode_and_signout_preserve_license(account):
    signed_in(account)
    original = account.sign()
    account.client.store.write_entitlement(original)
    account.transport.responses['token'] = AccountError('network_unavailable')
    with pytest.raises(AccountError):
        account.client.synchronize()
    assert account.client.status()['tier'] == 'Personal'
    account.transport.responses['revoke'] = AccountError('network_unavailable')
    assert 'locally' in account.client.sign_out()
    assert not account.client.status()['signed_in']
    assert account.client.store.read_entitlement() == original


def test_purchase_detected_is_not_activation_and_pending_is_distinct(account):
    signed_in(account)
    account.transport.responses['purchase'] = {'status':'pending'}
    with pytest.raises(AccountError) as exc:
        account.client.verify_purchase(True)
    assert exc.value.code == 'verification_pending'
    assert account.client.status()['verification'] == 'Purchase Verification Pending'
    assert account.client.store.read_entitlement() is None
    assert 'entitlement' not in [c[0] for c in account.transport.calls]


def test_purchase_server_rejects_local_hint(account):
    signed_in(account)
    account.transport.responses['purchase'] = {'status':'unverified'}
    with pytest.raises(AccountError):
        account.client.verify_purchase(True)
    assert not account.client.verification_pending
    assert account.client.store.read_entitlement() is None


def test_verified_purchase_still_requires_signed_entitlement(account):
    signed_in(account)
    assert account.client.verify_purchase(True).tier == 'Personal'


def test_production_is_inert_even_when_api_might_be_online(tmp_path):
    transport = Transport()
    client = AccountClient(LocalStore(tmp_path), vault=Vault(), transport=transport)
    for action in (client.begin_sign_in, client.synchronize, lambda:client.activate('CODE'), lambda:client.verify_purchase(True)):
        with pytest.raises(AccountError) as exc:
            action()
        assert exc.value.code == 'not_ready'
    assert not transport.calls
    assert not (tmp_path/'entitlement.jws').exists()


def test_public_display_contains_no_credentials_and_blender_read_is_read_only(account):
    signed_in(account)
    account.client.store.write_entitlement(account.sign())
    public = publish_status(account.client.store, account.client)
    assert public['tier'] == 'Personal'
    assert 'TEST-REFRESH' not in (account.client.store.root/'display-status.json').read_text()
    before = {p.name:p.read_bytes() for p in account.client.store.root.iterdir()}
    assert read_status(account.client.store.root)['email'] == 'artist@example.test'
    assert before == {p.name:p.read_bytes() for p in account.client.store.root.iterdir()}
    missing = account.client.store.root/'missing'
    read_status(missing)
    assert not missing.exists()


def test_device_projection_never_copies_arbitrary_server_fields(account):
    public = publish_status(account.client.store, account.client, devices=[{'secret':'TEST-SECRET','id':'device1'}])
    assert public['devices'] == [{}]
    assert 'TEST-SECRET' not in (account.client.store.root/'display-status.json').read_text()


def test_missing_crypto_provider_preserves_license(account, monkeypatch):
    import sys
    account.client.store.write_entitlement(account.sign())
    monkeypatch.setitem(sys.modules, 'cryptography.hazmat.primitives.asymmetric.ed25519', None)
    with pytest.raises(EntitlementError, match='unavailable'):
        account.client.local_license()
    assert account.client.store.read_entitlement()


def test_no_plaintext_keyring_fallback(monkeypatch):
    import sys
    fake = SimpleNamespace(get_keyring=lambda: SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'keyring', fake)
    with pytest.raises(StorageError):
        CredentialVault('TEST-INSTALLATION').save({'refresh_token':'TEST-SECRET'})


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI integration')
def test_real_windows_dpapi_roundtrip_and_installation_binding(tmp_path):
    from cloth_next.polyworks.storage import WindowsProtectedVault
    vault = WindowsProtectedVault(tmp_path, 'TEST-INSTALLATION')
    value = dict(refresh_token='SYNTHETIC-TEST-SECRET', email='test@example.test')
    vault.save(value)
    assert b'SYNTHETIC-TEST-SECRET' not in vault.path.read_bytes()
    assert vault.load() == value
    with pytest.raises(StorageError):
        WindowsProtectedVault(tmp_path, 'OTHER-INSTALLATION').load()
    vault.clear()
    assert vault.load() is None


def test_installation_identity_is_stable_and_corruption_does_not_rebind(tmp_path):
    store = LocalStore(tmp_path)
    identity = store.installation_id()
    assert LocalStore(tmp_path).installation_id() == identity
    (tmp_path/'installation-id').write_text('broken')
    with pytest.raises(StorageError):
        store.installation_id()


def test_companion_account_operations_cannot_overlap(tmp_path):
    from cloth_next.polyworks.storage import account_operation
    with account_operation(tmp_path):
        with pytest.raises(StorageError, match='Another Companion'):
            with account_operation(tmp_path):
                raise AssertionError('must not run concurrently')
    with account_operation(tmp_path):
        pass


def test_blender_account_operator_only_opens_exe(blender_env, monkeypatch):
    from cloth_next.blender import preferences, onboarding_manager
    calls = []
    monkeypatch.setattr(onboarding_manager, 'launch_screen', lambda *args, **kwargs: (calls.append((args, kwargs)) or (True, 'Opened')))
    operator = preferences.CLOTHNEXT_OT_manage_polyworks_account()
    operator.report = lambda *_:None
    assert operator.execute(blender_env.bpy.context) == {'FINISHED'}
    assert calls == [(('welcome',), dict(manual=True, open_account=True))]


def test_http_transport_does_not_redirect_secrets(account, monkeypatch):
    import urllib.request
    seen = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, limit): return b'{"status":"redeemed"}'
    def open_request(req, timeout):
        seen.append((req, timeout))
        return Response()
    def build(handler):
        assert handler.redirect_request(None, None, 302, '', {}, 'https://evil.test') is None
        return SimpleNamespace(open=open_request)
    monkeypatch.setattr(urllib.request, 'build_opener', build)
    HttpTransport(account.contract).request('redeem', {'code':'TEST-CODE'}, access_token='TEST-ACCESS')
    req, timeout = seen[0]
    assert req.full_url == 'https://api.cloth-next.de/test/redeem'
    assert timeout == 12 and 'TEST-CODE' not in req.full_url


def test_unavailable_api_transport_never_erases_entitlement(account, monkeypatch):
    import urllib.request
    import urllib.error
    old = account.sign()
    account.client.store.write_entitlement(old)
    def unavailable(*_args, **_kwargs):
        raise urllib.error.URLError('synthetic offline failure')
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *_:SimpleNamespace(open=unavailable))
    with pytest.raises(AccountError) as exc:
        HttpTransport(account.contract).request('device', {'client_id':'TEST'})
    assert exc.value.code == 'network_unavailable'
    assert account.client.store.read_entitlement() == old


def test_legacy_superhive_constructor_is_only_a_detection_alias():
    from cloth_next.superhive_status import SuperhiveStatus
    status = SuperhiveStatus(connected=True, purchase_validated=True)
    assert status.purchase_detected and status.purchase_validated


@pytest.mark.parametrize('route', ['https://evil.test/x', '//evil.test/x', '/x?token=1', '/x#fragment', '/\\evil'])
def test_unsafe_contract_routes_rejected_before_network(account, route):
    contract = replace(account.contract, routes={**account.contract.routes,'redeem':route})
    with pytest.raises(AccountError):
        HttpTransport(contract).request('redeem', {'code':'TEST-CODE'})
