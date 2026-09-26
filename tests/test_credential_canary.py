"""Credential canary (07 §3): a full sign-in with an emailed code against a stub Keycloak, using sentinel values,
with every logger at DEBUG. No sentinel may appear in logs, responses, status JSON or any file under the data dir,
and the stored token decrypts only with this runtime's key. Also: the sign-in rules around the allowlist, wrong
passwords, wrong codes and expired pending sign-ins."""

import asyncio
import json
import logging

import pytest
from cryptography.fernet import Fernet
from fake_afero import BENCH_MD, FakeAfero, make_world

from conductor import rows as R
from conductor.accounts_api import LoginState
from conductor.clock import run_virtual
from conductor.drivers.afero import AferoAccounts
from conductor.gate import Purpose, RuntimeMode
from conductor.tokens import TokenStore, TokenStoreUnavailable

EMAIL = "canary@example.invalid"
PASSWORD = "PW-SENTINEL-7f3a9c1e5b"
OTP = "OTP-SENTINEL-58213"
WRONG_OTP = "OTP-WRONG-SENTINEL-99"
TOKEN_PREFIX = "TOKSENTINEL"


class Capture(logging.Handler):
    """Formats each record at emit time (as a real stream handler would) and keeps the text."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + logging.Formatter().formatException(record.exc_info)
        self.lines.append(text)


@pytest.fixture
def all_debug_logs():
    root = logging.getLogger()
    handler = Capture()
    saved = {name: logging.getLogger(name).level for name in list(logging.root.manager.loggerDict)}
    saved_root = root.level
    root.setLevel(logging.DEBUG)
    for name in saved:
        logging.getLogger(name).setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(saved_root)
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)


def test_full_sign_in_with_emailed_code_leaks_no_secret(tmp_path, all_debug_logs):
    key = Fernet.generate_key().decode()
    fake = FakeAfero(email=EMAIL, password=PASSWORD, otp_code=OTP, token_prefix=TOKEN_PREFIX)

    async def main():
        w = await make_world(
            tmp_path, fake=fake, seed_token=False, connect=False, secret_key=key, emails=(EMAIL,)
        )
        # Loggers aioafero creates on the fly (per account) must be at DEBUG too.
        for name in list(logging.root.manager.loggerDict):
            logging.getLogger(name).setLevel(logging.DEBUG)
        accounts = AferoAccounts(w.driver)
        outcomes = [await accounts.start_login("bench", EMAIL, PASSWORD)]
        nonce = outcomes[0].nonce
        outcomes.append(await accounts.submit_otp(nonce, WRONG_OTP))
        outcomes.append(await accounts.submit_otp(nonce, OTP))
        write = await w.driver.write(BENCH_MD, R.slot_select("custom-1"), purpose=Purpose.ENGINE)
        read = await w.driver.read_state(BENCH_MD)
        w.wall[0] += 110
        fresh = await w.driver.ensure_fresh_token("bench")
        await asyncio.sleep(40)  # background polling at DEBUG too
        docs = {
            "outcomes": [o.__dict__ for o in outcomes],
            "status": [s.to_json() for s in accounts.status()],
            "health": w.driver.health(),
            "write": write.error,
            "read": read.error,
            "telemetry": w.telemetry.recent(limit=5000),
        }
        await w.driver.close()
        return outcomes, write, read, fresh, docs

    outcomes, write, read, fresh, docs = run_virtual(main)
    assert [o.state for o in outcomes] == [
        LoginState.OTP_REQUIRED,
        LoginState.OTP_REQUIRED,
        LoginState.CONNECTED,
    ]
    assert outcomes[0].nonce == outcomes[1].nonce and outcomes[2].nonce is None
    assert write.ok and read.ok and fresh.value == "ok"
    assert len(fake.issued) >= 6  # codes + at least two token generations

    sentinels = [PASSWORD, OTP, WRONG_OTP, *fake.issued]
    logs = "\n".join(all_debug_logs.lines)
    assert "afero PUT" in logs and len(all_debug_logs.lines) > 50  # the capture really saw DEBUG traffic
    dumped = json.dumps(docs, default=str)
    files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert any(p.suffix == ".token" for p in files)
    for sentinel in sentinels:
        assert sentinel not in logs, f"{sentinel[:6]}… leaked into the logs"
        assert sentinel not in dumped, f"{sentinel[:6]}… leaked into a response/status"
        for path, data in files.items():
            assert sentinel.encode() not in data, f"{sentinel[:6]}… leaked into {path.name}"
    assert EMAIL not in dumped  # messages never echo submitted fields

    # The stored token decrypts only with this runtime's key (and environment + account).
    current = TokenStore(tmp_path, "test", key).load("bench")
    assert current in fake.valid_refresh
    with pytest.raises(TokenStoreUnavailable):
        TokenStore(tmp_path, "test", Fernet.generate_key().decode()).load("bench")
    assert TokenStore(tmp_path, "staging", key).load("bench") is None


def test_non_allowlisted_email_sends_nothing(tmp_path):
    async def main():
        w = await make_world(tmp_path, seed_token=False, connect=False)
        out = await AferoAccounts(w.driver).start_login("bench", "show-account@realcorp.example", PASSWORD)
        await w.driver.close()
        return w, out

    w, out = run_virtual(main)
    assert out.state is LoginState.FAILED and "HUBSPACE_ACCOUNT_ALLOWLIST" in out.message
    assert w.fake.requests == []
    assert PASSWORD not in out.message and "realcorp" not in out.message


def test_account_connect_is_refused_where_the_gate_forbids_it(tmp_path):
    async def main():
        w = await make_world(tmp_path, seed_token=False, connect=False, mode=RuntimeMode.SIM, live=True)
        out = await AferoAccounts(w.driver).start_login("bench", "bench@example.invalid", PASSWORD)
        await w.driver.close()
        return w, out

    w, out = run_virtual(main)
    assert out.state is LoginState.FAILED and "only bench jobs" in out.message
    assert w.fake.requests == []


def test_wrong_password_unknown_account_and_missing_key(tmp_path):
    async def main():
        w = await make_world(tmp_path, seed_token=False, connect=False)
        accounts = AferoAccounts(w.driver)
        wrong = await accounts.start_login("bench", "bench@example.invalid", "not the password")
        unknown = await accounts.start_login("canopy", "bench@example.invalid", PASSWORD)
        w.driver.token_store = TokenStore(tmp_path, "test", None)
        nokey = await accounts.start_login("bench", "bench@example.invalid", PASSWORD)
        await w.driver.close()
        return wrong, unknown, nokey

    wrong, unknown, nokey = run_virtual(main)
    assert (
        wrong.state is LoginState.FAILED
        and wrong.message == "Hubspace didn't accept that email and password."
    )
    assert unknown.state is LoginState.FAILED and "registry" in unknown.message
    assert nokey.state is LoginState.FAILED and "CONDUCTOR_SECRET_KEY" in nokey.message
    assert not any(p.suffix == ".token" for p in tmp_path.rglob("*"))


def test_pending_sign_in_expires_and_unknown_nonces_explain_why(tmp_path):
    async def main():
        w = await make_world(tmp_path, fake=FakeAfero(otp_code="123456"), seed_token=False, connect=False)
        accounts = AferoAccounts(w.driver)
        first = await accounts.start_login("bench", "bench@example.invalid", "correct horse battery staple")
        await asyncio.sleep(601)
        expired = await accounts.submit_otp(first.nonce, "123456")
        unknown = await accounts.submit_otp("made-up", "123456")
        again = await accounts.start_login("bench", "bench@example.invalid", "correct horse battery staple")
        wrong = [await accounts.submit_otp(again.nonce, "000000") for _ in range(5)]
        await w.driver.close()
        return first, expired, unknown, wrong

    first, expired, unknown, wrong = run_virtual(main)
    assert first.state is LoginState.OTP_REQUIRED and "restart" in first.message
    for out in (expired, unknown):
        assert out.state is LoginState.FAILED and "10 minutes" in out.message and "restart" in out.message
    assert [o.state for o in wrong[:4]] == [LoginState.OTP_REQUIRED] * 4
    assert wrong[4].state is LoginState.FAILED and "Start the sign-in again" in wrong[4].message


def test_sign_in_without_a_code_connects_and_disconnect_forgets_the_token(tmp_path):
    async def main():
        w = await make_world(tmp_path, seed_token=False, connect=False)
        accounts = AferoAccounts(w.driver)
        out = await accounts.start_login("bench", "BENCH@example.invalid ", "correct horse battery staple")
        connected = accounts.status()[0]
        await accounts.disconnect("bench")
        after = accounts.status()[0]
        write = await w.driver.write(BENCH_MD, R.slot_select("custom-1"), purpose=Purpose.ENGINE)
        await w.driver.close()
        return out, connected, after, write

    out, connected, after, write = run_virtual(main)
    assert out.state is LoginState.CONNECTED and out.account == "bench"
    assert connected.connected and connected.token_generation == 1 and not connected.simulated
    assert not after.connected and after.token_generation is None
    assert write.outcome.value == "auth"
