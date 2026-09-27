"""SimAccounts: a simulated sign-in that stores nothing and says it's simulated (07 §3, §9)."""

import asyncio

from fake_afero import make_registry

from conductor.accounts_api import AccountManager, LoginState
from conductor.accounts_sim import SimAccounts
from conductor.clock import VirtualClock, run_virtual


def test_sim_sign_in_flows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def main():
        accounts: AccountManager = SimAccounts(make_registry(), VirtualClock())
        refused = await accounts.start_login("bench", "someone@realcorp.example", "pw")
        direct = await accounts.start_login("bench", "bench@example.invalid", "anything")
        await accounts.disconnect("bench")
        code = await accounts.start_login("bench", "bench@example.invalid", "needs-code")
        wrong = await accounts.submit_otp(code.nonce, "123456")
        right = await accounts.submit_otp(code.nonce, "000000")
        reused = await accounts.submit_otp(code.nonce, "000000")
        late = await accounts.start_login("bench", "bench@example.invalid", "needs-code")
        await asyncio.sleep(601)
        expired = await accounts.submit_otp(late.nonce, "000000")
        unknown_account = await accounts.start_login("nope", "bench@example.invalid", "x")
        return accounts, [refused, direct, code, wrong, right, reused, expired, unknown_account]

    accounts, outs = run_virtual(main)
    refused, direct, code, wrong, right, reused, expired, unknown_account = outs
    assert accounts.simulated is True
    assert refused.state is LoginState.FAILED and "realcorp" not in refused.message
    assert direct.state is LoginState.CONNECTED
    assert code.state is LoginState.OTP_REQUIRED and code.nonce
    assert wrong.state is LoginState.OTP_REQUIRED and wrong.nonce == code.nonce
    assert right.state is LoginState.CONNECTED
    assert reused.state is LoginState.FAILED and expired.state is LoginState.FAILED
    assert unknown_account.state is LoginState.FAILED
    assert all(o.message.startswith("Simulator:") for o in outs)
    [status] = accounts.status()
    assert status.connected and status.simulated and status.account == "bench"
    assert list(tmp_path.iterdir()) == []  # nothing stored anywhere
