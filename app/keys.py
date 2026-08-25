"""Simulates the *user's device* holding a private key that signs mandates.

In a real AP2/ACP-style system the user's wallet/device signs the mandate and
only the signature + public key travel to the merchant/agent infrastructure.
Here we generate that keypair once and persist it locally to keep demo runs
deterministic. The private key never leaves this module.
"""

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

KEY_DIR = Path(os.getenv("SENTINEL_KEY_DIR", "./data/keys"))
KEY_DIR.mkdir(parents=True, exist_ok=True)
PRIVATE_KEY_PATH = KEY_DIR / "user_private_key.pem"
PUBLIC_KEY_PATH = KEY_DIR / "user_public_key.pem"


def _generate_and_save() -> Ed25519PrivateKey:
    private_key = Ed25519PrivateKey.generate()
    PRIVATE_KEY_PATH.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    PUBLIC_KEY_PATH.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_key


def load_or_create_private_key() -> Ed25519PrivateKey:
    if PRIVATE_KEY_PATH.exists():
        return serialization.load_pem_private_key(PRIVATE_KEY_PATH.read_bytes(), password=None)
    return _generate_and_save()


def load_public_key() -> Ed25519PublicKey:
    if not PUBLIC_KEY_PATH.exists():
        load_or_create_private_key()
    return serialization.load_pem_public_key(PUBLIC_KEY_PATH.read_bytes())
