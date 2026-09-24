"""Decrypt the optional desktop transfer archive into the repository root.

Run after installing cryptography: python -m pip install cryptography
The transfer key is supplied separately and is never stored in Git.
"""

import argparse
from pathlib import Path
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "transfer" / "desktop-transfer.zip.enc"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", help="64-character hex transfer key")
    args = parser.parse_args()
    key = bytes.fromhex(args.key)
    if len(key) != 32:
        parser.error("The transfer key must be 64 hex characters")
    payload = ARCHIVE.read_bytes()
    if payload[:8] != b"CLXFER01":
        raise ValueError("Unrecognized transfer archive")
    decrypted = AESGCM(key).decrypt(payload[8:20], payload[20:], None)
    zip_path = ROOT / ".local" / "desktop-transfer-decrypted.zip"
    zip_path.parent.mkdir(exist_ok=True)
    zip_path.write_bytes(decrypted)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (ROOT / member.filename).resolve()
            if not target.is_relative_to(ROOT.resolve()):
                raise ValueError(f"Unsafe archive path: {member.filename}")
        archive.extractall(ROOT)
    print("Transfer data restored to .env and .local/.")


if __name__ == "__main__":
    main()
