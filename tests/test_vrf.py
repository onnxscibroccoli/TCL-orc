"""ECVRF-EDWARDS25519-SHA512-TAI against RFC 9381 Appendix B.3 vectors."""
from __future__ import annotations

import pytest

from tcl.consensus import vrf

V = [  # (SK, PK, alpha, pi, beta) -- RFC 9381 Examples 16, 17, 18
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "8657106690b5526245a92b003bb079ccd1a92130477671f6fc01ad16f26f723f26f8a57ccaed74ee1b190bed1f"
     "479d9727d2d0f9b005a6e456a35d4fb0daab1268a1b0db10836d9826a528ca76567805",
     "90cf1df3b703cce59e2a35b925d411164068269d7b2d29f3301c03dd757876ff66b71dda49d2de59d03450451a"
     "f026798e8f81cd2e333de5cdf4f3e140fdd8ae"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "f3141cd382dc42909d19ec5110469e4feae18300e94f304590abdced48aed5933bf0864a62558b3ed7f2fea45c"
     "92a465301b3bbf5e3e54ddf2d935be3b67926da3ef39226bbc355bdc9850112c8f4b02",
     "eb4440665d3891d668e7e0fcaf587f1b4bd7fbfe99d0eb2211ccec90496310eb5e33821bc613efb94db5e5b54c"
     "70a848a0bef4553a41befc57663b56373a5031"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "9bc0f79119cc5604bf02d23b4caede71393cedfbb191434dd016d30177ccbf8096bb474e53895c362d8628ee9f"
     "9ea3c0e52c7a5c691b6c18c9979866568add7a2d41b00b05081ed0f58ee5e31b3a970e",
     "645427e5d00c62a23fb703732fa5d892940935942101e456ecca7bb217c61c452118fec1219202a0edcf038bb6"
     "373241578be7217ba85a2687f7a0310b2df19f"),
]


@pytest.mark.parametrize("sk,pk,alpha,pi,beta", V)
def test_rfc9381_vectors(sk, pk, alpha, pi, beta):
    sk, pk, alpha = bytes.fromhex(sk), bytes.fromhex(pk), bytes.fromhex(alpha)
    assert vrf.prove(sk, alpha).hex() == pi
    assert vrf.proof_to_hash(bytes.fromhex(pi)).hex() == beta
    assert vrf.verify(pk, alpha, bytes.fromhex(pi)).hex() == beta


def test_rejects_tampering():
    sk, pk, alpha, pi, _ = (bytes.fromhex(x) for x in V[1])
    assert vrf.verify(pk, alpha + b"x", pi) is None
    for i in (0, 40, 60):
        bad = bytearray(pi)
        bad[i] ^= 1
        assert vrf.verify(pk, alpha, bytes(bad)) is None
    assert vrf.verify(bytes.fromhex(V[0][1]), alpha, pi) is None      # wrong key
    assert vrf.verify(pk, alpha, pi[:79]) is None
    big_s = pi[:48] + (2**253).to_bytes(32, "little")
    assert vrf.verify(pk, alpha, big_s) is None
    assert vrf.verify(bytes(32), alpha, pi) is None                    # small-order / invalid
